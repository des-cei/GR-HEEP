#!/usr/bin/env python3
"""Generate the int8 ReLU test data header (strela_v1_relu8 and cpu_relu8).

The two apps share this generator byte for byte, so they run the same kernel
over the same data and their cycle counts are directly comparable.

    y = x > 0 ? x : 0          i.e. y = max(x, 0), elementwise over int8

It is the narrow-data showcase of STRELA v1's stream engines: the input stream
engine reads 8-bit elements (four to a bus word) and sign-extends them to the
fabric's 32 bits, and the output stream engine packs the low byte of each result
back four to a word. The fabric itself still computes in 32 bits; nothing in
the bitstream knows the data is narrow.

--width 16 / 32 emits the same values as int16_t / int32_t arrays, with the
same SEW in STRELA_V1_RELU_SEW, so the accelerator can be measured on identical
data at every element width (`make app PROJECT=strela_v1_relu8
GEN_DATA_ARGS="--width 32"`). The sample values always fit in int8, whatever the
width, so the expected output does not change with it.

Boundary case: the DFG's compare is `> 0`, not `>= 0`, so an input of exactly
zero takes the false branch and yields the constant 0. The stimulus plants an
explicit zero, and pins the int8 extremes -128 and 127, so the sign extension
of the most negative value and the largest positive one are both exercised.
"""

import argparse
import random
import sys

WIDTHS = {8: ("int8_t", "STRELA_V1_SEW_8"),
          16: ("int16_t", "STRELA_V1_SEW_16"),
          32: ("int32_t", "STRELA_V1_SEW_32")}


def relu(x):
    """Elementwise max(x, 0), the same integer semantics as the DFG."""
    return [v if v > 0 else 0 for v in x]


def emit_array(ctype, name, values, size_expr, per_line=16, interleaved=False):
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
        description="Generate the int8 ReLU test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults are shared with cpu_relu8, which is the point of the pair: change N
in one and the CPU baseline no longer measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="elements (default: 4096, a 64x64 activation map)")
    parser.add_argument("--width", type=int, choices=sorted(WIDTHS), default=8,
                        help="element width of the arrays in bits (default: 8)")
    parser.add_argument("--seed", type=int, default=1,
                        help="RNG seed, fixed so the header is reproducible")
    args = parser.parse_args()

    n = args.N
    ctype, sew = WIDTHS[args.width]
    if n < 4:
        sys.exit(f"error: N={n} must be at least 4")
    # One stream engine streams the whole array, and its 16-bit size field is
    # the byte span of the stream.
    span = n * args.width // 8
    if span > 0xFFFF:
        sys.exit(f"error: N={n} spans {span} bytes at {args.width} bits, past "
                 f"the 16-bit stream engine size field")

    rng = random.Random(args.seed)
    x = [rng.randint(-128, 127) for _ in range(n)]
    x[0], x[1], x[2] = 0, -128, 127
    y = relu(x)

    negatives = sum(1 for v in x if v < 0)
    print("#include <stdint.h>")
    print("")
    print(f"#define DATA_TYPE          {ctype}")
    print(f"#define RELU_WIDTH         {args.width}")
    print(f"#define STRELA_V1_RELU_SEW {sew}")
    print(f"#define RELU_SAMPLES       {n}")
    print("")
    print(f"/* int8 samples in [-128, 127], {negatives}/{n} negative (clamped to 0),")
    print(f" * one exact zero and both extremes. Seed {args.seed}. */")
    print("")

    emit_array("DATA_TYPE", "x", x, "RELU_SAMPLES", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "y", [0] * n, "RELU_SAMPLES", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "y_expected", y, "RELU_SAMPLES")
    print("")


if __name__ == "__main__":
    main()
