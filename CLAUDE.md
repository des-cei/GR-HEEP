# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

GR-HEEP is a downstream SoC built on top of [X-HEEP](https://github.com/x-heep/x-heep) (vendored as a git
submodule/vendor tree at `hw/vendor/x-heep`), extended with the STRELA CGRA accelerator
(vendored at `hw/vendor/ceimm_upm_strela`, a private GitLab submodule). Almost none of the
vendored code is authored in this repo — GR-HEEP only adds the glue RTL, pad configuration,
software applications, and SoC-level integration on top.

Because of this, most X-HEEP concepts (mcu-gen, peripherals, linker flow, `sw/vendor` drivers)
apply unchanged here; only what GR-HEEP adds/overrides is documented below.

## Environment setup

Before running any command, source the toolchain environment:

```bash
source /tools/env_x-heep.sh
```

This sets up the RISC-V GCC toolchain (`RISCV_XHEEP`), Verilator, Verible, OpenOCD, Vivado, and
activates the `core-v-mini-mcu` conda env. Without this, `make` targets that shell out to
`fusesoc`, the RISC-V compiler, or Verilator will fail.

## Submodules

On a fresh clone, submodules must be initialized (STRELA is a private GitLab repo, so it requires
access to `gitlab.cei.upm.es`):

```bash
git submodule update --init --recursive
```

## Common commands

All commands run from the repo root via the top-level `Makefile`. Any make target *not* defined
in this Makefile is transparently forwarded to X-HEEP's own Makefile (`hw/vendor/x-heep/Makefile`,
included via `hw/vendor/x-heep/external.mk`) with `SOURCE` pointed at this repo's `sw/` tree — so
e.g. `make app`, `make clean`, `make run-questasim`, etc. all "just work" as documented in X-HEEP
even though they aren't defined here.

```bash
# Generate the X-HEEP MCU + GR-HEEP RTL/headers from the mcu-gen config (run after any
# config/*.hjson, config/*.py, or hw/gr-heep/*.tpl change; also formats/lints the generated RTL)
make mcu-gen

# Build a software application (forwarded to X-HEEP's Makefile)
make app PROJECT=<app_name>            # apps live in sw/applications/ and hw/vendor/x-heep/sw/applications/

# Verilator: build the C++ sim model, then build+run firmware
make verilator-build
make verilator-run-app PROJECT=<app_name>   # builds the app then runs it
make verilator-run                          # runs a previously-built sw/build/main.hex

# Questasim/ModelSim equivalents
make questasim-build
make questasim-build-opt        # optimized HDL compile (run once after questasim-build)
make questasim-run-app          # builds app then simulates
make questasim-run-app-gui      # same, with GUI waveform viewer
make questasim-run-opt-app      # uses the -opt build

# Vivado FPGA build/program (TARGET selects the board)
make vivado-fpga     TARGET=<pynq-z2|nexys-a7-100t|genesys2|aup-zu3|zcu102|zcu104>
make vivado-fpga-pgm TARGET=<same as above>

# Full regression: builds Verilator, compiles + simulates every app in
# hw/vendor/x-heep/sw/applications (whitelist-filtered) and sw/applications, checks for
# "Program Finished with value N" in the UART/log output
make test

# RTL lint/format for hand-written GR-HEEP sources (hw/gr-heep/*.sv)
make verible

# Python formatting check (config/, test/)
make format-python

# Update a vendored dependency after bumping its .vendor.hjson rev
make vendor-update MODULE_NAME=x-heep      # or another *.vendor.hjson in hw/vendor
make vendor-update-all
```

`TARGET` defaults to `asic`; set it to an FPGA board name for FPGA flows. `PROJECT` defaults to
`hello_world`.

### Running a single test / app

`test/gr_heep_test_apps.py` is a thin wrapper around X-HEEP's `test/test_apps` test runner
(imported from `hw/vendor/x-heep/test`). It has its own `WHITELIST`/`BLACKLIST` (and
`WHITELIST_XHEEP`/`BLACKLIST_XHEEP`) — edit those lists to restrict which apps are exercised, or
pass `--compile-only` / `--dry-run`:

```bash
make mcu-gen   # test target depends on this; run once beforehand
python test/gr_heep_test_apps.py --compile-only
python test/gr_heep_test_apps.py --dry-run
```

To iterate on a single app instead of the whole suite, use `make app PROJECT=<name>` +
`make verilator-run-app PROJECT=<name>` directly rather than going through the test script.

## Architecture

### Layering

```
hw/vendor/x-heep/      X-HEEP core (core-v-mini-mcu, peripherals, sw runtime/drivers, mcu-gen tool)
hw/vendor/ceimm_upm_strela/   STRELA CGRA accelerator IP (private submodule)
hw/gr-heep/            GR-HEEP-specific RTL, generated from .tpl templates by `make mcu-gen`
hw/fpga_ext/           Xilinx-specific top-level wrapper + Vivado TCL for FPGA builds
config/                mcu-gen inputs: SoC memory map/peripheral config (Python + hjson) and pad ring config
sw/applications/       GR-HEEP-specific firmware apps (CPU benchmarks + STRELA-accelerated apps)
tb/                    Testbench (Verilator/Questasim harness, JTAG, UART DPI)
test/                  Regression runner (delegates to X-HEEP's test_apps machinery)
```

### RTL generation flow (`make mcu-gen`)

The SoC is not hand-written monolithically — `hw/gr-heep/*.sv` files are generated from the
matching `*.sv.tpl` templates by X-HEEP's `mcu-gen` tool, driven by:
- `config/mcu-gen-config.py` / `config/mcu-gen-config.hjson` — CPU choice (cv32e40p), bus type,
  memory subsystem, peripheral set and their addresses/interrupts (base + user peripheral domains).
- `config/gr-heep_pad_cfg.py` — defines every chip-level pin and how it's muxed onto pads
  (`PadRing`, `Side`, `Input`/`Output`/`Inout` from X-HEEP's `pads` package).

**Never hand-edit the generated `.sv` files in `hw/gr-heep/` (e.g. `gr_heep.sv`,
`gr_heep_pkg.sv`, `gr_heep_peripherals.sv`, `gr_heep_pad_ring.sv`) — edit the corresponding
`.sv.tpl` template and/or the `config/` inputs, then re-run `make mcu-gen`.** CI's `lint` job
fails the build if generated files don't match what `mcu-gen` would produce from the templates.
`gr_heep_xbar.sv`, `gr_heep_bus.sv`, and `gr_heep_waivers.vlt` are hand-written (no `.tpl`
counterpart) and can be edited directly.

### FuseSoC integration

`gr-heep.core` is the FuseSoC core file (`x-heep:systems:gr-heep`) that ties everything together:
RTL filesets depend on X-HEEP's `core-v-mini-mcu` and pad control cores plus STRELA's `ceiupm::strela`
core; separate filesets exist for Verilator waivers/harness, Questasim, and Xilinx FPGA
(`rtl-fpga`, using `hw/fpga_ext/xilinx_gr_heep_wrapper.sv`). All `make verilator-*`/`questasim-*`/
`vivado-*` targets ultimately invoke `fusesoc --cores-root . run ... x-heep:systems:gr-heep`.

### STRELA accelerator

STRELA is a CGRA (Coarse-Grained Reconfigurable Architecture) accelerator with 8 streaming memory
nodes, integrated as a memory-mapped peripheral/external device. Applications that use it
(`sw/applications/strela_*`) talk to it through `strela.h`/`strela_regs.h` and descriptor headers
(e.g. `descriptors.h`) generated by per-app `gen_descriptors.py`/`gen_data.py` scripts — regenerate
those headers with the app's Python script rather than hand-editing generated `_kernel.h`/`dataset.h`
files.

### Software applications (`sw/applications/`)

Two families:
- `cpu_*` (`cpu_atax`, `cpu_gemm`, `cpu_gemver`, `cpu_gesummv`, `cpu_jacobi1d`, `cpu_mm`,
  `cpu_mvt`, `cpu_threemm`): plain CPU-only
  PolyBench-derived benchmarks (`polybench_cpu.c/h`) run on the core-v-mini-mcu CPU, used as a
  performance baseline against STRELA-accelerated versions. Data is generated by `gen_data.py`
  into `dataset.h`. Each PolyBench-derived one defaults to that kernel's **`SMALL_DATASET`**
  shape from PolyBench 4.2.1 — gemm 60x70x80, 3mm 40/50/60/70/80, gesummv 90, gemver 120,
  mvt 120, atax 116x124, jacobi-1d 40 steps over 120 — and takes the dimensions as positional
  arguments (`python3 gen_data.py <dims...>`) to override. They are rectangular, not square:
  `cpu_atax` takes `M N` in that order, so `A` is `M x N`. `cpu_mm` is *not* a PolyBench kernel
  (it is the CPU twin of `strela_mm`) and keeps its own default.
- `strela_*` (`strela_mm`, `strela_fft`, `strela_gesummv_single`, `strela_gesummv`,
  `strela_gemm`, `strela_gemver`, `strela_2mm`,
  `strela_3mm`, `strela_doitgen`, `strela_atax`, `strela_bicg`, `strela_mvt`, `strela_fir`,
  `strela_fir8`, `strela_relu`, `strela_find2min`, `strela_dither_filter`,
  `strela_fully_connected`, `strela_test`): use the STRELA
  CGRA to offload compute; include pre-generated kernel/bitstream headers (`*_kernel.h`, `*.bin`).
  All except `strela_fully_connected` and `strela_test` also commit the
  mapper's `*_io_map.json` next to
  the kernel header and derive their descriptor tables from it with STRELA's `strela_desc` layer,
  so `gen_data.py` / `gen_descriptors.py` (run by `make gen-app-data`, a prerequisite of `app`)
  regenerate the gitignored `dataset.h` / `descriptors.h` without re-running the mapper.
  The kernel headers of `strela_fir`, `strela_fir8`, `strela_relu`, `strela_find2min`,
  `strela_doitgen`, both
  halves of `strela_gemm`, of `strela_gesummv`, of `strela_atax`, of `strela_bicg` and of
  `strela_mvt`, and every phase of `strela_2mm` / `strela_3mm` / `strela_gemver`
  come from the committed
  regress bitstreams via `scripts/regress2kernel.py`, not from a mapper run;
  `strela_dither_filter` has no regress entry, so its header came from a real (few-minute) Gurobi
  solve of `mapper/applications/dither_filter`.
  Between them these five cover the four descriptor shapes worth copying: `strela_fir` /
  `strela_fir8` are the minimum (one streamed input, one streamed output, a single pass, no
  scratchpad, nothing to patch); `strela_relu` is the unrolled map (four independent lanes over
  slices of one flat array, and the app to copy when the mapper's port-to-router order is not the
  lane order); `strela_find2min` is a reduction whose four scalar outputs are decimated by per-FU
  delay counters, so `main.c` patches both the reduction length and the tracker sentinel into the
  bitstream at runtime; `strela_dither_filter` has the same minimal one-in/one-out descriptor
  shape as `strela_fir` but is **recurrence-bound** — its error feedback is a loop-carried
  dependence closed inside the fabric (`select0`'s `initial_valid` seed circulating through
  `add0 -> cmp0 -> select0`) rather than a `delay_value` accumulator, so there is nothing to
  patch, yet throughput is set by that 3-hop ring instead of by the streams — measured 2618
  cycles for 256 pixels, i.e. 10.2 cycles/pixel against the ~1 a feed-forward kernel reaches.
  It is the app to copy for a sequential scan, and the one to read before assuming a STRELA
  kernel is input-bound.
  `strela_gemm` is the fifth shape and the simplest **chained** app: PolyBench gemm split over two
  bitstreams that run back to back in one execution — `gemm_1_hv` (byte-identical to `mm_hv`, so
  phase 0 is `strela_mm`'s schedule writing a temporary `matAB`) and then `gemm_2_hv`
  (`matD = alpha*matAB + beta*matC`, two lanes over halves of the flat array, alpha/beta patched
  into the four `mul` PEs with `set_pe_const`). The hand-off is a `FENCE_SE` followed by a second
  `TR_CONF` on all four ISEs: re-entering `TR_CONF` clears `conf_done_o`, which drops the global
  `conf_reg`, re-gates every fabric handshake and — on the way back up — pulses `clr_cgra` to flush
  the fabric between kernels, so anything still in flight without that fence would hang.
  `strela_desc.StreamProgram.load_kernel()` expresses this (it rebinds the mapper ports to the
  second io_map, since port names belong to a DFG), and `validate()` rejects a phase that is not
  preceded by a `FENCE_SE`. Copy it for any kernel too big for one fabric configuration.
  It defaults to PolyBench gemm `SMALL_DATASET` (60x70x80).
  `strela_2mm` and `strela_3mm` are the same chained shape carried to **three** phases, and are
  the apps to read for a kernel that reuses one bitstream at different reduction lengths.
  `2mm_1_hv` and `3mm_hv` are byte-identical to `mm_hv`, so every matmul phase is `strela_mm`'s
  schedule, factored out as a `matmul_phase()` helper in each `gen_descriptors.py`:
  `strela_2mm` runs `matAB = matA*matB`, then `matABC = matAB*matC`, then `2mm_2_hv`
  (byte-identical to `gemm_2_hv`) for `matD = alpha*matABC + beta*matDin` — PolyBench folds alpha
  into the first product, but alpha is a scalar, so pulling it into the last kernel leaves both
  matmul phases running the unmodified bitstream. `strela_3mm` runs `matE = matA*matB`,
  `matF = matC*matD`, `matG = matE*matF`. The non-obvious part is that **each matmul phase needs
  its own copy of the bitstream in RAM**: `delay_value` *is* the reduction length, the three
  products reduce over different dimensions, and `set_pe_delay_value()` patches an array before
  `TR_CONF` reads it — so the phases cannot share one config load even though the bits are the
  same. `scripts/regress2kernel.py --array-name` is what emits those copies
  (`mm_ab_kernel` / `mm_abc_kernel`, `mm_e_kernel` / `mm_f_kernel` / `mm_g_kernel`); the io_map is
  per-DFG, so one committed copy is shared by all phases that use it.
  A second wrinkle in `strela_3mm`: one pass consumes four rows of the left operand, and phase 1's
  left operand `matC` has `NJ` rows (50), which is not a multiple of 4 — so `matC` and `matF` are
  allocated with `NJ_PAD` (52) rows, the added rows of `matC` are zeroed, and phase 2 reduces over
  the first `NJ` rows of `matF` only. Both apps check every intermediate against its golden, not
  just the final result, because in a chain that is what places a failure.
  `strela_2mm` defaults to PolyBench 2mm `SMALL_DATASET` (40/50/70/80) and `strela_3mm` to
  PolyBench 3mm `SMALL_DATASET` (40/50/60/70/80), the same shape as `cpu_threemm`.
  `strela_doitgen` is the opposite lesson — a kernel that *looks* like it needs more than a matmul
  and does not. PolyBench doitgen contracts a 3-D tensor against a 2-D matrix
  (`sum[r][q][p] = SUM_s A[r][q][s]*C4[s][p]`), but the `r` and `q` loops are independent and only
  ever address whole rows of the last axis, and `A` is row-major — so the `NR x NQ x NP` tensor
  *is* an `(NR*NQ) x NP` matrix in memory, and the app is one single-phase matmul against the
  square `C4`, with `doitgen_hv` again byte-identical to `mm_hv`. The tensor rank never reaches
  the descriptors; only the *product* `NR*NQ` has to be a multiple of the fabric's 4 rows, so
  either dimension alone may be odd. Because `C4` is square, the reduction length and the tiled
  column count are both `NP`. It defaults to PolyBench doitgen `SMALL_DATASET` (25/20/30).
  It is also the app that shows where **descriptor-table fetch** starts to dominate: measured
  `TOT` 97059 with `TAB` 79655, i.e. 82% of the run spent fetching descriptors, against 57% for
  `strela_2mm` and 55% for `strela_3mm`. The shape explains it — a row-stream descriptor moves
  `K` words for a fixed 12-byte fetch, and doitgen's `K` is `NP` = 30 against 50–80 in the other
  two, while flattening `NR*NQ` into 125 row groups makes the table long (~11k descriptors). Short
  reduction lengths are what make that ratio bite; it is not a correctness issue, but it is the
  first thing to look at before reading a low `TOT` as a fabric problem.
  `strela_gesummv` is the chained app to read **before** assuming that a second operand needs a
  second phase. PolyBench gesummv (`y = alpha*A@x + beta*B@x`) has two committed HV bitstreams,
  `gesummv_1_hv` (four accumulator lanes reducing four matrix rows against an `x` that a constant
  multiply broadcasts from one scratchpad over the row-0 horizontal bus) and `gesummv_2_hv`
  (byte-identical to `gemm_2_hv` / `2mm_2_hv`, so the same two-lane scale-and-add) — but the run
  has only **two** phases for three products, because `A@x` and `B@x` want the *same*
  configuration: the same reduction length `N` and the same PE constant. Keeping alpha and beta
  out of the matrix-vector kernel (they are applied once at the end, which is how PolyBench writes
  it too) is what makes that true, so `B@x` is simply more passes of the bitstream `A@x` already
  runs under, with one `FENCE_SE` between passes and one loaded copy of `matvec_kernel`. That is
  the exact counterpoint to `strela_2mm`, where the two matmuls reduce over different lengths and
  therefore *cannot* share a load: **a phase is a configuration, not an operand.** Two smaller
  points: `M` is padded up to a multiple of the fabric's 4 lanes (90 -> 92, zeroed rows, goldens
  computed over the padding so `y` must be exactly 0 there), and no output of either kernel is
  scratchpad-backed, so this app is free of the `MAX_GROUPS` race below and one pass covers the
  whole matrix. It is also the counter-example to `strela_doitgen`'s descriptor-fetch problem:
  measured `TOT` 5139 with `TAB` 1728 (34%) and `STL` 201, because one descriptor here streams a
  whole `N`=90 row for its fixed 12-byte fetch, and 5139 cycles for 2x92 rows of 90 is within ~25%
  of the 4140 the four lanes would take at one element per cycle. It defaults to PolyBench
  gesummv `SMALL_DATASET` (90x90), the same shape as
  `strela_gesummv_single` and `cpu_gesummv` — the three are directly comparable, and the older
  `strela_gesummv_single` does the whole thing in one fused bitstream with A and B row blocks in
  scratchpads instead.
  `strela_atax`, `strela_bicg` and `strela_mvt` are the **transpose trio** and are best read
  together: all three compute the same pair of products over one matrix, `A@u` and `A^T@v`, and
  all three run the same committed bitstream — `atax_hv`, `bicg_hv` and `mvt_1_hv` are
  byte-identical, a *different* solve from `gesummv_1_hv` in which the vector lands straight on
  router 0's horizontal bus instead of passing through a constant-multiply PE, so PEs 4-7 are the
  accumulators and there is no PE constant to patch at all. The lesson they exist to make is that
  **transposing costs a stride, not a pass**: a line of `A^T` is a column of `A`, a descriptor
  already carries an arbitrary stride, and so the transposed product differs from the forward one
  only in `stride=ELEM, count=N` becoming `stride=N*ELEM, count=M`. `A` is never rearranged in
  memory and there is no transpose kernel. What it *does* cost is span — a column descriptor
  reaches across the whole matrix, so its byte count is `M*N*4` rather than `N*4`, and the 16-bit
  byte count caps that at 65535 (PolyBench SMALL sits at 57536, close). `stream_line()` therefore
  splits a line across several back-to-back descriptors on the same channel when it has to, which
  works because a lane accumulator counts *tokens*, not descriptors (verified in simulation at a
  forced 64-byte cap, `--max-stream-bytes`); the real per-dimension cap is the 511-word scratchpad
  holding the replayed vector.
  Where the three differ is a clean three-way answer to **what forces a phase**. `strela_mvt`
  (PolyBench mvt `SMALL_DATASET`, 120x120, the same shape as `cpu_mvt`) has a **square** `A`, so
  `A@y_1` and `A^T@y_2` reduce over the same `N` and share **one** loaded configuration — the
  transposed product is just more passes at a different stride — giving two phases for three
  operations, the second being `mvt_2_hv`, a plain two-lane add (`x1 = x1_in + t1`,
  `x2 = x2_in + t2`) with neither constants nor accumulators, so nothing in that bitstream is
  patched at all. `strela_atax` (116x124, the same shape as `cpu_atax`) has a **rectangular** `A`,
  so `tmp = A@x` reduces over `N` and `y = A^T@tmp` over `M`; `delay_value` *is* the reduction
  length and it lives in the bitstream, so the identical pair of products now needs one patched
  copy each and two phases — and it is a true chain, `tmp` being written by phase 0's OSEs and
  preloaded into phase 1's scratchpad. `strela_bicg` (`A` is 124x116, `M`=116/`N`=124 as in
  PolyBench, which is why its two positional arguments are `M N` but `A` is `N x M`) runs the same
  rectangular pair, but its two products are **independent** — `q = A@p` and `s = A^T@r`, neither
  consuming the other — and still needs two phases. That is the sharpest form of the rule
  `strela_gesummv` states: what splits a run into phases is a change of configuration, not a data
  dependence and not a direction. Measured `TOT` 8340 for both `strela_atax` and `strela_bicg`
  (identical, because the same `2*116*124` elements cross the fabric either way) with `TAB` ~26%
  and `STL` ~4%, and 8615 for `strela_mvt`. All three pad both dimensions up to a multiple of 4
  with zeroed rows/columns and compute the goldens over the padding, and none of the five
  bitstreams has a scratchpad-backed output, so all three are free of the `MAX_GROUPS` race below
  and one pass covers the whole matrix. One data-generation trap worth copying from
  `strela_atax`: it reduces *twice*, so its worst case grows as `M*N*range^3` and its default
  `--range` is 40 where the single-reduction apps use 100.
  `strela_gemver` is PolyBench gemver's four statements as **four** phases, the deepest chain here,
  and it completes the answer to *what forces a phase*. Its two matrix-vector phases
  (`vec_tmp = beta*(A2^T@y)` and `vec_w = alpha*(A2@x)`) run the same bitstream over the same
  **square** matrix, so they reduce over the same `N` — neither `strela_atax`'s differing
  `delay_value` nor `strela_gesummv`'s "same configuration, more passes" applies — and they still
  need two loaded copies (`matvec_beta_kernel`, `matvec_alpha_kernel`, `--array-name` again),
  because the scalar each folds into its replayed vector is a **PE constant**, and a constant lives
  in the bitstream exactly like a delay_value does. So the rule is neither about lengths nor
  operands: **a phase is a configuration, and a constant is part of one.** `strela_gesummv` escapes
  this by pushing alpha/beta into a later scale-and-add kernel; gemver cannot, because its only
  other kernel is a plain add with no constants at all and beta has to be applied before `z` is.
  The other three phases are proven ground — `gemver_2_hv` is byte-identical to `gesummv_1_hv` and
  `gemver_3_hv` to `mvt_2_hv`, same io_map locations — so the new work is all in phase 0.
  That phase, `gemver_1_hv` (`A2 = A + u1*v1^T + u2*v2^T`), is the app to read for **an operand
  that is constant along the streamed line**. Its two lanes take one matrix row each, and its eight
  inputs split into two kinds: `input6`/`input7` land on the horizontal buses of rows 0 and 2 and
  fork one token to *both* lanes, while `input2`..`input5` are per-lane PE scratchpads. Since a
  scratchpad replays a fixed block a whole number of times, an operand varying along the row and
  one constant along it cannot both be replayed for more than one line per pass — `[u1[i0] x N,
  u1[i0+1] x N, ...]` is a run-length pattern and not a repeat of anything. Hence v1/v2 on the
  shared buses (`size=N, iters=1`), the u entries in the per-lane scratchpads as one word replayed
  once per element (`size=1, iters=N`), and **one row per lane per pass**: 60 fenced two-row passes
  at `N`=120, reloading v1/v2 each time. That costs one extra word of scratchpad traffic per element
  updated; the mirror-image schedule, per-lane halves of v with the u scalars on the buses, reloads
  twice as much to update half as much. It also brings a descriptor limit no other app hits: the
  replay count *is* the row length, so `N` is capped at **255** by the 8-bit `iters` field, where
  everywhere else `iters` counts row groups. `mat_a2` is a separate array rather than a write-back
  over `mat_a` — the update is elementwise, so in place would in fact be safe, but within a pass the
  ISE reading an element and the OSE writing it are not ordered, and a separate destination keeps
  that argument out of the app.
  Two more things gemver is the first app to hit. It multiplies **three** deep (a rank-2 update
  feeding two chained matrix-vector products), so its worst case grows as
  `N^2 * range^5 * (1+2*range)^2` and the default `--range` is **4** at `N`=120, against 40 for
  `strela_atax` and 100 for the single-reduction apps; the guard is worst-case and shape-only on
  purpose, because CI runs `gen_data.py` with no arguments and a range that only *usually* fits
  would be a flaky build. And it is what exposed a gap in `strela_desc.validate()`: port names
  belong to a DFG, so a chained program reuses `input2` for an unrelated channel of the next
  bitstream — and for an unrelated *kind*, a scratchpad in phase 0 and a north stream in the matvec
  phases — and the mode check resolved every descriptor against the last io_map loaded rather than
  its own phase, reporting 120 mismatches on a correct schedule. It now resolves per phase, the way
  the port-coverage check already did (a STRELA submodule change, so it needs a pointer bump).
  Measured `TOT` 25521 with `CFG` 90, `TAB` 10310 (40%, the price of phase 0's 1392-descriptor
  table) and `STL` 1351 (5%). It defaults to PolyBench gemver `SMALL_DATASET` (120x120), the same
  shape as `cpu_gemver`.

**Two size limits that are not obvious from the descriptor ISA**, both hit when scaling these
apps past their original toy shapes:
- `strela_mm`, `strela_doitgen`, `strela_gemm`'s phase 0 and every matmul phase of
  `strela_2mm` / `strela_3mm`
  split each B column pair into chunks of at most
  `gen_descriptors.MAX_GROUPS` (8) A row groups, one pass each, rather than one pass over all
  `M/4`. Past a shape-dependent number of row groups per pass the run deadlocks with exactly one
  element of the product unwritten, always the last word of `output7`. `output7` is the only
  output of this kernel whose scratchpad is reached over a router's horizontal bus (`MEM_W1`,
  mode 1); in `rtl/strela_memory.sv` the `valid_out` register presenting a word is shared between
  the fabric-write and OSE-read directions, and its `S_IDLE` update guard accepts either, so a
  `hor_ready_i` from the fabric can clear the last pending word before `ose_ready_i` takes it and
  the OSE waits forever. The boundary moves with the number of B columns (the OSE write stride,
  hence how fast obione's FIFO drains): measured 13 groups fine / 14 hanging at `NJ`=70 in gemm,
  but 11 fine / 12 hanging at `N`=8 in mm. It is a race — chunking is a margin, not a proof, and
  the real fix belongs in `strela_memory.sv`. Because `mm_hv`, `gemm_1_hv`, `2mm_1_hv`,
  `3mm_hv` and `doitgen_hv` are all the same solve (byte-identical bitstream and io_map), the five
  apps' `MAX_GROUPS` must stay in step. Without
  the chunking `strela_mm` at its 64x64x64 default (16 row groups) hangs, while 8x8x8 (2 groups)
  passes.
- `strela_gesummv_single` preloads whole *blocks of rows* of A and B into the 512-word
  scratchpads, `gen_descriptors.rows_per_pass()` rows at a time, instead of a whole `M/2` half.
  Only one row block has to fit, so `M*N` is no longer capped at 1022 and the app defaults to
  PolyBench gesummv `SMALL_DATASET` (90x90, nine passes of five rows). Those passes **must be
  separated by a `FENCE_SE`** — every ISE in this kernel does nothing but preload scratchpads,
  so without a fence an ISE reaches the next pass's `TR_MEM_*` while the fabric is still draining
  the current replay. `strela_memory.sv` presents the last word of a replay from `S_IDLE`, where
  `ready_o` is already high, so the new `mem_param` is accepted, the FSM leaves for `S_WR`, and
  that word is never handed to the fabric. 8x16 in two passes of two rows deadlocks without the
  fence and passes with it — the same `valid_out` handoff as the `strela_gemm` limit above, seen
  from the ISE side.

All apps follow X-HEEP's `PRINTF_IN_SIM`/`PRINTF_IN_FPGA` convention: printf output is generally
enabled for FPGA runs and disabled for simulation (for speed) unless overridden per-app.

**Turning an elastic-cgra kernel into a `strela_*` app** — the whole procedure (mapper invocation,
io_map-driven descriptor generation, the data-generator contract, and the deadlock checklist) lives
in the **`strela-app` skill**; invoke it rather than reconstructing the steps. The two long,
log-heavy halves have agents: **`strela-map`** (Gurobi solve + artifact install + kernel lint) and
**`strela-sim`** (build + Verilator run + verdict). Every toolchain command must go through
`scripts/gr_heep_env.sh`, because `source /tools/env_x-heep.sh` ends in a `conda activate` that
fails in a non-interactive shell and makes an `&&` chain skip the real command.

Two things that bite when bringing up a new app. First, `rtl/elastic-cgra/regress/4x4-HV/<app>/`
already holds a committed `bitstream.bin` + `io_map.json` for 21 HV kernels, and
`scripts/regress2kernel.py` replays those into the app's `<app>_kernel.h` in seconds — byte-identical
to what the mapper emits, so check there before starting a ~10-minute Gurobi solve. Second, a
deadlocked fabric never ends the simulation (`Vtestharness` has no max-cycle cutoff), so always run
`make verilator-run-app` under a `timeout`; an expired timeout *is* the deadlock signal.

There's also a TFLite Micro integration path: `make tflm-app` / `make questasim-run-tflm-app`
expect a sibling `../tflm_x-heep` build directory (a separate TFLM-enabled X-HEEP checkout) and
just convert/load its compiled ELF — this is a cross-repo workflow, not self-contained here.

### Vendoring

Vendored dependencies (currently only `x-heep`) are tracked via `hw/vendor/<name>.vendor.hjson`
(pinned upstream URL/rev + patch dir) and `hw/vendor/<name>.lock.hjson`. Patches applied on top of
upstream X-HEEP live in `hw/vendor/patches/x-heep/*.patch`. Use `make vendor-update
MODULE_NAME=<name>` after bumping a `.vendor.hjson` rev; CI's `check-vendor` job fails if the
vendored tree doesn't match what re-vendoring would produce, so patches/rev bumps must be
committed together with the resulting vendored file changes.

STRELA (`hw/vendor/ceimm_upm_strela`) is a plain git submodule (see `.gitmodules`), not managed
through the vendor tool.

### CI (`.github/workflows/ci.yml`)

Jobs: `determine-image-tag` (resolves the vendored X-HEEP toolchain Docker image tag from the
vendored rev), `lint` (`make mcu-gen` + diff check against committed generated files),
`check-vendor` (re-vendor + diff check), `black-formatter` (checks `config/` and `test/`), and
`simulate-apps` (`make mcu-gen` + `test/gr_heep_test_apps.py`, i.e. the same as `make test`).
