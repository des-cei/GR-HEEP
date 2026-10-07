#!/usr/bin/env python3
"""Generate the STRELA ReLU ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that pairs with the bitstream, resolved by STRELA's shared binding layer. This
matters more here than in a one-port app -- the mapper did not keep the ports in
order, binding input0/input2/input1/input3 to routers 0/1/2/3 and
output0/output2/output1/output3 to PE12/13/14/15, so a hand-written table would
silently cross two lanes. The bitstream is the one committed in elastic-cgra's
regression, replayed into a kernel header without re-running the mapper:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    python3 scripts/regress2kernel.py relu_opt \\
        -o sw/applications/strela_relu/relu_opt_kernel.h
    cp $CG/regress/4x4-HV/relu_opt/io_map.json \\
       sw/applications/strela_relu/relu_opt_io_map.json
    make gen-app-data PROJECT=strela_relu      # or: python3 gen_descriptors.py

relu_opt_io_map.json is committed next to the kernel header so descriptors.h can
be regenerated (it is gitignored) without re-running the mapper. Keep the two in
step: they describe the same solve, and a re-solve reshuffles the binding.

Kernel contract (mapper/applications/relu_opt/main.dot), which fixes what each
port means:

    input<k>  = lane k of x, streamed     (k = 0..3)
    output<k> = lane k of y = max(x, 0)   (k = 0..3)

Four independent feed-forward lanes: no scratchpad, no accumulator, one pass.
Lane k reads the slice x[k*N ...] and writes y[k*N ...], so the four ISE streams
together cover the flat array exactly once. RELU_PER_LANE is a free parameter of
the descriptors -- the bitstream fixes the lane count, not the length.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 samples
SEW = 32
LANES = 4         # fixed by the DFG
MAX_BYTES = (1 << 16) - 1     # the descriptor byte-count field is 16 bits


def build(n, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="relu_opt_kernel",
        app="strela_relu",
        arrays={"x": (LANES * n, ELEM), "y": (LANES * n, ELEM)},
        sew=SEW,
    )
    prog.mark_output("y")
    prog.conf_all()

    with prog:
        for lane in range(LANES):
            prog.stream(f"input{lane}", "x", lane * n, stride=ELEM, count=n)
        for lane in range(LANES):
            prog.out(f"output{lane}", "y", lane * n, stride=ELEM, count=n)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA ReLU ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data PROJECT=strela_relu`
runs both with no arguments, so change N in both together.
""")
    parser.add_argument("N", type=int, nargs="?", default=1024,
                        help="elements per lane (default: 1024, i.e. 4096 "
                             "elements: a 64x64 activation map)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "relu_opt_io_map.json"),
                        help="io_map.json for the bitstream (default: the copy "
                             "committed next to the kernel header)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n = args.N
    if n < 1:
        raise SystemExit(f"N={n} must be positive")
    # validate() checks this too, but fail here with the knob to turn.
    if n * ELEM > MAX_BYTES:
        raise SystemExit(f"N={n} needs {n * ELEM} bytes per lane, past the "
                         f"{MAX_BYTES} the 16-bit byte count holds "
                         f"(max N = {MAX_BYTES // ELEM})")

    prog = build(n, args.io_map)
    prog.validate()

    note = (f"ReLU over {LANES * n} elements as {LANES} independent lanes of "
            f"{n}: {LANES} ISE streams in, {LANES} OSE streams out, single pass")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "relu_opt_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
