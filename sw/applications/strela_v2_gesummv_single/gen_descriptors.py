#!/usr/bin/env python3
"""Generate the STRELA gesummv ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that describes the solve the committed bitstream came from, resolved by STRELA's
shared binding layer. The kernel header and the io_map are committed together
next to this script and describe one solve; keep them in step.

Kernel contract (mapper/applications/gesummv_hv/main.dot), which fixes what each
port means:

    input4 = x                      shared vector, scratchpad, preloaded once
                                    for the whole run and replayed once per
                                    output row
    input0 = A rows of chain 0      input1 = A rows of chain 1
    input2 = B rows of chain 0      input3 = B rows of chain 1
    output0 = y of chain 0          output1 = y of chain 1

Chain 0 owns rows [0, M/2) and chain 1 rows [M/2, M). A pass preloads a block of
rows_per_pass() rows for each chain, so M*N is no longer bounded by the 512-word
scratchpad -- only one row block is. What is *not* per pass is x: the matrix
blocks differ from pass to pass, x does not, so it is loaded once and replayed
M/2 times. Only the loads that change have to be repeated, which is also why the
FENCE_SE between passes survives -- see build().

so y[i] = alpha*sum_j A[i][j]*x[j] + beta*sum_j B[i][j]*x[j], with alpha and
beta PE constants and the reduction length N carried as the accumulators'
delay_value. main.c patches all three into the kernel array at runtime.

How this solve lands on the engines (the reason the emission order below is not
arbitrary):

    ISE 0  --                                    (TR_CONF only)
    ISE 1  input4 -> MEM_E 1 mode 1  +  input0 -> MEM_W 2 mode 0
    ISE 2  input1 -> MEM_W 1 mode 0
    ISE 3  input2 -> MEM_E 3 mode 0  +  input3 -> north stream
    OSE 0  output1 -> MEM_W 0 mode 1 (arm + drain)
    OSE 1  output0 -> south stream

ISE 3 carries both a scratchpad and a direct stream, so its mem() must be
emitted before its stream(): descriptors within a pass keep call order, and an
engine parked on a stream can no longer release the scratchpad the fabric is
waiting on. Every mem() below therefore precedes every stream().
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_V2_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_V2_SW)

from strela_v2_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 throughout
SEW = 32
MEM_DEPTH = 512   # scratchpad words (StrelaV2MemDepth, rtl/strela_v2_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits


def rows_per_pass(n):
    """Output rows per accumulator chain in one pass.

    A pass preloads `rows_per_pass * n` elements of A and of B into one 512-word
    scratchpad each, so the row block is what has to fit -- not the whole half,
    which is why anything past M*N = 1022 needs more than one pass."""
    return max(1, min(MAX_SIZE, MEM_DEPTH) // n)


def build(m, n, io_map, rows=None):
    half = m // 2
    rows = rows or rows_per_pass(n)

    prog = StreamProgram(
        io_map=io_map,
        kernel="gesummv_hv_kernel",
        app="strela_v2_gesummv",
        arrays={
            "mat_a": (m * n, ELEM),
            "mat_b": (m * n, ELEM),
            "vec_x": (n, ELEM),
            "vec_y": (m, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("vec_y")
    prog.conf_all()

    # Each pass takes `rows` output rows from each half; the last one takes the
    # remainder. Chain 0 walks rows [0, half), chain 1 rows [half, m), so a pass
    # starting at row `first` reads A/B rows `first` and `half + first`.
    #
    # The FENCE_SE between passes is *not* optional, and it is not about the
    # memory hand-off -- every pass reads and writes disjoint rows. It exists
    # because every ISE here does nothing but preload scratchpads, so without it
    # an ISE reaches the next pass's mem() while the fabric is still draining the
    # current one. `strela_v2_memory.sv` presents the last word of a replay from
    # S_IDLE (`pe_valid_o`/`hor_valid_o` are driven in `S_WR_CGRA || S_IDLE`
    # only), and `ready_o` is already high there, so the new mem_param is
    # accepted, the FSM leaves for S_WR and that last word is never handed to the
    # fabric: the run hangs. Verified -- 8x16 in two passes of two rows deadlocks
    # without the fence and passes with it.
    for first in range(0, half, rows):
        block = min(rows, half - first)     # output rows per chain this pass
        words = block * n                   # A/B elements per chain this pass
        if first:
            prog.fence()
        with prog:
            # Scratchpads first: ISE 3 must release MEM_E 3 before it blocks on
            # the input3 stream.
            #
            # x, the operand both products share, is loaded once for the whole
            # run rather than once per pass: every pass replays the *same*
            # block, so a reload would re-read what the scratchpad already held.
            # `iters` counts replays, one per output row, so it is `half` and
            # not this pass's `block`. The fences below do not disturb that -- a
            # FENCE_SE waits on the engines, it does not reset a scratchpad, and
            # the pending replays survive it.
            if not first:
                prog.mem("input4", "vec_x", 0, stride=ELEM, count=n,
                         size=n, iters=half)
            prog.mem("input0", "mat_a", first * n, stride=ELEM, count=words,
                     size=words, iters=1)
            prog.mem("input1", "mat_a", (half + first) * n, stride=ELEM,
                     count=words, size=words, iters=1)
            prog.mem("input2", "mat_b", first * n, stride=ELEM, count=words,
                     size=words, iters=1)

            # Direct streams last.
            prog.stream("input3", "mat_b", (half + first) * n,
                        stride=ELEM, count=words)

            # Each half of y is produced by one accumulator chain.
            prog.out("output0", "vec_y", first, stride=ELEM, count=block)
            prog.out("output1", "vec_y", half + first, stride=ELEM, count=block)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA gesummv ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's. `make gen-app-data
PROJECT=strela_v2_gesummv` runs both with no arguments, so change the two together.
""")
    parser.add_argument("M", type=int, nargs="?", default=90,
                        help="rows of A and B, i.e. length of y (default: 90, "
                             "PolyBench SMALL)")
    parser.add_argument("-n", "--cols", type=int, default=90,
                        help="columns of A and B (default: 90, PolyBench SMALL)")
    parser.add_argument("--rows-per-pass", type=int, metavar="R",
                        help="output rows per chain in one pass (default: as "
                             "many N-element rows as fit a 512-word scratchpad)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "gesummv_hv_io_map.json"),
                        help="io_map.json of the solve the kernel header came "
                             "from (default: the copy committed beside it)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    m, n = args.M, args.cols
    if m < 2 or m % 2:
        raise SystemExit(f"M={m} must be even and at least 2")
    if n < 1:
        raise SystemExit(f"--cols {n} must be at least 1")

    half = m // 2
    rows = args.rows_per_pass or rows_per_pass(n)
    # size is 9 bits and iters 8 (sw/strela_v2.h), and the scratchpad is 512 words
    # deep; all three would wrap silently.
    if rows < 1 or rows * n > min(MAX_SIZE, MEM_DEPTH):
        raise SystemExit(f"{rows} rows of {n} columns need {rows * n}-word "
                         f"scratchpads, past the {min(MAX_SIZE, MEM_DEPTH)} "
                         "available; lower --rows-per-pass or --cols")
    if half > MAX_ITERS:
        raise SystemExit(f"one preload of x is replayed {half} times, once per "
                         f"output row per chain, past the {MAX_ITERS} iters can "
                         "hold; lower M")

    prog = build(m, n, args.io_map, rows)
    prog.validate()

    passes = -(-half // rows)
    note = (f"gesummv {m}x{n}: y = alpha*A@x + beta*B@x, two accumulator chains "
            f"of {half} rows, walked in {passes} pass(es) of up to {rows} rows "
            f"with one preload of x replayed {half} times, once per row")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "gesummv_hv_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
