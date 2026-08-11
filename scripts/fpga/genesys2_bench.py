#!/usr/bin/env python3
# Copyright 2026 EPFL
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Description: Sweep the CPU polybench apps (cpu_gemm, cpu_gemver, cpu_gesummv,
# cpu_threemm) over a set of square-matrix sizes on the real Genesys2 FPGA
# board and collect the CPU cycle count each run prints over UART.
#
# For each (app, size) combination this script:
#   1. Regenerates sw/applications/<app>/dataset.h for square matrices of the
#      given size, using that app's gen_data.py.
#   2. Builds it for the board: `make app TARGET=genesys2 PROJECT=<app>`.
#   3. Loads and runs the resulting ELF over JTAG via a non-interactive GDB
#      batch script (pagination disabled so it never blocks on a pager), then
#      reads the app's UART output (it PRINTFs "Total cycles: N" and
#      "SUCCESS!!!"/"FAIL!!!" on real hardware) to record the result.
#
# Prerequisites (not automated by this script):
#   - source /tools/env_x-heep.sh
#   - OpenOCD already running and attached to the board, e.g.:
#       make openOCD_epflp
#   - No other program (picocom, screen, ...) holding the UART device open.
#
# Usage:
#   python3 scripts/fpga/genesys2_bench.py
#   python3 scripts/fpga/genesys2_bench.py --apps cpu_gemm cpu_threemm --sizes 16 32
#   python3 scripts/fpga/genesys2_bench.py --uart-port /dev/ttyUSB2 --uart-baud 9600

import argparse
import csv
import glob
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit(
        "error: pyserial is required (pip install pyserial) to read UART output."
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS_DIR = REPO_ROOT / "sw" / "applications"
MAIN_ELF = REPO_ROOT / "sw" / "build" / "main.elf"

GDB_PORT = 3333

# Maps each app to the gen_data.py argv for a square problem of size N.
APP_GEN_ARGS = {
    "cpu_gemm": lambda n: [str(n)] * 3,  # NI NJ NK
    "cpu_gemver": lambda n: [str(n)],  # N
    "cpu_gesummv": lambda n: [str(n)],  # N
    "cpu_threemm": lambda n: [str(n)] * 5,  # NI NJ NK NL NM
    "cpu_atax": lambda n: [str(n), str(n)],  # M N (square: ATAX_M == ATAX_N)
    "cpu_mvt": lambda n: [str(n)],  # N
    "cpu_jacobi1d": lambda n: [str(n), str(n)],  # TSTEPS N (both scale with size)
}

CYCLES_RE = re.compile(r"Total cycles:\s*(\d+)")
DONE_RE = re.compile(r"SUCCESS|FAIL")

GDB_SCRIPT_TEMPLATE = """\
set pagination off
set confirm off
set remotetimeout 2000
target extended-remote :{port}
monitor reset halt
load
monitor resume
detach
quit
"""


def find_riscv_gdb():
    riscv_xheep = subprocess.os.environ.get("RISCV_XHEEP")
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
        "Did you `source /tools/env_x-heep.sh`?"
    )


def check_openocd_running():
    try:
        with socket.create_connection(("localhost", GDB_PORT), timeout=2):
            pass
    except OSError:
        sys.exit(
            f"error: nothing is listening on localhost:{GDB_PORT}.\n"
            "Start OpenOCD in another terminal first, e.g.: make openOCD_epflp"
        )


def generate_dataset(app, size):
    app_dir = APPLICATIONS_DIR / app
    gen_script = app_dir / "gen_data.py"
    args = APP_GEN_ARGS[app](size)
    result = subprocess.run(
        [sys.executable, str(gen_script), *args],
        cwd=app_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gen_data.py failed for {app} N={size}:\n{result.stderr}")
    (app_dir / "dataset.h").write_text(result.stdout)


def build_app(app):
    subprocess.run(
        ["make", "app", "TARGET=genesys2", f"PROJECT={app}"],
        cwd=REPO_ROOT,
        check=True,
    )


def flash_and_run(gdb_bin, gdb_timeout):
    """Load main.elf over JTAG, resume the core, and detach.

    Deliberately avoids GDB's own `continue`: that blocks GDB waiting for a
    stop-reply that never comes (the app free-runs on the board), and killing
    GDB afterwards is unreliable because its remote-detach cleanup can itself
    hang waiting on a target that's busy running the program. `monitor
    resume` instead asks OpenOCD directly to resume the core (a single
    request/response, does not put GDB in a wait state), so `detach` and
    `quit` right after return immediately with the core left running.
    `timeout -k` is still a safety net in case the JTAG link stalls.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".gdb", delete=False
    ) as gdb_script_file:
        gdb_script_file.write(GDB_SCRIPT_TEMPLATE.format(port=GDB_PORT))
        gdb_script_path = gdb_script_file.name

    try:
        result = subprocess.run(
            [
                "timeout",
                "-k",
                "10",
                str(gdb_timeout),
                gdb_bin,
                "-batch",
                "-nx",
                "-x",
                gdb_script_path,
                str(MAIN_ELF),
            ],
        )
        if result.returncode == 124:
            print("    warning: gdb timed out and was killed (JTAG link stuck?)")
        elif result.returncode != 0:
            print(f"    warning: gdb exited with code {result.returncode}")
    finally:
        Path(gdb_script_path).unlink(missing_ok=True)


def capture_uart(port, baud, done_timeout, out_lines):
    with serial.Serial(port, baud, timeout=1) as ser:
        ser.reset_input_buffer()
        deadline = time.time() + done_timeout
        while time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            out_lines.append(line)
            print(f"    UART: {line}")
            if DONE_RE.search(line):
                return


def run_one(app, size, gdb_bin, uart_port, uart_baud, uart_timeout, gdb_timeout):
    print(f"=== {app} N={size} ===")
    generate_dataset(app, size)
    build_app(app)

    lines = []
    uart_thread = threading.Thread(
        target=capture_uart, args=(uart_port, uart_baud, uart_timeout, lines)
    )
    uart_thread.start()
    time.sleep(0.5)  # let the serial port open before the board resets

    flash_and_run(gdb_bin, gdb_timeout)
    uart_thread.join(timeout=uart_timeout + 10)

    cycles = None
    status = "TIMEOUT"
    for line in lines:
        m = CYCLES_RE.search(line)
        if m:
            cycles = int(m.group(1))
        if "SUCCESS" in line:
            status = "SUCCESS"
        elif "FAIL" in line:
            status = "FAIL"

    print(f"    -> status={status} cycles={cycles}")
    return {"app": app, "size": size, "status": status, "cycles": cycles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apps", nargs="+", default=list(APP_GEN_ARGS), choices=list(APP_GEN_ARGS)
    )
    parser.add_argument("--sizes", nargs="+", type=int, default=[16, 32, 64, 128])
    parser.add_argument("--uart-port", default="/dev/ttyUSB2")
    parser.add_argument("--uart-baud", type=int, default=9600)
    parser.add_argument(
        "--uart-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait on UART for SUCCESS!!!/FAIL!!! before giving up",
    )
    parser.add_argument(
        "--gdb-timeout",
        type=float,
        default=180.0,
        help="Safety-net seconds for the JTAG load+resume step "
        "(large datasets take a while to load over JTAG); this is a hard "
        "cap on a normally-quick operation, not the run's main wait",
    )
    parser.add_argument("--gdb-bin", default=None, help="Override the gdb binary path")
    parser.add_argument(
        "--results", default="genesys2_bench_results.csv", help="Output CSV path"
    )
    args = parser.parse_args()

    gdb_bin = args.gdb_bin or find_riscv_gdb()
    check_openocd_running()

    results = []
    results_path = REPO_ROOT / args.results
    with open(results_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["app", "size", "status", "cycles"])
        writer.writeheader()
        for app in args.apps:
            for size in args.sizes:
                try:
                    row = run_one(
                        app,
                        size,
                        gdb_bin,
                        args.uart_port,
                        args.uart_baud,
                        args.uart_timeout,
                        args.gdb_timeout,
                    )
                except Exception as exc:
                    print(f"    -> ERROR: {exc}")
                    row = {"app": app, "size": size, "status": "ERROR", "cycles": None}
                results.append(row)
                writer.writerow(row)
                f.flush()

    print(f"\nResults written to {results_path}")
    for row in results:
        print(row)


if __name__ == "__main__":
    main()
