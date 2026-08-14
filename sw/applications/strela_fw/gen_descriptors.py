#!/usr/bin/env python3
"""Generate the STRELA Floyd-Warshall ISE/OSE descriptor tables (one kernel, N*N/4 passes).

PolyBench floyd-warshall is a single statement swept N times over the matrix:

    for k: for i: for j:
        path[i][j] = min(path[i][j], path[i][k] + path[k][j])

and the whole app is one bitstream run over and over. The bitstream comes from
elastic-cgra's committed regression, replayed into a kernel header without
running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py fw_hv \\
        --array-name fw_kernel -o sw/applications/strela_fw/fw_kernel.h
    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/fw_hv/io_map.json \\
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
The lanes are (cur, via_a, via_b) = (input0, input1, input2), (input3, input4,
input5), (input6, input7, input8) and (input9, input10, input11), feeding
output0..output3 in that order. `add` is commutative, so which via port carries
which operand is ours to choose -- and that choice is the whole schedule:

**One operand is constant along the streamed line, and that decides everything.**
Sweeping a line of the matrix at fixed k, the three operands behave differently:

    path[i][j]   the line being updated       varies along the line
    path[k][j]   the pivot row                varies along the line
    path[i][k]   the pivot column entry       *constant* along the line

A scratchpad replays a fixed block of words a whole number of times, so a single
word replayed `count` times is exactly how a constant operand is expressed
(size=1, iters=N), and a stream cannot do it at all -- repeating one address
would need stride 0, which hangs obione. So path[i][k] must land on a scratchpad
port, and, as in strela_gemver's rank-2 update, the replay count *is* the line
length: over several lines per lane the port would have to emit
[path[i0][k] x N, path[i1][k] x N, ...], a run-length pattern and not a repeat of
anything. Hence **one line per lane per pass**, four lines per pass, and N/4
passes per k. The 8-bit iters field then caps N at 255 rather than counting row
groups, the same limit gemver hits.

The mapper's placement happens to make three of the four lanes forced and two
free, which is why the port roles below are not symmetric:

    lane 0  cur input0  MEM_W 3        via input1  MEM_W 1   via input2  MEM_W 2
    lane 1  cur input3  north col 1    via input4  MEM_W 0   via input5  north col 0
    lane 2  cur input6  MEM_E 1 bus    via input7  north col 3  via input8  MEM_E 0
    lane 3  cur input9  ver bus 2      via input10 MEM_E 2   via input11 MEM_E 3

Lanes 1 and 2 have exactly one scratchpad among their two via ports, so the
pivot column has to go there (input4, input8) and the pivot row on the stream;
lanes 0 and 3 have two scratchpads and the assignment is free. `feed()` below
reads the kind out of the io_map rather than hard-coding it, so a re-solve that
moves a port between a scratchpad and a stream still produces a correct table as
long as the pivot-column port stays a scratchpad -- which is checked.

Engine load, three input descriptors each, perfectly balanced:

    ISE 0  input0 -> MEM_W 3   input8 -> MEM_E 0   input5 -> north col 0
    ISE 1  input2 -> MEM_W 2   input6 -> MEM_E 1   input3 -> north col 1
    ISE 2  input1 -> MEM_W 1   input10 -> MEM_E 2  input9 -> ver bus 2
    ISE 3  input4 -> MEM_W 0   input11 -> MEM_E 3  input7 -> north col 3
    OSE 0  output0 -> south col 0     OSE 1  output1 -> ver bus 1
    OSE 2  output3 -> south col 2     OSE 3  output2 -> ver bus 3

Every ISE carries both scratchpads and a stream, so every mem() is emitted
before any stream(): descriptors within a pass keep call order, and an engine
parked on a stream can no longer release the scratchpad the fabric waits on. No
output is scratchpad-backed, so this app is free of the OSE-side
`strela_memory.sv` race that caps strela_mm's pass length.

**Why the buffers ping-pong.** k must complete before k+1, so every pass is
separated by a FENCE_SE -- which is also what keeps a scratchpad replay from
being clobbered by the next pass's TR_MEM_* (`strela_memory.sv` presents the last
word of a replay from S_IDLE, where ready_o is already high). Within one k the
passes are independent, but PolyBench relaxes in place, and within a pass the ISE
reading an element and the OSE writing it are not ordered. Iteration k reads two
lines it does not own -- the pivot row and the pivot column -- so rather than
argue about that, each k reads one buffer and writes the other. The argument that
this is still PolyBench's answer is short: at iteration k,

    path[k][j] <- min(path[k][j], path[k][k] + path[k][j]) = path[k][j]
    path[i][k] <- min(path[i][k], path[i][k] + path[k][k]) = path[i][k]

whenever path[k][k] >= 0, so the pivot row and column do not change during their
own iteration and reading them from the previous buffer is reading the same
values. Non-negativity is what buys that, and gen_data.py both guarantees it and
checks the two orders agree on the actual data. The cost is one extra N_PAD x N
buffer; the result ends in path_a for even N and path_b for odd N.

**What this app cannot do: PolyBench SMALL.** The pass count is N*N/4 and each
pass costs 24 descriptors (12 input, 4 output, 8 fence), so the tables grow as
6*N^2 descriptors -- 259 KiB at N=60, but 2.3 MiB at N=180, against the 512 KiB
interleaved section the tables and both matrices share (config/mcu-gen-config.py
adds 8 x 64 KiB). MINI is therefore the default and the guard below reports the
real number rather than letting the link fail. This is a table-size limit, not a
descriptor-ISA one: N=180 is comfortably inside the 255 the iters field holds.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
LANES = 4         # independent lanes of fw_hv = matrix lines per pass
DESC_BYTES = 12   # one descriptor: {opcode+mem_param, address, stride|size}

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_ITERS = 255    # mem_param iters is 8 bits (sw/strela.h)
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count

# The interleaved section holds the descriptor tables and both matrices:
# config/mcu-gen-config.py adds 8 interleaved banks of 64 KiB.
IL_SECTION_BYTES = 8 * 64 * 1024

# lane -> (line being updated, pivot column entry, pivot row). The pivot column
# must be a scratchpad port; the other two may be either.
LANE_PORTS = (
    ("input0", "input1", "input2"),      # lane 0: three scratchpads
    ("input3", "input4", "input5"),      # lane 1: only input4 is a scratchpad
    ("input6", "input8", "input7"),      # lane 2: only input8 is a scratchpad
    ("input9", "input10", "input11"),    # lane 3: cur streams, both vias are pads
)
OUT_PORTS = ("output0", "output1", "output2", "output3")


def feed(prog, port, sym, index, count, iters):
    """One input channel for a pass, as whatever the io_map says the port is.

    `iters` > 1 is the pivot column entry: a single word replayed once per
    element of the line, which only a scratchpad can do.
    """
    if prog.inputs[port].kind == "mem":
        prog.mem(port, sym, index, stride=ELEM, count=count,
                 size=count, iters=iters)
    else:
        prog.stream(port, sym, index, stride=ELEM, count=count)


def sweep(prog, k, src, dst, rows, n):
    """One pass: relax `rows` (one line per lane) against pivot k, src -> dst."""
    feeds = []
    for lane, row in enumerate(rows):
        cur, col, piv = LANE_PORTS[lane]
        feeds.append((col, row * n + k, 1, n))    # path[i][k], held constant
        feeds.append((cur, row * n, n, 1))        # path[i][*], the line updated
        feeds.append((piv, k * n, n, 1))          # path[k][*], the pivot row

    # Scratchpads first. Every ISE here carries two of them and a stream, and an
    # engine parked on a stream can no longer release the scratchpad the fabric
    # is waiting on. sorted() is stable, so this only moves the streams last.
    for port, index, count, iters in sorted(
            feeds, key=lambda f: prog.inputs[f[0]].kind != "mem"):
        feed(prog, port, src, index, count, iters)

    for lane, row in enumerate(rows):
        prog.out(OUT_PORTS[lane], dst, row * n, stride=ELEM, count=n)


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
    # The pivot column entry is one word replayed once per element of the line,
    # which only a scratchpad can do: a stream would need stride 0, and emitting
    # it as a one-element transfer instead would starve the lane of n-1 tokens
    # and deadlock. Everything else adapts to the io_map, so this is the single
    # placement a re-solve is not allowed to change.
    for lane, (_, col, _) in enumerate(LANE_PORTS):
        if prog.inputs[col].kind != "mem":
            raise SystemExit(
                f"lane {lane}: the io_map puts the pivot column port {col} on "
                f"{prog.inputs[col].location}, which is a direct stream; it has "
                "to be a scratchpad to replay one word along the line. Swap it "
                "with the lane's other via port in LANE_PORTS if that one is a "
                "scratchpad -- add is commutative, so the two are interchangeable.")

    # Neither buffer is mark_output()ed: each is rewritten once per pivot, and
    # the whole-program coverage check counts writes across the entire run, so
    # it would report N/2 duplicates per element on a correct schedule. The
    # invariant that actually holds is per-pivot, and check_coverage() below
    # checks that one instead.
    prog.conf_all()

    written = []
    for k in range(n):
        src, dst = ("path_a", "path_b") if k % 2 == 0 else ("path_b", "path_a")
        for group in range(n_pad // LANES):
            rows = tuple(LANES * group + lane for lane in range(LANES))
            if written:
                prog.fence()        # FENCE_SE, all eight engines
            with prog:
                sweep(prog, k, src, dst, rows, n)
            written.append((k, dst, rows))

    return prog, written


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
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n > limit:
        raise SystemExit(f"a line is {n} words, past the {limit} a 9-bit size "
                         f"field and a {MEM_DEPTH}-word scratchpad allow; lower N")
    if n > MAX_ITERS:
        raise SystemExit(f"the pivot column entry is replayed {n} times per "
                         f"line, past the {MAX_ITERS} an 8-bit iters field "
                         "holds; lower N")
    if n * ELEM > MAX_BYTES:
        raise SystemExit(f"a line is {n * ELEM} bytes, past the {MAX_BYTES} a "
                         "16-bit byte count holds")

    prog, written = build(n, n_pad, args.io_map)
    prog.validate()
    check_coverage(written, n, n_pad)

    tables = table_bytes(prog)
    data = 2 * n_pad * n * ELEM
    if tables + data > args.max_table_bytes:
        raise SystemExit(
            f"the descriptor tables are {tables} bytes and the two matrices "
            f"{data}, together past the {args.max_table_bytes} of interleaved "
            f"RAM; the tables grow as 6*N^2 descriptors, so lower N (N={n} needs "
            f"{n * (n_pad // LANES)} passes)")

    passes = n * (n_pad // LANES)
    note = (f"floyd-warshall {n}x{n} (rows padded to {n_pad}): "
            f"path[i][j] = min(path[i][j], path[i][k] + path[k][j]) in {passes} "
            f"fenced passes of {LANES} lines, {n} pivots x {n_pad // LANES} line "
            f"groups, ping-ponging path_a <-> path_b; tables {tables} bytes")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "fw_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
