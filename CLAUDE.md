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
- `cpu_*` (`cpu_gemm`, `cpu_gemver`, `cpu_gesummv`, `cpu_mm`, `cpu_threemm`): plain CPU-only
  PolyBench-derived benchmarks (`polybench_cpu.c/h`) run on the core-v-mini-mcu CPU, used as a
  performance baseline against STRELA-accelerated versions. Data is generated by `gen_data.py`
  into `dataset.h`.
- `strela_*` (`strela_mm`, `strela_fft`, `strela_gesummv`, `strela_fir`, `strela_fir8`,
  `strela_relu`, `strela_find2min`, `strela_dither_filter`, `strela_fully_connected`,
  `strela_test`): use the STRELA
  CGRA to offload compute; include pre-generated kernel/bitstream headers (`*_kernel.h`, `*.bin`).
  All except `strela_fully_connected` and `strela_test` also commit the
  mapper's `*_io_map.json` next to
  the kernel header and derive their descriptor tables from it with STRELA's `strela_desc` layer,
  so `gen_data.py` / `gen_descriptors.py` (run by `make gen-app-data`, a prerequisite of `app`)
  regenerate the gitignored `dataset.h` / `descriptors.h` without re-running the mapper.
  The kernel headers of `strela_fir`, `strela_fir8`, `strela_relu` and `strela_find2min` come
  from the committed regress bitstreams via `scripts/regress2kernel.py`, not from a mapper run;
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
