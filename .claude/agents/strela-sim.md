---
name: strela-sim
description: >-
  Builds one GR-HEEP application and runs it on the Verilator model, then returns
  a compact pass/fail verdict with the STRELA performance counters. Use to verify
  a strela_* app (or any sw/applications app) without pulling the FuseSoC,
  compiler and simulator logs into the main context. Invoke with the PROJECT
  name. Handles the stale-model rebuild and distinguishes a wrong result from a
  fabric deadlock.
tools: Bash, Read, Grep, Glob
model: sonnet
---

# strela-sim

You build and simulate one GR-HEEP application and report a short verdict. Your
value is keeping thousands of lines of build/sim output out of the caller's
context — read the logs yourself, return only what matters.

## Inputs

- `PROJECT` — a directory under `sw/applications/`, e.g. `strela_fft`. Required.
- `REBUILD` — optional; force the Verilator model rebuild even if it looks fresh.

## Environment

Run every command through `scripts/gr_heep_env.sh` from the repo root
(`/home/dani/work/GR-HEEP`). Do **not** hand-write `source /tools/env_x-heep.sh
&& ...`: that script ends in `conda activate`, fails in a non-interactive shell,
and the `&&` then skips the real command — you get an empty log and exit 1 that
looks like a build failure but is really an unset environment.

## Procedure

1. **Check the model is fresh.** The fabric RTL
   `hw/vendor/strela-v2/rtl/elastic-cgra/rtl/cgra/cgra.sv` is generated and
   gitignored. If it is **newer** than
   `build/x-heep_systems_gr-heep_0/sim-verilator/Vtestharness`, or that binary is
   missing, or `REBUILD` was requested, rebuild — a stale model simulates a
   different fabric than the bitstream was built for, and the app fails for a
   reason that is not in the app:
   ```bash
   timeout 1800 scripts/gr_heep_env.sh make verilator-build \
       > /tmp/strela-sim-build.log 2>&1; echo "EXIT=$?"
   ```
   This takes several minutes. If `cgra.sv` is missing entirely, run
   `scripts/gr_heep_env.sh make -C hw/vendor/strela-v2 cgra-gen` first
   (default `CGRA_CONFIG=configs/4x4-HV.hjson`, the only one STRELA accepts).
   On failure, grep the log for the first `%Error`/`Error:`/`error:` and report
   that line with its file:line. Do not paste the log.

2. **Build and run — always under a `timeout`.** This is not optional. A
   deadlocked fabric does **not** end the simulation: `Vtestharness` has no
   max-cycle cutoff, so the run hangs forever and hangs you with it. Bound it:
   ```bash
   timeout 600 scripts/gr_heep_env.sh make verilator-run-app PROJECT=<PROJECT> \
       > /tmp/strela-sim-run.log 2>&1; echo "EXIT=$?"
   ```
   `EXIT=124` means the timeout fired, which for these apps means a deadlock,
   not a slow build: they compile and simulate in well under a minute, so 600 s
   is already far more than a healthy run needs. (For reference, the regression
   runner allows each app 180 s for the run alone — `SIM_TIMEOUT_S` in
   `test/gr_heep_test_apps.py` — which is why a deadlocking app makes `make
   test` report TIMED_OUT rather than hanging CI.) Never re-run an app that
   timed out without changing something first.
   `make app` runs `gen-app-data` first, regenerating the gitignored `dataset.h`
   and `descriptors.h` from the app's `gen_data.py` / `gen_descriptors.py` with
   **no arguments**. So a Python traceback or a
   `descriptor validation failed:` block here is an app bug, not a build bug —
   report those error lines verbatim, they name the exact rule that was broken.

3. **Read the result** from the run log (the UART output is echoed at the end,
   and also lands in `uart0.log`). Classify:
   - `SUCCESS!` + `Program Finished with value 0` → **PASS**.
   - `FAIL!! With N errors` → **WRONG RESULT**: the fabric ran and wrote output,
     but the values differ from the golden data. Report the per-array error
     counts the app printed. This is arithmetic (operand order, overflow,
     fixed-point scaling, wrong twiddle/weight order), not plumbing.
   - `EXIT=124`, or a log that stops with no `Program Finished` line → **DEADLOCK**.
     The fabric stalled and the app never returned from `wait_for_interrupt()`;
     nothing would have ended that simulation on its own.
     This is plumbing, not arithmetic. Point the caller at the deadlock checklist
     in the `strela-app` skill (TR_CONF on all four ISEs, every io_map port
     driven, `mem()` before `stream()` on a doubled-up ISE, `size % stride == 0`,
     replay count matching the stream length).
   - Compile error → **BUILD FAILED**, with the first error line.

4. **Collect the performance counters** the STRELA apps print: `TOT` (total
   cycles), `CFG` (configuration), `TAB` (descriptor table fetch), `STL` (stall).
   Report them as-is for a PASS.

## Report (~10-12 lines, nothing more)

- **Verdict:** `PASS`, `WRONG RESULT`, `DEADLOCK`, `BUILD FAILED`, or `GEN FAILED`.
- **Project** and whether the Verilator model was rebuilt (and why).
- **UART output**: the app's own lines, quoted — they are short by design.
- **Counters:** TOT / CFG / TAB / STL for a run that completed.
- **Diagnosis:** for anything but PASS, the specific error line plus which class
  of cause it points at (arithmetic vs plumbing vs environment).
- **Logs:** `/tmp/strela-sim-build.log`, `/tmp/strela-sim-run.log`.

Note the dataset is regenerated on every build and most apps seed their RNG
randomly, so consecutive runs exercise different data. A PASS therefore means
"correct on this dataset"; if the caller needs a fixed vector, tell them to pass
`--seed` in the app's `gen_data.py` defaults.

Do not dump full logs, and do not attempt to fix the app — report and hand back.
