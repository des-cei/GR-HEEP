---
name: strela-app
description: >-
  Port an elastic-cgra DFG (mapper/applications/<app>/main.dot) into a GR-HEEP
  STRELA application under sw/applications/strela_<name>/ that runs on the CGRA
  and self-checks against golden data. Use whenever asked to "make an app for
  <kernel>", to accelerate an elastic-cgra kernel on GR-HEEP, or to regenerate an
  existing strela_* app after a re-solve. Covers the mapper invocation, the
  io_map-driven descriptor generation, the data generator contract, main.c, the
  checklist for scheduling it at full performance (scratchpad reuse, fence
  removal, engine assignment), and the deadlock checklist.
---

# Porting an elastic-cgra kernel into a GR-HEEP STRELA app

The reference implementations are `sw/applications/strela_fft` (streams only, no
accumulators) and `sw/applications/strela_mm` (accumulator PEs, multi-pass).
Copy whichever is closer and adapt — do not start from scratch.

Every shell step goes through the env wrapper:

```bash
scripts/gr_heep_env.sh <command>
```

**Never write `source /tools/env_x-heep.sh && make ...`.** That script ends in
`conda activate`, which fails in a non-interactive shell, so the `&&` chain
silently skips make and you get an empty log with exit 1 that looks like a build
failure. The wrapper loads conda's shell hook first.

## 1. Read the DFG before anything else

`hw/vendor/ceimm_upm_strela/rtl/elastic-cgra/mapper/applications/<app>/main.dot`
is the contract. Its header comment names what each port means; you cannot infer
it from the io_map, which only says *where* the mapper put each port.

Note these four things, they decide the whole app:

- **Port roles** — `input0..N` / `output0..M` and what each carries.
- **`[border=...]`** — `north` is a streamed input, `"west,east"` means the
  mapper must park it in a scratchpad, i.e. it is preloaded once and replayed.
- **`delay_value` / `feedback`** — accumulator PEs. Their delay is the reduction
  length and must be patched at runtime in `main.c` with `set_pe_delay_value()`
  (see `strela_mm` or `strela_gesummv`). A DFG with all `delay_value=0` needs no
  patching. The DFG says *how many* accumulators there are; it does not say
  which PE each landed on, and neither does the io_map. Read that out of the
  kernel array itself — word 2 holds `delay_value` in bits 31:16 and word 4 the
  PE constant, both at `PE_OFFSET[pe] + PE_POSITION[pe]*5` (`sw/strela.h`):

  ```bash
  python3 -c "import re,sys
  w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+',
      re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]
  P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12]; O=[0,1,2,3]*4
  for pe in range(16):
      b=O[pe]+P[pe]*5
      print(pe, 'delay', w[b+2]>>16, 'const', w[b+4])" <app>_kernel.h
  ```

  PEs with a non-zero delay are the accumulators; PEs with a non-zero constant
  are the `constant=` nodes, patchable with `set_pe_const()`. Those indices
  belong to *that* solve — re-derive them after a re-solve, and say so in
  `main.c` (`strela_gesummv` records the command in a comment).
- **Op mix** — only add/sub/mul/shift/logic/cmp/select exist. If the kernel needs
  a shift the DFG does not have, the rescale must happen on the host (below).

## 2. Get the bitstream

### Fast path: a committed regress bitstream (no Gurobi, seconds not minutes)

`hw/vendor/ceimm_upm_strela/rtl/elastic-cgra/regress/4x4-HV/<app>/` holds a
committed `bitstream.bin` + `io_map.json` for 21 HV kernels. That io_map carries
the `location` fields, which is all `strela_bind` needs, and `CGRA.load_bin()`
replays the bitstream into the same model `write_c_header()` emits from — so the
kernel header can be rebuilt without the mapper. **Check `ls regress/4x4-HV/`
before starting a solve.** This was verified against a real `fir8_hv` solve: the
replayed header and io_map are byte-identical to what the mapper produced, and
`load_bin` → `write_bin` reproduces the committed `.bin` exactly.

```bash
scripts/gr_heep_env.sh python3 scripts/regress2kernel.py <app> \
    -o sw/applications/strela_<name>/<app>_kernel.h
cp hw/vendor/ceimm_upm_strela/rtl/elastic-cgra/regress/4x4-HV/<app>/io_map.json \
   sw/applications/strela_<name>/<app>_io_map.json
```

The script writes the `map2bitstream.py` provenance line that `strela_lint.py`
greps for the DFG, so §6's lint then works with no `--dfg`. Use the mapper below
only for a DFG with no committed bitstream, or after changing a DFG.

### Slow path: run the mapper (~10 min, run it in the background)

```bash
scripts/gr_heep_env.sh make -C hw/vendor/ceimm_upm_strela/rtl/elastic-cgra \
    map-bitstream PROJECT=<app> CGRA_CONFIG=configs/4x4-HV.hjson
```

- **`configs/4x4-HV.hjson` is the only config STRELA accepts.** The shell
  hardcodes 4x4, 24 channels and 8 config lanes; any other config produces a
  `cgra` module whose ports do not match `strela.sv`.
- The Gurobi ILP took **~9 minutes** for a 10-node DFG, and **its output is
  fully buffered** — an empty log is not a hang. Launch it with
  `run_in_background: true` and do the app-writing work meanwhile.
- If it reports infeasible, that is a DFG/fabric problem, not an app problem:
  hand it to elastic-cgra's `mapper-debug` agent rather than editing the DFG here.

Or delegate the whole step to the **`strela-map` agent**, which maps, copies the
artifacts in, and runs the kernel lint.

Then copy **both** artifacts into the app directory, always as a pair — they
describe one solve, and a re-solve reshuffles the engine assignment:

```bash
CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
cp $CG/build/bitstream/<app>_kernel.h   sw/applications/strela_<name>/
cp $CG/build/bitstream/<app>_io_map.json sw/applications/strela_<name>/
```

Both are committed. The io_map is what lets `descriptors.h` be regenerated
without re-running the mapper.

## 3. `gen_data.py` — the dataset and the golden values

Contract enforced by `make gen-app-data` (a prerequisite of `make app`):

- **Runs with no arguments** and writes the header to **stdout**. Give every
  positional argument a default; the app is built by CI with no arguments.
- Arrays the CGRA *reads* get
  `__attribute__((section(".xheep_data_interleaved")))`; arrays it *writes* and
  the `*_expected` goldens do not (follow `strela_mm`/`strela_fft` exactly).
- Compute the goldens **with the same integer semantics as the DFG** — same
  operand order for `sub`, same truncation — so a mismatch means the hardware is
  wrong, not the reference.
- Guard overflow explicitly and `sys.exit` with a message that names the knob to
  turn. int32 wraps silently otherwise and the failure looks like a CGRA bug.

**Fixed point:** the fabric has no shifter unless the DFG contains `shl`/`shr`,
so a Q-format kernel cannot rescale between operands. Pre-scale on the host (in
`strela_fft`, the `a` operand is emitted already multiplied by 2^FRAC) and say so
in the docstring. This is also why FFT stages cannot be chained in one run.

## 4. `gen_descriptors.py` — the ISE/OSE tables

Use `strela_desc.StreamProgram`; never hand-encode descriptors, and never
hard-code which engine serves a port.

```python
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)
from strela_desc import StreamProgram

prog = StreamProgram(io_map=..., kernel="<app>_kernel", app="strela_<name>",
                     arrays={"name": (count, elem_bytes), ...}, sew=32)
prog.mark_output("out_array", ...)
prog.conf_all()                      # TR_CONF on all four ISEs, always first
with prog:                           # one pass; repeat the block for more
    prog.mem(port, sym, idx, stride=…, count=…, size=…, iters=…)   # scratchpad
    prog.stream(port, sym, idx, stride=…, count=…)                 # north/bus
    prog.out(port, sym, idx, stride=…, count=…)                    # OSE
prog.validate()
prog.emit_c_header(out, includes=("strela.h", "<app>_kernel.h", "dataset.h"))
```

Rules that are not obvious and that `validate()` does **not** catch:

- **`mem()` before `stream()` in the same pass.** An ISE that both preloads a
  scratchpad and carries a stream must release the scratchpad first, or the
  fabric waits on twiddles/weights the engine is no longer free to send. This is
  common: there are only 4 ISEs, so a 6-input DFG always doubles up two of them.
- **`mem_param` is narrow and wraps silently.** Word 0 packs
  `opcode[4:0] | addr[13:5] | size[22:14] | mode[23] | iters[31:24]`, so
  **`size` is 9 bits (max 511)** and **`iters` is 8 bits (max 255)**; the
  scratchpad itself is **512 words** (`StrelaMemDepth`, `rtl/strela_pkg.sv`).
  `validate()` checks none of these — check them in the app and fail loudly
  (`strela_fft` and `strela_gesummv` both show the form).
- The byte count and stride fields are 16 bits; `validate()` does check those,
  plus `stride != 0` and `bytes % stride == 0`.
- **Destination coverage is whole-program**: every element of a `mark_output()`
  array must be written exactly once across all passes. Multi-pass kernels that
  revisit a buffer need one destination array per pass.
- Keep `gen_data.py` and `gen_descriptors.py` defaults **identical**. `make
  gen-app-data` runs both with no arguments; if their shapes disagree, the C
  array sizes and the descriptor byte counts silently diverge.

## 5. Schedule for performance, not just correctness

A schedule that passes is often 1.5x off the one it could be. The two costs that
dominate are **descriptor fetches** (12 bytes plus a round trip, and the engine
does nothing else while it fetches) and **redundant main-memory traffic** (the
same operand loaded into a scratchpad again and again). Both come from the same
habit — treating a scratchpad as something you reload every pass. Work down this
list before declaring an app done; `strela_gemver` went 25521 -> 16325 cycles on
it, with descriptors falling 1392 -> 430.

**1. Load a scratchpad once and replay it — `iters` is reuse.** `mem()`'s `iters`
is how many times the block is replayed into the fabric before the engine has to
touch it again, so set it from the whole *phase*, not the pass. If an operand is
consumed once per row and the phase has R rows, that is `iters=R` and one load.
`strela_mm` does this with the B columns; `strela_gemver` replays v1/v2 `N/2`
times from a single load.

**2. Re-point, do not reload — `mem_param()`.** When the address has to change
(a per-row scalar; a run-length pattern a single replay cannot produce), park the
whole vector once with `iters=0` — S_WR returns to S_IDLE without replaying — and
then issue a **param-only** descriptor per step: word 0 only, byte count zero, no
bus traffic and no SRAM access. See `strela_desc.mem_param()`.

**3. That is also what removes the fences.** A *loading* descriptor writes the
SRAM from S_WR, which clobbers the `data_out` register still presenting the
previous replay's last word; the word is lost and the lane hangs on it. That is
why a reload needs a `FENCE_SE` in front of it. A param-only descriptor makes no
SRAM access, so the pending word survives, and the ISE blocks on the scratchpad's
`ready_o` until the replay it is replacing has finished — back-pressure per
scratchpad instead of a barrier across all eight engines. **The only fences you
should need are phase boundaries** (a `TR_CONF` re-gates the fabric, so a
reconfiguration genuinely needs one). An in-phase fence is nearly always a
symptom of a reload. When you drop them, check the blocking cannot close a cycle:
no engine may park on a scratchpad while still owing a stream that the lane
holding that scratchpad is waiting for.

**4. Give each lane a contiguous slice.** If lane k walks every k-th row it needs
one descriptor per row; if it walks a contiguous block it needs one descriptor
total. The fabric does not care which rows a lane gets — only that the lanes stay
in step — so choose the assignment that makes the addresses contiguous. This is
what collapses a matrix half into a single descriptor.

**5. Keep per-step descriptors off the engines that stream.** There are only four
ISEs, so a six-input DFG doubles up two of them — and an engine that carries both
a stream and a scratchpad has to interleave them, because it can only push a
scratchpad parameter while that scratchpad is between replays. Its fetches then
land on the critical path. Measured on `strela_gemver`: the same 59 extra
descriptors cost **29 cycles** on an engine with slack (it prefetches while the
fabric is busy) and about **35 cycles per row** on the engine that was also
streaming. And lanes fed by a shared bus token are locked in lockstep, so the
busy engine paces every lane, not just its own.

  Which engine a port lands on is the **mapper's** choice, and its objective is
  switching activity — it knows nothing about descriptor tables. Fix it in the
  DFG with **`at=`** pins (see elastic-cgra's `new-dfg` skill): `[at=8]` pins an
  operand to the engine at that channel's flank position, `[at="17!"]` to that
  exact channel when the router-bus-vs-PE-border choice matters. Pin only what
  you rely on — every pin is placement freedom the solver loses — and remember
  that a pinned kernel can no longer be replayed from `regress2kernel.py`, so say
  so in the app's docstring.

**6. Read the counters, do not guess.** `TOT/CFG/TAB/STL` are printed by every
app. High `TAB` share means descriptor-bound: the fix is fewer, longer
descriptors (1-5 above), not a faster fabric. High `STL` means waiting on
memory. Both near zero with a high `TOT` means recurrence-bound, and no amount of
descriptor work will help — see `strela_dither_filter`. A descriptor moving `K`
words costs a fixed 12-byte fetch, so short reduction lengths are what make `TAB`
bite: `strela_doitgen` spends 82% of its run fetching descriptors at `K`=30,
against 34% for `strela_gesummv` at `K`=90.

**7. Audit it.** The **`strela-audit` skill** checks all of the above mechanically
against the emitted schedule and reports what it finds, with the redundant bytes
and the descriptor counts. Run it before calling an app finished.

## 6. `main.c`

Copy `sw/applications/strela_fft/main.c` and change only: the banner, the
`set_pe_delay_value()` block (drop it entirely if the DFG has no accumulators),
and the golden comparison. Keep the MMIO sequence, the interrupt setup and the
perf-counter readout as they are, and keep returning the error count — the
regression greps for `Program Finished with value 0`.

## 7. Build, run, verify

```bash
scripts/gr_heep_env.sh make app PROJECT=strela_<name>          # regenerates both headers
scripts/gr_heep_env.sh make verilator-run-app PROJECT=strela_<name>
```

Delegate this to the **`strela-sim` agent** to keep the build logs out of
context. A pass looks like `SUCCESS!` plus `Program Finished with value 0`.

- **Rebuild the Verilator model if it is older than
  `hw/vendor/ceimm_upm_strela/rtl/elastic-cgra/rtl/cgra/cgra.sv`** — that file is
  generated and gitignored, and a stale `Vtestharness` simulates a different
  fabric than the bitstream was built for. `make verilator-build` takes minutes;
  run it in the background.
- Check the bitstream against the DFG too — this catches the class of bug where a
  committed kernel silently decodes to the wrong ALU op:
  ```bash
  scripts/gr_heep_env.sh make -C hw/vendor/ceimm_upm_strela lint-kernel \
      KERNEL=../../../sw/applications/strela_<name>/<app>_kernel.h
  ```

## 8. Register the app

Add it to the `strela_*` list in `CLAUDE.md`. Nothing else is needed: the test
whitelist in `test/gr_heep_test_apps.py` is empty, so **every** directory under
`sw/applications/` is compiled and simulated by `make test`. A broken app breaks
CI immediately.

## When it deadlocks (no output, no error)

That is the normal failure mode — the fabric just stops. Work down this list:

1. `TR_CONF` first in all four ISE tables, including engines with no data work
   (`conf_all()` does this; `validate()` verifies it).
2. Every io_map port driven by some descriptor. An undriven channel blocks the
   fabric. `validate()` checks this.
3. `mem()` emitted before `stream()` on a doubled-up ISE (§4).
4. `size % stride == 0` and `stride != 0`, or obione never finishes the transfer.
5. Twiddle/weight replay count matches the token count the streams produce
   (`size * iters` must equal the number of stream elements consuming them).
6. Inspect the schedule visually:
   ```bash
   python3 sw/applications/strela_<name>/gen_descriptors.py \
       -o /dev/null --streams hw/vendor/ceimm_upm_strela/build/streams/<name>.json
   scripts/gr_heep_env.sh make -C hw/vendor/ceimm_upm_strela webgui \
       STREAMS=build/streams/<name>.json
   ```
7. `doc/STRELA_PROGRAMMING_GUIDE.md` §9 in the STRELA submodule is the golden-rule
   summary; the descriptor ISA and the fixed channel map are in that submodule's
   `CLAUDE.md`.
