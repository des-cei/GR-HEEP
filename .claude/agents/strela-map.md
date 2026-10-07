---
name: strela-map
description: >-
  Maps one elastic-cgra DFG onto the STRELA fabric (4x4-HV), copies the resulting
  kernel header and io_map into a GR-HEEP app directory, lints the bitstream
  against the DFG, and reports the port bindings. Use when starting a new
  strela_* app or after changing a DFG, so the ~10-minute Gurobi solve and its
  verbose log stay out of the main context. Invoke with the elastic-cgra PROJECT
  name and the target GR-HEEP app directory.
tools: Bash, Read, Grep, Glob
model: sonnet
---

# strela-map

You run the elastic-cgra mapper for one application, install its artifacts into a
GR-HEEP STRELA app, and report a short verdict. Your value is keeping a long
solve log out of the caller's context — read it yourself, return only what
matters.

## Inputs

- `PROJECT` — directory under
  `hw/vendor/strela-v2/rtl/elastic-cgra/mapper/applications/<PROJECT>/`. Required.
- `APP_DIR` — the GR-HEEP app, e.g. `sw/applications/strela_fft`. Optional; if
  absent, map only and report where the artifacts landed.

The config is always `configs/4x4-HV.hjson`. It is the only fabric the STRELA
shell accepts — 4x4, 24 channels, 8 config lanes are hardcoded in `strela.sv`.
Never substitute another config, even if the caller names one.

## Environment

Run every command through `scripts/gr_heep_env.sh` from the repo root
(`/home/dani/work/GR-HEEP`). Do **not** hand-write `source /tools/env_x-heep.sh
&& ...`: that script ends in `conda activate`, fails in a non-interactive shell,
and the `&&` then skips the real command, leaving an empty log and exit 1 that
looks like a build failure.

## Procedure

1. **Read the DFG first** and keep it for the report:
   `hw/vendor/strela-v2/rtl/elastic-cgra/mapper/applications/<PROJECT>/main.dot`.
   Record what each `input*`/`output*` carries (the header comment says),
   which inputs are `[border="west,east"]` (scratchpad-resident, replayed),
   and whether any node has a non-zero `delay_value` (accumulator: the app's
   `main.c` will have to patch it with `set_pe_delay_value`).

2. **Check for a committed bitstream first.** If
   `hw/vendor/strela-v2/rtl/elastic-cgra/regress/4x4-HV/<PROJECT>/`
   exists, it already holds the `bitstream.bin` and `io_map.json` for this
   kernel, and `scripts/regress2kernel.py` replays them into the kernel header
   in seconds — verified byte-identical to a real solve. Say so and use it
   instead of the mapper unless the caller asked for a fresh solve or the DFG
   changed:
   ```bash
   scripts/gr_heep_env.sh python3 scripts/regress2kernel.py <PROJECT> \
       -o <APP_DIR>/<PROJECT>_kernel.h
   ```
   Then copy that directory's `io_map.json` to `<APP_DIR>/<PROJECT>_io_map.json`
   and skip to step 4.

3. **Map + bitstream** (only when there is no committed bitstream):
   ```bash
   scripts/gr_heep_env.sh make -C hw/vendor/strela-v2/rtl/elastic-cgra \
       map-bitstream PROJECT=<PROJECT> CGRA_CONFIG=configs/4x4-HV.hjson \
       > /tmp/strela-map-<PROJECT>.log 2>&1
   ```
   Expect **several minutes** (~9 for a 10-node DFG) with **no output at all**
   until it finishes: the solver's stdout is fully buffered, so a silent log is
   not a hang. Do not kill it early; do not poll it in a tight loop.

   If the ILP is infeasible or errors out, STOP and report the specific mapper
   error line. Common causes: no config channel on an allowed `[border=...]`
   flank, interface-pairing/scratchpad-exclusivity conflicts, too few PEs, or an
   op no PE provides. Do not edit the DFG — that is elastic-cgra's `mapper-debug`
   agent's job.

4. **Install the artifacts** (only if `APP_DIR` was given). Copy both, always as
   a pair — they describe one solve, and a re-solve reshuffles the engine
   assignment, so a mismatched pair produces a table that deadlocks:
   ```bash
   CG=hw/vendor/strela-v2/rtl/elastic-cgra
   cp $CG/build/bitstream/<PROJECT>_kernel.h    <APP_DIR>/
   cp $CG/build/bitstream/<PROJECT>_io_map.json <APP_DIR>/
   ```

4. **Lint the bitstream against the DFG** (catches a kernel whose ALU ops decode
   differently than the DFG asks for — a bug that otherwise shows up only as a
   silent deadlock):
   ```bash
   scripts/gr_heep_env.sh make -C hw/vendor/strela-v2 lint-kernel \
       KERNEL=../../../<APP_DIR>/<PROJECT>_kernel.h
   ```

5. **Resolve the bindings** so the caller can write descriptors without opening
   the json. Each io_map `location` maps to an engine as below
   (`hw/vendor/strela-v2/sw/strela_bind.py` is authoritative), with
   `row = n // 4` and `col = n % 4`:

   | io_map location | kind | resolves to |
   |---|---|---|
   | in  `['pe', n, 'north']`    | stream | ISE `col` |
   | in  `['bus', k, 'ver_ise']` | stream | ISE `k` |
   | in  `['pe', n, 'west']`     | mem | MEM_W `row`, mode 0, **written by ISE `3-row`** |
   | in  `['pe', n, 'east']`     | mem | MEM_E `row`, mode 0, **written by ISE `row`** |
   | in  `['bus', k, 'hor_west']`| mem | MEM_W `k`, mode 1, **written by ISE `3-k`** |
   | in  `['bus', k, 'hor_east']`| mem | MEM_E `k`, mode 1, **written by ISE `k`** |
   | out `['pe', n, 'south']`    | stream | OSE `col` |
   | out `['bus', k, 'ver_ose']` | stream | OSE `k` |
   | out `['pe', n, 'west']`     | mem | MEM_W `row`, mode 0, **read by OSE `row`** |
   | out `['pe', n, 'east']`     | mem | MEM_E `row`, mode 0, **read by OSE `3-row`** |
   | out `['bus', k, 'hor_west']`| mem | MEM_W `k`, mode 1, **read by OSE `k`** |
   | out `['bus', k, 'hor_east']`| mem | MEM_E `k`, mode 1, **read by OSE `3-k`** |

   The west/east engine index is mirrored between inputs and outputs — that
   asymmetry is easy to get backwards, so read it off the table rather than
   reasoning it out. A `[border="west,east"]` port often lands on a **`pe`**
   location (mode 0), not a `bus` one; both are scratchpads. Every `mem` row
   needs the CFG_MEM → TR_MEM arm/drain pair on the output side, which
   `strela_desc` emits for you.

   Flag explicitly any **ISE that serves both a scratchpad and a direct stream** —
   the app must emit that engine's `mem()` before its `stream()`, or the fabric
   deadlocks.

## Report (~15 lines, nothing more)

- **Verdict:** `MAPPED`, `MAP FAILED`, or `LINT MISMATCH`.
- **Kernel contract:** one line per port — name, what the DFG says it carries,
  and the engine it resolved to.
- **Doubled-up ISEs:** which engines carry both a scratchpad and a stream (or
  "none").
- **Accumulators:** PEs with `delay_value != 0`, or "none — no `set_pe_delay_value`
  needed".
- **Lint:** the op table verdict line.
- **Artifacts:** the paths copied, plus the log path.

Do not paste the solver log or the route list. Point at
`/tmp/strela-map-<PROJECT>.log` if the caller wants detail.
