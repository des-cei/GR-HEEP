#!/usr/bin/env python3
"""Generate the CPU find-two-minima test data header.

The software twin of strela_find2min: same reduction, same stimulus, same
defaults, so the cycle counts of the two apps are directly comparable.

    result[0] = min1   result[1] = min2   result[2] = idx1   result[3] = idx2

**The C kernel mirrors the fabric's three coupled trackers rather than the
obvious `if (v < min1) ... else if (v < min2) ...` scan**, because the two are
not the same function on duplicates. In the fabric each tracker reads its own
*previous* output (an elastic channel holding one token is a unit delay), so the
min2 tracker sees min1 before this element updated it, and it only accepts the
demoted candidate when `min2 > cand` -- strictly. With x = [5, 5, 3] that gives
idx2 = 1, where the sequential scan's unconditional `min2 = min1; idx2 = idx1`
gives idx2 = 0. Both are defensible answers; only one of them is the one the
accelerator produces, and a baseline that disagrees with its accelerator is not
a baseline. See find2min() below.

Strict `>` throughout: a later element equal to the running minimum does not
displace it, so on duplicates the *earliest* occurrence wins the index.

The sentinel: the fabric seeds its trackers with a real token that flows through
the demotion path, so the seed must be larger than every element or it would end
up in the answer; strela_find2min patches INT32_MAX in with
set_pe_initial_value. The CPU has the same requirement for the same reason and
uses the same value, emitted here so the two cannot drift.

Everything is exact integer arithmetic, so the expected values below are what
the CPU must produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

# The same seed strela_find2min programs into its two min-tracker PEs.
SENTINEL = INT32_MAX


def find2min(x, sentinel):
    """The two smallest values and their indices, in the DFG's exact order."""
    min1, idx1 = sentinel, 0
    min2, idx2 = sentinel, 0
    for i, v in enumerate(x):
        cond0 = min1 > v
        # min1 demotes into the min2 candidate exactly when it is displaced.
        cand, cand_idx = (min1, idx1) if cond0 else (v, i)
        new_min1, new_idx1 = (v, i) if cond0 else (min1, idx1)
        cond1 = min2 > cand
        new_min2, new_idx2 = (cand, cand_idx) if cond1 else (min2, idx2)
        min1, idx1, min2, idx2 = new_min1, new_idx1, new_min2, new_idx2
    return min1, min2, idx1, idx2


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
        description="Generate the CPU find-two-minima test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_find2min's, which is the point of this app: change N
here and the CPU baseline no longer measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=128,
                        help="elements to reduce (default: 128)")
    parser.add_argument("--amplitude", type=int, default=10000,
                        help="max |sample| of the input data (default: 10000)")
    parser.add_argument("--seed", type=int, default=1,
                        help="RNG seed, fixed so the header is reproducible")
    args = parser.parse_args()

    n, amp = args.N, args.amplitude
    if n < 2:
        sys.exit(f"error: N={n} must be at least 2, there are two minima")
    if not 1 <= amp < SENTINEL:
        sys.exit(f"error: --amplitude {amp} must be in [1, {SENTINEL - 1}]; the "
                 f"sentinel has to stay larger than every element")

    rng = random.Random(args.seed)
    x = [rng.randint(-amp, amp) for _ in range(n)]
    min1, min2, idx1, idx2 = find2min(x, SENTINEL)

    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE          int32_t")
    print(f"#define FIND2MIN_SAMPLES   {n}")
    print(f"#define FIND2MIN_RESULTS   4   /* min1, min2, idx1, idx2 */")
    print(f"#define FIND2MIN_SENTINEL  {SENTINEL}")
    print("")
    print(f"/* Data range [-{amp}, {amp}], seed {args.seed}. Two smallest:")
    print(f" * {min1} at index {idx1}, {min2} at index {idx2}. */")
    print("")

    emit_array("DATA_TYPE", "x", x, "FIND2MIN_SAMPLES", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "result", [0] * 4, "FIND2MIN_RESULTS")
    print("")
    emit_array("DATA_TYPE", "result_expected", [min1, min2, idx1, idx2],
               "FIND2MIN_RESULTS")
    print("")


if __name__ == "__main__":
    main()
