#!/usr/bin/env python3
"""Generate the STRELA ReLU test data header.

The fabric runs four *independent* ReLU lanes in parallel
(mapper/applications/relu_opt/main.dot), each a compare against zero feeding a
select:

    cond   = x > 0
    y      = cond ? x : 0          i.e. y = max(x, 0)

The four lanes are the whole point of the `relu_opt` DFG: ReLU is one compare
and one select per element, so a single lane would leave 14 of the 16 PEs idle
and the fabric would be bound by one input channel. Unrolling by four uses four
ISEs and four OSEs and moves four elements per cycle.

Layout: the four lanes are contiguous slices of one flat array, lane k covering
x[k*RELU_PER_LANE .. (k+1)*RELU_PER_LANE - 1], so the app reads as one
RELU_SAMPLES-element tensor pushed through the fabric four elements at a time.
gen_descriptors.py points lane k's ISE at that offset.

Boundary case: the DFG's compare is `> 0`, not `>= 0`, so an input of exactly
zero takes the false branch and yields the constant 0 -- the same answer
max(x, 0) gives, but by the other path. The stimulus plants an explicit zero in
every lane so that path is actually exercised rather than assumed.

Everything is exact integer arithmetic and ReLU cannot overflow, so the expected
values below are what the hardware must produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

LANES = 4          # fixed by the DFG: four cmp/select pairs, four ISE/OSE pairs


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
        description="Generate the STRELA ReLU test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_v2_relu` runs both with no arguments, so change N in both or the C
array sizes and the descriptor byte counts silently diverge.
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
    print(f"#define RELU_LANES      {LANES}   /* fixed by the DFG */")
    print(f"#define RELU_PER_LANE   {n}")
    print(f"#define RELU_SAMPLES    {total}  /* = RELU_LANES * RELU_PER_LANE */")
    print("")
    print(f"/* Signal range [-{amp}, {amp}], {negatives}/{total} negative")
    print(f" * (clamped to 0), one exact zero per lane. Seed {args.seed}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "x", x, "RELU_SAMPLES", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "y", [0] * total, "RELU_SAMPLES")
    print("")
    emit_array("int32_t", "y_expected", y, "RELU_SAMPLES")
    print("")


if __name__ == "__main__":
    main()
