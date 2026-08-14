#!/usr/bin/env python3
"""Generate the CPU 1-D error-diffusion dither test data header.

The software twin of strela_dither_filter: same filter, same stimulus, same
defaults, so the cycle counts of the two apps are directly comparable.

    err = 0;
    for (i = 0; i < N; i++) {
        out    = (input[i] & 0xFF) + err;   // byte extract, then carried error
        pixel  = (out > 127) ? 255 : 0;     // threshold
        err    = out - pixel;               // quantisation error, fed forward
        output[i] = pixel;
    }

i.e. a threshold with error feedback, so a smooth grey ramp comes out as a
black/white pattern whose *local density* tracks the input intensity.

This is the interesting one to compare against the accelerator, and the reason
is the loop-carried dependence. strela_dither_filter is the app in the suite
that is **recurrence-bound**: its error feedback is closed inside the fabric
(select0's initial_valid seed circulating through add0 -> cmp0 -> select0)
rather than accumulated in a delay counter, so its throughput is set by a 3-hop
ring rather than by the streams -- measured 2618 cycles for 256 pixels, 10.2
cycles/pixel against the ~1 a feed-forward kernel reaches. The same dependence
is what the CPU has to serialise on here, which is exactly what makes the two
numbers worth putting side by side.

Why the input words carry a tag in their upper 24 bits
------------------------------------------------------
One pixel lives in the low byte of each 32-bit word and the byte extract is part
of the kernel (`and0` in the DFG). Emitting bare 0..255 words would make the mask
a no-op and leave it untested, so every word gets a deterministic pseudo-random
24-bit tag above the pixel byte. A broken mask then feeds ~10^9-sized values into
the threshold, the error feedback saturates and essentially every output pixel
flips -- a loud failure instead of a silent pass. The tag deliberately spans both
signs so a sign-extension bug in the extract is caught too.

Stimulus: a linear grey ramp 0..255 across the image, the canonical
error-diffusion test -- it sweeps the full input range, and the resulting golden
has exactly half its pixels set with the block density tracking the ramp, so a
wrong threshold, a wrong error sign or a dropped feedback term all show up as a
large error count rather than a subtle one.
"""

import argparse
import sys

INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1

THRESHOLD = 127     # the same constants strela_dither_filter bakes into its PEs
LEVEL = 255


def dither(words, threshold=THRESHOLD, level=LEVEL):
    """1-D error-diffusion dither, bit-exact with the DFG.

    Mirrors the fabric node for node: `and0` masks the pixel byte out of the
    streamed word, `add0` adds the carried error, `cmp0` emits 0/1, `mul0`
    scales that to 0/255 and `select0` picks `out - 255` on the taken side.
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
        description="Generate the CPU error-diffusion dither test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_dither_filter's, which is the point of this app:
change N here and the CPU baseline no longer measures the same work as the
accelerator.
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

    out = dither(words)
    set_pixels = sum(1 for p in out if p == LEVEL)

    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE         int32_t")
    print(f"#define DITHER_PIXELS     {n}")
    print(f"#define DITHER_THRESHOLD  {THRESHOLD}")
    print(f"#define DITHER_LEVEL      {LEVEL}")
    print("")
    print(f"/* Linear 0..{LEVEL} ramp over {n} pixels, tag seed {args.seed};")
    print(f" * {set_pixels}/{n} output pixels set. */")
    print("")

    emit_array("DATA_TYPE", "input", words, "DITHER_PIXELS", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "output", [0] * n, "DITHER_PIXELS")
    print("")
    emit_array("DATA_TYPE", "output_expected", out, "DITHER_PIXELS")
    print("")


if __name__ == "__main__":
    main()
