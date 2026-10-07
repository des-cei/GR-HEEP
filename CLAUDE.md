# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

GR-HEEP is a downstream SoC built on top of [X-HEEP](https://github.com/x-heep/x-heep) (vendored as a git
submodule/vendor tree at `hw/vendor/x-heep`), extended with two generations of the STRELA CGRA accelerator, side by side:
STRELA v2 (`hw/vendor/strela-v2`, a git submodule of `des-cei/strela-v2`) and STRELA v1
(`hw/vendor/strela-v1`, a git submodule of `des-cei/strela-v1`). Almost none of the
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

On a fresh clone, submodules must be initialized. STRELA and its own submodules (`rtl/elastic-cgra`,
`rtl/obione`) are cloned over SSH from `github.com:des-cei`, so this needs GitHub SSH access to
that organisation:

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

# Vivado FPGA build/program (FPGA_BOARD selects the board, as in X-HEEP; it is the FuseSoC
# target name, so it is *not* the same variable as TARGET above)
make vivado-fpga     FPGA_BOARD=<pynq-z2|nexys-a7-100t|genesys2|aup-zu3|zcu102|zcu104>
make vivado-fpga-pgm FPGA_BOARD=<same as above>

# SPI flash image for the Genesys2 (no board parameter: it is Genesys2-only). Reopens the
# implemented design in build/x-heep_systems_gr-heep_*/genesys2-vivado via
# scripts/fpga/bitstream2flash.tcl, rewrites the bitstream with the SPIx4 settings and writes
# <project>_flash.bit/.bin next to the original .bit.
make vivado-flash-bin

# Write that image to the Genesys2 on-board SPI flash with openFPGALoader (Genesys2-only too;
# a plain alias for the openFPGALoader call, it does not rebuild the image)
make flash-pgm

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
hw/vendor/strela-v2/   STRELA v2 CGRA accelerator IP (submodule; carries elastic-cgra and obione)
hw/vendor/strela-v1/   STRELA v1 CGRA accelerator IP (submodule; carries its own elastic-cgra, unused by FuseSoC)
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
RTL filesets depend on X-HEEP's `core-v-mini-mcu` and pad control cores plus STRELA's `ceimmupm:accelerators:strela-v2`
and `ceimmupm:accelerators:strela-v1` cores; separate filesets exist for Verilator waivers/harness, Questasim, and Xilinx FPGA
(`rtl-fpga`, using `hw/fpga_ext/xilinx_gr_heep_wrapper.sv`). All `make verilator-*`/`questasim-*`/
`vivado-*` targets ultimately invoke `fusesoc --cores-root . run ... x-heep:systems:gr-heep`.

### STRELA accelerator

STRELA is a CGRA (Coarse-Grained Reconfigurable Architecture) accelerator with 8 streaming memory
nodes, integrated as a memory-mapped peripheral/external device, in two generations (next section).
Everything below about descriptors is STRELA v2. Applications that use it
(`sw/applications/strela_v2_*`) talk to it through `strela.h`/`strela_v2_regs.h` and descriptor headers
(e.g. `descriptors.h`) generated by per-app `gen_descriptors.py`/`gen_data.py` scripts — regenerate
those headers with the app's Python script rather than hand-editing generated `_kernel.h`/`dataset.h`
files.

### STRELA v1 next to STRELA v2

Both generations are instantiated in the same SoC (`gr_heep_peripherals.sv.tpl`), each as its own
external peripheral, interrupt and set of bus masters (`config/mcu-gen-config.py`):

| | STRELA v2 | STRELA v1 |
|---|---|---|
| Peripheral (`gr_heep.h`) | `STRELA_V2_PERIPH_*`, ext. periph `+0x1000` | `STRELA_V1_PERIPH_*`, ext. periph `+0x0000` |
| External bus masters | 8–15 (4 ISEs, 4 OSEs) | 0–7 (4 input, 4 output memory nodes) |
| External interrupt | 1 | 0 (both are OR'd onto the fast external-peripheral interrupt) |
| Registers | `strela_v2_regs.h`, `STRELA_V2_*` | `strela_v1_regs.h`, `STRELA_V1_*` |
| RTL names | `strela_v2*`, `StrelaV2*` parameters | `strela_v1*` |
| Fabric | elastic-cgra 4x4-HV, module `cgra` | elastic-cgra 4x4, module `strela_v1_cgra` |
| Programming | descriptor tables, one START per run | MMIO node registers, one START per stream set |
| Apps | `strela_v2_<kernel>` | `strela_v1_<kernel>` |
| Tooling | `strela_v2_{desc,isa,bind,lint}.py`, `strela-v2-app`/`strela-v2-audit` skills, `strela-v2-map` agent | `regress2kernel.py --fabric 4x4` |

What makes the coexistence work, and so what not to undo:
- **Plain `strela` is reserved for what both share**: the `strela.h` driver header and its folder,
  the PE accessors in it, the `strela-sim` agent, and the bench scripts' `strela` family. Anything
  that belongs to one generation says which — `strela_v1*` / `strela_v2*` in RTL (modules, packages,
  the regtool blocks, wrapper ports), `StrelaV2*` package parameters, `STRELA_V1_*` / `STRELA_V2_*`
  macros and peripherals, app directories, tooling. Both submodules are owned by des-cei, and the
  RTL renames live there. Keep to this when adding anything.
- **One elastic-cgra library serves both.** The two submodules pin elastic-cgra at the *same*
  commit, whose library modules (PEs, ALUs, `cgra_pkg`) have the same names and whose core has the
  same VLNV. `hw/vendor/strela-v1/rtl/FUSESOC_IGNORE` hides v1's copy, so FuseSoC compiles v2's;
  v1 only contributes its own fabric top, `strela_v1_cgra.sv`, rendered from the same template for
  `configs/4x4.hjson`, renamed and committed (`make -C hw/vendor/strela-v1 cgra-gen`). **Bump the
  two elastic-cgra pointers together**, or v1's top gets compiled against a library it was not
  generated from.
- **One driver header, `strela.h`**, is now a GR-HEEP file (`sw/external/lib/drivers/strela/strela.h`,
  no longer a symlink into strela-v2). Its first section is shared: both fabrics have the same PE
  configuration word and the same `PE_POSITION`, and differ only in the router word v2 appends to each
  column, so `strela_{get,set}_pe_{const,delay_value,initial_value}()` take the router word count.
  Below that, the v2 section keeps the descriptor API unprefixed (`memory_node_t`, the opcodes,
  `CONFIG_SIZE`, `set_pe_const()`… — none of it is named `strela`) and v1's names are
  `STRELA_V1_*` / `strela_v1_*` (`STRELA_V1_CONFIG_SIZE`, the per-node register offset macros,
  `strela_v1_imn_param()`, `strela_v1_omn_size()`, the v1 PE accessors). The v2 part mirrors
  `hw/vendor/strela-v2/sw/strela_v2.h`; keep the two in step.
- **v1 kernels come from elastic-cgra's `regress/4x4/`**, through
  `scripts/regress2kernel.py <app> --fabric 4x4`, which sizes the array `STRELA_V1_CONFIG_SIZE`.
  The bitstreams of the old GR-HEEP-with-STRELA-v1 tree (`../GR-HEEP_strela_v1`) target the
  pre-elastic-cgra in-tree fabric, with a different PE word layout and chain order, and **cannot be
  reused** — port the app, not its `kernels.h`.

Programming STRELA v1, as `strela_v1_fir` and `strela_v1_gemm` do: memory node `i` is fabric channel
`i`, so a kernel header's `<ARRAY>_IN_<PORT>_CH` / `_OUT_<PORT>_CH` is the node index directly. `CLR`
clears every parameter, after which an unused node has size 0 and finishes immediately; `CONF_ADDR`
takes the whole bitstream and each input node fetches its own column. The configuration is loaded by
the first `START` only and reused by later ones until `CLR_CONF`, and node parameters persist across
runs, so a loop of runs re-writes only the addresses that move. Switching kernels needs
`CLR_CONF | CLR_PARAM` — the latter so that nodes the new kernel does not use go back to size 0 instead
of waiting forever for data — and `CLR_PARAM` also clears `CONF_ADDR`, so write it after. An input
node's size field is the byte *span* `stride * count`, 16 bits, so one stream reaches at most 64 KiB.
A kernel whose input is forked to several lanes (mm's B column) cannot run with a lane's node idle;
the lazy fork stalls the others. v1 apps print `TOT`/`CFG`/`EXE`/`STL` (no descriptor fetch, so no
`TAB`), and `TOT` counts from the `MODE` write, CPU register programming between runs included.
`scripts/sim/bench_apps.py` pairs `strela_v1_<kernel>` with the same `cpu_<kernel>` twin as v2.
Measured on Verilator, same data as the v2 twins: `strela_v1_fir` (4096 samples, one run) `TOT` 5539
against `strela_v2_fir`'s 6499 — a single stream with no descriptor fetch is where v1 is at its best —
and `strela_v1_gemm` (60x70x80, 1400 mm runs + 1 gemm_2 run) `TOT` 213509 against `strela_v2_gemm`'s
60123, the cost of a CPU-programmed START per 3x1 block of the product.

### Software applications (`sw/applications/`)

Two families:
- `cpu_*`: plain CPU-only benchmarks run on the core-v-mini-mcu CPU, used as a performance
  baseline against the STRELA-accelerated versions. Data is generated by `gen_data.py` into
  `dataset.h`, and every one of them self-checks against a golden and returns non-zero on
  mismatch. **Every `strela_*` app that computes a defined kernel has a `cpu_*` twin**, at the
  same default shape, so the two cycle counts are directly comparable; the only ones without a
  twin are `strela_v2_test` (a bring-up/bypass app) and `strela_v2_fully_connected` (legacy TFLM-style,
  no golden dataset). `cpu_jacobi1d` is the one twin without an accelerator counterpart.
  Two sub-families:
  - PolyBench-derived (`cpu_atax`, `cpu_bicg`, `cpu_doitgen`, `cpu_fw`, `cpu_gemm`, `cpu_gemver`,
    `cpu_gesummv`, `cpu_jacobi1d`, `cpu_mvt`, `cpu_threemm`, `cpu_twomm`): the kernel itself lives
    in `polybench_cpu.c/h`, a copy of which sits in each app directory, and `main.c` is just the
    timed call plus the check. Each defaults to that kernel's **`SMALL_DATASET`** shape from
    PolyBench 4.2.1 — gemm 60x70x80, 2mm 40/50/70/80, 3mm 40/50/60/70/80, gesummv 90, gemver 120,
    mvt 120, atax 116x124, bicg 116x124, doitgen 25/20/30, jacobi-1d 40 steps over 120 — and takes
    the dimensions as positional arguments (`python3 gen_data.py <dims...>`) to override. They are
    rectangular, not square, and the argument order follows PolyBench's: `cpu_atax` takes `M N`,
    so `A` is `M x N`, while `cpu_bicg` also takes `M N` but its `A` is `N x M`. `cpu_doitgen`
    takes `NR NQ NP`. The exception to `SMALL` is **`cpu_fw`, which defaults to floyd-warshall
    `MINI_DATASET` (60x60)** — it is the baseline for `strela_v2_fw`, whose descriptor tables do not
    fit at `SMALL`, and a baseline is only worth reading at the shape the accelerator runs. It is
    also the only one whose data is not a random draw: it uses PolyBench's own `init_array`, for
    the reasons `strela_v2_fw` documents (non-negativity, and real shortest-path structure to find).
    The 2mm and doitgen generators derive a safe element range from the shape instead of the house
    ±50, because those kernels multiply deeply enough for the *golden* to wrap int32 otherwise.
  - Signal/DSP twins (`cpu_dither_filter`, `cpu_fft`, `cpu_fft_st`, `cpu_find2min`, `cpu_fir`,
    `cpu_fir8`, `cpu_relu`): not PolyBench, so there is no `polybench_cpu.c` — the kernel is a `static`
    function at the top of `main.c`, and `gen_data.py` mirrors its `strela_v2_*` twin's generator
    closely enough to diff against it (same defaults, same seeded stimulus, same fixed-point
    conventions), which is the property that keeps the pair comparable. Two of them are
    deliberately *not* written the way a from-scratch CPU version would be: `cpu_find2min` mirrors
    the fabric's three coupled trackers rather than the obvious
    `if (v < min1) … else if (v < min2) …` scan, because the two disagree on `idx2` when values
    repeat (`[5, 5, 3]` gives 1 against 0) and a baseline that disagrees with its accelerator is
    not a baseline; and `cpu_fft` keeps the deinterleaved operand layout STRELA needs, so both
    apps move the same words in the same order. `cpu_dither_filter` is the interesting comparison
    of the set — its error feedback is the same loop-carried dependence that makes
    `strela_v2_dither_filter` the one recurrence-bound app in the suite.
    Where the PolyBench twins take their shape from PolyBench, these six share **one** default:
    **4096 elements**, the 64x64 image `relu` and `dither_filter` are sized for, and the same
    element count for `fir`, `fir8` and `find2min`. `fft` matches it as 4096 complex points —
    one middle stage, `--block 64`, so 2048 butterflies over 8192 streamed words; its block
    cannot go below 32 at that size, because the twiddle replay count is `FFT_POINTS/block` and
    the scratchpad descriptor holds it in 8 bits. Nothing else in the descriptors moves: all six
    are single-pass kernels whose length is a free parameter, so raising the default is one
    `default=` in each generator (`gen_data.py` and, on the STRELA side, `gen_descriptors.py` —
    the two are run with no arguments by `make gen-app-data` and must be changed together).
    `cpu_fft_st` is the one signal twin *off* that default on purpose: it is the port of
    x-trela's `cpu_fft_nt` / `strela_fft_nt` pair and keeps their shape — **512 complex points**,
    256 butterflies, 8-bit samples, in place over `real[]`/`imag[]` — so the two SoCs run the
    same kernel on the same data volume. Do not raise it to 4096 without raising the x-trela
    twin too.
  `cpu_mm` predates both groups: it is *not* a PolyBench kernel (it is the CPU twin of
  `strela_v2_mm`, sharing its generator and its 64x64x64 default), has no `polybench_cpu.c` — the
  triple loop is inlined in `main.c` — and toggles GPIOs around the timed region.

  **These apps are slow under Verilator** — `tb/tb_top.cpp` dumps an FST waveform
  unconditionally, so a run costs far more than the cycle count suggests — measured at roughly
  **2 kcycles/s**, which makes `test/gr_heep_test_apps.py`'s `SIM_TIMEOUT_S` a cycle budget rather
  than a wall clock. The matmul-shaped ones (`cpu_threemm`, `cpu_twomm`, `cpu_doitgen`, `cpu_fw`)
  take tens of minutes and exceed it. That does not fail CI — the runner exits
  non-zero only on compilation or simulation *failures*, and counts timeouts separately — but it
  does mean `make test` reports them as timed out rather than passing, which was already true of
  `cpu_threemm` before the newer twins were added. `SIM_TIMEOUT_S` is **300 s**, raised from 180
  when the signal twins went to 4096 elements: `cpu_fir8` is the largest of them at 367 k cycles /
  180 s, i.e. exactly the old limit, so it would have flapped. Nothing else sits near the new one.
  To simulate several of them at once, note that they all share `sw/build/main.hex`: build each
  app first, copy the hex aside, and run
  `Vtestharness +firmware=<abs path>` from a **separate working directory per run**, or the
  parallel runs will clobber each other's firmware, `uart0.log` and `waveform.fst`.
- `strela_v2_*` (`strela_v2_mm`, `strela_v2_fft`, `strela_v2_fft_st`, `strela_v2_gesummv_single`, `strela_v2_gesummv`,
  `strela_v2_gemm`, `strela_v2_gemver`, `strela_v2_2mm`,
  `strela_v2_3mm`, `strela_v2_doitgen`, `strela_v2_atax`, `strela_v2_bicg`, `strela_v2_mvt`, `strela_v2_fir`,
  `strela_v2_fir8`, `strela_v2_relu`, `strela_v2_find2min`, `strela_v2_dither_filter`, `strela_v2_fw`,
  `strela_v2_fully_connected`, `strela_v2_test`): use the STRELA
  CGRA to offload compute; include pre-generated kernel/bitstream headers (`*_kernel.h`, `*.bin`).
  All except `strela_v2_fully_connected` and `strela_v2_test` also commit the
  mapper's `*_io_map.json` next to
  the kernel header and derive their descriptor tables from it with STRELA's `strela_v2_desc` layer,
  so `gen_data.py` / `gen_descriptors.py` (run by `make gen-app-data`, a prerequisite of `app`)
  regenerate the gitignored `dataset.h` / `descriptors.h` without re-running the mapper.
  The kernel headers of `strela_v2_fir`, `strela_v2_fir8`, `strela_v2_relu`, `strela_v2_find2min`,
  `strela_v2_doitgen`, both
  halves of `strela_v2_gemm`, of `strela_v2_gesummv`, of `strela_v2_atax`, of `strela_v2_bicg` and of
  `strela_v2_mvt`, and every phase of `strela_v2_2mm` / `strela_v2_3mm` / `strela_v2_gemver`
  come from the committed
  regress bitstreams via `scripts/regress2kernel.py`, not from a mapper run;
  `strela_v2_dither_filter` and `strela_v2_fft_st` have no regress entry, so their headers came from a
  real (few-minute) Gurobi solve of `mapper/applications/dither_filter` / `fft_st`, and
  `strela_v2_fw` has one but **must not use it** —
  its DFG carries `border=` constraints the committed solve predates, exactly as
  `strela_v2_gemver`'s carries `at=` pins, so `regress2kernel.py` would silently restore the slow
  binding.
  Between them these five cover the four descriptor shapes worth copying: `strela_v2_fir` /
  `strela_v2_fir8` are the minimum (one streamed input, one streamed output, a single pass, no
  scratchpad, nothing to patch); `strela_v2_relu` is the unrolled map (four independent lanes over
  slices of one flat array, and the app to copy when the mapper's port-to-router order is not the
  lane order); `strela_v2_find2min` is a reduction whose four scalar outputs are decimated by per-FU
  delay counters, so `main.c` patches both the reduction length and the tracker sentinel into the
  bitstream at runtime; `strela_v2_dither_filter` has the same minimal one-in/one-out descriptor
  shape as `strela_v2_fir` but is **recurrence-bound** — its error feedback is a loop-carried
  dependence closed inside the fabric (`select0`'s `initial_valid` seed circulating through
  `add0 -> cmp0 -> select0`) rather than a `delay_value` accumulator, so there is nothing to
  patch, yet throughput is set by that 3-hop ring instead of by the streams — measured 41018
  cycles for 4096 pixels, i.e. 10.0 cycles/pixel against the ~1 a feed-forward kernel reaches.
  It is the app to copy for a sequential scan, and the one to read before assuming a STRELA
  kernel is input-bound.
  Measured together at the shared 4096-element default, these five split three ways by **what
  paces them**, which is the thing to establish before optimising any of them:
  `strela_v2_fir` (6499 cycles, 1.6/element) and `strela_v2_fir8` (9490, 2.3/element) are **fabric-paced**
  — `STL` is 2 and 3 cycles, so nothing is waiting on memory and the cost is the depth of the
  transposed-form add chain; `strela_v2_relu` (4007 for 4096 elements) and `strela_v2_fft` (8299 for 8192
  streamed words) are **memory-paced**, both landing within 2% of one input word and one output
  word per cycle with `STL` at 99% of `TOT`, so `strela_v2_relu`'s four lanes buy throughput only up
  to that port and not 4x; and `strela_v2_find2min` (32828, 8.0/element) joins
  `strela_v2_dither_filter` as **recurrence-paced**, `STL` again near zero. The practical consequence
  is that the first four gain speedup as the dataset grows (the fixed ~130-cycle `CFG`+`TAB` floor
  amortises: `strela_v2_fir` went 14.3x -> 19.5x and `strela_v2_fft` 3.5x -> 10.7x between 100/32 and
  4096) while the last two do not — both families scale linearly, so 2.2x and 1.3x hold at every
  size.
  `strela_v2_fft_st` is `strela_v2_fft` with the twiddle **stationary**: x-trela's `strela_fft_nt`
  ported at its own parameters (512 complex points, 256 butterflies, 8-bit samples, in place
  over `real[]`/`imag[]` with `a` in the first half and `b` in the second) so the two SoCs run
  the same kernel, and it is the app to read for **a constant that is part of the bitstream
  and nothing else**. `fft_st/main.dot` bakes `w = 5 + 3i` into its four `mul` PEs
  (`constant=`, `constant_fu_input=2`), so there is no twiddle table, no scratchpad, no Q
  format and no `set_pe_*` call — four north streams in, four south streams out, one
  descriptor per engine, no barrier. `gen_data.py`'s `W_RE`/`W_IM` mirror the DFG and must
  change with it. In place is safe because every output word depends only on the input words
  at its own butterfly index, which the fabric has consumed before the OSE has anything to
  write. Measured `TOT` 387 with `CFG` 23, `TAB` 147 and `STL` 260, identical on Verilator and
  on the Genesys2 — i.e. the ~130-cycle floor plus one butterfly per cycle, memory-paced like
  `strela_v2_fft` — against 7177 for `cpu_fft_st`, **18.5x**. It has no regress entry and no
  `border=`/`at=` constraints, so a re-solve is a plain mapper run (~5 min).
  `strela_v2_gemm` is the fifth shape and the simplest **chained** app: PolyBench gemm split over two
  bitstreams that run back to back in one execution — `gemm_1_hv` (byte-identical to `mm_hv`, so
  phase 0 is `strela_v2_mm`'s schedule writing a temporary `matAB`) and then `gemm_2_hv`
  (`matD = alpha*matAB + beta*matC`, two lanes over halves of the flat array, alpha/beta patched
  into the four `mul` PEs with `set_pe_const`). The hand-off is a `FENCE_SE` followed by a second
  `TR_CONF` on all four ISEs: re-entering `TR_CONF` clears `conf_done_o`, which drops the global
  `conf_reg`, re-gates every fabric handshake and — on the way back up — pulses `clr_cgra` to flush
  the fabric between kernels, so anything still in flight without that fence would hang.
  `strela_v2_desc.StreamProgram.load_kernel()` expresses this (it rebinds the mapper ports to the
  second io_map, since port names belong to a DFG), and `validate()` rejects a phase that is not
  preceded by a `FENCE_SE`. Copy it for any kernel too big for one fabric configuration.
  It defaults to PolyBench gemm `SMALL_DATASET` (60x70x80).
  `strela_v2_2mm` and `strela_v2_3mm` are the same chained shape carried to **three** phases, and are
  the apps to read for a kernel that reuses one bitstream at different reduction lengths.
  `2mm_1_hv` and `3mm_hv` are byte-identical to `mm_hv`, so every matmul phase is `strela_v2_mm`'s
  schedule, factored out as a `matmul_phase()` helper in each `gen_descriptors.py`:
  `strela_v2_2mm` runs `matAB = matA*matB`, then `matABC = matAB*matC`, then `2mm_2_hv`
  (byte-identical to `gemm_2_hv`) for `matD = alpha*matABC + beta*matDin` — PolyBench folds alpha
  into the first product, but alpha is a scalar, so pulling it into the last kernel leaves both
  matmul phases running the unmodified bitstream. `strela_v2_3mm` runs `matE = matA*matB`,
  `matF = matC*matD`, `matG = matE*matF`. The non-obvious part is that **each matmul phase needs
  its own copy of the bitstream in RAM**: `delay_value` *is* the reduction length, the three
  products reduce over different dimensions, and `set_pe_delay_value()` patches an array before
  `TR_CONF` reads it — so the phases cannot share one config load even though the bits are the
  same. `scripts/regress2kernel.py --array-name` is what emits those copies
  (`mm_ab_kernel` / `mm_abc_kernel`, `mm_e_kernel` / `mm_f_kernel` / `mm_g_kernel`); the io_map is
  per-DFG, so one committed copy is shared by all phases that use it.
  A second wrinkle in `strela_v2_3mm`: one pass consumes four rows of the left operand, and phase 1's
  left operand `matC` has `NJ` rows (50), which is not a multiple of 4 — so `matC` and `matF` are
  allocated with `NJ_PAD` (52) rows, the added rows of `matC` are zeroed, and phase 2 reduces over
  the first `NJ` rows of `matF` only. Both apps check every intermediate against its golden, not
  just the final result, because in a chain that is what places a failure.
  `strela_v2_2mm` defaults to PolyBench 2mm `SMALL_DATASET` (40/50/70/80) and `strela_v2_3mm` to
  PolyBench 3mm `SMALL_DATASET` (40/50/60/70/80), the same shape as `cpu_threemm`.
  `strela_v2_doitgen` is the opposite lesson — a kernel that *looks* like it needs more than a matmul
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
  `strela_v2_2mm` and 55% for `strela_v2_3mm`. The shape explains it — a row-stream descriptor moves
  `K` words for a fixed 12-byte fetch, and doitgen's `K` is `NP` = 30 against 50–80 in the other
  two, while flattening `NR*NQ` into 125 row groups makes the table long (~11k descriptors). Short
  reduction lengths are what make that ratio bite; it is not a correctness issue, but it is the
  first thing to look at before reading a low `TOT` as a fabric problem.
  `strela_v2_gesummv` is the chained app to read **before** assuming that a second operand needs a
  second phase. PolyBench gesummv (`y = alpha*A@x + beta*B@x`) has two committed HV bitstreams,
  `gesummv_1_hv` (four accumulator lanes reducing four matrix rows against an `x` that a constant
  multiply broadcasts from one scratchpad over the row-0 horizontal bus) and `gesummv_2_hv`
  (byte-identical to `gemm_2_hv` / `2mm_2_hv`, so the same two-lane scale-and-add) — but the run
  has only **two** phases for three products, because `A@x` and `B@x` want the *same*
  configuration: the same reduction length `N` and the same PE constant. Keeping alpha and beta
  out of the matrix-vector kernel (they are applied once at the end, which is how PolyBench writes
  it too) is what makes that true, so `B@x` is simply more rows through the bitstream `A@x` already
  runs under, with one loaded copy of `matvec_kernel`. That is
  the exact counterpoint to `strela_v2_2mm`, where the two matmuls reduce over different lengths and
  therefore *cannot* share a load: **a phase is a configuration, not an operand.** The two products
  are not even two *passes*: both reduce against the same `x`, so the one preload is replayed for
  the row groups of both (`iters = 2*block`) and `B`'s rows simply queue behind `A`'s on each
  engine. What keeps them apart without a barrier is the fabric, not the schedule — `x` reaches all
  four lanes through one fork, so no lane can consume a row of `B` while another is still on `A`,
  and the accumulators therefore emit `A`'s words before `B`'s on every lane. **A pass is a
  scratchpad load, not a statement**, and the only `FENCE_SE` left is the phase boundary. Two
  smaller
  points: `M` is padded up to a multiple of the fabric's 4 lanes (90 -> 92, zeroed rows, goldens
  computed over the padding so `y` must be exactly 0 there), and no output of either kernel is
  scratchpad-backed, so this app is free of the `MAX_GROUPS` race below and one pass covers the
  whole matrix. It is also the counter-example to `strela_v2_doitgen`'s descriptor-fetch problem:
  measured `TOT` 4961 with `TAB` 1578 (32%) and `STL` 110 (5139/1728/201 with the reload and the
  barrier), because one descriptor here streams a
  whole `N`=90 row for its fixed 12-byte fetch, and 4961 cycles for 2x92 rows of 90 is within ~20%
  of the 4140 the four lanes would take at one element per cycle. It defaults to PolyBench
  gesummv `SMALL_DATASET` (90x90), the same shape as
  `strela_v2_gesummv_single` and `cpu_gesummv` — the three are directly comparable, and the older
  `strela_v2_gesummv_single` does the whole thing in one fused bitstream with A and B row blocks in
  scratchpads instead.
  `strela_v2_atax`, `strela_v2_bicg` and `strela_v2_mvt` are the **transpose trio** and are best read
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
  Where the three differ is a clean three-way answer to **what forces a phase**. `strela_v2_mvt`
  (PolyBench mvt `SMALL_DATASET`, 120x120, the same shape as `cpu_mvt`) has a **square** `A`, so
  `A@y_1` and `A^T@y_2` reduce over the same `N` and share **one** loaded configuration — the
  transposed product is just more lines at a different stride — giving two phases for three
  operations, the second being `mvt_2_hv`, a plain two-lane add (`x1 = x1_in + t1`,
  `x2 = x2_in + t2`) with neither constants nor accumulators, so nothing in that bitstream is
  patched at all. It is also one *pass*, for the reason `strela_v2_gesummv` explains, with the extra
  wrinkle that the two products replay **different** vectors: `y_1` and `y_2` are emitted as the
  two halves of one `vec_y` array (a scratchpad holds one contiguous block), parked together with
  `iters=0`, and each product re-points the replay at its half with a param-only `mem_param()`.
  A load would clobber the word `data_out` still presents and needs the barrier; a re-point does
  not. `strela_v2_atax` (116x124, the same shape as `cpu_atax`) has a **rectangular** `A`,
  so `tmp = A@x` reduces over `N` and `y = A^T@tmp` over `M`; `delay_value` *is* the reduction
  length and it lives in the bitstream, so the identical pair of products now needs one patched
  copy each and two phases — and it is a true chain, `tmp` being written by phase 0's OSEs and
  preloaded into phase 1's scratchpad. `strela_v2_bicg` (`A` is 124x116, `M`=116/`N`=124 as in
  PolyBench, which is why its two positional arguments are `M N` but `A` is `N x M`) runs the same
  rectangular pair, but its two products are **independent** — `q = A@p` and `s = A^T@r`, neither
  consuming the other — and still needs two phases. That is the sharpest form of the rule
  `strela_v2_gesummv` states: what splits a run into phases is a change of configuration, not a data
  dependence and not a direction. Measured `TOT` 8340 for both `strela_v2_atax` and `strela_v2_bicg`
  (identical, because the same `2*116*124` elements cross the fabric either way) with `TAB` ~26%
  and `STL` ~4%, and 8566 for `strela_v2_mvt`. All three pad both dimensions up to a multiple of 4
  with zeroed rows/columns and compute the goldens over the padding, and none of the five
  bitstreams has a scratchpad-backed output, so all three are free of the `MAX_GROUPS` race below
  and one pass covers the whole matrix. One data-generation trap worth copying from
  `strela_v2_atax`: it reduces *twice*, so its worst case grows as `M*N*range^3` and its default
  `--range` is 40 where the single-reduction apps use 100.
  `strela_v2_gemver` is PolyBench gemver's four statements as **four** phases, the deepest chain here,
  and it completes the answer to *what forces a phase*. Its two matrix-vector phases
  (`vec_tmp = beta*(A2^T@y)` and `vec_w = alpha*(A2@x)`) run the same bitstream over the same
  **square** matrix, so they reduce over the same `N` — neither `strela_v2_atax`'s differing
  `delay_value` nor `strela_v2_gesummv`'s "same configuration, more passes" applies — and they still
  need two loaded copies (`matvec_beta_kernel`, `matvec_alpha_kernel`, `--array-name` again),
  because the scalar each folds into its replayed vector is a **PE constant**, and a constant lives
  in the bitstream exactly like a delay_value does. So the rule is neither about lengths nor
  operands: **a phase is a configuration, and a constant is part of one.** `strela_v2_gesummv` escapes
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
  shared buses, the u entries in the per-lane scratchpads as one word replayed once per element
  (`size=1, iters=N`), and **one row per lane per step**; the mirror-image schedule, per-lane halves
  of v with the u scalars on the buses, reloads twice as much to update half as much.
  It is equally the app to read for the *other* half of that lesson — **changing a scratchpad's
  address is not the same as reloading it**, which is what makes the one-row-per-lane constraint
  cost nothing. The whole u vector is parked in each of the four per-lane scratchpads once, loaded
  with `iters=0` so S_WR returns to S_IDLE without replaying it, and each step re-points the replay
  with a **param-only descriptor** (`strela_v2_desc.mem_param()`): word 0 alone, byte count zero, no
  bus traffic and no SRAM access. v1/v2 are loaded once with `iters=N/2` — a lane consumes one
  replay per row — and never touched again. Because a param-only descriptor writes nothing, it also
  **removes the per-step fence**: a *loading* descriptor writes the SRAM from S_WR and clobbers the
  `data_out` still presenting the previous replay's last word, which is the deadlock every
  scratchpad reload has to fence against, while a param-only one leaves `data_out`/`valid_out`
  intact and the ISE simply blocks on the scratchpad's `ready_o` until the replay it is replacing
  has been issued. Back-pressure per scratchpad instead of a barrier across all eight engines. With
  the fences gone each lane can also take a *contiguous* half of the rows instead of alternating
  ones, so the two OSEs and the ISE with no scratchpad move their whole half of the matrix in one
  descriptor. Net: 1392 descriptors → **489**, `TAB` 40% → 21%, `TOT` **25521 → 17650**.
  Then a second, independent win: **which engine a port lands on is a performance decision, and the
  mapper does not make it for you.** Its objective is switching activity, which knows nothing about
  descriptor tables — and it had put lane a's `A` stream on ISE 1, the engine that also owns two of
  the u scratchpads. An engine carrying both has to interleave them, and it can only push a
  scratchpad parameter while that scratchpad is between replays, so its descriptor fetches land on
  the critical path instead of being prefetched. Measured: the identical fetches on ISE 2, which
  carries nothing else, cost **0.5 cycles each** (59 extra descriptors → 29 cycles), and the two
  lanes are locked in lockstep by the shared v token, so ISE 1 paced the whole phase. `gemver_1_hv`
  is therefore the first DFG to carry **`at=` pins** (elastic-cgra's mapper, `parse_pin`), moving
  `input0` to ISE 0 and leaving ISE 1 and ISE 3 with nothing but their two re-points per row. Every
  other binding of the unpinned solve is reproduced exactly. Net: 489 descriptors → **430**,
  `TOT` 17650 → **16325**, `STL` 921 → **327**. `update_kernel.h` consequently comes from a mapper
  run and **not** from `regress2kernel.py`, which would silently restore the ISE 1 binding.
  One caveat that only simulation sees: the stride field of a param-only descriptor must be the
  element width, not zero. The ISE starts its DMA for every transfer descriptor and obione's
  `a_stride_nonzero` contract is checked on entry, before the empty-transfer shortcut — so a
  zero-stride version runs fine on FPGA (assertions compiled out under `SYNTHESIS`) and stops
  Verilator dead.
  It also brings a descriptor limit no other app hits: the
  replay count *is* the row length, so `N` is capped at **255** by the 8-bit `iters` field, where
  everywhere else `iters` counts row groups. `mat_a2` is a separate array rather than a write-back
  over `mat_a` — the update is elementwise, so in place would in fact be safe, but within a pass the
  ISE reading an element and the OSE writing it are not ordered, and a separate destination keeps
  that argument out of the app.
  Two more things gemver is the first app to hit. It multiplies **three** deep (a rank-2 update
  feeding two chained matrix-vector products), so its worst case grows as
  `N^2 * range^5 * (1+2*range)^2` and the default `--range` is **4** at `N`=120, against 40 for
  `strela_v2_atax` and 100 for the single-reduction apps; the guard is worst-case and shape-only on
  purpose, because CI runs `gen_data.py` with no arguments and a range that only *usually* fits
  would be a flaky build. And it is what exposed a gap in `strela_v2_desc.validate()`: port names
  belong to a DFG, so a chained program reuses `input2` for an unrelated channel of the next
  bitstream — and for an unrelated *kind*, a scratchpad in phase 0 and a north stream in the matvec
  phases — and the mode check resolved every descriptor against the last io_map loaded rather than
  its own phase, reporting 120 mismatches on a correct schedule. It now resolves per phase, the way
  the port-coverage check already did (a STRELA submodule change, so it needs a pointer bump).
  Measured `TOT` 16325 with `CFG` 87, `TAB` 3150 (19%) and `STL` 327 (2%), against a ~14.4k
  streaming bound — 32.0x over `cpu_gemver`, where the original schedule was 20.5x. It defaults
  to PolyBench gemver `SMALL_DATASET` (120x120), the same shape as `cpu_gemver`.
  `strela_v2_fw` is PolyBench floyd-warshall, and it is the **counterpoint to the whole phase
  discussion above**: `path[i][j] = min(path[i][j], path[i][k] + path[k][j])` swept once per pivot
  `k`, which is 900 *steps* at the default shape and yet **one loaded configuration for the entire
  run**, with `CFG` measured at 26 cycles. A pivot is an *address*, not a bitstream field — it
  changes which line the descriptors point at and nothing else — so where `strela_v2_atax` needs two
  copies for two reduction lengths and `strela_v2_gemver` two for two constants, `fw_hv` is loaded
  once and never patched at all: every one of its PEs reads back `delay_value` 0 and constant 0,
  there are no `set_pe_*` calls in `main.c`, and there is no accumulator anywhere (`min` is a
  `sub` -> signed `>0` `cmp` -> `mux` chain per lane, four lanes, one line each, `mux_dout = cin ?
  din_2 : din_1` picking the candidate exactly when the current value exceeds it). It is the app
  to read for **a kernel whose cost is sequencing rather than configuration**.
  Two schedule points. The pivot-column entry `path[i][k]` is *constant along the streamed line*,
  which is `strela_v2_gemver`'s rank-2-update problem again — only a scratchpad can hold one word and
  replay it (`size=1, iters=N`), a stream would need stride 0 and hang obione — so it is again
  **one line per lane per step**, and again the 8-bit `iters` field caps `N` at 255 because the
  replay count *is* the line length. But — and this is the lesson the app now exists to make —
  **a step is not a pass**: that constraint fixes how much data crosses the fabric per step and
  says nothing about how many steps one fenced pass may cover. All three operands are re-pointed
  rather than reloaded (the column parked with `iters=0` and walked with `mem_param()`, the pivot
  row loaded once per pivot with `iters=steps`, the line streamed), no descriptor in the body of a
  pass writes SRAM, so **the whole pivot is one pass**: `N` barriers, not `N*N_PAD/4`.
  That in turn is what the DFG's `border=` constraints are for, and `strela_v2_fw` is the app to read
  for **borders as a performance decision** the way `strela_v2_gemver` is for `at=` pins. Pinning both
  `add` operands to scratchpads (`border="west,east"`) is what lets the pivot row be replayed
  instead of re-streamed; pinning the updated line to a stream (`border=north`) matters even more,
  because `strela_v2_memory.sv` does not overlap load and replay — S_WR absorbs a whole block before
  the start pulse flips it to S_WR_CGRA — so a scratchpad-bound line costs `2N` bus cycles per step
  against `N`. Together they also make the engine balance structural: eight vias over eight
  scratchpad positions and four lines over four north positions are both bijections, and ISE `i`
  owns exactly `MEM_W(3-i)`, `MEM_E(i)` and north column `i`, so every ISE ends up with one
  `col` re-point, one `piv` load and one line stream — **two descriptors per engine per step**, with
  no `at=` pin needed. `assign_roles()` re-derives which via is which from the io_map rather than
  hard-coding it, so a re-solve that moves a port still emits a correct (if slower) table. And the two buffers
  **ping-pong**, one pivot each, rather than relaxing in place as PolyBench does: it costs one extra
  `N_PAD x N` array and buys the removal of the entire in-pass ordering argument, since iteration
  `k` reads two lines it does not own. That the two are the same answer is a two-line proof —
  `path[k][j] <- min(path[k][j], path[k][k]+path[k][j])` and `path[i][k] <- min(path[i][k],
  path[i][k]+path[k][k])` are both identities whenever `path[k][k] >= 0`, so the pivot row and
  column do not change during their own iteration — which is why the data is PolyBench's own
  `init_array` rather than the usual random draw: it is non-negative by construction (and
  structured, mixing short edges with a 999 sentinel, where a uniform random matrix collapses to
  near-constant minima after one pivot and would pass with the pivot column wired wrong).
  `gen_data.py` computes the golden *both* ways and asserts they agree, turning that proof into a
  check on the actual data; it reports how many entries the sweep changes (2476 of 3600 at the
  default), which is the number to look at before trusting a pass.
  It is also the app that is **descriptor-table-size bound**, a limit no other one here hits. A
  pass costs 8 loads, 8 descriptors per step, 4 writes and 8 fence entries, so the tables grow as
  `~2*N^2` descriptors — 98 KiB at `N`=60 against the **512 KiB interleaved section** the tables
  and both matrices share, with a ceiling at `N`=124. PolyBench `SMALL_DATASET` (180) is
  comfortably inside every descriptor-ISA limit and still cannot be built, so it defaults to
  floyd-warshall **`MINI_DATASET` (60x60)**, the only app here not on `SMALL`, and
  `gen_descriptors.py` reports the real byte count rather than letting the link fail. (The
  pass-per-step schedule this replaced needed `6*N^2` descriptors, 253 KiB at `N`=60, and capped
  `N` at 80.)
  Measured `TOT` **301890** with `CFG` 26, `TAB` 61403 (20%) and `STL` 21650 (7%), down from
  423294 / 162797 (38%) — 21596 descriptors and 899 barriers became **8404 and 59**. Against
  `cpu_fw`'s 3046316 cycles at the same 60x60 that is **10.1x**, where the pass-per-step schedule
  reached 7.2x. What is left
  is **not** descriptor cost: 301890 cycles over 900 steps of 60 elements per lane is 5.6
  cycles/element, and elastic-cgra's own `make perf` measures `fw` at about 2 tokens every 10
  cycles, so the app is now **fabric-paced**. The likely mechanism, not yet confirmed in
  simulation, is that the `min` diamond reconverges on `select` two FU hops apart
  (`sub` -> `cmp` -> cond) while `configs/4x4-HV.hjson` uses lazy forks, so a token waits for the
  slow branch. The low `STL` says the same thing (compare `strela_v2_relu`/`strela_v2_fft`, which
  are memory-paced at `STL` ~99% of `TOT`). Squeezing it further is a DFG/fabric-config question —
  balancing the reconvergence, or `eager_fork` — not a descriptor one. At ~300k simulated cycles it
  is still **the slowest app in the suite** (about three minutes under Verilator, ~3x
  `strela_v2_doitgen`), so lower `N` if `make test` needs to be quicker.

**Two size limits that are not obvious from the descriptor ISA**, both hit when scaling these
apps past their original toy shapes:
- `strela_v2_mm`, `strela_v2_doitgen`, `strela_v2_gemm`'s phase 0 and every matmul phase of
  `strela_v2_2mm` / `strela_v2_3mm`
  split each B column pair into chunks of at most
  `gen_descriptors.MAX_GROUPS` (8) A row groups, one pass each, rather than one pass over all
  `M/4`. Past a shape-dependent number of row groups per pass the run deadlocks with exactly one
  element of the product unwritten, always the last word of `output7`. `output7` is the only
  output of this kernel whose scratchpad is reached over a router's horizontal bus (`MEM_W1`,
  mode 1); in `rtl/strela_v2_memory.sv` the `valid_out` register presenting a word is shared between
  the fabric-write and OSE-read directions, and its `S_IDLE` update guard accepts either, so a
  `hor_ready_i` from the fabric can clear the last pending word before `ose_ready_i` takes it and
  the OSE waits forever. The boundary moves with the number of B columns (the OSE write stride,
  hence how fast obione's FIFO drains): measured 13 groups fine / 14 hanging at `NJ`=70 in gemm,
  but 11 fine / 12 hanging at `N`=8 in mm. It is a race — chunking is a margin, not a proof, and
  the real fix belongs in `strela_v2_memory.sv`. Because `mm_hv`, `gemm_1_hv`, `2mm_1_hv`,
  `3mm_hv` and `doitgen_hv` are all the same solve (byte-identical bitstream and io_map), the five
  apps' `MAX_GROUPS` must stay in step. Without
  the chunking `strela_v2_mm` at its 64x64x64 default (16 row groups) hangs, while 8x8x8 (2 groups)
  passes.
- `strela_v2_gesummv_single` preloads whole *blocks of rows* of A and B into the 512-word
  scratchpads, `gen_descriptors.rows_per_pass()` rows at a time, instead of a whole `M/2` half.
  Only one row block has to fit, so `M*N` is no longer capped at 1022 and the app defaults to
  PolyBench gesummv `SMALL_DATASET` (90x90, nine passes of five rows). Those passes **must be
  separated by a `FENCE_SE`** — every ISE in this kernel does nothing but preload scratchpads,
  so without a fence an ISE reaches the next pass's `TR_MEM_*` while the fabric is still draining
  the current replay. `strela_v2_memory.sv` presents the last word of a replay from `S_IDLE`, where
  `ready_o` is already high, so the new `mem_param` is accepted, the FSM leaves for `S_WR`, and
  that word is never handed to the fabric. 8x16 in two passes of two rows deadlocks without the
  fence and passes with it — the same `valid_out` handoff as the `strela_v2_gemm` limit above, seen
  from the ISE side. What that argument covers is only the loads that **change**: the A/B row
  blocks differ from pass to pass, `x` does not, so `x` is preloaded once for the whole run with
  `iters = M/2` (one replay per output row) rather than reloaded per pass. A `FENCE_SE` waits on
  the engines, it does not reset a scratchpad, so the pending replays survive every barrier.
  Measured `TOT` 9393 -> **8659** (−7.8%) on that alone, which is more than the 8 saved descriptor
  fetches are worth: the redundant loads also moved 8x90 words of real bus traffic on an engine
  that carries a matrix block too.

All apps follow X-HEEP's `PRINTF_IN_SIM`/`PRINTF_IN_FPGA` convention: printf output is generally
enabled for FPGA runs and disabled for simulation (for speed) unless overridden per-app.

**Turning an elastic-cgra kernel into a `strela_v2_*` app** — the whole procedure (mapper invocation,
io_map-driven descriptor generation, the data-generator contract, and the deadlock checklist) lives
in the **`strela-v2-app` skill**; invoke it rather than reconstructing the steps. Its §5 is the
checklist for scheduling an app at *full* performance — scratchpad reuse via `iters`, re-pointing
with `mem_param()` instead of reloading, why that deletes the fences, contiguous lane slices, and
keeping per-step descriptors off the engines that stream. The **`strela-v2-audit` skill** checks all of
that mechanically against the emitted schedule
(`python3 .claude/skills/strela-v2-audit/audit.py [app...]`) and reports the redundant bytes and
barriers with the fix; run it before calling an app finished. The two long, log-heavy halves have
agents: **`strela-v2-map`** (Gurobi solve + artifact install + kernel lint) and
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

Both STRELAs (`hw/vendor/strela-v2`, `hw/vendor/strela-v1`) are plain git submodules (see
`.gitmodules`), not managed through the vendor tool. Each imports X-HEEP's `xheep_obi_pkg` / `xheep_reg_pkg` (and their
`xheep_obi_req_t`, `xheep_obi_rsp_t`, `xheep_reg_req_t`, `xheep_reg_rsp_t` types) directly, as does its
nested `obione` (v1 has none); the old `obi_pkg` / `reg_pkg` names no longer exist in the vendored X-HEEP, so do not
reintroduce them as shims here. A change to STRELA is committed innermost first (obione, then
strela-v2 or strela-v1, then the pointer bump in GR-HEEP) and pushed in the same order.

### CI (`.github/workflows/ci.yml`)

Jobs: `determine-image-tag` (resolves the vendored X-HEEP toolchain Docker image tag from the
vendored rev), `lint` (`make mcu-gen` + diff check against committed generated files),
`check-vendor` (re-vendor + diff check), `black-formatter` (checks `config/` and `test/`), and
`simulate-apps` (`make mcu-gen` + `test/gr_heep_test_apps.py`, i.e. the same as `make test`).
