#!/usr/bin/env python3
"""Generate the STRELA doitgen dataset (PolyBench doitgen, int32).

PolyBench's kernel is a 3-D tensor times a 2-D matrix:

    for r in NR, for q in NQ:
        sum[r][q][p] = SUM_s A[r][q][s] * C4[s][p]      for p in NP
        A[r][q][p]   = sum[r][q][p]

Every (r, q) pair contributes one independent length-NP vector times the
NP x NP matrix C4, and `A` is stored row-major, so its last axis is contiguous
and the whole tensor *is* an (NR*NQ) x NP matrix with no copy. The kernel is
therefore one plain matrix product,

    matSum(NR*NQ x NP) = matA(NR*NQ x NP) * matC4(NP x NP)

which is what the CGRA runs -- a single pass over the mm_hv bitstream, no
chaining. See gen_descriptors.py for the schedule.

Deviation from PolyBench, which copies `sum` back over `A` at the end of each
(r, q): the result goes to its own array. That changes no value -- the rows are
independent, so nothing reads a row of `A` after it has been overwritten -- but
it matters for the fabric, where within a pass the ISE reading an element and
the OSE writing it are not ordered with respect to each other.

The reference is computed with plain Python integers and then range-checked
against int32, so a mismatch in simulation means the fabric is wrong rather
than the golden having silently wrapped.

The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_doitgen` runs both with no arguments, so change NR/NQ/NP in both
together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1
INT32_MIN = -(1 << 31)

ELEM_CAP = 50          # readability cap, tightened when NP makes it unsafe


def matmul(a, b, ni, nk, nj):
    out = [0] * (ni * nj)
    for i in range(ni):
        for k in range(nk):
            aik = a[i * nk + k]
            for j in range(nj):
                out[i * nj + j] += aik * b[k * nj + j]
    return out


def safe_elem_limit(np_):
    """Largest |v| such that |matSum| = NP*v^2 fits int32."""
    lim = 0
    while np_ * (lim + 1) ** 2 <= INT32_MAX:
        lim += 1
    return lim


def format_matrix(values, rows, cols, indent="    "):
    if not values:
        return ""
    width = max(len(str(v)) for v in values)
    return ",\n".join(
        indent + ", ".join(f"{values[i * cols + j]:>{width}d}" for j in range(cols))
        for i in range(rows))


def print_matrix(ctype, name, rows, cols, values, size_expr, interleaved):
    section = (' __attribute__((section(".xheep_data_interleaved")))'
               if interleaved else "")
    print(f"volatile {ctype} {name}[{size_expr}]{section} =")
    print("{")
    print(format_matrix(values, rows, cols))
    print("\n};")


def main():
    parser = argparse.ArgumentParser(
        description="Generate the STRELA doitgen dataset header.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("NR", type=int, nargs="?", default=25,
                        help="outer tensor dimension (default: 25, PolyBench SMALL)")
    parser.add_argument("NQ", type=int, nargs="?", default=20,
                        help="middle tensor dimension (default: 20, PolyBench SMALL)")
    parser.add_argument("NP", type=int, nargs="?", default=30,
                        help="contracted dimension, and the side of C4 "
                             "(default: 30, PolyBench SMALL)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    nr, nq, np_ = args.NR, args.NQ, args.NP
    rows = nr * nq          # the flattened row count the CGRA actually sees

    limit = min(ELEM_CAP, safe_elem_limit(np_))
    if limit < 1:
        sys.exit(f"error: NP={np_} leaves no element range that keeps "
                 f"A*C4 inside int32; lower NP")

    a = [random.randint(-limit, limit) for _ in range(rows * np_)]
    c4 = [random.randint(-limit, limit) for _ in range(np_ * np_)]

    s = matmul(a, c4, rows, np_, np_)

    lo, hi = min(s), max(s)
    if lo < INT32_MIN or hi > INT32_MAX:
        sys.exit(f"internal error: matSum range [{lo}, {hi}] does not fit "
                 f"int32 (bug in safe_elem_limit for NP={np_})")

    print("#include <stdint.h>")
    print("")
    print(f"#define NR {nr}")
    print(f"#define NQ {nq}")
    print(f"#define NP {np_}")
    print("")
    print("/* A is (NR x NQ x NP) row-major, so its last axis is contiguous and")
    print(" * the tensor is an NROWS x NP matrix with no copy: one (r, q) pair")
    print(" * per row. */")
    print("#define NROWS (NR*NQ)")
    print("")
    print(f"/* elements in [{-limit}, {limit}] */")
    print("")

    print_matrix("int32_t", "matA", rows, np_, a, "NROWS*NP", True)
    print("")
    print_matrix("int32_t", "matC4", np_, np_, c4, "NP*NP", True)
    print("")
    print_matrix("int32_t", "matSum", rows, np_, [0] * (rows * np_),
                 "NROWS*NP", False)
    print("")
    print_matrix("int32_t", "matSum_expected", rows, np_, s, "NROWS*NP", False)


if __name__ == "__main__":
    main()
