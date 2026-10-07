#!/usr/bin/env python3
"""Generate the STRELA FFT butterfly ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that `make map-bitstream PROJECT=fft_full` writes next to the bitstream,
resolved by STRELA's shared binding layer. Regenerate the bitstream and the
descriptors together:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    make -C $CG map-bitstream PROJECT=fft_full CGRA_CONFIG=configs/4x4-HV.hjson
    cp $CG/build/bitstream/fft_full_kernel.h   sw/applications/strela_v2_fft/
    cp $CG/build/bitstream/fft_full_io_map.json sw/applications/strela_v2_fft/
    make gen-app-data PROJECT=strela_v2_fft       # or: python3 gen_descriptors.py

fft_full_io_map.json is committed next to the kernel header so descriptors.h
can be regenerated (it is gitignored) without re-running the mapper. Keep the
two in step: they describe the same solve.

Kernel contract (mapper/applications/fft_full/main.dot), which fixes what each
port means:

    input0 = a_re   input1 = b_re   input4 = b_im   input5 = a_im   (streamed)
    input2 = w_re   input3 = w_im                   (twiddles, in a scratchpad)
    output0 = x_re  output1 = y_re  output2 = x_im  output3 = y_im

with x = a + w*b and y = a - w*b, one radix-2 butterfly per element of the
streams. The FFT_TWIDDLES twiddles of the stage are preloaded once and replayed
FFT_BLOCKS times, which is the whole reason the operands are presented
deinterleaved: butterfly i then always wants twiddle i % FFT_TWIDDLES. See
gen_data.py for the layout and the Q<frac> convention.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_V2_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_V2_SW)

from strela_v2_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 samples throughout: the fabric has no shifter, so the
SEW = 32          # Q<frac> product needs the full 32-bit range (see gen_data.py)
MEM_DEPTH = 512   # scratchpad words (strela_v2_memory.sv)
MAX_ITERS = 255   # mem_param iters is 8 bits


def build(nb, twiddles, blocks, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="fft_full_kernel",
        app="strela_v2_fft",
        arrays={
            "a_re": (nb, ELEM), "a_im": (nb, ELEM),
            "b_re": (nb, ELEM), "b_im": (nb, ELEM),
            "w_re": (twiddles, ELEM), "w_im": (twiddles, ELEM),
            "x_re": (nb, ELEM), "x_im": (nb, ELEM),
            "y_re": (nb, ELEM), "y_im": (nb, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("x_re", "x_im", "y_re", "y_im")
    prog.conf_all()

    with prog:
        # Twiddles first: an ISE that also carries a sample stream must fill and
        # release its scratchpad before it blocks on the stream, or the fabric
        # waits for twiddles that the same engine is no longer free to send.
        prog.mem("input2", "w_re", 0, stride=ELEM, count=twiddles,
                 size=twiddles, iters=blocks)
        prog.mem("input3", "w_im", 0, stride=ELEM, count=twiddles,
                 size=twiddles, iters=blocks)

        for port, sym in (("input0", "a_re"), ("input5", "a_im"),
                          ("input1", "b_re"), ("input4", "b_im")):
            prog.stream(port, sym, 0, stride=ELEM, count=nb)

        for port, sym in (("output0", "x_re"), ("output2", "x_im"),
                          ("output1", "y_re"), ("output3", "y_im")):
            prog.out(port, sym, 0, stride=ELEM, count=nb)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA FFT butterfly ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: one middle stage of a 4096-point FFT, 32
twiddles replayed over 64 blocks, 2048 butterflies. `make gen-app-data
PROJECT=strela_v2_fft` runs both with no arguments, so change the two together.
""")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="points in the transform (default: 4096)")
    parser.add_argument("--block", type=int, default=64,
                        help="butterfly block size of this stage (default: 64)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "fft_full_io_map.json"),
                        help="io_map.json from map-bitstream (default: the copy "
                             "committed next to the kernel header)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n, block = args.N, args.block
    if n < 4 or n & (n - 1):
        raise SystemExit(f"N={n} must be a power of two and at least 4")
    if block < 2 or block & (block - 1) or block > n:
        raise SystemExit(f"--block {block} must be a power of two in [2, {n}]")

    twiddles, blocks = block // 2, n // block
    # mem_param packs size in 9 bits and iters in 8 (sw/strela_v2.h), and the
    # scratchpad is 512 words deep; both would wrap silently.
    if twiddles > MEM_DEPTH:
        raise SystemExit(f"--block {block} needs {twiddles} twiddles, past the "
                         f"{MEM_DEPTH}-word scratchpad")
    if blocks > MAX_ITERS:
        raise SystemExit(f"N={n} with --block {block} replays the twiddles "
                         f"{blocks} times, past the {MAX_ITERS} iters can hold")

    prog = build(n // 2, twiddles, blocks, args.io_map)
    prog.validate()

    note = (f"{n}-point FFT, stage with {block}-point blocks: {n // 2} "
            f"butterflies, {block // 2} twiddles replayed {n // block} times")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "fft_full_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
