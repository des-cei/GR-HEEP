#!/usr/bin/env python3
"""Generate the STRELA Floyd-Warshall ISE/OSE descriptor tables (one kernel, N pivots).

PolyBench floyd-warshall is a single statement swept N times over the matrix:

    for k: for i: for j:
        path[i][j] = min(path[i][j], path[i][k] + path[k][j])

and the whole app is one bitstream run over and over. The bitstream comes from a
mapper solve of `mapper/applications/fw_hv`, **not** from the committed regress
entry -- see "Why the DFG pins the borders" below:

    scripts/gr_heep_env.sh make -C hw/vendor/strela-v2/rtl/elastic-cgra \\
        map-bitstream PROJECT=fw_hv CGRA_CONFIG=configs/4x4-HV.hjson \\
        ARRAY_NAME=fw_kernel
    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    cp $CG/build/bitstream/fw_hv_kernel.h sw/applications/strela_fw/fw_kernel.h
    cp $CG/build/bitstream/fw_hv_io_map.json \\
        sw/applications/strela_fw/fw_hv_io_map.json
    make gen-app-data PROJECT=strela_fw   # or: python3 gen_descriptors.py

The io_map is committed next to the header so descriptors.h can be regenerated
(it is gitignored) without the mapper. They describe one solve; a re-solve
reshuffles the engine assignment, so keep the pair in step.

Kernel contract (mapper/applications/fw_hv/main.dot), four independent lanes of
the relaxation, one line each:

    add_l    = via_a + via_b                  the candidate path through k
    sub_l    = cur - add_l                    ALU sub, operand1 - operand2
    cmp_l    = sub_l > 0                      one data operand, compared to 0
    out_l    = cmp_l ? add_l : cur            mux_dout = cin ? din_2 : din_1

so `out_l = min(cur, via_a + via_b)`, which is the relaxation exactly. Nothing
in this bitstream is parameterised -- every PE reads back delay_value 0 and
constant 0 -- so main.c patches nothing and there is no reduction length to set.
The lanes are (cur, via, via) = (input0, input1, input2), (input3, input4,
input5), (input6, input7, input8) and (input9, input10, input11), feeding
output0..output3 in that order. `add` is commutative, so which via port carries
which operand is ours to choose, and `assign_roles()` below makes that choice
from the io_map rather than hard-coding it.

**One operand is constant along the streamed line, and that decides everything.**
Sweeping a line of the matrix at fixed k, the three operands behave differently:

    path[i][j]   the line being updated       varies along the line   -- `cur`
    path[k][j]   the pivot row                varies along the line   -- `piv`
    path[i][k]   the pivot column entry       *constant* along the line -- `col`

A scratchpad replays a fixed block of words a whole number of times, so a single
word replayed `count` times is exactly how a constant operand is expressed
(size=1, iters=N), and a stream cannot do it at all -- repeating one address
would need stride 0, which hangs obione. So `col` must land on a scratchpad
port, and, as in strela_gemver's rank-2 update, the replay count *is* the line
length: over several lines per lane the port would have to emit
[path[i0][k] x N, path[i1][k] x N, ...], a run-length pattern and not a repeat of
anything. Hence **one line per lane per step**, four lines per step, and N_PAD/4
steps per pivot. The 8-bit iters field then caps N at 255 rather than counting
row groups, the same limit gemver hits.

**A step is not a pass.** That constraint fixes how much data crosses the fabric
per step; it says nothing about how many steps one descriptor pass may cover, and
this is where the app spends or saves its cycles. Each of the three operands is
re-pointed, not reloaded, between steps:

  * `col` is parked once per pass with `iters=0` (S_WR returns to S_IDLE without
    replaying) holding one column entry per line of the pass, and each step
    re-points it with a **param-only** descriptor (`strela_desc.mem_param()`):
    word 0 alone, byte count zero, no bus traffic and no SRAM access.
  * `piv` is the *same* line for every step of the pivot, so a scratchpad-bound
    `piv` is loaded **once per pass** with `iters=<steps>` and simply replayed.
    A stream-bound one has no such trick and must be re-streamed every step,
    which is the whole reason the DFG pins the vias to scratchpads.
  * `cur` genuinely differs per step. On a stream port that costs one descriptor
    per step and nothing else; on a scratchpad port the pass loads all its lines
    at once (`size=steps*N, iters=1`) and the fabric walks them as one block.

Because a param-only descriptor writes nothing, it also **removes the per-step
fence**: a *loading* descriptor writes the SRAM from S_WR and clobbers the
`data_out` still presenting the previous replay's last word, which is the
deadlock every scratchpad reload has to fence against, while a param-only one
leaves `data_out`/`valid_out` intact and the ISE blocks on the scratchpad's
`ready_o` until the replay it is replacing has been issued. Back-pressure per
scratchpad instead of a barrier across all eight engines. What is left is one
`FENCE_SE` per *pass*, and a pass is as long as the scratchpads allow: with the
vias on scratchpads and `cur` streamed, nothing caps it below the whole pivot, so
the run is **N barriers, not N*N/4**. Lanes then take *contiguous* slices of the
matrix rather than interleaved rows, which makes each OSE's whole slice one
descriptor.

**Why the DFG pins the borders.** The port kinds are not incidental: this app
wants all eight vias on scratchpads (`border="west,east"`) and all four `cur`
ports on streaming engines (`border=north`), which is what the committed
`main.dot` asks for and what `regress/4x4-HV/fw_hv` -- an older, unconstrained
solve -- does *not* provide. Two things follow from it, and both are worth more
than the descriptors they save:

  * A scratchpad-bound `cur` is loaded and then replayed, and `strela_memory.sv`
    does not overlap the two: S_WR absorbs the whole block before the ISE's start
    pulse flips it to S_WR_CGRA. So a lane whose line arrives through a
    scratchpad costs 2N bus cycles per step where a streamed one costs N. With
    two such lanes the *streaming bound* of the run is set by them, not by the
    fabric.
  * The engine balance then falls out on its own, which is why this app needs no
    `at=` pin where strela_gemver did. Eight vias over the eight scratchpad
    positions and four `cur` ports over the four north positions are both
    bijections, and ISE i owns exactly MEM_W(3-i), MEM_E(i) and north column i --
    so every ISE ends up with two vias and one stream, whatever the solve picks.
    The solve then places each `add` on an edge column taking its own row's edge
    scratchpad plus the one a row above, routed one hop south (add0 at PE12 with
    W3+W2, add3 at PE4 with W1+W0, add1 at PE7 with E1+E0, add2 at PE15 with
    E3+E2), so a lane's two vias sit on adjacent scratchpads of one side and
    therefore on two *different* engines. That is what lets assign_roles() hand
    every ISE one `col` and one `piv`, for exactly **two descriptors per engine
    per step** and no engine pacing the others the way gemver's ISE 1 did.

`assign_roles()` still reads the kinds out of the io_map instead of assuming
them, so a re-solve that moves a port between a scratchpad and a stream produces
a correct (if slower) table rather than a wrong one; it only insists that each
lane has at least one scratchpad among its two vias, since `col` has nowhere else
to go.

**Why the buffers ping-pong.** k must complete before k+1, so every pass ends in
a FENCE_SE. Within one k the passes would be independent, but PolyBench relaxes
in place, and within a pass the ISE reading an element and the OSE writing it are
not ordered. Iteration k reads two lines it does not own -- the pivot row and the
pivot column -- so rather than argue about that, each k reads one buffer and
writes the other. The argument that this is still PolyBench's answer is short: at
iteration k,

    path[k][j] <- min(path[k][j], path[k][k] + path[k][j]) = path[k][j]
    path[i][k] <- min(path[i][k], path[i][k] + path[k][k]) = path[i][k]

whenever path[k][k] >= 0, so the pivot row and column do not change during their
own iteration and reading them from the previous buffer is reading the same
values. Non-negativity is what buys that, and gen_data.py both guarantees it and
checks the two orders agree on the actual data. The cost is one extra N_PAD x N
buffer; the result ends in path_a for even N and path_b for odd N.

**What this app still cannot do: PolyBench SMALL.** A pass costs 8 loads, 8
descriptors per step, 4 writes and 8 fence entries, so the tables grow as ~2*N^2
descriptors against the 512 KiB interleaved section they share with both matrices
(config/mcu-gen-config.py adds 8 x 64 KiB). That is a third of what the
pass-per-step schedule needed -- 100848 bytes at N=60 where it took 259152, and a
ceiling that moves from **N=80 to N=124** -- but SMALL is 180 and remains out of
reach, so MINI stays the default and the guard below reports the real byte count
rather than letting the link fail. It is a table-size limit, not a descriptor-ISA
one: the 8-bit `iters` field is asked for N here (the pivot column's replay
count), which is fine to 255, and for N_PAD/4 on the pivot row, which is 45 even
at SMALL.

**Measured, and where the cycles now go.** At the default shape: TOT 301890,
CFG 26, TAB 61403 (20%), STL 21650 (7%), from 21596 descriptors and 899 barriers
down to 8404 and 59 (the previous schedule measured TOT 423294 with TAB 162797,
38%). The remaining cost is no longer the descriptors: 301890 cycles over 900
steps of 60 elements per lane is 5.6 cycles/element, against the ~2 tokens per 10
cycles elastic-cgra's own `make perf` reports for this kernel, and the low STL
agrees (a memory-paced app like strela_relu runs with STL at 99% of TOT). So the
app is **fabric-paced** now, and the next lever is the DFG or the fabric config --
most likely the `min` diamond, whose two branches reconverge on `select` two FU
hops apart while 4x4-HV uses lazy forks -- not the schedule.
"""

import argparse
import itertools
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
LANES = 4         # independent lanes of fw_hv = matrix lines per step
DESC_BYTES = 12   # one descriptor: {opcode+mem_param, address, stride|size}

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_ITERS = 255    # mem_param iters is 8 bits (sw/strela.h)
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count
MEM_WORDS = min(MEM_DEPTH, MAX_SIZE)   # what one scratchpad can hold and replay

# The interleaved section holds the descriptor tables and both matrices:
# config/mcu-gen-config.py adds 8 interleaved banks of 64 KiB.
IL_SECTION_BYTES = 8 * 64 * 1024

# The line being updated, per lane. Fixed by the DFG: `cur` is what sub and
# select take as operand 1, so unlike the two vias it is not interchangeable.
LANE_CUR = ("input0", "input3", "input6", "input9")
# The two `add` operands per lane. `add` is commutative, so which of the pair
# carries the pivot column and which the pivot row is assign_roles()' choice.
LANE_VIAS = (("input1", "input2"), ("input4", "input5"),
             ("input7", "input8"), ("input10", "input11"))
OUT_PORTS = ("output0", "output1", "output2", "output3")


def assign_roles(prog):
    """Per lane, which via carries the pivot column and which the pivot row.

    The pivot column entry is one word replayed along the whole line, which only
    a scratchpad can do, so `col` has to be a scratchpad port. When both vias
    are scratchpads -- what the DFG's `border="west,east"` asks for -- the choice
    is free, and it is made to spread the per-step descriptors evenly over the
    four ISEs: an engine issues a `col` re-point every step, and a stream-bound
    `piv` or `cur` a transfer every step, while everything else is once a pass.
    """
    options = []
    for lane, (va, vb) in enumerate(LANE_VIAS):
        opts = [(col, piv) for col, piv in ((va, vb), (vb, va))
                if prog.inputs[col].kind == "mem"]
        if not opts:
            raise SystemExit(
                f"lane {lane}: the io_map puts both via ports ({va} on "
                f"{prog.inputs[va].location}, {vb} on {prog.inputs[vb].location}) "
                "on direct streams, but the pivot column entry is one word "
                "replayed along the line and only a scratchpad can do that. "
                "Re-solve fw_hv with border=\"west,east\" on both vias.")
        options.append(opts)

    def cost(combo):
        per_step = Counter()
        for lane, (col, piv) in enumerate(combo):
            per_step[prog.inputs[col].engine] += 1
            for port in (piv, LANE_CUR[lane]):
                if prog.inputs[port].kind == "stream":
                    per_step[prog.inputs[port].engine] += 1
        load = sorted((per_step[e] for e in range(LANES)), reverse=True)
        return (load[0], load)

    return min(itertools.product(*options), key=cost)


def pass_lines(prog, n, lines_per_lane):
    """How many lines per lane one descriptor pass may cover.

    A pass ends in a FENCE_SE, and the only thing that forces one is a
    scratchpad *load*: it writes the SRAM from S_WR and would clobber the word
    the previous replay is still presenting. So the pass length is exactly what
    the loaded blocks can hold -- `col` needs one word per line and a
    scratchpad-bound `piv` is replayed once per line (both covered by the limits
    below), while a scratchpad-bound `cur` needs a whole line each, which is what
    actually bites: it caps a pass at 511/N lines where a streamed `cur` does not
    cap it at all.
    """
    limit = min(lines_per_lane, MEM_WORDS, MAX_ITERS)
    if any(prog.inputs[cur].kind == "mem" for cur in LANE_CUR):
        limit = min(limit, MEM_WORDS // n)
    if limit < 1:
        raise SystemExit(
            f"a line is {n} words and the scratchpad holds {MEM_WORDS}, so no "
            "whole line fits; lower N")
    return limit


def split(total, cap):
    """`total` lines as evenly as possible over ceil(total/cap) passes."""
    passes = -(-total // cap)
    base, extra = divmod(total, passes)
    return [base + (1 if i < extra else 0) for i in range(passes)]


def sweep(prog, k, src, dst, roles, first, lines, n, per_lane):
    """One pass: `lines` consecutive lines per lane, relaxed against pivot k."""
    for lane, (col, piv) in enumerate(roles):
        cur, row0 = LANE_CUR[lane], lane * per_lane + first
        if prog.inputs[cur].kind == "mem":
            # The lane's whole slice as one block; the replay walks it in order,
            # so the line boundaries never reach the descriptors.
            prog.mem(cur, src, row0 * n, stride=ELEM, count=lines * n,
                     size=lines * n, iters=1)
        if prog.inputs[piv].kind == "mem":
            # The pivot row is the same line for every step of the pass.
            prog.mem(piv, src, k * n, stride=ELEM, count=n, size=n, iters=lines)
        # One pivot-column entry per line, parked (iters=0) for mem_param() to
        # walk a word at a time.
        prog.mem(col, src, row0 * n + k, stride=n * ELEM, count=lines,
                 size=lines, iters=0)

    for step in range(lines):
        # Every re-point before any stream: an engine parked on a stream can no
        # longer release the scratchpad another lane is waiting on, and the
        # re-points are what let all four lanes advance to this step.
        for col, _ in roles:
            prog.mem_param(col, mem_addr=step, size=1, iters=n)
        for lane, (_, piv) in enumerate(roles):
            cur, row = LANE_CUR[lane], lane * per_lane + first + step
            if prog.inputs[cur].kind == "stream":
                prog.stream(cur, src, row * n, stride=ELEM, count=n)
            if prog.inputs[piv].kind == "stream":
                prog.stream(piv, src, k * n, stride=ELEM, count=n)

    for lane in range(LANES):
        # Contiguous slices, so a lane's whole share of the pass is one write.
        prog.out(OUT_PORTS[lane], dst, (lane * per_lane + first) * n,
                 stride=ELEM, count=lines * n)


def build(n, n_pad, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="fw_kernel",
        app="strela_fw",
        arrays={
            "path_a": (n_pad * n, ELEM, [n_pad, n]),
            "path_b": (n_pad * n, ELEM, [n_pad, n]),
        },
        sew=SEW, acc_sew=SEW,
    )
    roles = assign_roles(prog)
    per_lane = n_pad // LANES
    sizes = split(per_lane, pass_lines(prog, n, per_lane))

    # Neither buffer is mark_output()ed: each is rewritten once per pivot, and
    # the whole-program coverage check counts writes across the entire run, so
    # it would report N/2 duplicates per element on a correct schedule. The
    # invariant that actually holds is per-pivot, and check_coverage() below
    # checks that one instead.
    prog.conf_all()

    written = []
    for k in range(n):
        src, dst = ("path_a", "path_b") if k % 2 == 0 else ("path_b", "path_a")
        first = 0
        for lines in sizes:
            if written:
                prog.fence()        # FENCE_SE, all eight engines
            with prog:
                sweep(prog, k, src, dst, roles, first, lines, n, per_lane)
            rows = [lane * per_lane + first + s
                    for lane in range(LANES) for s in range(lines)]
            written.append((k, dst, rows))
            first += lines

    return prog, written, roles, sizes


def check_coverage(written, n, n_pad):
    """Every line of the destination buffer written exactly once per pivot, and
    the buffers alternating, which is what makes the ping-pong equivalent to
    PolyBench's in-place relaxation."""
    for k in range(n):
        expect = "path_b" if k % 2 == 0 else "path_a"
        rows, targets = [], set()
        for pivot, dst, group in written:
            if pivot == k:
                rows.extend(group)
                targets.add(dst)
        if targets != {expect}:
            raise SystemExit(f"pivot {k} writes {sorted(targets)}, expected "
                             f"[{expect}]; the ping-pong is out of step")
        if sorted(rows) != list(range(n_pad)):
            missing = sorted(set(range(n_pad)) - set(rows))
            dupes = sorted({r for r in rows if rows.count(r) > 1})
            raise SystemExit(
                f"pivot {k} does not write each of the {n_pad} lines exactly "
                f"once (missing {missing[:4]}, repeated {dupes[:4]})")


def table_bytes(prog):
    """What the eight tables occupy, including each one's IDLE terminator."""
    return DESC_BYTES * sum(len(t) + 1 for t in prog.tables.values())


def roles_note(prog, roles):
    """One line per lane naming the port that carries each operand, so a
    re-solve that reshuffles the bindings is visible in the generated header."""
    out = []
    for lane, (col, piv) in enumerate(roles):
        cur = LANE_CUR[lane]
        parts = []
        for role, port in (("cur", cur), ("col", col), ("piv", piv)):
            b = prog.inputs[port]
            where = b.mem if b.kind == "mem" else b.port
            parts.append(f"{role} {port} ISE{b.engine} {where}")
        out.append(f"lane {lane}: " + ", ".join(parts))
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA Floyd-Warshall ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data PROJECT=strela_fw` runs
both with no arguments, so change N in both together. The row count is rounded up
to a multiple of 4 here exactly as it is there; the row length stays N.
""")
    parser.add_argument("N", type=int, nargs="?", default=60,
                        help="order of the distance matrix "
                             "(default: 60, PolyBench MINI -- SMALL is 180, "
                             "whose tables do not fit, see the module docstring)")
    parser.add_argument("--max-table-bytes", type=int, default=IL_SECTION_BYTES,
                        metavar="B",
                        help="fail if the tables plus both matrices exceed this "
                             "(default: %(default)s, the interleaved section)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "fw_hv_io_map.json"),
                        help="io_map.json of the relaxation kernel "
                             "(default: the copy committed next to its header)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n = args.N
    if n < 1:
        raise SystemExit(f"N={n} must be at least 1")

    n_pad = -(-n // LANES) * LANES

    # validate() catches none of these -- size, iters and the byte counts all
    # wrap silently.
    if n > MEM_WORDS:
        raise SystemExit(f"a line is {n} words, past the {MEM_WORDS} a 9-bit "
                         f"size field and a {MEM_DEPTH}-word scratchpad allow; "
                         "lower N")
    if n > MAX_ITERS:
        raise SystemExit(f"the pivot column entry is replayed {n} times per "
                         f"line, past the {MAX_ITERS} an 8-bit iters field "
                         "holds; lower N")
    if n * ELEM > MAX_BYTES:
        raise SystemExit(f"a line is {n * ELEM} bytes, past the {MAX_BYTES} a "
                         "16-bit byte count holds")

    prog, written, roles, sizes = build(n, n_pad, args.io_map)
    prog.validate()
    check_coverage(written, n, n_pad)

    tables = table_bytes(prog)
    data = 2 * n_pad * n * ELEM
    if tables + data > args.max_table_bytes:
        raise SystemExit(
            f"the descriptor tables are {tables} bytes and the two matrices "
            f"{data}, together past the {args.max_table_bytes} of interleaved "
            f"RAM; the tables grow as ~2*N^2 descriptors, so lower N -- 124 is "
            f"the largest that fits (N={n} needs {n * len(sizes)} passes of "
            f"{sizes} lines per lane)")

    passes = n * len(sizes)
    note = ("floyd-warshall {n}x{n} (rows padded to {p}): "
            "path[i][j] = min(path[i][j], path[i][k] + path[k][j]) in {q} "
            "fenced passes, {n} pivots x {c} pass(es) of {s} lines per lane, "
            "ping-ponging path_a <-> path_b; tables {t} bytes").format(
                n=n, p=n_pad, q=passes, c=len(sizes), s=sizes, t=tables)
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "fw_kernel.h", "dataset.h"),
                       # emit_c_header comments the note as a whole, so every
                       # continuation line has to carry its own marker.
                       extra_note="\n// ".join([note] + roles_note(prog, roles)))
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
