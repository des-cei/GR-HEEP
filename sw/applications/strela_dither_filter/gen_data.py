#!/usr/bin/env python3
"""Generate the STRELA 1-D error-diffusion dither test data header.

The fabric runs the whole filter in one streaming pass over DITHER_PIXELS
pixels (mapper/applications/dither_filter/main.dot):

    err = 0;
    for (i = 0; i < N; i++) {
        out = (input[i] & 0xFF) + err;      // and0, add0
        pixel = (out > 127) ? 255 : 0;      // cmp0, mul0
        err   = out - pixel;                // sub0, select0
        output[i] = pixel;
    }

i.e. a threshold with error feedback: the quantisation error of each pixel is
carried into the next one, so a smooth grey ramp comes out as a black/white
pattern whose *local density* tracks the input intensity. The threshold (127),
the black/white levels (255) and the error-feedback seed (0) are all baked into
the bitstream as PE constants, so they are documented here but not emitted:
changing them means re-solving the DFG, not editing this file.

Why the input words carry a tag in their upper 24 bits
------------------------------------------------------
The DFG's `and0` node is the `uint8_t` load: one pixel lives in the low byte of
each 32-bit word the streaming engine delivers, and the byte extract is part of
the kernel. Emitting bare 0..255 words would make `and0` a no-op and leave it
untested, so every word gets a deterministic pseudo-random 24-bit tag above the
pixel byte. A broken mask then feeds ~10^9-sized values into the threshold, the
error feedback saturates and essentially every output pixel flips -- a loud,
immediate failure instead of a silent pass. The tag deliberately spans both
signs so a sign-extension bug in the extract is caught too.

Stimulus: a linear grey ramp 0..255 across the image. This is the canonical
error-diffusion test -- it sweeps the full input range, and the resulting golden
has exactly half its pixels set with the block density tracking the ramp to
better than 1/255, so a wrong threshold, a wrong error sign or a dropped
feedback token all show up as a large error count rather than a subtle one. A
constant grey level would only exercise a single point of the transfer curve.

Integer semantics: everything is exact 32-bit arithmetic with no rescale (the
DFG has no shift node), and `err` provably stays inside [-255, 255], so nothing
here can wrap. The reference below uses the same operand order as the DFG --
`out - 255` for the taken side, matching sub0's `[operand=1] - constant`.
"""

import argparse
import sys

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1

THRESHOLD = 127     # cmp0's constant is -127, compared '>0'
LEVEL = 255         # and0's mask, sub0's and mul0's constant


def dither(words, threshold=THRESHOLD, level=LEVEL):
    """1-D error-diffusion dither, bit-exact with the DFG.

    Mirrors the fabric node for node: `and0` masks the pixel byte out of the
    streamed word, `add0` adds the carried error, `cmp0` emits 0/1 (the RTL
    comparator zero-extends its result, see functional_unit.sv), `mul0` scales
    that to 0/255 and `select0` picks `out - 255` on the taken side, `out`
    otherwise -- LLVM-style, operand 1 is the false side.
    """
    err = 0
    out_pixels = []
    for w in words:
        acc = (w & 0xFF) + err
        cond = 1 if (acc - threshold) > 0 else 0
        pixel = level * cond
        err = acc - pixel
        out_pixels.append(pixel)
    return out_pixels


def tag_words(pixels, seed):
    """Pack each pixel into the low byte of a 32-bit word under a varying tag.

    The tag is a plain LCG so the header is reproducible; the words are then
    reinterpreted as signed int32 because that is the C type the array has.
    """
    words = []
    state = seed & 0xFFFFFF
    for p in pixels:
        state = (state * 1103515245 + 12345) & 0xFFFFFF
        w = (state << 8) | (p & 0xFF)
        words.append(w - (1 << 32) if w >= (1 << 31) else w)
    return words


def emit_array(ctype, name, values, size_expr, per_line=8, interleaved=False):
    section = (' __attribute__((section(".xheep_data_interleaved")))'
               if interleaved else "")
    print(f"volatile {ctype} {name}[{size_expr}]{section} =")
    print("{")
    width = max((len(str(v)) for v in values), default=1)
    for start in range(0, len(values), per_line):
        row = values[start:start + per_line]
        print("    " + ", ".join(f"{v:>{width}d}" for v in row)
              + ("," if start + per_line < len(values) else ""))
    print("};")


def main():
    parser = argparse.ArgumentParser(
        description="Generate the STRELA error-diffusion dither test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_dither_filter` runs both with no arguments, so change N in both
or the C array sizes and the descriptor byte counts silently diverge.
""")
    parser.add_argument("N", type=int, nargs="?", default=256,
                        help="pixels to dither (default: 256, a full 0..255 ramp)")
    parser.add_argument("--seed", type=int, default=1,
                        help="tag RNG seed, fixed so the header is reproducible")
    args = parser.parse_args()

    n = args.N
    if n < 2:
        sys.exit(f"error: N={n} must be at least 2 to form a ramp")

    # Linear grey ramp across the full 0..255 input range.
    pixels = [(i * 255) // (n - 1) for i in range(n)]
    words = tag_words(pixels, args.seed)

    if any(not INT32_MIN <= w <= INT32_MAX for w in words):
        sys.exit("error: tagged input word left the int32 range")
    if any((w & 0xFF) != p for w, p in zip(words, pixels)):
        sys.exit("error: tag corrupted the pixel byte; check tag_words()")

    y = dither(words)

    # The fabric accumulates in a plain int32 that wraps silently, so bound the
    # error feedback here instead of letting a wrap look like a CGRA bug.
    err, span = 0, 0
    for w in words:
        acc = (w & 0xFF) + err
        span = max(span, abs(acc))
        err = acc - (LEVEL if (acc - THRESHOLD) > 0 else 0)
    if span > INT32_MAX:
        sys.exit(f"error: |out| reaches {span}, past int32 -- the pixel byte or "
                 f"the {LEVEL} level baked into the bitstream is wrong")

    density = sum(1 for v in y if v)
    print("#include <stdint.h>")
    print("")
    print(f"#define DITHER_PIXELS   {n}")
    print("")
    print(f"/* 1-D error-diffusion dither, threshold {THRESHOLD}, levels 0/{LEVEL},")
    print(f" * all baked into the bitstream as PE constants. Input is a linear")
    print(f" * grey ramp 0..255 in the low byte of each word, under a tag in bits")
    print(f" * 31:8 (seed {args.seed}) that exercises and0's byte extract.")
    print(f" * {density}/{n} output pixels set, max |out| = {span}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "x", words, "DITHER_PIXELS", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "y", [0] * n, "DITHER_PIXELS")
    print("")
    emit_array("int32_t", "y_expected", y, "DITHER_PIXELS")
    print("")


if __name__ == "__main__":
    main()
