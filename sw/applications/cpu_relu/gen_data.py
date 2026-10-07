#!/usr/bin/env python3
"""Generate the CPU ReLU test data header.

The software twin of strela_v2_relu: same elementwise kernel, same stimulus, same
defaults, so the cycle counts of the two apps are directly comparable.

    y = x > 0 ? x : 0          i.e. y = max(x, 0)

Layout: strela_v2_relu splits the array into four lanes because the DFG unrolls by
four (four cmp/select pairs over contiguous slices of one flat array), and the
lanes are kept here as well -- not because the CPU cares, but so that both apps
run over the same RELU_SAMPLES = RELU_LANES * RELU_PER_LANE elements and the
comparison is one number against one number.

Boundary case: the DFG's compare is `> 0`, not `>= 0`, so an input of exactly
zero takes the false branch and yields the constant 0 -- the same answer
max(x, 0) gives, but by the other path. The stimulus plants an explicit zero in
every lane so that path is exercised rather than assumed, and the C kernel is
written with the same strict compare.

Everything is exact integer arithmetic and ReLU cannot overflow, so the expected
values below are what the CPU must produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

LANES = 4          # fixed by strela_v2_relu's DFG; kept so the shapes match


def relu(x):
    """Elementwise max(x, 0), the same integer semantics as the DFG."""
    return [v if v > 0 else 0 for v in x]


def emit_array(ctype, name, values, size_expr, per_line=10, interleaved=False):
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
        description="Generate the CPU ReLU test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_v2_relu's, which is the point of this app: change N here
and the CPU baseline no longer measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=1024,
                        help="elements per lane (default: 1024, i.e. 4096 "
                             "elements: a 64x64 activation map)")
    parser.add_argument("--amplitude", type=int, default=10000,
                        help="max |sample| of the input signal (default: 10000)")
    parser.add_argument("--seed", type=int, default=1,
                        help="RNG seed, fixed so the header is reproducible")
    args = parser.parse_args()

    n, amp = args.N, args.amplitude
    if n < 2:
        sys.exit(f"error: N={n} must be at least 2")
    if not 1 <= amp <= INT32_MAX:
        sys.exit(f"error: --amplitude {amp} must be in [1, {INT32_MAX}]")

    rng = random.Random(args.seed)
    x = [rng.randint(-amp, amp) for _ in range(LANES * n)]
    # Exercise the `> 0` boundary explicitly in every lane, and pin one strictly
    # negative and one strictly positive element so no lane can pass by accident.
    for lane in range(LANES):
        base = lane * n
        x[base] = 0
        x[base + 1] = -abs(x[base + 1]) - 1
        x[base + 2] = abs(x[base + 2]) + 1
    y = relu(x)

    total = LANES * n
    negatives = sum(1 for v in x if v < 0)
    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE       int32_t")
    print(f"#define RELU_LANES      {LANES}   /* strela_v2_relu's DFG unroll */")
    print(f"#define RELU_PER_LANE   {n}")
    print(f"#define RELU_SAMPLES    {total}  /* = RELU_LANES * RELU_PER_LANE */")
    print("")
    print(f"/* Signal range [-{amp}, {amp}], {negatives}/{total} negative")
    print(f" * (clamped to 0), one exact zero per lane. Seed {args.seed}. */")
    print("")

    emit_array("DATA_TYPE", "x", x, "RELU_SAMPLES", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "y", [0] * total, "RELU_SAMPLES")
    print("")
    emit_array("DATA_TYPE", "y_expected", y, "RELU_SAMPLES")
    print("")


if __name__ == "__main__":
    main()
