#!/usr/bin/env python3
# Copyright 2026 EPFL, Politecnico di Torino, and Universidad Politecnica de Madrid.
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Description: Run every cpu_* and strela_* application on the Verilator model,
# collect the cycle count each one reports over UART, and pair the two families
# up into a speedup report.
#
# The two families measure the same thing in two different places, which is
# exactly what makes them comparable:
#   - cpu_*    reads mcycle around the kernel call and PRINTFs "Total cycles: N".
#   - strela_* reads STRELA's own performance counters after the run and PRINTFs
#              "TOT/CFG/TAB/STL: N", TOT being the whole accelerated execution
#              (configuration + descriptor fetch + streaming).
# So `speedup = cpu TOTal cycles / strela TOT` compares kernel against kernel,
# with the CPU-side setup excluded on both sides. The whole-program number the
# testbench prints ("Simulation finished after N clock cycles") is recorded too,
# but no speedup is derived from it: it includes boot, the golden check and --
# because this script has to enable them, see below -- the UART printfs.
#
# Three practical points, all of them lessons from CLAUDE.md:
#   1. Most cpu_* apps ship with PRINTF_IN_SIM 0 (printf is disabled in
#      simulation for speed), so they say nothing over UART. The value is a
#      literal `#define` in main.c, so -D cannot override it; this script
#      patches main.c to 1 for the compile and restores it immediately after,
#      leaving the working tree untouched between builds.
#   2. Every app compiles to the same sw/build/main.hex, so the builds run
#      serially up front and each hex is stashed in its own run directory.
#   3. tb/tb_top.cpp dumps an FST waveform unconditionally, which costs far more
#      than the cycle counts suggest (~2 kcycles/s), so the simulations run in
#      parallel, one working directory each -- sharing one would clobber
#      uart0.log and waveform.fst. The waveforms are deleted as they are
#      produced unless --keep-waves is given.
#
# A deadlocked fabric never ends the simulation (Vtestharness has no max-cycle
# cutoff), so every run is under a timeout and an expired timeout is reported as
# TIMEOUT -- for a strela_* app that is the deadlock signal.
#
# Usage:
#   python3 scripts/sim/bench_apps.py
#   python3 scripts/sim/bench_apps.py --apps strela_fir cpu_fir --jobs 4
#   python3 scripts/sim/bench_apps.py --rerun            # reuse the built hexes
#   python3 scripts/sim/bench_apps.py --report-only      # re-parse a finished run

import argparse
import concurrent.futures
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS_DIR = REPO_ROOT / "sw" / "applications"
MAIN_HEX = REPO_ROOT / "sw" / "build" / "main.hex"
ENV_WRAPPER = REPO_ROOT / "scripts" / "gr_heep_env.sh"
VERILATOR_MODEL = (
    REPO_ROOT / "build" / "x-heep_systems_gr-heep_0" / "sim-verilator" / "Vtestharness"
)

# Apps that are not benchmarks: strela_test is a bring-up/bypass app and
# strela_fully_connected is a legacy TFLM-style app with no golden dataset.
EXCLUDED = {"strela_test", "strela_fully_connected"}

# strela_<name> -> cpu_<name> wherever the two families disagree on the name.
TWIN_OVERRIDES = {
    "2mm": "twomm",
    "3mm": "threemm",
    "gesummv_single": "gesummv",
}

# tb/XHEEP_CmdLineOptions.hh: CLK_FREQUENCY_kHz (100*1000).
CLK_FREQUENCY_HZ = 100e6

CPU_CYCLES_RE = re.compile(r"Total cycles:\s*(\d+)")
DATA_SIZE_RE = re.compile(r"Data size:\s*(.+)")
STRELA_CTR_RE = {
    "tot": re.compile(r"^TOT:\s*(\d+)", re.MULTILINE),
    "cfg": re.compile(r"^CFG:\s*(\d+)", re.MULTILINE),
    "tab": re.compile(r"^TAB:\s*(\d+)", re.MULTILINE),
    "stl": re.compile(r"^STL:\s*(\d+)", re.MULTILINE),
}
SIM_CYCLES_RE = re.compile(r"Simulation finished after (\d+) clock cycles")
EXIT_VALUE_RE = re.compile(r"Program Finished with value (\d+)")
PRINTF_IN_SIM_RE = re.compile(r"^(#define\s+PRINTF_IN_SIM\s+)0(\s*)$", re.MULTILINE)


class Result:
    """One app's build + simulation outcome."""

    def __init__(self, name):
        self.name = name
        self.family = "cpu" if name.startswith("cpu_") else "strela"
        self.kernel = name.split("_", 1)[1]
        self.status = "NOT RUN"
        self.cycles = None  # kernel cycles: mcycle delta (cpu) or TOT (strela)
        self.counters = {}  # strela only: cfg / tab / stl
        self.sim_cycles = None  # whole program, testbench-reported
        self.data_size = None
        self.exit_value = None
        self.wall_s = None
        self.note = ""

    @property
    def ok(self):
        return self.status == "OK"


def discover_apps(selection):
    """Every cpu_*/strela_* app directory holding a main.c, minus EXCLUDED."""
    apps = sorted(
        p.name
        for p in APPLICATIONS_DIR.iterdir()
        if p.is_dir()
        and (p.name.startswith("cpu_") or p.name.startswith("strela_"))
        and (p / "main.c").is_file()
        and p.name not in EXCLUDED
    )
    if selection:
        unknown = set(selection) - set(apps)
        if unknown:
            sys.exit(f"error: unknown or excluded app(s): {', '.join(sorted(unknown))}")
        apps = [a for a in apps if a in selection]
    return apps


def twin_of(strela_app):
    """The cpu_* app a strela_* app should be compared against, if any."""
    kernel = strela_app.split("_", 1)[1]
    return "cpu_" + TWIN_OVERRIDES.get(kernel, kernel)


def run_make(args, log_path):
    """Run a make target through the toolchain wrapper, teeing into log_path."""
    proc = subprocess.run(
        [str(ENV_WRAPPER), "make", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    log_path.write_text(proc.stdout + proc.stderr)
    return proc.returncode == 0


def build_app(app, run_dir):
    """Compile one app with printf enabled in simulation and stash its hex.

    The PRINTF_IN_SIM patch is reverted before returning, whatever happens, so
    the working tree is only ever modified for the duration of one compile.
    """
    main_c = APPLICATIONS_DIR / app / "main.c"
    original = main_c.read_text()
    patched, n = PRINTF_IN_SIM_RE.subn(r"\g<1>1\g<2>", original)
    if n:
        main_c.write_text(patched)
    try:
        ok = run_make(["app", f"PROJECT={app}"], run_dir / "build.log")
    finally:
        if n:
            main_c.write_text(original)

    if not ok:
        return False, "make app failed"
    if not MAIN_HEX.is_file():
        return False, "no sw/build/main.hex produced"
    shutil.copy2(MAIN_HEX, run_dir / "main.hex")
    return True, ""


def simulate_app(app, run_dir, timeout_s, keep_waves):
    """Run one app on the Verilator model in its own working directory."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [str(VERILATOR_MODEL), f"+firmware={run_dir / 'main.hex'}"],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout, timed_out = proc.stdout + proc.stderr, False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode(errors="replace") + (
            exc.stderr or b""
        ).decode(errors="replace")
        timed_out = True
    wall_s = time.monotonic() - started

    (run_dir / "sim.log").write_text(stdout)
    if not keep_waves:
        (run_dir / "waveform.fst").unlink(missing_ok=True)
    return stdout, timed_out, wall_s


def parse_run(result, run_dir, stdout, timed_out):
    """Fill a Result from the app's UART log and the testbench's own output."""
    uart_path = run_dir / "uart0.log"
    uart = uart_path.read_text(errors="replace") if uart_path.is_file() else ""

    match = SIM_CYCLES_RE.search(stdout)
    if match:
        result.sim_cycles = int(match.group(1))
    match = EXIT_VALUE_RE.search(stdout)
    if match:
        result.exit_value = int(match.group(1))
    match = DATA_SIZE_RE.search(uart)
    if match:
        result.data_size = match.group(1).strip()

    if result.family == "cpu":
        match = CPU_CYCLES_RE.search(uart)
        if match:
            result.cycles = int(match.group(1))
    else:
        for key, pattern in STRELA_CTR_RE.items():
            match = pattern.search(uart)
            if match:
                result.counters[key] = int(match.group(1))
        result.cycles = result.counters.pop("tot", None)

    if timed_out:
        result.status = "TIMEOUT"
        result.note = "no end of simulation (deadlock?)"
    elif result.exit_value is None:
        result.status = "NO EXIT"
        result.note = "simulation ended without 'Program Finished'"
    elif result.exit_value != 0:
        result.status = "FAIL"
        result.note = f"self-check returned {result.exit_value}"
    elif result.cycles is None:
        result.status = "NO CYCLES"
        result.note = "nothing matching the cycle count on UART"
    else:
        result.status = "OK"


def us(cycles):
    return cycles / CLK_FREQUENCY_HZ * 1e6


def write_csv(results, path):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "app",
                "family",
                "kernel",
                "status",
                "data_size",
                "kernel_cycles",
                "kernel_us_at_100mhz",
                "strela_cfg_cycles",
                "strela_tab_cycles",
                "strela_stl_cycles",
                "whole_program_cycles",
                "sim_wall_s",
                "note",
            ]
        )
        for res in results:
            writer.writerow(
                [
                    res.name,
                    res.family,
                    res.kernel,
                    res.status,
                    res.data_size or "",
                    res.cycles if res.cycles is not None else "",
                    f"{us(res.cycles):.2f}" if res.cycles is not None else "",
                    res.counters.get("cfg", ""),
                    res.counters.get("tab", ""),
                    res.counters.get("stl", ""),
                    res.sim_cycles if res.sim_cycles is not None else "",
                    f"{res.wall_s:.1f}" if res.wall_s is not None else "",
                    res.note,
                ]
            )


def build_report(results, elapsed_s):
    """Render the markdown report: the speedup table first, then the details."""
    by_name = {res.name: res for res in results}
    strela_apps = [r for r in results if r.family == "strela"]
    cpu_apps = [r for r in results if r.family == "cpu"]

    lines = []
    lines.append("# GR-HEEP CPU vs STRELA benchmark")
    lines.append("")
    lines.append(
        f"{len(results)} applications, Verilator, "
        f"{time.strftime('%Y-%m-%d %H:%M')}, {elapsed_s / 60:.1f} min wall."
    )
    lines.append("")
    lines.append(
        "Cycles are what each app reports over UART: `Total cycles` (mcycle around "
        "the kernel) for `cpu_*`, `TOT` (STRELA's own performance counter over the "
        "whole accelerated execution) for `strela_*`. Times assume the 100 MHz "
        "simulation clock. `CFG`/`TAB`/`STL` split STRELA's `TOT` into "
        "configuration, descriptor-table fetch and stall cycles."
    )
    lines.append("")

    # --- Speedups -----------------------------------------------------------
    paired, unpaired = [], []
    for res in sorted(strela_apps, key=lambda r: r.name):
        twin = by_name.get(twin_of(res.name))
        if twin is not None and twin.ok and res.ok:
            paired.append((res, twin))
        else:
            unpaired.append((res, twin))

    lines.append("## Speedup")
    lines.append("")
    if paired:
        lines.append(
            "| Kernel | Shape | CPU cycles | STRELA cycles | Speedup | "
            "CPU us | STRELA us | CFG | TAB | STL |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for res, twin in sorted(
            paired, key=lambda p: p[1].cycles / p[0].cycles, reverse=True
        ):
            speedup = twin.cycles / res.cycles
            lines.append(
                f"| {res.name} | {twin.data_size or '-'} | {twin.cycles:,} | "
                f"{res.cycles:,} | **{speedup:.1f}x** | {us(twin.cycles):,.1f} | "
                f"{us(res.cycles):,.1f} | {res.counters.get('cfg', '-')} | "
                f"{res.counters.get('tab', '-')} | {res.counters.get('stl', '-')} |"
            )
        speedups = [t.cycles / r.cycles for r, t in paired]
        geomean = 1.0
        for value in speedups:
            geomean *= value
        geomean **= 1.0 / len(speedups)
        lines.append("")
        lines.append(
            f"Geometric mean over {len(paired)} pairs: **{geomean:.1f}x** "
            f"(min {min(speedups):.1f}x, max {max(speedups):.1f}x)."
        )
    else:
        lines.append("_No cpu/strela pair completed successfully._")
    lines.append("")

    if unpaired:
        lines.append("### Not paired")
        lines.append("")
        lines.append("| App | Status | Twin | Twin status |")
        lines.append("|---|---|---|---|")
        for res, twin in unpaired:
            twin_name = twin_of(res.name)
            lines.append(
                f"| {res.name} | {res.status} | {twin_name} | "
                f"{twin.status if twin else 'missing'} |"
            )
        lines.append("")

    orphan_cpu = [
        r for r in cpu_apps if not any(twin_of(s.name) == r.name for s in strela_apps)
    ]
    if orphan_cpu:
        lines.append(
            "CPU apps with no accelerator counterpart: "
            + ", ".join(f"`{r.name}` ({r.status})" for r in orphan_cpu)
            + "."
        )
        lines.append("")

    # --- Per-app detail -----------------------------------------------------
    for family, title in (("cpu", "CPU baselines"), ("strela", "STRELA")):
        rows = [r for r in results if r.family == family]
        lines.append(f"## {title}")
        lines.append("")
        if family == "cpu":
            lines.append(
                "| App | Status | Shape | Kernel cycles | us | Whole program |"
            )
            lines.append("|---|---|---|---:|---:|---:|")
            for res in rows:
                cycles = f"{res.cycles:,}" if res.cycles is not None else "-"
                micros = f"{us(res.cycles):,.1f}" if res.cycles is not None else "-"
                whole = f"{res.sim_cycles:,}" if res.sim_cycles is not None else "-"
                lines.append(
                    f"| {res.name} | {res.status} | {res.data_size or '-'} | "
                    f"{cycles} | {micros} | {whole} |"
                )
        else:
            lines.append(
                "| App | Status | TOT | CFG | TAB | STL | TAB % | us | Whole program |"
            )
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
            for res in rows:
                tab = res.counters.get("tab")
                cycles = f"{res.cycles:,}" if res.cycles is not None else "-"
                micros = f"{us(res.cycles):,.1f}" if res.cycles is not None else "-"
                whole = f"{res.sim_cycles:,}" if res.sim_cycles is not None else "-"
                tab_pct = (
                    f"{100 * tab / res.cycles:.0f}%"
                    if tab is not None and res.cycles
                    else "-"
                )
                lines.append(
                    f"| {res.name} | {res.status} | {cycles} | "
                    f"{res.counters.get('cfg', '-')} | {res.counters.get('tab', '-')} | "
                    f"{res.counters.get('stl', '-')} | {tab_pct} | {micros} | {whole} |"
                )
        lines.append("")

    failures = [r for r in results if not r.ok]
    if failures:
        lines.append("## Failures")
        lines.append("")
        lines.append("| App | Status | Note |")
        lines.append("|---|---|---|")
        for res in failures:
            lines.append(f"| {res.name} | {res.status} | {res.note} |")
        lines.append("")

    lines.append(
        "_Whole-program cycles are the testbench's own count (boot, data init, "
        "kernel, golden check and the UART printfs this script has to enable to "
        "read the numbers at all), so they are listed for reference only and no "
        "speedup is derived from them._"
    )
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark every cpu_* and strela_* app on Verilator."
    )
    parser.add_argument(
        "--apps", nargs="+", help="Only these apps (default: all of them)."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / "build" / "bench",
        help="Where the per-app run directories and the report go.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, min(os.cpu_count() or 1, 32)),
        help="Simulations to run in parallel (builds are always serial).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5400,
        help="Per-app simulation timeout in seconds.",
    )
    parser.add_argument(
        "--keep-waves", action="store_true", help="Keep each run's waveform.fst."
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Skip the build phase and reuse the hexes already in --work-dir.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Only re-parse the logs already in --work-dir.",
    )
    args = parser.parse_args()

    apps = discover_apps(args.apps)
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    results = {app: Result(app) for app in apps}
    started = time.monotonic()

    if not args.report_only and not VERILATOR_MODEL.is_file():
        print(
            f"Verilator model missing, building it ({VERILATOR_MODEL})...", flush=True
        )
        if not run_make(["verilator-build"], work_dir / "verilator-build.log"):
            sys.exit(
                f"error: make verilator-build failed, see {work_dir}/verilator-build.log"
            )

    # --- Build phase (serial: every app compiles to the same main.hex) -------
    runnable = []
    for index, app in enumerate(apps, 1):
        run_dir = work_dir / app
        run_dir.mkdir(parents=True, exist_ok=True)
        if args.report_only:
            runnable.append(app)
            continue
        if args.rerun:
            if (run_dir / "main.hex").is_file():
                runnable.append(app)
            else:
                results[app].status = "NO HEX"
                results[app].note = "no main.hex in the work dir, drop --rerun"
            continue
        print(f"[{index}/{len(apps)}] building {app}...", flush=True)
        ok, error = build_app(app, run_dir)
        if ok:
            runnable.append(app)
        else:
            results[app].status = "BUILD FAIL"
            results[app].note = f"{error}, see {run_dir / 'build.log'}"
            print(f"    build failed: {error}", flush=True)

    # --- Simulation phase (parallel: one working directory per app) ---------
    if not args.report_only:
        print(
            f"\nSimulating {len(runnable)} apps, {args.jobs} at a time "
            f"(timeout {args.timeout}s each). This takes a while: the testbench "
            f"dumps a waveform, so Verilator runs at roughly 2 kcycles/s.\n",
            flush=True,
        )
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    simulate_app, app, work_dir / app, args.timeout, args.keep_waves
                ): app
                for app in runnable
            }
            for future in concurrent.futures.as_completed(futures):
                app = futures[future]
                stdout, timed_out, wall_s = future.result()
                res = results[app]
                res.wall_s = wall_s
                parse_run(res, work_dir / app, stdout, timed_out)
                done += 1
                cycles = f"{res.cycles:,} cycles" if res.cycles is not None else "-"
                print(
                    f"[{done}/{len(runnable)}] {app}: {res.status}, {cycles} "
                    f"({wall_s / 60:.1f} min sim)",
                    flush=True,
                )
    else:
        for app in runnable:
            run_dir = work_dir / app
            sim_log = run_dir / "sim.log"
            stdout = sim_log.read_text(errors="replace") if sim_log.is_file() else ""
            parse_run(results[app], run_dir, stdout, timed_out=False)

    ordered = [results[app] for app in apps]
    elapsed_s = time.monotonic() - started

    csv_path = work_dir / "bench_results.csv"
    report_path = work_dir / "bench_report.md"
    write_csv(ordered, csv_path)
    report = build_report(ordered, elapsed_s)
    report_path.write_text(report)

    print("\n" + report)
    print(f"CSV:    {csv_path}")
    print(f"Report: {report_path}")

    return 1 if any(not r.ok for r in ordered) else 0


if __name__ == "__main__":
    sys.exit(main())
