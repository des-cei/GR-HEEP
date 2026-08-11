#!/usr/bin/env python3
"""Generate the STRELA gesummv ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that describes the solve the committed bitstream came from, resolved by STRELA's
shared binding layer. The kernel header and the io_map are committed together
next to this script and describe one solve; keep them in step.

Kernel contract (mapper/applications/gesummv_hv/main.dot), which fixes what each
port means:

    input4 = x                      shared vector, scratchpad, replayed M/2 times
    input0 = A rows [0, M/2)        input1 = A rows [M/2, M)
    input2 = B rows [0, M/2)        input3 = B rows [M/2, M)
    output0 = y[0, M/2)             output1 = y[M/2, M)

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
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 throughout
SEW = 32
MEM_DEPTH = 512   # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits


def build(m, n, io_map):
    half = m // 2
    rows = half * n            # elements in each half of A and of B

    prog = StreamProgram(
        io_map=io_map,
        kernel="gesummv_hv_kernel",
        app="strela_gesummv",
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

    with prog:
        # Scratchpads first. x is the shared operand every product waits on, and
        # ISE 3 must release MEM_E 3 before it blocks on the input3 stream.
        prog.mem("input4", "vec_x", 0, stride=ELEM, count=n,
                 size=n, iters=half)
        prog.mem("input0", "mat_a", 0, stride=ELEM, count=rows,
                 size=rows, iters=1)
        prog.mem("input1", "mat_a", rows, stride=ELEM, count=rows,
                 size=rows, iters=1)
        prog.mem("input2", "mat_b", 0, stride=ELEM, count=rows,
                 size=rows, iters=1)

        # Direct streams last.
        prog.stream("input3", "mat_b", rows, stride=ELEM, count=rows)

        # Each half of y is produced by one accumulator chain.
        prog.out("output0", "vec_y", 0, stride=ELEM, count=half)
        prog.out("output1", "vec_y", half, stride=ELEM, count=half)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA gesummv ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's. `make gen-app-data
PROJECT=strela_gesummv` runs both with no arguments, so change the two together.
""")
    parser.add_argument("M", type=int, nargs="?", default=8,
                        help="rows of A and B, i.e. length of y (default: 8)")
    parser.add_argument("-n", "--cols", type=int, default=16,
                        help="columns of A and B (default: 16)")
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

    half, rows = m // 2, (m // 2) * n
    # size is 9 bits and iters 8 (sw/strela.h), and the scratchpad is 512 words
    # deep; all three would wrap silently.
    if rows > MAX_SIZE or rows > MEM_DEPTH:
        raise SystemExit(f"M={m} with --cols {n} needs {rows}-word scratchpads, "
                         f"past the {min(MAX_SIZE, MEM_DEPTH)} available")
    if half > MAX_ITERS:
        raise SystemExit(f"M={m} replays x {half} times, past the {MAX_ITERS} "
                         "iters can hold")

    prog = build(m, n, args.io_map)
    prog.validate()

    note = (f"gesummv {m}x{n}: y = alpha*A@x + beta*B@x, two accumulator chains "
            f"of {half} rows, x replayed {half} times")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "gesummv_hv_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
