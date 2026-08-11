#!/usr/bin/env python3
"""Generate the STRELA find-two-minima test data header.

The fabric reduces one streamed array down to four scalars
(mapper/applications/find2min/main.dot): the two smallest values and the two
indices where they occur.

    result[0] = min1   result[1] = min2   result[2] = idx1   result[3] = idx2

It is a single pass with no scratchpad, but unlike a plain map it is a genuine
reduction, built from three coupled tracker chains:

  * min1 tracker -- cond0 = (min1_old > x[i]); min1 = cond0 ? x[i] : min1_old.
  * min2 tracker -- the value min1 *demotes* when it is displaced, i.e. the
    candidate is cond0 ? min1_old : x[i], then the same compare/select again.
  * index trackers -- a free-running feedback add of 1 emits 0,1,2,... and two
    more selects, driven by the *same* two conditions, carry the indices along.

Two consequences the reference below has to model exactly:

  * Strict `>`. A later element equal to the running minimum does not displace
    it, so on duplicates the *earliest* occurrence wins the index. If the two
    smallest values are equal, min1 == min2 and the indices are the first two
    occurrences.
  * The sentinel. The trackers are seeded via initial_valid, and the seed is a
    real token that flows through the demotion path -- so a seed that is not
    larger than every element would end up in the answer. The bitstream ships a
    seed of 255, which would silently cap the result; main.c overwrites it with
    FIND2MIN_SENTINEL below, using set_pe_initial_value on the two min-tracker
    PEs, so the app is correct for any int32 input. FIND2MIN_SENTINEL is emitted
    here so the C code and this reference cannot drift apart.

The reduction length is likewise not fixed by the bitstream: the trackers
decimate with a per-FU delay counter that main.c patches to FIND2MIN_SAMPLES + 1
(the seed firing is itself counted -- see the DFG comment). The default N is
deliberately *not* the 100 the bitstream was solved for, so that every run
actually exercises that patch: at N=100 the patched delay would coincide with
the baked-in one and a wrong PE index would go unnoticed.

Everything is exact integer arithmetic, so the expected values below are what
the hardware must produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

# What main.c programs into the two min-tracker PEs with set_pe_initial_value.
# Larger than any int32 input, so the seed can never survive into the answer.
SENTINEL = INT32_MAX


def find2min(x, sentinel):
    """The two smallest values and their indices, in the DFG's exact order.

    Mirrors the dataflow rather than calling sorted(): the trackers read their
    own *previous* output (an elastic channel holding one token is a unit
    delay), so the min2 candidate sees min1 before this element updated it.
    """
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
        description="Generate the STRELA find-two-minima test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_find2min` runs both with no arguments, so change N in both or the
C array sizes and the descriptor byte counts silently diverge.
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
    if min1 == SENTINEL or min2 == SENTINEL:
        sys.exit("internal error: the sentinel survived into the result")

    print("#include <stdint.h>")
    print("")
    print(f"#define FIND2MIN_SAMPLES   {n}")
    print(f"#define FIND2MIN_SENTINEL  {SENTINEL}  /* main.c seeds the min trackers with this */")
    print("")
    print(f"/* Data range [-{amp}, {amp}], seed {args.seed}.")
    print(f" * min1 = {min1} at {idx1}, min2 = {min2} at {idx2}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "x", x, "FIND2MIN_SAMPLES", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against. The four
    # scalars are one contiguous array, matching the DFG's own output addresses
    # (0x400, 0x404, 0x408, 0x40C) and the port order output0..output3.
    emit_array("int32_t", "result", [0] * 4, "4")
    print("")
    emit_array("int32_t", "result_expected", [min1, min2, idx1, idx2], "4")
    print("")


if __name__ == "__main__":
    main()
