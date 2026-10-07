#!/usr/bin/env python3
"""Generate the STRELA stationary-twiddle FFT butterfly ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that `make map-bitstream PROJECT=fft_st` writes next to the bitstream, resolved
by STRELA's shared binding layer. Regenerate the bitstream and the descriptors
together:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    make -C $CG map-bitstream PROJECT=fft_st CGRA_CONFIG=configs/4x4-HV.hjson
    cp $CG/build/bitstream/fft_st_kernel.h   sw/applications/strela_v2_fft_st/
    cp $CG/build/bitstream/fft_st_io_map.json sw/applications/strela_v2_fft_st/
    make gen-app-data PROJECT=strela_v2_fft_st    # or: python3 gen_descriptors.py

fft_st_io_map.json is committed next to the kernel header so descriptors.h can
be regenerated (it is gitignored) without re-running the mapper. Keep the two
in step: they describe the same solve. There is no regress/4x4-HV/fft_st
entry, so scripts/regress2kernel.py cannot replay this one.

Kernel contract (mapper/applications/fft_st/main.dot), which fixes what each
port means:

    input0 = a_re   input1 = b_re   input2 = b_im   input3 = a_im   (streamed)
    output0 = x_re  output1 = y_re  output2 = x_im  output3 = y_im

with x = a + w*b and y = a - w*b, one radix-2 butterfly per element of the
streams, and the twiddle w a pair of PE constants in the bitstream -- so, unlike
strela_v2_fft, every input is a north stream and no scratchpad is loaded.

The layout is x-trela's strela_fft_nt one, in place: `a` is the first half of
real[]/imag[] and `b` the second, x overwrites `a` and y overwrites `b`. In place
is safe here because each output word depends only on the input words at the
same butterfly index, which the fabric has necessarily consumed before the OSE
can have anything to write, and a stream descriptor reads its addresses in
order -- the OSE never overtakes an ISE on the same word, and never touches a
word an ISE has yet to read. Every engine carries exactly one descriptor.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_V2_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_V2_SW)

from strela_v2_desc import StreamProgram  # noqa: E402

ELEM = 4                        # int32 samples throughout
SEW = 32
MAX_STREAM_BYTES = 0xFFFF       # a descriptor's byte count is 16 bits


def build(n, io_map):
    g = n // 2                  # butterflies, and the a/b split point
    prog = StreamProgram(
        io_map=io_map,
        kernel="fft_st_kernel",
        app="strela_v2_fft_st",
        arrays={"real": (n, ELEM), "imag": (n, ELEM)},
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("real", "imag")
    prog.conf_all()

    with prog:
        for port, sym, idx in (("input0", "real", 0),     # a_re
                               ("input1", "real", g),     # b_re
                               ("input2", "imag", g),     # b_im
                               ("input3", "imag", 0)):    # a_im
            prog.stream(port, sym, idx, stride=ELEM, count=g)

        for port, sym, idx in (("output0", "real", 0),    # x_re -> a's slot
                               ("output1", "real", g),    # y_re -> b's slot
                               ("output2", "imag", 0),    # x_im
                               ("output3", "imag", g)):   # y_im
            prog.out(port, sym, idx, stride=ELEM, count=g)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA stationary-twiddle FFT butterfly ISE/OSE "
                    "descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: 512 complex points, 256 butterflies.
`make gen-app-data PROJECT=strela_v2_fft_st` runs both with no arguments, so
change the two together.
""")
    parser.add_argument("N", type=int, nargs="?", default=512,
                        help="complex points, two per butterfly (default: 512)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "fft_st_io_map.json"),
                        help="io_map.json from map-bitstream (default: the copy "
                             "committed next to the kernel header)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n = args.N
    if n < 2 or n % 2:
        raise SystemExit(f"N={n} must be even and at least 2")
    # Each stream is one descriptor over N/2 words; validate() would reject the
    # byte count too, but name the knob.
    if (n // 2) * ELEM > MAX_STREAM_BYTES:
        raise SystemExit(f"N={n} streams {n // 2 * ELEM} bytes per port, past "
                         f"the {MAX_STREAM_BYTES} a descriptor can carry")

    prog = build(n, args.io_map)
    prog.validate()

    note = (f"{n}-point stationary-twiddle butterfly stage: {n // 2} "
            f"butterflies in place over real[]/imag[]")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "fft_st_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
