---
name: gr-heep-ip-integration
description: >-
  Integrate a new department IP (accelerator, peripheral, coprocessor) into GR-HEEP,
  starting from the URL of its git repository: add it as a submodule under hw/vendor,
  survey and classify its interfaces, optionally make it GR-HEEP-compatible (regtool
  MMIO register block, X-HEEP bus types, SystemVerilog, FuseSoC core), wire it into the
  SoC through config/mcu-gen-config.py and hw/gr-heep/gr_heep_peripherals.sv.tpl
  (register-interface peripheral, OBI slave, OBI master ports, interrupts, CV-X-IF
  coprocessor, DMA hw_fifo), and add a driver header plus a self-checking test app.
  Use whenever the user asks to "integrate", "add", "connect" or "meter" a new IP or
  submodule into GR-HEEP, or gives a repository URL to add under hw/vendor.
---

# Integrating a new IP into GR-HEEP

Follow the phases in order. Every phase ends with a check; do not start the next one
until the check passes. Every new file starts from a fill-in template in
`templates/` next to this file (how to instantiate them: end of Phase 1). Do not
copy the files of IPs already integrated in the tree: their names, offsets and
quirks are specific to them and would leak into the new one.
Two reference files hold the details. Read each one when its phase starts:

- `reference/mcu-gen.md` covers every `gr_heep_config()` parameter, the names it
  generates, the template variables, instantiation snippets per interface class,
  interrupt routing, and address limits.
- `reference/interfaces.md` covers making an incompatible IP compatible: the regtool
  MMIO wrapper recipe, AXI/APB bridges, CV-X-IF, hw_fifo, VHDL, vendor primitives and
  clock gating.

## Ground rules (apply to every phase)

1. **Never commit or push** unless the user explicitly asks. When they do, commit
   **inside the submodule first**, then GR-HEEP (glue + submodule pointer bump), and
   push in the same order. End commit messages with the attribution line the session
   gives you.
2. All code and comments you write are **in English**. Do not translate comments that
   already exist in the IP unless asked.
3. Run every toolchain command (make, fusesoc, verilator, verible, riscv gcc) through
   `scripts/gr_heep_env.sh <command>`. If tools are still missing, check
   `ls /tools/env_*`: on some hosts the script is `/tools/env_xheep.sh`, not
   `env_x-heep.sh`. Then source conda's `etc/profile.d/conda.sh`, that script, and
   `conda activate core-v-mini-mcu` yourself.
4. **Never hand-edit generated files**: `hw/gr-heep/gr_heep.sv`, `gr_heep_pkg.sv`,
   `gr_heep_peripherals.sv`, `gr_heep_pad_ring.sv`, `sw/external/lib/runtime/gr_heep.h`,
   and the IP's regtool outputs (`*_reg_pkg.sv`, `*_reg_top.sv`, `sw/*_regs.h`). Edit
   the `.tpl` / `config/` / `.hjson` source and regenerate.
5. Changes to the IP's own repository stay **inside `hw/vendor/<ip>`**. Changes to
   GR-HEEP stay outside it. Never patch the IP from GR-HEEP files (no shims, no copies
   of IP files in `hw/gr-heep`).
6. Simulations can deadlock and never end: always wrap `verilator-run*` in
   `timeout` (e.g. `timeout 1800`). An expired timeout means a hang, not a slow run.
7. Stop and ask (AskUserQuestion) at the points marked **ASK**. Present a plan and stop
   at the end of Phase 0. Do not modify anything beyond adding the submodule before the
   user approves the plan.

Naming used below: `<ip>` is the snake_case IP name, which is also the directory under
`hw/vendor` (e.g. `foo_acc`). `<Ip>` is its CamelCase form as mcu-gen produces it
(split on non-alphanumerics, capitalize each word: `foo_acc` → `FooAcc`,
`bar_v2` → `BarV2`). `<IP>` is SCREAMING_SNAKE (`FOO_ACC`).

---

## Phase 0: Add the submodule and survey the IP

### 0.1 Add the submodule

```bash
git submodule add <URL> hw/vendor/<ip>
git submodule update --init --recursive hw/vendor/<ip>
```

- `<ip>` defaults to the repository name in snake_case. If that name is ambiguous or
  collides with something already in the tree (`grep -rn "<ip>" hw/ config/ sw/`),
  **ASK**.
- The existing `.gitmodules` entries use the SSH form `git@github.com:<org>/<repo>.git`.
  Use the URL exactly as the user gave it. If it is HTTPS and the others are SSH,
  mention the difference in the report; do not change it silently.
- If `git submodule add` fails for SSH access, report the error and stop; the user has
  to fix their keys.

### 0.2 Survey: fill in this table

Run, from the repo root:

```bash
cd hw/vendor/<ip> && git log --oneline | head -5 && find . -type f -not -path './.git/*' | sort
grep -rn "^\s*module\b" --include=*.sv --include=*.v .           # SV/Verilog modules
grep -rni "^\s*entity\b" --include=*.vhd --include=*.vhdl .       # VHDL entities
ls *.core 2>/dev/null && grep -n "^name:" *.core                  # FuseSoC core + VLNV
grep -rln "reg_pkg::\|obi_pkg::\|xheep_reg_pkg\|xheep_obi_pkg\|axi\|apb\|if_xif\|x_issue" . | head
```

Read the README, any `doc/` file (PDFs: read the text parts) and the top module's port
list. Then write down:

| Item | Value |
|---|---|
| HDL language(s) | SV / Verilog / **VHDL (not supported by the Verilator flow, see interfaces.md §6)** / mixed |
| Top module and file | |
| Clock / reset ports | name, polarity (GR-HEEP: `clk_i`, active-low async `rst_ni`) |
| FuseSoC `.core` | present? VLNV? dependencies? |
| Bus types used | `xheep_reg_pkg` / `xheep_obi_pkg` (OK), old `reg_pkg` / `obi_pkg` (rename), AXI / AXI-Lite / APB / custom (adapt) |
| Register map | regtool `.hjson` present? documented elsewhere? |
| Memories / vendor IP | Xilinx/Intel primitives, `.coe`/`.mif` init files, generated IP cores |
| Software | C headers, drivers, examples |
| Interrupt outputs | name, level or pulse |
| Other top ports | clock enables, pads, debug, power |

### 0.3 Classify every top-level port group

Each port group of the IP's top falls into exactly one class. The class decides the
mcu-gen parameter and the snippet to use (`reference/mcu-gen.md` §3):

| Class | What the IP exposes | GR-HEEP mechanism | Parameter in `gr_heep_config()` |
|---|---|---|---|
| **A. Reg slave** | `xheep_reg_req_t` / `xheep_reg_rsp_t` control port | External peripheral (0x2007_0000 + offset) | `ext_periph` |
| **B. OBI slave** | `xheep_obi_req_t` / `xheep_obi_rsp_t` memory-like port (big buffers, CPU/DMA load-store) | External slave on the external xbar (0xF000_0000 + offset) | `ext_xbar_slaves` |
| **C. OBI master** | `xheep_obi_req_t` out / `xheep_obi_rsp_t` in, the IP reads/writes system memory | Ports into X-HEEP's bus (DMA-like engines, streaming memory nodes) | `ext_xbar_nmasters` |
| **D. Interrupt** | 1-bit level (preferred) or pulse | PLIC `EXT_INTR_<k>` + fast interrupt 15 | `external_interrupts` |
| **E. CV-X-IF** | `if_xif` (v0.2) or `x_issue_*`/`x_result_*` (v1.0) coprocessor port | Coprocessor on the CPU | `system.set_xif(CvXIf(...))` in `config()` + CPU change |
| **F. hw_fifo stream** | push/pop FIFO fed by the DMA | DMA hardware-FIFO mode | `hw_fifo_channels` |
| **G. Anything else** | AXI4, AXI-Lite, APB, Wishbone, raw start/done + RAM ports, AXI-Stream, 64-bit buses | Must be adapted first (Phase 1) | n/a |

An IP can combine classes (e.g. an accelerator with its own memory engines is
A + C + D: registers to program it, masters to fetch data, an IRQ when done). Class G always needs Phase 1; so do
VHDL sources, old `reg_pkg`/`obi_pkg` names, and vendor primitives.

### 0.4 Read the current SoC configuration

```bash
sed -n '/def gr_heep_config/,/kwargs = {/p' config/mcu-gen-config.py
grep -n "set_cpu\|set_xif" config/mcu-gen-config.py
```

Note: which external peripherals are enabled and at what offsets, `ext_xbar_nmasters`
and who owns which master slice, `external_interrupts`, and the CPU. Some IPs may be
commented out on purpose (the user may have disabled one to integrate something
else, or to shorten simulations). **Do not re-enable or disable other IPs** unless the plan says so.

### 0.5 ASK, then present the plan

Ask in one AskUserQuestion call, with your recommendation first:

1. **Address**: the offset for each class A/B port. Propose the next free 4 KiB-aligned
   offset in `ext_periph` (window is 64 KiB total) or `ext_xbar_slaves` (16 MiB). If
   the user already gave one, do not ask again.
2. **Interrupt**: connect the IP's IRQ (recommended when it has one) or not.
3. **Adaptation** (only if Phase 1 is needed): regtool MMIO wrapper (recommended for
   control/status registers), protocol bridge (AXI/APB IPs that must stay unchanged),
   or the user provides new RTL.
4. **Renaming**: if module/package names are generic (`top`, `core`, `fsm`, `pkg`,
   `ram`...) they will collide in FuseSoC's single namespace. Options: prefix every
   module/package with `<ip>_` only (recommended); prefix and also translate comments;
   leave as is.
5. **Other IPs**: keep the current set enabled, or disable some (e.g. to save sim time).
6. **CPU change** (class E only): CV-X-IF needs `cv32e40px`, `cv32e40x` or `cv32e20`;
   GR-HEEP ships `cv32e40p`, which mcu-gen rejects with an XIF. Changing the CPU
   affects every app and every FPGA build, so it is always the user's call.

Then write the plan: the classification table, the files to create or modify per
phase, the address and IRQ index, and the open risks. **Stop and wait for approval.**

---

## Phase 1 (optional): Make the IP GR-HEEP-compatible

Skip this phase if the IP already has: SV/Verilog only; `xheep_reg_pkg` / `xheep_obi_pkg`
types on its top; a FuseSoC core with a unique VLNV; uniquely prefixed module names;
no vendor primitives. Otherwise follow `reference/interfaces.md`. Summary of the steps:

1. **Language**: everything must be SystemVerilog/Verilog (§6). If the IP is VHDL,
   **ASK** whether the user has an SV version (e.g. new files dropped in a
   temporary folder of the repo) or wants it ported. When new files replace old ones, `git rm` the old ones; do not keep both.
2. **Bus types**: replace `reg_pkg::reg_req_t` → `xheep_reg_pkg::xheep_reg_req_t`,
   `reg_rsp_t` → `xheep_reg_rsp_t`, `obi_pkg::obi_req_t` → `xheep_obi_pkg::xheep_obi_req_t`,
   `obi_resp_t` → `xheep_obi_rsp_t`. Never add `reg_pkg`/`obi_pkg` shims.
3. **Register block (class G control ports, or no register map)**: write
   `data/<ip>_regs.hjson`, generate it with `data/gen_<ip>_regs.sh`, and write the
   wrapper `rtl/<ip>.sv` exposing class A ports (+ D). See interfaces.md §1 for the
   register patterns (start pulse, sticky done rw1c, busy, interrupt enable, memory
   windows, FIFOs).
4. **Other buses**: AXI/AXI-Lite/APB slaves → bridge (§2); AXI masters → `axi_to_mem`
   to OBI (§3); raw RAM ports → regtool window (§1.5); AXI-Stream → hw_fifo (§5) or a
   window.
5. **Vendor primitives / init files**: replace with behavioural SV (§7).
6. **FuseSoC core**: create or fix `<ip>.core` from `templates/ip.core`. VLNV
   convention for department IPs: `ceimmupm:accelerators:<ip-with-dashes>`, file name
   `ceimm_upm_<ip>.core`. Its `depend:` must cover everything the RTL uses:
   `x-heep::packages` (xheep_* types), `pulp-platform.org::common_cells`, plus for a
   regtool block `pulp-platform.org::register_interface` **and `lowrisc:prim:subreg`**
   (the generated reg_top instantiates `prim_subreg`; missing it fails elaboration).
   List files in dependency order (packages first, wrapper last).
7. **Lint waivers**: `lint/<ip>.vlt` from `templates/ip.vlt`, referenced from the
   core under `tool_verilator? (files_verilator_waiver)`. Waive only what you have read
   and understood, with a comment per waiver saying why it is intentional.
8. **Headers**: department-authored files carry the CEIMM-UPM header (see
   `templates/`). regtool outputs keep their lowRISC header; do not edit them.

To instantiate a template, copy it and replace the placeholders:

```bash
T=.claude/skills/gr-heep-ip-integration/templates
sed -e 's/{{ip}}/foo_acc/g' -e 's/{{IP}}/FOO_ACC/g' -e 's/{{Ip}}/FooAcc/g' \
    -e 's/{{ip-dash}}/foo-acc/g' -e 's/{{YEAR}}/2026/g' \
    -e 's/{{AUTHOR}}/Name Surname (name@upm.es)/g' "$T/ip.core" > hw/vendor/foo_acc/ceimm_upm_foo_acc.core
```

Then edit the `TODO` lines by hand.

**Check 1** (must pass before Phase 2):

```bash
scripts/gr_heep_env.sh bash hw/vendor/<ip>/data/gen_<ip>_regs.sh  # if there is a regtool block (env = verible)
git -C hw/vendor/<ip> status --short                       # regenerating twice shows no diff
scripts/gr_heep_env.sh fusesoc --cores-root . core-info ceimmupm:accelerators:<ip-dash>
X=hw/vendor/x-heep/hw; PRIM=$X/vendor/lowrisc/opentitan/hw/ip/prim/rtl
scripts/gr_heep_env.sh verilator --lint-only -Wall -Wno-DECLFILENAME -Wno-SYNCASYNCNET \
    -I$PRIM -I$X/vendor/pulp_platform/register_interface/include \
    -I$X/vendor/pulp_platform/common_cells/include \
    $X/core-v-mini-mcu/include/xheep_reg_pkg.sv $X/core-v-mini-mcu/include/xheep_obi_pkg.sv \
    $(find $X/vendor/lowrisc -name prim_util_pkg.sv | head -1) $PRIM/prim_subreg{,_ext,_arb}.sv \
    $X/vendor/pulp_platform/register_interface/src/reg_demux.sv \
    <every .sv of the IP, in core order> --top-module <ip> 2>&1 | grep -E "^%(Error|Warning)" | grep -v "_reg_top.sv\|_reg_pkg.sv\|reg_demux.sv"
```

Warnings inside the regtool-generated `*_reg_top.sv` / `*_reg_pkg.sv`
(`PINCONNECTEMPTY`, `UNUSEDPARAM`) come from regtool and are never fixed by hand; the full GR-HEEP
build accepts them. Everything else must be fixed or waived with a reason. Add more dependency files to that command if the IP uses common_cells modules or
AXI. The full GR-HEEP build in Phase 2 is the authoritative check. Also run
`verible-verilog-lint` on the hand-written files.

---

## Phase 2: Wire it into the SoC

Read `reference/mcu-gen.md` first. Steps:

1. **`config/mcu-gen-config.py`, `gr_heep_config()`**: add the IP to `ext_periph`
   and/or `ext_xbar_slaves` (key `"<ip>"` = snake name; the insertion order is the
   index), raise `ext_xbar_nmasters` by the IP's master count, and set
   `external_interrupts` so that it covers the IP's interrupt index (interrupt index =
   peripheral index, see mcu-gen.md §4). Update the comments that document who owns
   which master slice / interrupt bit. For class E, `system.set_xif(...)` and the CPU in
   `config()`. For class F, `hw_fifo_channels`.
2. **`gr-heep.core`**: add the IP's VLNV under `files_rtl_generic: depend:`.
3. **`hw/gr-heep/gr_heep_peripherals.sv.tpl`**: add the instantiation in the matching
   loop or block, using the snippet for its class (mcu-gen.md §3). Use the generated
   index parameters (`gr_heep_pkg::<Ip>PeriphIdx`, `gr_heep_pkg::<Ip>Idx`) and
   `${a_slave['idx']}`; never hard-code a number that mcu-gen generates. Tie every
   unused input of the IP to a constant on the instance (`1'b1` clock enables, `'0`
   test modes) and leave unused outputs unconnected with `()`.
4. **Regenerate**: `scripts/gr_heep_env.sh make mcu-gen`. It also runs verible on
   `hw/gr-heep`. Then check the outputs:
   ```bash
   grep -n "<Ip>" hw/gr-heep/gr_heep_pkg.sv hw/gr-heep/gr_heep_peripherals.sv
   grep -n "<IP>" sw/external/lib/runtime/gr_heep.h
   git diff --stat hw/gr-heep sw/external/lib/runtime
   ```
   The generated files are committed, and CI's `lint` job diffs them, so they are part
   of the change.
5. **Format Python**: `scripts/gr_heep_env.sh make format-python` (black on `config/`
   and `test/`). CI fails on unformatted Python.
6. **Build the model**: `scripts/gr_heep_env.sh make verilator-build 2>&1 | tail -40`.
   Fix every `%Error` and every new `%Warning` from the IP. Warnings inside the IP go in
   the IP's `lint/<ip>.vlt`. Warnings in GR-HEEP's top (unused ports of a new
   class) go in `hw/gr-heep/gr_heep_waivers.vlt`, which is hand-written.

**Check 2**: `make mcu-gen` and `make verilator-build` both finish with no errors, and
running `make mcu-gen` a second time leaves `git status` unchanged.

---

## Phase 3: Software

1. **Register header**: link the generated header into the driver folder (a symlink,
   so that the IP repository stays the single source of truth):
   ```bash
   mkdir -p sw/external/lib/drivers/<ip>
   ln -s ../../../../../hw/vendor/<ip>/sw/<ip>_regs.h sw/external/lib/drivers/<ip>/<ip>_regs.h
   ```
   Everything under `sw/external/` is picked up by X-HEEP's CMake automatically (include
   paths and `.c` files); there is no list to edit.
2. **Driver**: `sw/external/lib/drivers/<ip>/<ip>.h` from `templates/driver.h`.
   Base address macros come from the generated `gr_heep.h`:
   `<IP>_PERIPH_START_ADDRESS` (class A), `<IP>_START_ADDRESS` (class B).
   Use only `static inline` functions on `volatile` pointers, and use the regtool
   macros (`<IP>_<REG>_REG_OFFSET`, `<IP>_<REG>_<FIELD>_BIT`). Do not repeat raw
   offsets.
3. **Test app**: `sw/applications/<ip>/` from `templates/app_main.c` and
   `templates/gen_data.py`. Contract (same as every other app here):
   - `gen_data.py` writes `dataset.h` to stdout with the inputs and a golden computed
     by an **independent** Python model. `make app` runs it automatically (target
     `gen-app-data`). `dataset.h` is gitignored; check with
     `git check-ignore sw/applications/<ip>/dataset.h`.
   - `main.c` checks every result against the golden and returns the number of
     failures, so `0` means pass. When the IP accelerates a computation, also run a
     CPU reference on the same data, check it too, and print the cycle counts and the
     speedup (`mcycle`, after clearing `mcountinhibit`).
   - Exercise every feature you wired: each register, each access width the IP
     supports (word / halfword / byte), the restart path, error responses if any, and
     the interrupt (FIC enable bit + pending bit, mcu-gen.md §4). Bound every wait loop
     with a spin limit, so that a missing done or IRQ fails the test instead of hanging.
   - Keep the run short: Verilator dumps a waveform on every run (~2 kcycles/s).
4. **Run it**:
   ```bash
   timeout 1800 scripts/gr_heep_env.sh make verilator-run-app PROJECT=<ip> 2>&1 | tail -30
   cat build/x-heep_systems_gr-heep_0/sim-verilator/uart0.log
   ```
   Pass = `Program Finished with value 0` and every check printing OK.

**Check 3**: the app passes. `make test` picks up every directory in
`sw/applications` automatically. If the app is too slow for `SIM_TIMEOUT_S` in
`test/gr_heep_test_apps.py`, shrink its default data set rather than raising the
timeout.

---

## Phase 4: Wrap-up

1. Add a short section to `CLAUDE.md` (Architecture): what the IP is, its classes and
   address/IRQ/master slice, its driver and app, and any constraint a future change must
   respect. Keep it factual.
2. Final checklist, all yes:
   - [ ] No VHDL, no vendor primitives, no `reg_pkg`/`obi_pkg` left in the IP's compiled files
   - [ ] Regenerating the IP's regtool block and `make mcu-gen` both leave `git status` clean
   - [ ] `make verilator-build` has no new warnings
   - [ ] App passes, and its result covers every wired feature
   - [ ] `make format-python` and `make verible` clean
   - [ ] Comments that document master slices / interrupt bits in `mcu-gen-config.py` are updated
   - [ ] Nothing committed (unless the user asked)
3. Report to the user: what changed inside the submodule and outside it, the measured
   numbers, anything skipped or deviating from the plan, and the two-step commit order
   for when they want to commit.

---

## Pitfalls that already cost time (read before Phase 2)

- **Interrupt index = peripheral index.** The template drives
  `gr_heep_peripheral_vec_int[${a_slave['idx']}]`. Adding a peripheral in front of
  others shifts their indices, and with them their `EXT_INTR_<k>` numbers. A
  peripheral without an IRQ still occupies an index: if a later one has an IRQ, tie
  the bit of the IRQ-less one to `1'b0` in its branch, or the vector bit is undriven.
- **The fast interrupt does not latch unless enabled.** The OR of all external IRQs goes
  to FIC line 15. Polling `FAST_INTR_PENDING` sees nothing until bit 15 of
  `FAST_INTR_ENABLE` is set.
- **Master slices are hard-coded** in the template: each IP with master ports takes
  `gr_heep_master_req_o[<first>+:<count>]`, where `<first>` is the sum of the counts
  of the IPs before it. Append new masters after the existing ones and update the comment in `mcu-gen-config.py`. If an earlier IP is
  disabled, its slice still has to exist or be removed consistently.
- **External slaves expose a latent bug.** In `hw/gr-heep/gr_heep_bus.sv`
  (hand-written) the DMA response mapping uses `DMA_*_P0_IDX+i` while the request uses
  `+i*3`. With `num_master_ports=2` (current config) DMA port 1 gets the wrong
  responses as soon as `ext_xbar_slaves` is non-empty (only then is `gr_heep_bus`
  instantiated). Point this out to the user before adding the first class B IP, and
  fix it with their approval (`+i` → `+i*3` in `gen_dma_master_resp_map`).
- **`ExtPeriphDefaultIdx` is 0**: an access to an unmapped offset in the external
  peripheral window goes to peripheral 0 instead of failing. Keep each peripheral's
  `length` honest.
- **Window reads need a wait state** when the memory behind them has a registered
  output: assert `ready` one cycle after `valid` (a 1-bit pending flag, see the
  window block in `templates/ip_wrapper.sv`).
  Writes can complete in the same cycle.
- **Byte strobes**: honour `wstrb` (halfword stores from C `int16_t` arrays are common).
- **Read the `mcu-gen` recipe in the `Makefile`** before running it: it may run extra
  generation steps for other IPs that need their submodules checked out
  even when those IPs are disabled in the config.
- **Verible formats generated regtool RTL** in `gen_<ip>_regs.sh`. Without it, the next
  regeneration shows spurious diffs.
- **Generic module names collide**: FuseSoC compiles every core into one namespace.
  Two IPs with a `fsm` or `top` module break the build in confusing ways.
