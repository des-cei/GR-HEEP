---
name: strela-audit
description: >-
  Audit a STRELA app's descriptor schedule for the costs that make a correct app
  slow: FENCE_SE barriers that are not phase boundaries, scratchpad blocks
  reloaded from main memory instead of replayed or re-pointed, streams split
  across descriptors that could be one, and engines whose descriptor fetches sit
  on the critical path. Use when asked to speed up or review a strela_* app,
  before declaring a new one finished, or to sweep sw/applications for apps worth
  reworking. Reports each finding with the redundant bytes and the fix.
---

# Auditing a STRELA schedule

An app that passes its golden check can still be 1.5x off. Two costs dominate,
and both look like nothing in the source:

- **descriptor fetches** — every descriptor is a 12-byte read, and the engine
  issuing it does nothing else while it waits;
- **redundant main-memory traffic** — the same operand pulled into a scratchpad
  again and again, when the hardware was willing to keep it.

They are the same habit: treating a scratchpad as something you refill each pass.
`strela_gemver` went **25521 → 16325 cycles** and **1392 → 430 descriptors** on
nothing but this, with no change to the kernel.

## Run it

```bash
python3 .claude/skills/strela-audit/audit.py                 # every app
python3 .claude/skills/strela-audit/audit.py strela_fw       # one or more
python3 .claude/skills/strela-audit/audit.py strela_mm --args '32 32 32'
python3 .claude/skills/strela-audit/audit.py --quiet         # one line per app
```

It runs each app's `gen_descriptors.py --streams` and analyses the emitted
**model**, not the Python. That matters: the model is what the hardware will
actually fetch, so a generator cannot hide behind a helper function, and the
analysis is per *phase* — a chained app reuses port names for unrelated channels
and an engine idle in one phase can be the busiest in the next.

`strela_fully_connected` and `strela_test` build their tables on the CPU and have
no generator; they are skipped.

## The findings, and what each one means

**`scratchpad reload (same block)`** — the strongest signal. N loads into one
scratchpad cover fewer than N distinct blocks, so some of them re-read data that
was already sitting there. The fix is `iters`: a scratchpad replays its block
`iters` times before the engine has to touch it again, so set it from what the
whole *phase* consumes, not from one pass. If a pass-length cap forces the split
(`strela_mm`'s `MAX_GROUPS`, a workaround for the `strela_memory.sv` race) the
traffic is the price of that cap — worth knowing, not worth "fixing" blindly.

**`reloadable scratchpad is parkable`** — the loads are all of *different*
blocks, so `iters` alone will not do, but some run of consecutive loads touches
no more than the 512-word scratchpad holds. Those loads could have been **one**
load plus `mem_param()` re-points:

```python
prog.mem(port, sym, 0, stride=ELEM, count=n, size=n, iters=0)  # park, no replay
...
prog.mem_param(port, mem_addr=row, size=1, iters=n)            # re-point
```

`iters=0` makes `S_WR` return to `S_IDLE` without replaying, so the block lands
in the SRAM and stays. A `mem_param()` descriptor carries word 0 only — no
address, no byte count — so it moves nothing over the bus and touches no SRAM.
This is the transformation that pays twice, because of the next finding.

**`in-phase fences`** — barriers that are not a phase boundary. A `FENCE_SE` is a
rendezvous of all eight engines plus eight descriptor fetches; a reconfiguration
genuinely needs one, because re-entering `TR_CONF` drops `conf_reg` and re-gates
every fabric handshake. Nothing else should. An in-phase fence is nearly always
guarding a reload: a *loading* descriptor writes the SRAM from `S_WR`, clobbering
the `data_out` register that is still presenting the previous replay's last word,
and the lane then waits for that word forever. A param-only descriptor makes no
SRAM access, so the word survives and is re-presented entering `S_WR_CGRA` — and
the ISE blocks on the scratchpad's `ready_o` until the replay it is replacing has
finished. **Back-pressure per scratchpad replaces the barrier across all eight
engines.** So fixing the reload usually deletes the fences for free.

  Before dropping them, check the blocking cannot close a cycle: no engine may
  park on a scratchpad while still owing a stream that the lane holding that
  scratchpad is waiting for. In `strela_gemver` it cannot, because the engines
  that re-point carry no streams at all — which is the next finding.

**`stream/scratchpad contention`** — one ISE interleaves a stream with per-step
scratchpad work while another sits nearly idle. It can only push a scratchpad
parameter while that scratchpad is between replays, so its fetches stop being
prefetchable and land on the critical path. Measured on `strela_gemver`: the same
59 descriptors cost **29 cycles** on an engine with slack and about **35 cycles
per row** on the busy one — and lanes fed by a shared bus token move in lockstep,
so the busy engine paces every lane. The fix is not in the app: which engine a
port lands on is the **mapper's** choice, and its objective is switching activity,
which knows nothing about descriptor tables. Pin it in the DFG with `[at=N]` (see
elastic-cgra's `new-dfg` skill), N being the channel index in the config's
`inputs:` list; north position *i* is ISE *i*. The check only fires when there is
somewhere emptier to move to — with four ISEs and six ports, two engines *must*
double up, and that alone is not a problem.

**`splittable stream`** — adjacent descriptors on one channel that are contiguous
in memory at one stride. Nothing separates them, so they are one transfer written
as several. Usually it means a lane walks every k-th line where a contiguous
slice would do: the fabric only needs the lanes to stay in step, not to interleave,
so give lane k a contiguous block and its whole slice becomes one descriptor.

**`descriptor-fetch heavy`** — an engine that fetches more bytes than it moves,
over at least eight real transfers. Reductions legitimately write one word per
pass and re-points are payload-free by design, so neither triggers this; what does
is many small transfers. A descriptor moving `K` words costs a fixed 12-byte
fetch, which is why short reduction lengths make `TAB` bite — `strela_doitgen`
spends 82% of its run fetching descriptors at `K`=30 against 34% for
`strela_gesummv` at `K`=90.

## After a change

A finding is an opportunity, not a bug — the app was correct before. Every one of
these transformations changes the handshake pattern, so **re-measure and re-check**:

```bash
scripts/gr_heep_env.sh python3 scripts/fpga/genesys2_bench.py --apps strela_<name>
```

on the board (seconds, and it reproduces Verilator cycle-for-cycle), or the
`strela-sim` agent for Verilator. Watch `TOT/TAB/STL`, and confirm `SUCCESS!` — a
schedule that drops a fence it actually needed does not fail, it **hangs**, so run
Verilator under `timeout` and treat an expiry as the deadlock signal.

Do not re-run the audit as proof: it checks the schedule, not the hardware.
