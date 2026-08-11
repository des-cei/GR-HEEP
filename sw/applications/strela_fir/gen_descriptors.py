#!/usr/bin/env python3
"""Generate the STRELA 4-tap FIR ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that pairs with the bitstream, resolved by STRELA's shared binding layer. The
bitstream is the one committed in elastic-cgra's regression, replayed into a
kernel header without re-running the mapper:

    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
    python3 scripts/regress2kernel.py fir -o sw/applications/strela_fir/fir_kernel.h
    cp $CG/regress/4x4-HV/fir/io_map.json sw/applications/strela_fir/fir_io_map.json
    make gen-app-data PROJECT=strela_fir       # or: python3 gen_descriptors.py

fir_io_map.json is committed next to the kernel header so descriptors.h can be
regenerated (it is gitignored) without re-running the mapper. Keep the two in
step: they describe the same solve, and a re-solve reshuffles the engine
assignment.

Kernel contract (mapper/applications/fir/main.dot), which fixes what each port
means:

    input0  = x   (streamed, one sample per cycle)
    output0 = y   (streamed, one sample per input)

The filter is entirely feed-forward and its three z^-1 delays are preloaded
channel tokens, so there is nothing to preload from memory: no scratchpad, one
ISE stream in and one OSE stream out, in a single pass. FIR_SAMPLES is a free
parameter of the descriptors -- the bitstream fixes the taps, not the length.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 samples
SEW = 32
MAX_BYTES = (1 << 16) - 1     # the descriptor byte-count field is 16 bits


def build(n, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="fir_kernel",
        app="strela_fir",
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
        description="Generate STRELA 4-tap FIR ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data PROJECT=strela_fir`
runs both with no arguments, so change N in both together.
""")
    parser.add_argument("N", type=int, nargs="?", default=100,
                        help="input samples to filter (default: 100)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "fir_io_map.json"),
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

    note = (f"4-tap Q8 low-pass FIR over {n} samples: one ISE stream in, one "
            f"OSE stream out, single pass, no scratchpad")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "fir_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
