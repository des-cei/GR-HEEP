# Making an IP GR-HEEP-compatible (Phase 1)

Every change in this file is made **inside the IP's submodule** (`hw/vendor/<ip>`).
Pick the section for each interface that the Phase 0 survey marked as incompatible.
When several options fit, the first one listed is the recommended one.

All bridges named below are already vendored in X-HEEP and found by FuseSoC:

| Module | Converts | FuseSoC dependency | File |
|---|---|---|---|
| `reg_to_apb` | reg interface → APB | `pulp-platform.org::register_interface` | `hw/vendor/x-heep/hw/vendor/pulp_platform/register_interface/src/reg_to_apb.sv` |
| `reg_to_axi` | reg interface → AXI4 | `pulp-platform.org::reg_to_axi` | `.../register_interface/src/reg_to_axi.sv` |
| `axi_lite_to_reg`, `axi_to_reg_v2` | AXI → reg interface | `pulp-platform.org::reg_to_axi` | same folder |
| `periph_to_reg` | req/gnt/rvalid (OBI-like) → reg interface | `pulp-platform.org::register_interface` | same folder |
| `reg_cdc_src` / `reg_cdc_dst` | reg interface across clock domains | `pulp-platform.org::register_interface` | `.../src/reg_cdc.sv` |
| `axi_to_mem` | AXI4 slave → req/gnt/rvalid memory port (≈ OBI master) | `pulp-platform.org::axi` | `.../pulp_platform/axi/src/axi_to_mem.sv` |
| `axi_to_axi_lite`, `axi_lite_to_axi`, `axi_dw_converter` | AXI variants and width | `pulp-platform.org::axi` | `.../pulp_platform/axi/src/` |

AXI types come from macros: `` `include "axi/typedef.svh" `` then
`` `AXI_TYPEDEF_ALL(name, addr_t, id_t, data_t, strb_t, user_t) `` (or
`` `AXI_LITE_TYPEDEF_ALL ``). X-HEEP does not vendor the `apb` package, so declare
the two APB request/response structs by hand in the wrapper for `reg_to_apb`.

---

## 1. The regtool MMIO wrapper (recommended for control/status)

Use it when the IP has: an AXI-Lite or APB **register file** (a Vivado AXI-Lite slave
template with `slv_reg0..N` is the typical case), raw control signals (`start`,
`done`, `busy`, config inputs), or raw RAM ports that software must read and write.
The IP's datapath stays untouched; a new top
`rtl/<ip>.sv` exposes the X-HEEP register interface and drives the old core.

### 1.1 Files

| File | From template | Notes |
|---|---|---|
| `data/<ip>_regs.hjson` | `templates/ip_regs.hjson` | Register description, the single source of truth |
| `data/gen_<ip>_regs.sh` | `templates/gen_ip_regs.sh` | Runs regtool, writes `rtl/<ip>_reg_pkg.sv`, `rtl/<ip>_reg_top.sv`, `sw/<ip>_regs.h`, formats with verible |
| `rtl/<ip>.sv` | `templates/ip_wrapper.sv` | Wrapper: reg_top + glue + the original core |
| `<ip>.core` | `templates/ip.core` | Adds `register_interface` and `lowrisc:prim:subreg` |

`chmod +x data/gen_<ip>_regs.sh`, then run it. It finds regtool in X-HEEP's vendored
tree (`hw/vendor/x-heep/hw/vendor/pulp_platform/register_interface/vendor/lowrisc_opentitan/util/regtool.py`).
Commit its outputs to the IP repo (GR-HEEP's software links to `sw/<ip>_regs.h`).

### 1.2 Mapping an existing register map

1. List every register of the old interface: offset, width, fields, who writes it
   (software or hardware), and any side effect on access (start on write, clear on
   read, FIFO pop).
2. Keep the **same offsets** if software for the IP already exists; otherwise group
   control at 0x0, status at 0x4, interrupt enable at 0x8, configuration after that, and
   windows at an offset aligned to their size.
3. Translate each register with the table in 1.3. A Vivado `slv_regN` that software
   writes and hardware reads → `swaccess: "rw", hwaccess: "hro"`; one that hardware
   writes → `swaccess: "ro", hwaccess: "hwo"`.
4. Data wider than 32 bits → several registers or a `multireg` (`count`, `cname`). The
   wrapper concatenates them.

### 1.3 Register patterns

| Need | hjson | Wrapper |
|---|---|---|
| One-cycle start pulse | `swaccess: "wo", hwaccess: "hro", hwqe: "true"`, field `START` | `start = reg2hw.ctrl.qe & reg2hw.ctrl.q & ~busy;` |
| Sticky done, cleared by software | field `DONE`: `swaccess: "rw1c", hwaccess: "hrw"` | `hw2reg.status.done.de = done_pulse \| start; hw2reg.status.done.d = done_pulse;` (START also clears it) |
| Live status bit | field `BUSY`: `swaccess: "ro", hwaccess: "hwo"` | `hw2reg.status.busy.de = 1'b1; hw2reg.status.busy.d = busy;` |
| Interrupt enable + level IRQ | `INTR_EN`, `swaccess: "rw", hwaccess: "hro"` | `intr_o = reg2hw.status.done.q & reg2hw.intr_en.q;` |
| Configuration value | `swaccess: "rw", hwaccess: "hro"`, `resval` = the IP's reset value | `core.cfg = reg2hw.cfg.q;` |
| Read side effect (FIFO pop, clear-on-read counter) | `hwext: "true", hwre: "true"` | `pop = reg2hw.rx.re;`, `hw2reg.rx.d = fifo_head;` (the register lives outside regtool) |
| Write side effect with data (FIFO push) | `hwext: "true", hwqe: "true"` | `push = reg2hw.tx.qe; data = reg2hw.tx.q;` |
| Memory software reads/writes | `window` (1.5) | Window port of reg_top |

Field naming in the generated structs: a register with **one** field is flattened
(`reg2hw.intr_en.q`). A register with several fields has one level per field
(`reg2hw.status.done.q`, `hw2reg.status.busy.d`). When unsure, read the generated
`rtl/<ip>_reg_pkg.sv`.

### 1.4 Wrapper rules

- Ports: `clk_i`, `rst_ni` (active-low async), `reg_req_i` / `reg_rsp_o` with
  `xheep_reg_pkg` types, then masters, then `intr_o`. Instantiate the reg_top with
  `.reg_req_t(xheep_reg_pkg::xheep_reg_req_t)`, `.reg_rsp_t(...)` and `.devmode_i(1'b1)`.
- Adapt the old core's conventions in the wrapper, not inside the core: reset
  polarity (`.rst(~rst_ni)` for an active-high core), level vs pulse start (the regtool
  `qe` is already a pulse), done pulse vs level (sticky in DONE).
- Ignore START while busy, and say so in the field `desc`.
- Header comment: the register map in four lines (offset, name, meaning), as in
  `templates/ip_wrapper.sv`.

### 1.5 Memory windows (raw RAM ports exposed to software)

```hjson
{ skipto: "0x200" }
{ window: { name: "MEM", items: "128", validbits: "32", byte-write: "true",
            swaccess: "rw", desc: "..." } }
```

- `items` = number of 32-bit words. Put the window at an offset that is a multiple of
  its size rounded up to a power of two (regtool rejects misaligned windows).
- With one window, reg_top gets scalar ports `reg_req_win_o` / `reg_rsp_win_i` (the
  same `xheep_reg_*` types). With several, they become arrays indexed by window
  order. The address is the bus address: use its low bits as the word index
  (`win_req.addr[<log2(items)+1>:2]`).
- The window response is **combinational** in the wrapper:
  - write: `ready = 1` in the same cycle; byte enables = `win_req.wstrb` (merge to the
    memory's write granularity, e.g. `{|wstrb[3:2], |wstrb[1:0]}` for two 16-bit lanes).
  - read from a memory with registered output: `ready = 0` in the first cycle,
    `ready = 1` with `rdata` in the second (a 1-bit `rd_pending_q` flag, see the window block in
    `templates/ip_wrapper.sv`).
  - access while the core owns the memory: `ready = 1, error = 1`, and drop the write.
- If the core and the bus share one RAM port, add the mux in the wrapper, selected by
  busy. A memory with a second, dedicated port for the bus needs no mux.
- Software then sees the memory as a C array:
  `((volatile int16_t *)(<IP>_PERIPH_START_ADDRESS + <IP>_<WIN>_REG_OFFSET))`.

### 1.6 When the window is too big

A window lives in the 4 KiB slot of the peripheral (or up to the 64 KiB total). For
more, or for DMA-friendly bulk access, expose the memory as a **class B OBI slave**
instead (second entry in `ext_xbar_slaves`) and keep only the control registers on
the reg interface.

---

## 2. Slave buses that must stay (bridges)

Use a bridge when the IP's slave interface must not change (third-party IP, shared with
another SoC) or is not a simple register file.

| IP slave | GR-HEEP class | Bridge in the wrapper |
|---|---|---|
| APB | A | `reg_to_apb` (reg → APB) |
| AXI4-Lite | A | `reg_to_axi` (reg → AXI4) + `axi_to_axi_lite`, or rewrite as §1 if it is only a register file |
| AXI4 (memory-like, large) | B | OBI slave → `periph_to_reg` (`req`=req, `add`=addr, `wen`=**~we**, `be`, `wdata`; `gnt`, `r_valid`=rvalid, `r_rdata`=rdata) → `reg_to_axi` |
| Wishbone / Avalon / custom | A | Small hand-written FSM reg → protocol; the reg interface is simple: hold the request until `ready`, `error` flags a bus error |
| Different clock | A | `reg_cdc_src` in `clk_i`, `reg_cdc_dst` in the IP clock (the IP clock must come from somewhere: a new pad, or a divided `clk_i`; **ASK**) |

Check data widths: the GR-HEEP side is always 32 bits, so a 64-bit AXI slave needs
`axi_dw_converter`. The address passed through is the full system address; mask it to
the IP's range if the IP decodes absolute addresses.

---

## 3. Master buses (the IP reads/writes system memory, class C)

GR-HEEP master ports are 32-bit **OBI** (`xheep_obi_req_t`: `req, we, be, addr, wdata`;
`xheep_obi_rsp_t`: `gnt, rvalid, rdata`). Rules: hold `req` and the payload stable
until `gnt`; one `rvalid` per granted request (writes too), in order; no bursts.

| IP master | What to do |
|---|---|
| OBI-like req/gnt/rvalid with other names | Rename the signals in the wrapper; check the protocol rules above (stable payload until `gnt`, one `rvalid` per request) |
| AXI4 / AXI4-Lite master | IP AXI master → `axi_to_mem` (as the AXI **slave**, `NumBanks=1`, `DataWidth=32`, `AddrWidth=32`) → its `mem_*` port maps to OBI: `req=mem_req_o`, `we=mem_we_o`, `be=mem_strb_o`, `addr=mem_addr_o`, `wdata=mem_wdata_o`, `mem_gnt_i=gnt`, `mem_rvalid_i=rvalid`, `mem_rdata_i=rdata`. Tie `mem_atop_o` unused and make sure the IP issues no atomics. One `axi_to_mem` per AXI master = one OBI port. |
| 64-bit AXI master | `axi_dw_converter` to 32 bits first, then the above |
| Several logical masters | One OBI port each (more bandwidth, more crossbar area), or arbitrate inside the IP. A common choice is one port per independent data stream (e.g. one per input/output engine) |
| Bus-width addresses < 32 bits | Zero-extend and add the base the software programs |

Each OBI port counts in `ext_xbar_nmasters`. Expose the count as a package parameter
(`<ip>_pkg::NumMasters`) so that the template can slice with it.

---

## 4. CV-X-IF coprocessors (class E)

1. Find the version the IP implements: `if_xif` interface / modports
   `coproc_issue` etc. → **v0.2** (cv32e40px, cv32e40x); separate `x_issue_req_t`,
   `x_register_t`, `x_commit_t`, `x_result_t` structs → **v1.0** (cv32e20 in this
   X-HEEP). The two are not wire-compatible. Writing an adapter is a design task:
   **ASK**.
2. CPU change: GR-HEEP uses `cv32e40p`, which has no XIF. **ASK** before switching
   (it affects every app's timing, the FPGA builds, and `ARCH`).
3. Do not ship a copy of the interface definition in the IP. `if_xif` comes from
   X-HEEP's vendored cv32e40x (`hw/vendor/x-heep/hw/vendor/openhwgroup/cv32e40x/rtl/if_xif.sv`)
   and is always compiled, because `gr_heep.sv` instantiates it. A second definition
   fails elaboration.
4. Parameters must agree with `CvXIf(...)` in the config: `X_NUM_RS`, `X_ID_WIDTH`,
   `X_RFR_WIDTH`, `X_RFW_WIDTH`, `X_MEM_WIDTH`, `X_MISA`. Read them from the interface
   in the wrapper instead of hard-coding them.
5. Opcodes: make sure the coprocessor accepts only its own encodings (custom-0..3
   space) and rejects everything else with `accept = 0`, or it will steal standard
   instructions.
6. Unused memory interface (`xif_mem_if`) and compressed interface: tie `mem_valid = 0`,
   `compressed_ready`/`accept = 0`.
7. Test from C with `.insn r CUSTOM_0, funct3, funct7, rd, rs1, rs2` inline asm, and
   compare against a C model.

## 5. Streams (AXI-Stream, valid/ready FIFOs, class F)

- If the data comes from or goes to memory: use the DMA hw_fifo mode (mcu-gen.md §3,
  class F). Adapter: AXIS `tvalid/tready/tdata` ↔ `push`/`full` toward the IP, and
  `pop`/`empty` + `data` away from it. Put a small `fifo_v3` (common_cells) inside the
  wrapper for the response side, as X-HEEP's `dlc` example does
  (`hw/vendor/x-heep/hw/ip_examples/dlc/rtl/dlc.sv`). `hw_fifo_done_o` signals the end
  of the stream to the DMA.
- If the volume is small, a register (hwext + qe/re) or a window is simpler.
- `TLAST`/`TKEEP`: map to a register or a `done` condition; the hw_fifo has no sideband.

## 6. VHDL sources

The GR-HEEP simulation flow is Verilator, which reads no VHDL. Mixed-language
simulation and Vivado would work, but `make test` and CI would not. Options, to
**ASK** about:

1. The user provides SystemVerilog sources (e.g. dropped in a temporary folder of
   the repo). Move them into `rtl/`, then remove the VHDL and the temporary folder
   with `git rm`.
2. Port to SystemVerilog by hand, entity by entity, keeping names (prefixed) and
   comments. Verify against the VHDL with the original testbench if one exists, or at
   least with the golden model of the GR-HEEP app.
3. Automatic conversion (GHDL synth `--out=verilog`, or similar) gives flat,
   unreadable code. Use it only as a temporary step, and only if the user agrees.

After the port: no `.vhd` left in the core's filesets, and the old files deleted
from the repo (not kept "just in case").

## 7. Vendor primitives and initialization files

| Found | Replace with |
|---|---|
| Xilinx BRAM IP / `xpm_memory_*` / `RAMB*` | Behavioural `logic [W-1:0] mem [D]` with a synchronous read (infers BRAM in Vivado, works in Verilator). Keep the original read latency, since the wrapper's wait states depend on it |
| `.coe` / `.mif` ROM init | A generated SV ROM: a script in `data/` that reads the `.coe` and writes `rtl/<ip>_<rom>.sv` as a `case` or a constant array, committed together with the script. Generated output must be byte-identical on re-run |
| `$readmemh` with a relative path | The same generated ROM. Relative paths break under FuseSoC's build directory |
| DSP48 / clock primitives (`BUFG`, `MMCM`) | Behavioural code. Real clock primitives only in an FPGA-only fileset (§8) |
| Asynchronous FIFOs from vendor IP | `cdc_fifo_gray` (common_cells) |

Tell the user what was replaced. Timing/area on FPGA may change.

## 8. Clock gating and target-specific files

Pattern for an IP with a clock gate:

- A clock gate is a separate module with two implementations: `sim/<ip>_clock_gate.sv`
  (behavioural, latch-based) under `target_sim? (files_behav_rtl)`, and
  `fpga/<ip>_clock_gate.sv` (`BUFGCE`) under one `target_<board>?` entry per board that
  GR-HEEP supports (pynq-z2, nexys-a7-100t, genesys2, aup-zu3, zcu102, zcu104). A board
  missing there builds with **no** clock gate module and fails to elaborate.
- The enable comes in as a wrapper input (`<ip>_clk_en_i`) and is tied to `1'b1` in
  `gr_heep_peripherals.sv.tpl` unless a register drives it.
- No clock gate needed → no target filesets. Keep it simple.

## 9. Names and namespace

FuseSoC elaborates every module and package of every core in one namespace. Prefix
every module, package, interface and global `define` of the IP with `<ip>_` (the
"prefix only" option), and rename files to match (`DECLFILENAME` lint). Keep internal
signal names and comments unless the user asked for more. After renaming:

```bash
grep -rn "^\s*\(module\|package\|interface\)\s" hw/vendor/<ip>/rtl | grep -v "<ip>_\|<ip>\b"
```

must print nothing (apart from regtool's `*_reg_top_intf`, which is prefixed already).

## 10. Conventions checklist for the IP's top (`rtl/<ip>.sv`)

- [ ] `clk_i`, `rst_ni` (active-low, asynchronous), `_i`/`_o` suffixes
- [ ] Bus types from `xheep_reg_pkg` / `xheep_obi_pkg` / `xheep_fifo_pkg` only
- [ ] One `intr_o` per interrupt, level-sensitive, reset to 0
- [ ] No `initial` blocks in synthesizable code (except generated ROMs if needed), no `#` delays
- [ ] No latches except in the sim clock gate
- [ ] Parameters with defaults that match the GR-HEEP use; widths taken from packages
- [ ] Verilator `-Wall` clean or waived with a reason in `lint/<ip>.vlt`
- [ ] verible-format applied (the generated regtool files are formatted by the gen script)
