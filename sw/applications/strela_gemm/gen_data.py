#!/usr/bin/env python3
"""Generate the STRELA GEMM dataset (PolyBench gemm, int32).

    matAB = matA(NI x NK) * matB(NK x NJ)          <- kernel gemm_1_hv
    matD  = alpha * matAB + beta * matC            <- kernel gemm_2_hv

matAB is the hand-off between the two kernels: written to system memory by the
OSEs of phase 1 and streamed back in by the ISEs of phase 2, which is why it
carries the interleaved-section attribute like the other arrays the CGRA reads.

The reference is computed with plain Python integers and then range-checked
against int32, so a mismatch in simulation means the fabric is wrong rather
than the golden having silently wrapped. Deviation from PolyBench, which
accumulates in place over C: the result goes to its own array, because within a
pass the ISE reading matC and the OSE writing the same element are not ordered
with respect to each other.

The default must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_gemm` runs both with no arguments, so change NI/NJ/NK in both
together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1
INT32_MIN = -(1 << 31)

# alpha and beta are drawn from [-SCALE_CAP, SCALE_CAP] \ {0}; the element
# limit below is derived from this, so raising one lowers the other.
SCALE_CAP = 16
ELEM_CAP = 50          # readability cap, tightened when NK makes it unsafe


def matmul(a, b, ni, nk, nj):
    out = [0] * (ni * nj)
    for i in range(ni):
        for k in range(nk):
            aik = a[i * nk + k]
            for j in range(nj):
                out[i * nj + j] += aik * b[k * nj + j]
    return out


def safe_elem_limit(nk):
    """Largest |v| such that |alpha| * (NK * v * v) + |beta| * v fits int32."""
    lim = 0
    while SCALE_CAP * (nk * (lim + 1) ** 2) + SCALE_CAP * (lim + 1) <= INT32_MAX:
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
        description="Generate the STRELA GEMM dataset header.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("NI", type=int, nargs="?", default=60,
                        help="rows of A, C and D (default: 60, PolyBench SMALL)")
    parser.add_argument("NJ", type=int, nargs="?", default=70,
                        help="cols of B, C and D (default: 70, PolyBench SMALL)")
    parser.add_argument("NK", type=int, nargs="?", default=80,
                        help="cols of A / rows of B (default: 80, PolyBench SMALL)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ni, nj, nk = args.NI, args.NJ, args.NK

    limit = min(ELEM_CAP, safe_elem_limit(nk))
    if limit < 1:
        sys.exit(f"error: NK={nk} leaves no element range that keeps "
                 f"alpha*(A*B) + beta*C inside int32; lower NK or SCALE_CAP")

    scales = [v for v in range(-SCALE_CAP, SCALE_CAP + 1) if v]
    alpha = random.choice(scales)
    beta = random.choice(scales)
    a = [random.randint(-limit, limit) for _ in range(ni * nk)]
    b = [random.randint(-limit, limit) for _ in range(nk * nj)]
    c = [random.randint(-limit, limit) for _ in range(ni * nj)]

    ab = matmul(a, b, ni, nk, nj)
    d = [alpha * ab[i] + beta * c[i] for i in range(ni * nj)]

    for name, values in (("matAB", ab), ("matD", d)):
        lo, hi = min(values), max(values)
        if lo < INT32_MIN or hi > INT32_MAX:
            sys.exit(f"internal error: {name} range [{lo}, {hi}] does not fit "
                     f"int32 (bug in safe_elem_limit for NK={nk})")

    print("#include <stdint.h>")
    print("")
    print(f"#define NI {ni}")
    print(f"#define NJ {nj}")
    print(f"#define NK {nk}")
    print("")
    print(f"/* elements in [{-limit}, {limit}], scalars in "
          f"[{-SCALE_CAP}, {SCALE_CAP}] \\ {{0}} */")
    print(f"volatile int32_t alpha = {alpha};")
    print(f"volatile int32_t beta  = {beta};")
    print("")

    print_matrix("int32_t", "matA", ni, nk, a, "NI*NK", True)
    print("")
    print_matrix("int32_t", "matB", nk, nj, b, "NK*NJ", True)
    print("")
    print_matrix("int32_t", "matC", ni, nj, c, "NI*NJ", True)
    print("")
    # Written by phase 1's OSEs, read back by phase 2's ISEs.
    print_matrix("int32_t", "matAB", ni, nj, [0] * (ni * nj), "NI*NJ", True)
    print("")
    print_matrix("int32_t", "matD", ni, nj, [0] * (ni * nj), "NI*NJ", False)
    print("")
    print_matrix("int32_t", "matD_expected", ni, nj, d, "NI*NJ", False)


if __name__ == "__main__":
    main()
