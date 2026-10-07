#!/usr/bin/env python3
# Copyright 2026 EPFL, Politecnico di Torino, and Universidad Politecnica de Madrid.
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Description: Run the cpu_* and strela_* applications on the real Genesys2
# board and pair the two families up into a speedup report -- the hardware
# counterpart of scripts/sim/bench_apps.py, which does the same on Verilator.
#
# It is the same measurement in both places, which is what makes the two
# reports comparable with each other as well as internally:
#   - cpu_*    reads mcycle around the kernel call and PRINTFs "Total cycles: N".
#   - strela_* reads STRELA's own performance counters and PRINTFs "TOT/CFG/
#              TAB/STL: N", TOT covering the whole accelerated execution.
# So `speedup = cpu Total cycles / strela TOT`, kernel against kernel. Unlike
# the Verilator script this one has no whole-program number to report (the
# testbench's count does not exist on hardware) and needs no PRINTF_IN_SIM
# patching: every app already ships with PRINTF_IN_FPGA 1.
#
# For each app the script regenerates the app's headers, builds it for the
# board, loads the ELF over JTAG and reads the UART until the app reports
# SUCCESS/FAIL. Two practical points:
#   1. OpenOCD is started (and stopped) by this script unless one is already
#      listening on the GDB port, in which case that one is reused and left
#      alone. `make openOCD_epflp` is therefore no longer a prerequisite.
#   2. The load and the resume are two separate GDB batch runs. GDB never gets
#      to `continue` -- that blocks it waiting for a stop-reply that never comes
#      from a free-running board -- so `monitor resume` asks OpenOCD directly
#      and GDB detaches immediately. Splitting them also lets the serial input
#      buffer be flushed after the load and before the program starts, so a
#      run's log cannot contain the tail of the previous one.
# A hung app (a deadlocked fabric, say) never prints SUCCESS/FAIL, so every
# capture is under a timeout and an expired one is reported as TIMEOUT.
#
# Prerequisites:
#   - the toolchain environment: run through scripts/gr_heep_env.sh
#   - a Genesys2 programmed with the GR-HEEP bitstream, JTAG attached
#   - no other program (picocom, screen, ...) holding the UART device open
#
# Usage:
#   scripts/gr_heep_env.sh python3 scripts/fpga/genesys2_bench.py
#   scripts/gr_heep_env.sh python3 scripts/fpga/genesys2_bench.py \
#       --apps strela_v2_fir cpu_fir
#   # size sweep, for the cpu_* apps whose gen_data.py takes dimensions:
#   scripts/gr_heep_env.sh python3 scripts/fpga/genesys2_bench.py \
#       --apps cpu_gemm cpu_threemm --sizes 16 32 64

import argparse
import csv
import glob
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit("error: pyserial is required (pip install pyserial) to read UART output.")

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS_DIR = REPO_ROOT / "sw" / "applications"
MAIN_ELF = REPO_ROOT / "sw" / "build" / "main.elf"
XHEEP_DIR = REPO_ROOT / "hw" / "vendor" / "x-heep"
OPENOCD_CFG = XHEEP_DIR / "tb" / "core-v-mini-mcu-esl-programmer.cfg"

# The app list, the cpu/strela pairing and the UART patterns are shared with the
# Verilator benchmark so the two reports cannot drift apart.
sys.path.insert(0, str(REPO_ROOT / "scripts" / "sim"))
import bench_apps  # noqa: E402

GDB_PORT = 3333

# sw/device/target/genesys2/x-heep.h: REFERENCE_CLOCK_Hz / UART_BAUDRATE.
BOARD_CLOCK_HZ = 15e6
UART_BAUD = 9600

# gen_data.py argv for a square problem of size N, for the apps that take
# dimensions. Only used by --sizes; without it every app runs at its own
# committed default shape.
APP_GEN_ARGS = {
    "cpu_gemm": lambda n: [str(n)] * 3,  # NI NJ NK
    "cpu_gemver": lambda n: [str(n)],  # N
    "cpu_gesummv": lambda n: [str(n)],  # N
    "cpu_threemm": lambda n: [str(n)] * 5,  # NI NJ NK NL NM
    "cpu_twomm": lambda n: [str(n)] * 4,  # NI NJ NK NL
    "cpu_atax": lambda n: [str(n), str(n)],  # M N
    "cpu_bicg": lambda n: [str(n), str(n)],  # M N
    "cpu_mvt": lambda n: [str(n)],  # N
    "cpu_fw": lambda n: [str(n)],  # N
    "cpu_mm": lambda n: [str(n)] * 3,  # N M P
    "cpu_jacobi1d": lambda n: [str(n), str(n)],  # TSTEPS N
}

DONE_RE = re.compile(r"SUCCESS|FAIL")

GDB_LOAD_SCRIPT = """\
set pagination off
set confirm off
set remotetimeout 2000
target extended-remote :{port}
monitor reset halt
load
detach
quit
"""

GDB_RESUME_SCRIPT = """\
set pagination off
set confirm off
set remotetimeout 2000
target extended-remote :{port}
monitor resume
detach
quit
"""


# --- Toolchain and board plumbing -------------------------------------------


def find_riscv_gdb():
    riscv_xheep = os.environ.get("RISCV_XHEEP")
    if riscv_xheep:
        candidates = glob.glob(f"{riscv_xheep}/bin/*-elf-gdb")
        if candidates:
            return candidates[0]
    for name in ("riscv32-corev-elf-gdb", "riscv32-unknown-elf-gdb"):
        found = subprocess.run(
            ["which", name], capture_output=True, text=True
        ).stdout.strip()
        if found:
            return found
    sys.exit(
        "error: could not find a RISC-V gdb binary. "
        "Did you run through scripts/gr_heep_env.sh?"
    )


def gdb_port_open():
    try:
        with socket.create_connection(("localhost", GDB_PORT), timeout=2):
            return True
    except OSError:
        return False


def start_openocd(log_path, wait_s=30):
    """Start OpenOCD unless one is already listening; returns the process or None."""
    if gdb_port_open():
        print(f"OpenOCD already listening on :{GDB_PORT}, reusing it.", flush=True)
        return None

    log = log_path.open("w")
    proc = subprocess.Popen(
        ["openocd", "-f", str(OPENOCD_CFG)],
        cwd=XHEEP_DIR,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit(
                f"error: OpenOCD exited with code {proc.returncode}. "
                f"See {log_path}.\n" + log_path.read_text()[-2000:]
            )
        if gdb_port_open():
            print(f"OpenOCD started (pid {proc.pid}), log in {log_path}.", flush=True)
            return proc
        time.sleep(0.5)

    proc.terminate()
    sys.exit(
        f"error: OpenOCD did not open :{GDB_PORT} within {wait_s}s. "
        f"Is the board powered and the FPGA programmed? See {log_path}."
    )


def stop_openocd(proc):
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_gdb(gdb_bin, script_body, timeout_s, log_path):
    """Run one GDB batch script against the board; returns True on success."""
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False) as handle:
        handle.write(script_body.format(port=GDB_PORT))
        script_path = handle.name
    try:
        proc = subprocess.run(
            [
                "timeout",
                "-k",
                "10",
                str(int(timeout_s)),
                gdb_bin,
                "-batch",
                "-nx",
                "-x",
                script_path,
                str(MAIN_ELF),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)

    with log_path.open("a") as log:
        log.write(proc.stdout + proc.stderr)
    if proc.returncode == 124:
        print("    warning: gdb timed out and was killed (JTAG link stuck?)")
        return False
    if proc.returncode != 0:
        print(f"    warning: gdb exited with code {proc.returncode}")
        return False
    return True


# --- Build and run ----------------------------------------------------------


def build_app(app, size, log_path):
    """Build one app for the board, at `size` if the sweep asked for one.

    `make app` regenerates dataset.h/descriptors.h itself (gen-app-data), so a
    size has to reach the generator through GEN_DATA_ARGS rather than by
    writing dataset.h here -- the build would overwrite it.
    """
    make_args = ["make", "app", "TARGET=genesys2", f"PROJECT={app}"]
    if size is not None:
        make_args.append(f"GEN_DATA_ARGS={' '.join(APP_GEN_ARGS[app](size))}")

    proc = subprocess.run(
        make_args, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    log_path.write_text(proc.stdout + proc.stderr)
    if proc.returncode != 0:
        return False, f"make app failed, see {log_path}"
    if not MAIN_ELF.is_file():
        return False, "no sw/build/main.elf produced"
    return True, ""


def capture_uart(ser, timeout_s, log_path):
    """Read the app's UART output until it reports SUCCESS/FAIL or time runs out."""
    lines = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        lines.append(line)
        print(f"    UART: {line}", flush=True)
        if DONE_RE.search(line):
            break
    log_path.write_text("\n".join(lines) + "\n")
    return lines


def parse_run(result, lines, loaded, timed_out):
    """Fill a Result from the app's UART lines."""
    uart = "\n".join(lines)

    match = bench_apps.DATA_SIZE_RE.search(uart)
    if match:
        result.data_size = match.group(1).strip()

    if result.family == "cpu":
        match = bench_apps.CPU_CYCLES_RE.search(uart)
        if match:
            result.cycles = int(match.group(1))
    else:
        for key, pattern in bench_apps.STRELA_CTR_RE.items():
            match = pattern.search(uart)
            if match:
                result.counters[key] = int(match.group(1))
        result.cycles = result.counters.pop("tot", None)

    if not loaded:
        result.status = "LOAD FAIL"
        result.note = "gdb could not load/resume the ELF"
    elif timed_out:
        result.status = "TIMEOUT"
        result.note = "no SUCCESS/FAIL on UART (hang?)"
    elif any("FAIL" in line for line in lines):
        result.status = "FAIL"
        result.note = next(line for line in lines if "FAIL" in line)
    elif result.cycles is None:
        result.status = "NO CYCLES"
        result.note = "nothing matching the cycle count on UART"
    else:
        result.status = "OK"


def run_one(app, size, gdb_bin, run_dir, args):
    """Build, load and run one app; returns a filled Result."""
    label = app if size is None else f"{app} N={size}"
    print(f"=== {label} ===", flush=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    result = bench_apps.Result(app)
    result.size = size
    started = time.monotonic()

    ok, error = build_app(app, size, run_dir / "build.log")
    if not ok:
        result.status = "BUILD FAIL"
        result.note = error
        print(f"    -> {result.status}: {error}", flush=True)
        return result

    gdb_log = run_dir / "gdb.log"
    gdb_log.write_text("")
    lines, timed_out = [], False
    with serial.Serial(args.uart_port, args.uart_baud, timeout=1) as ser:
        ser.reset_input_buffer()
        loaded = run_gdb(gdb_bin, GDB_LOAD_SCRIPT, args.load_timeout, gdb_log)
        if loaded:
            # Drop anything the previous program left in flight, then start.
            ser.reset_input_buffer()
            loaded = run_gdb(gdb_bin, GDB_RESUME_SCRIPT, args.load_timeout, gdb_log)
        if loaded:
            lines = capture_uart(ser, args.uart_timeout, run_dir / "uart.log")
            timed_out = not any(DONE_RE.search(line) for line in lines)

    result.wall_s = time.monotonic() - started
    parse_run(result, lines, loaded, timed_out)
    cycles = f"{result.cycles:,} cycles" if result.cycles is not None else "-"
    print(f"    -> {result.status}, {cycles}", flush=True)
    return result


# --- Reporting --------------------------------------------------------------


def ms(cycles):
    return cycles / BOARD_CLOCK_HZ * 1e3


def write_csv(results, path):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "app",
                "family",
                "kernel",
                "size",
                "status",
                "data_size",
                "kernel_cycles",
                f"kernel_ms_at_{BOARD_CLOCK_HZ / 1e6:g}mhz",
                "strela_cfg_cycles",
                "strela_tab_cycles",
                "strela_stl_cycles",
                "wall_s",
                "note",
            ]
        )
        for res in results:
            writer.writerow(
                [
                    res.name,
                    res.family,
                    res.kernel,
                    res.size if res.size is not None else "",
                    res.status,
                    res.data_size or "",
                    res.cycles if res.cycles is not None else "",
                    f"{ms(res.cycles):.3f}" if res.cycles is not None else "",
                    res.counters.get("cfg", ""),
                    res.counters.get("tab", ""),
                    res.counters.get("stl", ""),
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
    lines.append("# GR-HEEP CPU vs STRELA benchmark (Genesys2)")
    lines.append("")
    lines.append(
        f"{len(results)} applications on the real board at "
        f"{BOARD_CLOCK_HZ / 1e6:g} MHz, {time.strftime('%Y-%m-%d %H:%M')}, "
        f"{elapsed_s / 60:.1f} min wall."
    )
    lines.append("")
    lines.append(
        "Cycles are what each app reports over UART: `Total cycles` (mcycle around "
        "the kernel) for `cpu_*`, `TOT` (STRELA's own performance counter over the "
        "whole accelerated execution) for `strela_*`. `CFG`/`TAB`/`STL` split `TOT` "
        "into configuration, descriptor-table fetch and stall cycles. Times are the "
        "cycle counts at the board clock; nothing here includes the JTAG load or "
        "the UART printing."
    )
    lines.append("")

    # --- Speedups -----------------------------------------------------------
    paired, unpaired = [], []
    for res in sorted(strela_apps, key=lambda r: r.name):
        twin = by_name.get(bench_apps.twin_of(res.name))
        if twin is not None and twin.ok and res.ok:
            paired.append((res, twin))
        else:
            unpaired.append((res, twin))

    lines.append("## Speedup")
    lines.append("")
    if paired:
        lines.append(
            "| Kernel | Shape | CPU cycles | STRELA cycles | Speedup | "
            "CPU ms | STRELA ms | CFG | TAB | STL |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for res, twin in sorted(
            paired, key=lambda p: p[1].cycles / p[0].cycles, reverse=True
        ):
            speedup = twin.cycles / res.cycles
            lines.append(
                f"| {res.name} | {twin.data_size or '-'} | {twin.cycles:,} | "
                f"{res.cycles:,} | **{speedup:.1f}x** | {ms(twin.cycles):,.2f} | "
                f"{ms(res.cycles):,.2f} | {res.counters.get('cfg', '-')} | "
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
            lines.append(
                f"| {res.name} | {res.status} | {bench_apps.twin_of(res.name)} | "
                f"{twin.status if twin else 'missing'} |"
            )
        lines.append("")

    orphan_cpu = [
        r
        for r in cpu_apps
        if not any(bench_apps.twin_of(s.name) == r.name for s in strela_apps)
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
        if not rows:
            continue
        lines.append(f"## {title}")
        lines.append("")
        if family == "cpu":
            lines.append("| App | Status | Shape | Kernel cycles | ms |")
            lines.append("|---|---|---|---:|---:|")
            for res in rows:
                cycles = f"{res.cycles:,}" if res.cycles is not None else "-"
                millis = f"{ms(res.cycles):,.2f}" if res.cycles is not None else "-"
                lines.append(
                    f"| {res.name} | {res.status} | {res.data_size or '-'} | "
                    f"{cycles} | {millis} |"
                )
        else:
            lines.append("| App | Status | TOT | CFG | TAB | STL | TAB % | ms |")
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
            for res in rows:
                tab = res.counters.get("tab")
                cycles = f"{res.cycles:,}" if res.cycles is not None else "-"
                millis = f"{ms(res.cycles):,.2f}" if res.cycles is not None else "-"
                tab_pct = (
                    f"{100 * tab / res.cycles:.0f}%"
                    if tab is not None and res.cycles
                    else "-"
                )
                lines.append(
                    f"| {res.name} | {res.status} | {cycles} | "
                    f"{res.counters.get('cfg', '-')} | {res.counters.get('tab', '-')} | "
                    f"{res.counters.get('stl', '-')} | {tab_pct} | {millis} |"
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

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the cpu_* and strela_* apps on the Genesys2 board."
    )
    parser.add_argument(
        "--apps", nargs="+", help="Only these apps (default: all of them)."
    )
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        help="Sweep each app over these square problem sizes instead of running "
        "it once at its committed default shape. Only for the apps listed in "
        "APP_GEN_ARGS.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / "build" / "genesys2-bench",
        help="Where the per-app logs, the CSV and the report go.",
    )
    parser.add_argument("--uart-port", default="/dev/ttyUSB2")
    parser.add_argument("--uart-baud", type=int, default=UART_BAUD)
    parser.add_argument(
        "--uart-timeout",
        type=float,
        default=300.0,
        help="Seconds to wait on UART for SUCCESS/FAIL before giving up.",
    )
    parser.add_argument(
        "--load-timeout",
        type=float,
        default=600.0,
        help="Safety-net seconds for one JTAG step (a large dataset takes a "
        "while to load); a hard cap, not the run's main wait.",
    )
    parser.add_argument("--gdb-bin", default=None, help="Override the gdb binary path")
    parser.add_argument(
        "--no-openocd",
        action="store_true",
        help="Do not start OpenOCD; require one to be listening already.",
    )
    args = parser.parse_args()

    apps = bench_apps.discover_apps(args.apps)
    if args.sizes:
        unsupported = [a for a in apps if a not in APP_GEN_ARGS]
        if unsupported:
            sys.exit(
                "error: --sizes given but these apps have no size mapping: "
                + ", ".join(unsupported)
            )
    runs = [(app, size) for app in apps for size in (args.sizes or [None])]

    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    gdb_bin = args.gdb_bin or find_riscv_gdb()

    openocd = None
    if args.no_openocd:
        if not gdb_port_open():
            sys.exit(
                f"error: --no-openocd given but nothing is listening on "
                f"localhost:{GDB_PORT}."
            )
    else:
        openocd = start_openocd(work_dir / "openocd.log")

    print(
        f"\nRunning {len(runs)} builds on {args.uart_port} @ {args.uart_baud} baud, "
        f"logs in {work_dir}.\n",
        flush=True,
    )

    results = []
    started = time.monotonic()
    csv_path = work_dir / "genesys2_bench_results.csv"
    report_path = work_dir / "genesys2_bench_report.md"
    try:
        for index, (app, size) in enumerate(runs, 1):
            run_dir = work_dir / (app if size is None else f"{app}_N{size}")
            print(f"[{index}/{len(runs)}]", end=" ", flush=True)
            try:
                result = run_one(app, size, gdb_bin, run_dir, args)
            except Exception as exc:  # a board hiccup must not lose the sweep
                result = bench_apps.Result(app)
                result.size = size
                result.status = "ERROR"
                result.note = str(exc)
                print(f"    -> ERROR: {exc}", flush=True)
            results.append(result)
            # Rewritten after every app: a sweep this long should survive a
            # Ctrl-C or a board unplug with everything up to that point kept.
            write_csv(results, csv_path)
            report_path.write_text(build_report(results, time.monotonic() - started))
    finally:
        stop_openocd(openocd)

    report = build_report(results, time.monotonic() - started)
    report_path.write_text(report)
    print("\n" + report)
    print(f"CSV:    {csv_path}")
    print(f"Report: {report_path}")

    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
