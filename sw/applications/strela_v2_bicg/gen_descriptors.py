#!/usr/bin/env python3
"""Generate the STRELA bicg ISE/OSE descriptor tables (two phases).

PolyBench bicg is one matrix traversed in both directions:

    phase 0  matvec_q   q = A  @ p     reduction over M, lines are rows of A
    phase 1  matvec_s   s = A^T @ r    reduction over N, lines are columns of A

Both phases run the same committed bitstream (`regress/4x4-HV/bicg_hv`, whose
DFG is byte-identical to atax_hv and mvt_1_hv):

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py bicg_hv \\
        --array-name matvec_q_kernel \\
        -o sw/applications/strela_v2_bicg/matvec_q_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py bicg_hv \\
        --array-name matvec_s_kernel \\
        -o sw/applications/strela_v2_bicg/matvec_s_kernel.h
    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/bicg_hv/io_map.json \\
        sw/applications/strela_v2_bicg/bicg_hv_io_map.json
    make gen-app-data PROJECT=strela_v2_bicg   # or: python3 gen_descriptors.py

**Independent products that still need two phases.** Nothing here is a chain:
q and s read A and an input vector each, and neither consumes the other's
result, so the two products could run in either order (PolyBench interleaves
them in one loop nest). What forces two phases is not a dependence but a
configuration: `delay_value` *is* the reduction length and it lives in the
bitstream, which `set_pe_delay_value()` patches in RAM before `TR_CONF` reads
it, so q's reduction over M and s's over N cannot share a loaded copy even
though the bits are otherwise identical. That is the rule strela_v2_gesummv states
from the other side -- there, both products reduce over the same N and are
simply more passes of one configuration. A phase is a configuration, not an
operand and not a dependence. Only the io_map is shared here: it is per DFG, and
one solve is what both phases run.

**The transpose costs nothing but a stride.** s never transposes A in memory. A
line of A^T is a column of A -- N elements one row apart -- and a descriptor
already carries an arbitrary stride, so the only difference between the phases
is `stride=ELEM, count=M` for a row versus `stride=M*ELEM, count=N` for a
column. What it *does* cost is span: a column descriptor reaches across the
whole matrix, so its byte count is N*M*4 rather than M*4, and a 16-bit byte
count caps that at 65535. PolyBench SMALL (124x116 -> 57536 bytes) fits with
little room, so `stream_line()` below splits a line across several descriptors
when it has to; consecutive descriptors on one channel are one continuous token
stream as far as the accumulator is concerned, which counts tokens and not
descriptors.

Kernel contract (mapper/applications/bicg_hv/main.dot), which fixes what each
port means:

    input4        the vector, preloaded into a scratchpad and replayed once per
                  group of four lines; it lands on router 0's *horizontal bus*
                  (MEM_W 0 mode 1), which broadcasts it to all four lanes
                  directly -- unlike gesummv_1_hv, there is no constant-multiply
                  PE in the path and therefore no PE constant to patch
    input0..3     one matrix line each, streamed element by element
    output_k      = line(input_k) . vector, one word per line, decimated by the
                  lane accumulator's delay_value

How this solve lands on the engines (the reason the emission order below is not
arbitrary):

    ISE 0  input3 -> north of column 0     ISE 1  input1 -> north of column 1
    ISE 2  input2 -> north of column 2     ISE 3  input4 -> MEM_W 0 mode 1
                                                  + input0 -> north of column 3
    OSE 0  output3 -> ver bus 0            OSE 1  output1 -> ver bus 1
    OSE 2  output2 -> ver bus 2            OSE 3  output0 -> ver bus 3

ISE 3 carries both the scratchpad and a direct stream, so its `mem()` is emitted
before any `stream()`: descriptors within a pass keep call order, and an engine
parked on a stream can no longer release the scratchpad every lane is waiting
on. No output is scratchpad-backed here, so this kernel is free of the OSE-side
`strela_v2_memory.sv` race that caps strela_v2_mm's pass length.

Why the fences are not optional. Between passes of the same phase, an ISE that
ran ahead would reach the next pass's TR_MEM_W while the fabric is still
draining the current replay of the vector: `strela_v2_memory.sv` presents the last
word of a replay from S_IDLE, where `ready_o` is already high, so the new
mem_param is accepted, the FSM leaves for S_WR and that word is never handed to
the fabric. Before a *phase*, the fence is what makes the reconfiguration safe:
an ISE re-entering TR_CONF clears its `conf_done_o`, which drops the global
`conf_reg` and re-gates every fabric handshake, so any transfer still in flight
when the reconfiguration starts would hang -- and that is true here even though
the phases share no data.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_V2_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_V2_SW)

from strela_v2_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
ROWS = 4          # accumulator lanes of bicg_hv = matrix lines per group
MEM_DEPTH = 512   # scratchpad words (StrelaV2MemDepth, rtl/strela_v2_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def groups_per_pass(groups):
    """Line groups in one pass: as many as the 8-bit `iters` field can replay
    the vector for. Nothing else here grows with the pass length -- every output
    is a direct stream, so there is no scratchpad drain to keep short."""
    return max(1, min(groups, MAX_ITERS))


def stream_line(prog, port, sym, index, stride, count, max_bytes=MAX_BYTES):
    """Feed one matrix line to a lane, split if its span overflows 16 bits.

    A row of A is contiguous (stride ELEM) and never needs splitting; a column
    is N elements a row apart, so its span is the whole matrix. The accumulator
    counts tokens, not descriptors, so several back-to-back descriptors on one
    channel reduce exactly as one would.
    """
    per_desc = max(1, max_bytes // stride)
    for first in range(0, count, per_desc):
        block = min(per_desc, count - first)
        prog.stream(port, sym, index + first * (stride // ELEM),
                    stride=stride, count=block)


def matvec(prog, out_sym, a_sym, vec_sym, line_base, line_stride, lines,
           red_len, per_pass, fence_first, max_bytes=MAX_BYTES):
    """Emit the passes computing `out(lines) = lines(a_sym) . vec_sym`.

    A "line" is a row of A for q and a column for s; `line_base(l)` gives its
    first element and `line_stride` the byte step between its elements, which is
    the only place the transpose shows up.

    One pass preloads the vector once, replays it once per group of ROWS lines,
    and streams the four lines of the group down the four lanes; lane k takes
    line ROWS*g+k and produces element ROWS*g+k of the result, so one output
    descriptor per lane walks the whole chunk with a stride of ROWS elements.
    """
    groups = lines // ROWS
    for first in range(0, groups, per_pass):
        block = min(per_pass, groups - first)
        if fence_first or first:
            prog.fence()                       # FENCE_SE, all eight engines
        with prog:
            # The scratchpad first: ISE 3 must release MEM_W 0 before it blocks
            # on the input0 stream, and the vector is what every lane waits on.
            prog.mem("input4", vec_sym, 0, stride=ELEM, count=red_len,
                     size=red_len, iters=block)

            for lane in range(ROWS):
                for group in range(first, first + block):
                    stream_line(prog, f"input{lane}", a_sym,
                                line_base(ROWS * group + lane),
                                line_stride, red_len, max_bytes)

            # One word per group on each lane, the accumulator's delayed output;
            # the lanes interleave, hence the ROWS-element stride.
            for lane in range(ROWS):
                prog.out(f"output{lane}", out_sym, ROWS * first + lane,
                         stride=ROWS * ELEM, count=block)


def build(m_pad, n_pad, io_map, per_pass_q, per_pass_s, max_bytes=MAX_BYTES):
    prog = StreamProgram(
        io_map=io_map,
        kernel="matvec_q_kernel",
        app="strela_v2_bicg",
        arrays={
            "mat_a": (n_pad * m_pad, ELEM, [n_pad, m_pad]),
            "vec_p": (m_pad, ELEM),
            "vec_r": (n_pad, ELEM),
            "vec_q": (n_pad, ELEM),
            "vec_s": (m_pad, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("vec_q", "vec_s")
    prog.conf_all()

    # ---- phase 0: q = A @ p, lines are rows (contiguous, reduce over M) -----
    matvec(prog, "vec_q", "mat_a", "vec_p",
           line_base=lambda row: row * m_pad, line_stride=ELEM,
           lines=n_pad, red_len=m_pad, per_pass=per_pass_q,
           fence_first=False, max_bytes=max_bytes)

    # ---- phase 1: s = A^T @ r, lines are columns (strided, reduce over N) ---
    # Same bitstream, second copy, patched with the other reduction length.
    prog.fence()
    prog.load_kernel("matvec_s_kernel", io_map=io_map)
    matvec(prog, "vec_s", "mat_a", "vec_r",
           line_base=lambda col: col, line_stride=m_pad * ELEM,
           lines=m_pad, red_len=n_pad, per_pass=per_pass_s,
           fence_first=False, max_bytes=max_bytes)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA bicg ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data PROJECT=strela_v2_bicg`
runs both with no arguments, so change M and N in both together. Both are
rounded up to a multiple of 4 here exactly as they are there. A is N x M, so the
two arguments are not interchangeable.
""")
    parser.add_argument("M", type=int, nargs="?", default=116,
                        help="columns of A = length of p and s (default: 116, "
                             "PolyBench SMALL)")
    parser.add_argument("N", type=int, nargs="?", default=124,
                        help="rows of A = length of r and q (default: 124, "
                             "PolyBench SMALL)")
    parser.add_argument("--groups-per-pass", type=int, metavar="G",
                        help="line groups (of 4 lines) per pass, i.e. how many "
                             "times the vector is replayed from one preload "
                             "(default: the whole matrix, capped by the 8-bit "
                             "iters field)")
    parser.add_argument("--max-stream-bytes", type=int, default=MAX_BYTES,
                        metavar="B",
                        help="split a matrix line across descriptors past this "
                             "many bytes (default: %(default)s, the 16-bit "
                             "byte count; lower it only to exercise the split)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "bicg_hv_io_map.json"),
                        help="io_map.json of the matrix-vector kernel; both "
                             "phases run the same solve (default: the copy "
                             "committed next to its headers)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    m, n = args.M, args.N
    if m < 1:
        raise SystemExit(f"M={m} must be at least 1")
    if n < 1:
        raise SystemExit(f"N={n} must be at least 1")
    if not 1 <= args.max_stream_bytes <= MAX_BYTES:
        raise SystemExit(f"--max-stream-bytes must be in 1..{MAX_BYTES}")

    m_pad = -(-m // ROWS) * ROWS
    n_pad = -(-n // ROWS) * ROWS
    per_pass_q = args.groups_per_pass or groups_per_pass(n_pad // ROWS)
    per_pass_s = args.groups_per_pass or groups_per_pass(m_pad // ROWS)

    # validate() catches none of these -- size and iters both wrap silently.
    limit = min(MAX_SIZE, MEM_DEPTH)
    for label, size in (("p", m_pad), ("r", n_pad)):
        if size > limit:
            raise SystemExit(f"{label} is {size} words, past the {limit} a "
                             "9-bit size field and a 512-word scratchpad "
                             "allow; lower the corresponding dimension")
    for label, per_pass in (("q", per_pass_q), ("s", per_pass_s)):
        if not 1 <= per_pass <= MAX_ITERS:
            raise SystemExit(f"--groups-per-pass={per_pass}: a pass of the {label} "
                             f"product replays its vector that many times, which "
                             f"must fit the {MAX_ITERS} the 8-bit iters field holds")
    for label, nbytes in (
            (f"a lane result of q ({per_pass_q} groups)",
             per_pass_q * ROWS * ELEM),
            (f"a lane result of s ({per_pass_s} groups)",
             per_pass_s * ROWS * ELEM)):
        if nbytes > MAX_BYTES:
            raise SystemExit(f"{label} is {nbytes} bytes, past the {MAX_BYTES} "
                             "a 16-bit byte count holds; lower "
                             "--groups-per-pass")

    prog = build(m_pad, n_pad, args.io_map, per_pass_q, per_pass_s,
                 args.max_stream_bytes)
    prog.validate()

    col_bytes = n_pad * m_pad * ELEM
    split = -(-col_bytes // args.max_stream_bytes)
    note = (f"bicg {n}x{m} (padded to {n_pad}x{m_pad}): q = A@p over "
            f"{-(-(n_pad // ROWS) // per_pass_q)} pass(es) of up to "
            f"{per_pass_q} four-row groups, then behind a FENCE_SE and a "
            f"second TR_CONF s = A^T@r over "
            f"{-(-(m_pad // ROWS) // per_pass_s)} pass(es) of up to "
            f"{per_pass_s} four-column groups; a column spans {col_bytes} bytes "
            f"and is streamed as {split} descriptor(s)")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "matvec_q_kernel.h",
                                 "matvec_s_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
