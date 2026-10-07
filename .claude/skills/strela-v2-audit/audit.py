#!/usr/bin/env python3
# Copyright 2026 Universidad Politecnica de Madrid.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Audit STRELA descriptor schedules for the two costs that dominate a well-formed
# but slow app: descriptor fetches and redundant main-memory traffic.
#
# It reads the schedule *model* (`gen_descriptors.py --streams`), not the Python
# that produced it, so it cannot be fooled by how a generator is written and it
# sees exactly what the hardware will fetch.

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
APPS = os.path.join(REPO, "sw", "applications")
ENV = os.path.join(REPO, "scripts", "gr_heep_env.sh")

DESC_BYTES = 12          # every descriptor is a 12-byte fetch, payload or not
MAX_SIZE = 511           # mem_param size is 9 bits; the SRAM is 512 words
TRANSFER = {"stream_in", "stream_out", "mem_load", "mem_drain", "config"}


def build_model(app, extra_args=()):
    """Run the app's generator and return its schedule model."""
    gen = os.path.join(APPS, app, "gen_descriptors.py")
    if not os.path.isfile(gen):
        raise SystemExit(f"{app}: no gen_descriptors.py (hand-written tables "
                         "cannot be audited this way)")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "streams.json")
        cmd = [ENV, "python3", gen, "-o", os.devnull, "--streams", out,
               *extra_args]
        run = subprocess.run(cmd, cwd=os.path.join(APPS, app),
                             capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f"{app}: generator failed\n{run.stderr[-2000:]}")
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)


def phase_slices(model):
    """[{engine: [descriptors of that phase]}], one entry per loaded bitstream.

    Everything here is per phase, for the same reason `strela_v2_desc.validate()`
    is: a chained program reuses port names for unrelated channels of the next
    kernel, and an engine that is idle in one phase can be the busiest in the
    next. Aggregating over the whole run invents contention that is not there.
    """
    engines = model["engines"]
    starts = ([p["start"] for p in model["phases"]] if model.get("phases")
              else [{e: 0 for e in engines}])
    out = []
    for i, start in enumerate(starts):
        end = (starts[i + 1] if i + 1 < len(starts)
               else {e: len(v["descriptors"]) for e, v in engines.items()})
        out.append({e: engines[e]["descriptors"][start[e]:end[e]]
                    for e in engines})
    return out


def phase_bounds(model):
    return [{e: d[0]["seq"] if d else 0 for e, d in ph.items()}
            for ph in phase_slices(model)]


def transfers(desc):
    return desc.get("class") in TRANSFER and desc.get("bytes")


def payload(desc):
    """Bytes of data a descriptor actually moves.

    Not its `bytes` field: that is the *span* (stride x count), so a strided
    column of 64 words across a 64-wide matrix reports 16384. obione moves
    `bytes / stride` elements, which is what `elems` already holds.
    """
    return desc.get("elems", 0) * ((desc.get("sew") or 32) // 8)


def resident_run(loads):
    """Longest run of consecutive loads whose words would all fit at once.

    A scratchpad holds 512 words. If N consecutive loads into one scratchpad
    between them touch no more than that, those N loads are one load plus N-1
    `mem_param()` re-points -- the data never had to leave. Returns the length
    of the longest such run, so 1 means the reloading really is inherent (the
    working set does not fit) and anything above 1 is an opportunity.
    """
    best, touched, run = 1, set(), 0
    for d in loads:
        words = set(d.get("touches") or [])
        if len(touched | words) > MAX_SIZE:
            touched, run = words, 1
        else:
            touched |= words
            run += 1
        best = max(best, run)
    return best


# --------------------------------------------------------------------- checks

def check_reloads(model):
    """The headline check: a scratchpad loaded more than once in one phase.

    Two distinct findings, because the fix differs:

    * the *same block* reloaded -- pure waste, `iters` should have replayed it;
    * *different blocks* of one array cycled through -- legitimate if the array
      is bigger than the 512-word scratchpad, but if the whole array fits it can
      be parked once (`iters=0`) and re-pointed with `mem_param()`, which is
      what turns a reload into a 12-byte descriptor with no bus traffic.
    """
    findings = []
    for p, phase in enumerate(phase_slices(model)):
        tag = f"phase {p} " if len(model.get("phases") or [1]) > 1 else ""
        loads = defaultdict(list)
        for engine, descs in phase.items():
            for d in descs:
                if d.get("class") != "mem_load":
                    continue
                mem = (d.get("mem") or {}).get("id")
                addr = d.get("addr") or {}
                loads[(engine, mem, addr.get("sym"), d.get("port"))].append(d)

        for (engine, mem, sym, port), group in sorted(loads.items()):
            if len(group) < 2:
                continue
            each = payload(group[0])
            seen = Counter((d.get("addr") or {}).get("index") for d in group)
            dups = sum(c - 1 for c in seen.values())
            if dups:
                findings.append({
                    "kind": "scratchpad reload (same block)",
                    "where": f"{tag}{engine} {mem} <- {sym} (port {port})",
                    "detail": (f"{len(group)} loads cover only {len(seen)} "
                               f"distinct block(s): {dups} of them re-read data "
                               f"the scratchpad had already held. "
                               f"{dups * each} B of main-memory traffic and "
                               f"{dups} descriptors are redundant"),
                    "fix": ("replay the block instead of re-reading it -- set "
                            "iters to the number of times the fabric consumes "
                            "it, not to one pass' worth. If a pass length cap "
                            "forces the split (strela_v2_mm's MAX_GROUPS), that is "
                            "the cost of the cap, not of the schedule"),
                    "cost": dups * each,
                })
                continue
            blocks = seen
            # Different blocks. Reloading is inherent only if the data really
            # does not fit; otherwise a run of consecutive loads could have been
            # one load kept resident.
            longest = resident_run(group)
            if longest < 2:
                continue                     # genuinely cannot stay resident
            findings.append({
                "kind": "reloadable scratchpad is parkable",
                "where": f"{tag}{engine} {mem} <- {sym} (port {port})",
                "detail": (f"{len(group)} loads of {len(blocks)} different "
                           f"blocks, {each} B each; but {longest} consecutive "
                           f"loads touch only <= {MAX_SIZE} words, so each such "
                           "run could have been one load kept resident"),
                "fix": ("park the whole array once with iters=0 (S_WR returns "
                        "to S_IDLE without replaying) and issue a mem_param() "
                        "per step to re-point the replay: word 0 only, no bus "
                        "traffic, no SRAM write -- and no fence needed, because "
                        "a param-only descriptor cannot clobber the word in "
                        "flight"),
                "cost": each * (len(group) - 1),
            })
    return findings


def check_fences(model):
    """Fences that are not phase boundaries.

    A FENCE_SE is a rendezvous of all eight engines plus eight descriptor
    fetches. Reconfiguration needs one; almost every other one is there to make
    a scratchpad reload safe, and disappears with the reload.
    """
    starts = phase_bounds(model)
    boundaries = set()
    for start in starts[1:]:                 # phase 0 needs no fence before it
        for engine, at in start.items():
            boundaries.add((engine, at - 1))

    total = 0
    stray = 0
    for engine, info in model["engines"].items():
        for d in info["descriptors"]:
            if d.get("class") != "fence":
                continue
            total += 1
            if (engine, d["seq"]) not in boundaries:
                stray += 1
    if not stray:
        return []
    return [{
        "kind": "in-phase fences",
        "where": f"{stray} of {total} fence descriptors",
        "detail": (f"{stray // 8 or stray} barrier(s) inside a phase, i.e. not a "
                   f"reconfiguration. Each is an 8-engine rendezvous plus "
                   f"{stray * DESC_BYTES} B of descriptor fetch"),
        "fix": ("a fence inside a phase is almost always guarding a scratchpad "
                "reload: a loading descriptor writes the SRAM from S_WR and "
                "clobbers the word still being handed to the fabric. Replace "
                "the reload with a park (iters=0) plus mem_param() re-points "
                "and the fence goes with it -- a param-only descriptor makes no "
                "SRAM access, so it cannot clobber anything and the ISE blocks "
                "on the scratchpad's ready_o instead. Check the parkable "
                "finding above; if there is none, the passes may still be "
                "shareable at a coarser grain than one load each"),
        "cost": stray * DESC_BYTES,
    }]


def check_engine_contention(model):
    """A busy engine that streams while a nearly idle one sits next to it.

    An engine carrying both a stream and per-step scratchpad work has to
    interleave them, and it can only push a scratchpad parameter while that
    scratchpad is between replays -- so its fetches stop being prefetchable and
    land on the critical path. Co-residency alone is not the problem: with four
    ISEs and six ports, two engines *must* double up. The problem is an
    imbalance, i.e. somewhere else to put the stream. Only then is there a fix,
    and the fix is an `at=` pin in the DFG.
    """
    findings = []
    multi = len(model.get("phases") or [1]) > 1
    for p, phase in enumerate(phase_slices(model)):
        tag = f"phase {p} " if multi else ""
        load = {}
        for engine, descs in phase.items():
            if not engine.startswith("ISE"):
                continue
            live = [d for d in descs if d.get("class") not in ("idle", "config")]
            load[engine] = live
        if len(load) < 2:
            continue
        busiest = max(load, key=lambda e: len(load[e]))
        lightest = min(load, key=lambda e: len(load[e]))
        n_busy, n_light = len(load[busiest]), len(load[lightest])
        streams = [d for d in load[busiest] if d.get("class") == "stream_in"]
        mems = [d for d in load[busiest]
                if d.get("class") in ("mem_load", "mem_param")]
        if not streams or len(mems) <= 2:
            continue
        if n_busy < 8 or n_light > n_busy // 4:
            continue                          # already balanced: no room to move
        findings.append({
            "kind": "stream/scratchpad contention",
            "where": f"{tag}{busiest} ({n_busy} descriptors) vs "
                     f"{lightest} ({n_light})",
            "detail": (f"{busiest} interleaves {len(streams)} stream "
                       f"descriptor(s) with {len(mems)} scratchpad one(s) while "
                       f"{lightest} carries {n_light}. Its fetches sit on the "
                       "critical path; the same fetches on an engine with slack "
                       "are prefetched and cost ~0.5 cycles"),
            "fix": (f"move the streamed operand off {busiest} with an `at=` pin "
                    "on that DFG input (elastic-cgra's new-dfg skill): "
                    "`[at=N]`, N being the channel index of the target engine "
                    "in the config's inputs list, north position i = ISE i. Pin "
                    "only this one -- every pin costs the mapper freedom"),
            "cost": len(streams) * DESC_BYTES,
        })
    return findings


def check_splittable_streams(model):
    """Adjacent descriptors on one channel that are contiguous in memory.

    Nothing separates them in the table and they address consecutive elements at
    the same stride, so they are one transfer written as many: pure fetch
    overhead. Usually means a lane walks every k-th line where a contiguous
    slice would do.
    """
    findings = []
    multi = len(model.get("phases") or [1]) > 1
    for p, phase in enumerate(phase_slices(model)):
        tag = f"phase {p} " if multi else ""
        for engine, descs in sorted(phase.items()):
            runs = defaultdict(int)
            for a, b in zip(descs, descs[1:]):
                if not (transfers(a) and transfers(b)):
                    continue
                if a.get("port") != b.get("port") or a["stride"] != b["stride"]:
                    continue
                ta, tb = a.get("touches"), b.get("touches")
                if not ta or not tb:
                    continue
                if tb[0] == ta[-1] + (ta[1] - ta[0] if len(ta) > 1 else 1):
                    runs[a["port"]] += 1
            for port, n in sorted(runs.items()):
                if n < 2:
                    continue
                findings.append({
                    "kind": "splittable stream",
                    "where": f"{tag}{engine} port {port}",
                    "detail": (f"{n + 1} adjacent descriptors are contiguous in "
                               f"memory at one stride; they are one transfer"),
                    "fix": ("merge them, splitting only where the 16-bit byte "
                            "count forces it. If they are per-line descriptors, "
                            "give the lane a contiguous slice of lines instead "
                            "of every k-th"),
                    "cost": n * DESC_BYTES,
                })
    return findings


def check_fetch_ratio(model):
    """Descriptor fetch bytes against payload bytes, per engine.

    A 12-byte fetch per descriptor is cheap only if the descriptor then moves a
    long run. Short reduction lengths are what make TAB dominate a run.
    """
    findings = []
    for engine, info in sorted(model["engines"].items()):
        descs = [d for d in info["descriptors"] if d.get("class") != "idle"]
        carriers = [d for d in descs if transfers(d)]
        moved = sum(payload(d) for d in descs)
        fetch = len(descs) * DESC_BYTES
        # A reduction legitimately writes one word per pass, and arms/fences/
        # re-points are payload-free by design -- neither is a finding. Only
        # flag an engine that issues enough real transfers for the ratio to
        # mean something and still fetches more than it moves.
        if len(carriers) < 8 or fetch <= moved:
            continue
        findings.append({
            "kind": "descriptor-fetch heavy",
            "where": engine,
            "detail": (f"{len(descs)} descriptors fetch {fetch} B to move "
                       f"{moved} B over {len(carriers)} transfer(s) "
                       f"({100.0 * fetch / moved:.0f}%)" if moved else
                       f"{len(descs)} descriptors fetch {fetch} B and move "
                       "nothing"),
            "fix": ("fewer, longer descriptors: merge adjacent transfers, or "
                    "give the engine a contiguous slice so one descriptor "
                    "covers what several do now"),
            "cost": fetch,
        })
    return findings


CHECKS = (check_reloads, check_fences, check_engine_contention,
          check_splittable_streams, check_fetch_ratio)


def audit(app, extra_args=()):
    model = build_model(app, extra_args)
    findings = []
    for check in CHECKS:
        findings.extend(check(model))
    findings.sort(key=lambda f: -f["cost"])

    n_desc = sum(len([d for d in e["descriptors"] if d.get("class") != "idle"])
                 for e in model["engines"].values())
    moved = sum(payload(d)
                for e in model["engines"].values() for d in e["descriptors"])
    return model, findings, n_desc, moved


def main():
    ap = argparse.ArgumentParser(
        description="Audit STRELA app schedules for descriptor and "
                    "main-memory waste.")
    ap.add_argument("apps", nargs="*",
                    help="app names (default: every strela_v2_* with a generator)")
    ap.add_argument("--args", default="",
                    help="extra arguments passed through to gen_descriptors.py "
                         "(quoted, e.g. --args '32 32 32')")
    ap.add_argument("--quiet", action="store_true",
                    help="one line per app, findings suppressed")
    args = ap.parse_args()

    apps = args.apps or sorted(
        a for a in os.listdir(APPS)
        if a.startswith("strela_v2_")
        and os.path.isfile(os.path.join(APPS, a, "gen_descriptors.py")))
    extra = args.args.split() if args.args else []

    worst = 0
    for app in apps:
        try:
            model, findings, n_desc, payload = audit(app, extra)
        except SystemExit as e:
            print(f"\n=== {app}\n  SKIP: {e}")
            continue
        fences = sum(1 for e in model["engines"].values()
                     for d in e["descriptors"] if d.get("class") == "fence")
        params = sum(1 for e in model["engines"].values()
                     for d in e["descriptors"] if d.get("class") == "mem_param")
        print(f"\n=== {app}: {n_desc} descriptors "
              f"({n_desc * DESC_BYTES} B fetched, {payload} B moved), "
              f"{fences // 8} barrier(s), {params} re-point(s)")
        if not findings:
            print("  clean")
            continue
        worst += len(findings)
        if args.quiet:
            print(f"  {len(findings)} finding(s): "
                  + ", ".join(sorted({f['kind'] for f in findings})))
            continue
        for f in findings:
            print(f"  [{f['kind']}] {f['where']}")
            print(f"      {f['detail']}")
            print(f"      fix: {f['fix']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
