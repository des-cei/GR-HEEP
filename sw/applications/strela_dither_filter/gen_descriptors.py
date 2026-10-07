#!/usr/bin/env python3
"""Generate the STRELA error-diffusion dither ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that pairs with the bitstream, resolved by STRELA's shared binding layer. There
is no committed regress bitstream for this kernel, so both artifacts come from a
real mapper run:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    make -C $CG map-bitstream PROJECT=dither_filter CGRA_CONFIG=configs/4x4-HV.hjson
    cp $CG/build/bitstream/dither_filter_kernel.h   sw/applications/strela_dither_filter/
    cp $CG/build/bitstream/dither_filter_io_map.json sw/applications/strela_dither_filter/
    make gen-app-data PROJECT=strela_dither_filter   # or: python3 gen_descriptors.py

dither_filter_io_map.json is committed next to the kernel header so descriptors.h
can be regenerated (it is gitignored) without re-running the ~10-minute Gurobi
solve. Keep the two in step: they describe the same solve, and a re-solve
reshuffles the engine assignment (this one landed input0 on ISE 3 and output0 on
OSE 1, but nothing below depends on that).

Kernel contract (mapper/applications/dither_filter/main.dot), which fixes what
each port means:

    input0  = the grayscale pixel stream, one pixel in the low byte of each
              32-bit word (the DFG's and0 does the byte extract)
    output0 = the dithered pixel stream, 0 or 255, one per input

Both ports are plain streams: `input0` is `[border=north]` and `output0` is
`[border=south]`, so no port is scratchpad-resident, there is nothing to preload
and no ISE is doubled up. That makes this the minimal descriptor shape -- one
ISE stream in, one OSE stream out, a single pass.

The error feedback that makes this a dither rather than a plain threshold is a
loop-carried recurrence *inside the fabric* (select0's seeded token circulating
through add0 -> cmp0 -> select0), not something the descriptors express. It costs
throughput -- the DFG measures ~10 cycles/pixel against the 1 cycle/pixel a
feed-forward kernel like the FIR reaches -- but it is invisible here: the stream
descriptors are the same as any other one-in/one-out kernel, and the fabric's
backpressure paces the input engine automatically. DITHER_PIXELS is therefore a
free parameter of the descriptors; the bitstream fixes the threshold and the
levels, not the length.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 words, one pixel per word
SEW = 32
MAX_BYTES = (1 << 16) - 1     # the descriptor byte-count field is 16 bits


def build(n, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="dither_filter_kernel",
        app="strela_dither_filter",
        arrays={"x": (n, ELEM), "y": (n, ELEM)},
        sew=SEW,
    )
    prog.mark_output("y")
    prog.conf_all()

    with prog:
        prog.stream("input0", "x", 0, stride=ELEM, count=n)
        prog.out("output0", "y", 0, stride=ELEM, count=n)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA error-diffusion dither ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data
PROJECT=strela_dither_filter` runs both with no arguments, so change N in both
together.
""")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="pixels to dither (default: 4096, a 64x64 image)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "dither_filter_io_map.json"),
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
        raise SystemExit(f"N={n} needs {n * ELEM} bytes per stream, past the "
                         f"{MAX_BYTES} the 16-bit byte count holds "
                         f"(max N = {MAX_BYTES // ELEM})")

    prog = build(n, args.io_map)
    prog.validate()

    note = (f"1-D error-diffusion dither over {n} pixels: one ISE stream in, one "
            f"OSE stream out, single pass, no scratchpad")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "dither_filter_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
