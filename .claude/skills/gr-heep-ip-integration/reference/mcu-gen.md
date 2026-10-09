# mcu-gen reference for GR-HEEP IP integration

Sources of truth, if anything here looks stale: `config/mcu-gen-config.py`,
`hw/gr-heep/gr_heep_pkg.sv.tpl`, `hw/gr-heep/gr_heep.sv.tpl`,
`hw/gr-heep/gr_heep_peripherals.sv.tpl`, `sw/external/lib/runtime/gr_heep.h.tpl`,
`hw/vendor/x-heep/util/xheep_gen/` (CPU, CvXIf, DMA classes) and
`hw/vendor/x-heep/hw/core-v-mini-mcu/include/*_pkg.sv`.

## 1. Parameters of `gr_heep_config()` (config/mcu-gen-config.py)

Edit only the part above the "Do not modify below this line" marker.

| Variable | Type | Meaning | Limits / notes |
|---|---|---|---|
| `ext_xbar_nmasters` | int | Total number of OBI **master** ports that external IPs drive into X-HEEP's bus (class C). Becomes `gr_heep_pkg::ExtXbarNMaster` and `core_v_mini_mcu`'s `EXT_XBAR_NMASTER`. | Sum over all enabled IPs. Every port can reach all of X-HEEP's memory map (RAM banks, peripherals) and, if `ext_xbar_slaves` is non-empty, the external slaves too. |
| `ext_xbar_slaves` | dict `name -> {"offset", "length"}` | OBI **slave** ports (class B) on the external crossbar. | Base `EXT_SLAVE_START_ADDRESS` = `0xF000_0000`, region size `0x0100_0000` (16 MiB). Offsets must not overlap; keep them aligned to their length. A non-empty dict instantiates `gr_heep_bus` (see the bus bug in SKILL.md Pitfalls). |
| `ext_periph` | dict `name -> {"offset", "length"}` | **Register-interface** slaves (class A). | Base `EXT_PERIPHERAL_START_ADDRESS` = `0x2007_0000`, window size `0x10000` (64 KiB). Use 4 KiB (`0x1000`) slots unless the IP needs more. `offset + length <= 0x10000`. |
| `ao_spc_num` | int | Number of always-on SPC ports (`ext_ao_peripheral_req`) of the DMA subsystem. | GR-HEEP ties them to `'0` in `gr_heep.sv.tpl`. Leave at 1 unless the IP is an SPC (smart peripheral controller), which needs template changes in `gr_heep.sv.tpl` too. |
| `external_interrupts` | int | Width of the external interrupt vector. | `<= NEXT_INT` = `PLIC_NINT - PLIC_USED_NINT` = 64 - 52 = **12**. Must be `>= 1 + highest peripheral idx that drives an IRQ` (see §4). |
| `hw_fifo_channels` | list[int] | DMA channels whose hardware FIFO port is wired to an IP (class F). | Channel numbers `< num_channels` of the DMA (4 now). Requires `hw_fifo_mode="yes"` in the `DMA(...)` call in `config()` (already set). Channels not in the list are tied off by the template. |

The name key (e.g. `"foo_acc"`) is transformed by helpers at the bottom of the file:

| Form | Function | `foo_acc` | `bar_v2` |
|---|---|---|---|
| `a_slave['name']` | `CamelCase()` | `FooAcc` | `BarV2` |
| `a_slave['SCREAMING_NAME']` | `SCREAMING_SNAKE_CASE()` | `FOO_ACC` | `BAR_V2` |

Each entry of `gr_heep["peripherals"]` / `gr_heep["slaves"]` has: `name`,
`SCREAMING_NAME`, `idx` (insertion order, from 0), `offset`, `size`, `end_address`.

Other `config()` settings an IP may need (outside `gr_heep_config()`):

- `system.set_cpu(CPU("..."))`: one of `cv32e20`, `cv32e40p` (current), `cv32e40px`, `cv32e40x`.
- `system.set_xif(CvXIf(x_num_rs=2, x_id_width=4, x_mem_width=32, x_rfr_width=32, x_rfw_width=32, x_misa=0x0, x_ecs_xs=0x0))`.
  mcu-gen errors out if an XIF is set with `cv32e40p`.
- `DMA(...)` arguments (`num_channels`, `num_master_ports`, `hw_fifo_mode`, `fifo_depth`, ...).
- Memory banks / linker sections, if the IP needs a dedicated SRAM region.
- `EXTERNAL_DOMAINS` in the top `Makefile` (power domains for external IPs, default 0).
  It controls `external_subsystem_*` signals, which GR-HEEP currently leaves unused.

## 2. What mcu-gen generates from them

`gr_heep_pkg.sv` (SV parameters; use these, never literal numbers):

| Class A (`ext_periph`) | Class B (`ext_xbar_slaves`) | Global |
|---|---|---|
| `<Ip>PeriphIdx` | `<Ip>Idx` | `ExtXbarNMaster`, `ExtXbarNMasterRnd` |
| `<Ip>PeriphStartAddr` / `Size` / `EndAddr` | `<Ip>StartAddr` / `Size` / `EndAddr` | `ExtXbarNSlave`, `ExtXbarNSlaveRnd` |
| `ExtPeriphAddrRules`, `ExtPeriphNSlave` | `ExtSlaveAddrRules` | `ExtInterrupts`, `AoSPCNum` |

`sw/external/lib/runtime/gr_heep.h` (C macros):

| Class A | Class B | Global |
|---|---|---|
| `<IP>_PERIPH_START_ADDRESS`, `_SIZE`, `_END_ADDRESS` | `<IP>_START_ADDRESS`, `_SIZE`, `_END_ADDRESS` | `EXT_XBAR_NMASTER`, `EXT_XBAR_NSLAVE` |

X-HEEP's `core_v_mini_mcu.h` gives `EXT_INTR_0` .. `EXT_INTR_11` (PLIC IDs 52..63),
`FAST_INTR_CTRL_START_ADDRESS`, `EXT_PERIPHERAL_START_ADDRESS`, `EXT_SLAVE_START_ADDRESS`.

## 3. Instantiation snippets for `gr_heep_peripherals.sv.tpl`

Template variables available in that file: `gr_heep` (the kwargs dict above), `xif`
(`CvXIf` or `None`), `cpu` (`cpu.name`), `dma`, `hw_fifo` (bool). The module's ports
exist only when their parameter is non-zero. The template already handles that, along
with the trailing commas. Do not touch the port list unless you add a new class
(e.g. pads).

Port names inside `gr_heep_peripherals`:

| Signal | Type | Exists when |
|---|---|---|
| `gr_heep_peripheral_req[<Ip>PeriphIdx]` / `gr_heep_peripheral_rsp[...]` | `xheep_reg_req_t` / `xheep_reg_rsp_t` (already demuxed) | `periph_nslaves > 0` |
| `gr_heep_slave_req_i[<Ip>Idx]` / `gr_heep_slave_resp_o[...]` | `xheep_obi_req_t` / `xheep_obi_rsp_t` | `xbar_nslaves > 0` |
| `gr_heep_master_req_o[...]` / `gr_heep_master_resp_i[...]` | `xheep_obi_req_t` / `xheep_obi_rsp_t`, `[ExtXbarNMasterRnd-1:0]` | `xbar_nmasters > 0` |
| `gr_heep_peripheral_vec_int[k]` | `logic` (OR'd into `gr_heep_peripheral_int_o` for you) | `ext_interrupts > 0` |
| `hw_fifo_req_i[ch]` / `hw_fifo_rsp_o[ch]` / `hw_fifo_done_o[ch]` | `xheep_fifo_req_t` / `xheep_fifo_rsp_t` / `logic` | `hw_fifo` |
| `xif_*_if` | `if_xif.coproc_*` modports | `xif` |

### Class A (+D, +C): inside the existing `% for a_slave in gr_heep["peripherals"]:` loop

Add an `% elif` branch next to the existing ones (one branch per peripheral, matched
by its CamelCase name):

```
        % elif (a_slave['name'] == "<Ip>"):
          // <short description>
          <ip> <ip>_i (
              .clk_i(clk_i),
              .rst_ni(rst_ni),
              .reg_req_i(gr_heep_peripheral_req[gr_heep_pkg::<Ip>PeriphIdx]),
              .reg_rsp_o(gr_heep_peripheral_rsp[gr_heep_pkg::<Ip>PeriphIdx]),
              // class C only: a contiguous slice after the masters already in use
              .masters_req_o(gr_heep_master_req_o[<FIRST>+:<COUNT>]),
              .masters_resp_i(gr_heep_master_resp_i[<FIRST>+:<COUNT>]),
              // class D only
              .intr_o(gr_heep_peripheral_vec_int[${a_slave['idx']}])
          );
```

- `<COUNT>` should be a parameter from the IP's package (`<ip>_pkg::NumMasters`), and
  `<FIRST>` the sum of the earlier IPs' counts written with their package parameters
  (e.g. `[a_pkg::NumMasters+:b_pkg::NumMasters]` for the second IP with masters). Then `ext_xbar_nmasters` in the config
  must equal the total. Write that total in the comment above it.
- If the IP has no IRQ but a later peripheral does, add
  `assign gr_heep_peripheral_vec_int[${a_slave['idx']}] = 1'b0;` in its branch.

### Class B: new loop, does not exist yet

Add it after the peripherals block, guarded the same way:

```
  % if (gr_heep["xbar_nslaves"] > 0):
    % for a_slave in gr_heep["slaves"]:
        % if (a_slave['name'] == "<Ip>"):
          <ip>_mem <ip>_mem_i (
              .clk_i(clk_i),
              .rst_ni(rst_ni),
              .slave_req_i(gr_heep_slave_req_i[gr_heep_pkg::<Ip>Idx]),
              .slave_resp_o(gr_heep_slave_resp_o[gr_heep_pkg::<Ip>Idx])
          );
        % endif
    % endfor
  % endif
```

The OBI slave must implement the protocol as X-HEEP's memories do: `gnt` may be given
in the same cycle as `req`, `rvalid` exactly once per granted request, in order (for
writes too), `rdata` valid with `rvalid`. The address is the full 32-bit system address:
subtract `<Ip>StartAddr` or use the low bits. An IP that is both A and B (registers +
big buffer) appears once in each dict, with two different names (e.g. `"foo"` and
`"foo_mem"`).

### Class C without registers

This is rare. Add a branch in the peripherals loop anyway if the IP has any control
port. Otherwise instantiate it unconditionally under `% if (gr_heep["xbar_nmasters"] > 0):`.

### Class D only (an IP that just raises an interrupt)

It still needs an index. Give it a peripheral entry if it has registers. If not, take
the next bit after the last peripheral's, and raise `external_interrupts` by one.

### Class E: CV-X-IF coprocessor

The template has two commented examples at the bottom:

- `cpu.name == "cv32e40px"` (and `cv32e40x`): CV-X-IF **v0.2**. Connect the six
  `if_xif` modports directly (`xif_compressed_if`, `xif_issue_if`, `xif_commit_if`,
  `xif_mem_if`, `xif_mem_result_if`, `xif_result_if`), as X-HEEP's `fpu_ss_wrapper`
  does (`hw/vendor/x-heep/hw/ip_examples/fpu_ss_wrapper`). Make the guard match the
  CPU you set: the example only checks `cv32e40px`.
- `cpu.name == "cv32e20"`: CV-X-IF **v1.0**. The template already converts the
  interface into `cve2_x_issue_*`, `cve2_x_register*`, `cve2_x_commit*`,
  `cve2_x_result*` signals with `cve2_pkg` types. Connect a v1.0 coprocessor to those,
  following the `cvxif_example_coprocessor` example.
- Only **one** coprocessor can sit on the interface. Two need an arbiter/decoder that
  you write (accept by opcode, route results by `id`).
- Unused modport outputs must be driven (tie-offs), or Verilator complains.
- Also: `x_num_rs` must match what the coprocessor reads (2 or 3), `x_misa`/`x_ecs_xs`
  stay 0 unless the coprocessor provides floating point/vector state. Apps are built with
  `ARCH=rv32imc_zicsr` by default. Custom instructions can be emitted with `.insn` in
  inline asm without changing it. Standard extensions (e.g. `zfinx`) need an `ARCH`
  override on `make app`, and the toolchain must support them. **ASK** before changing
  the default.

### Class F: hw_fifo (DMA hardware-FIFO mode)

```
  % if (hw_fifo):
          <ip> <ip>_i (
              ...
              .hw_fifo_req_i (hw_fifo_req_i[<CH>]),
              .hw_fifo_resp_o(hw_fifo_rsp_o[<CH>]),
              .hw_fifo_done_o(hw_fifo_done_o[<CH>])
          );
  % endif
```

Put `<CH>` in `hw_fifo_channels`. Semantics (see X-HEEP's `hw/ip_examples/dlc`):
`req.push` + `req.data` = the DMA writes a word into the IP, which answers with
`rsp.full` / `rsp.alm_full` as back-pressure; `req.pop` = the DMA takes `rsp.data`
while `rsp.empty` is low; `req.flush` clears. Software sets up the DMA channel in
hw-FIFO mode with X-HEEP's DMA driver.

## 4. Interrupt routing

```
IP intr_o --> gr_heep_peripheral_vec_int[k] --+--> ext_int_vector[k] --> PLIC source EXT_INTR_k (= 52 + k)
                                              |                      --> power manager (wake-up)
                                              +--> OR --> intr_ext_peripheral --> fast interrupt 15 (FIC)
```

- `k` is `${a_slave['idx']}` = the IP's position in `ext_periph`.
- Polling without a handler (what `templates/app_main.c` does): set bit 15 in
  `FAST_INTR_CTRL_FAST_INTR_ENABLE`, then poll `FAST_INTR_PENDING` bit 15 and clear it
  via `FAST_INTR_CLEAR`. The FIC does not latch disabled lines.
- With a handler: go through the PLIC. Call `ext_irq_init()` from
  `sw/external/lib/runtime/ext_irq.c`, then use X-HEEP's `rv_plic` driver
  (`hw/vendor/x-heep/sw/device/lib/drivers/rv_plic`) to set a priority for
  `EXT_INTR_k`, enable it, and register a handler. X-HEEP's `fast_intr_ctrl` driver has
  no weak handler for line 15, so a handler on the fast line means writing the trap
  path yourself. Avoid that unless latency matters.
- Fast interrupt 15 is shared by all external IPs. A handler on it must read each IP's
  status to find the source. Prefer the PLIC when several IPs interrupt.
- Prefer a **level** interrupt = `sticky_done & enable`, cleared by software (rw1c). A
  one-cycle pulse is caught by the FIC and the PLIC, but software cannot tell later
  what caused it.

## 5. Addresses at a glance

| Region | Base | Size | Who |
|---|---|---|---|
| External peripherals (reg interface) | `0x2007_0000` | 64 KiB | `ext_periph` |
| External slaves (OBI) | `0xF000_0000` | 16 MiB | `ext_xbar_slaves` |
| Offsets already taken | read `ext_periph` / `ext_xbar_slaves` in `config/mcu-gen-config.py` | | count commented-out entries as taken too: they will come back |
