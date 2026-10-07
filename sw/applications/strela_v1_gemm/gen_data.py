#!/usr/bin/env python3
"""Generate the STRELA v1 GEMM dataset (PolyBench gemm, int32).

    matAB = matA(NI x NK) * matB(NK x NJ)          <- kernel mm
    matD  = alpha * matAB + beta * matC            <- kernel gemm_2

The same split as strela_v2_gemm (STRELA v2), on elastic-cgra's 4x4 solves of the
same two DFGs. STRELA v1 has no descriptor tables: main.c programs the memory
nodes and starts the fabric once per 3-row x 1-column block of matAB, then once
more for the whole of phase 2. matAB is the hand-off between the two phases,
written by the output nodes of the first and streamed back by the input nodes
of the second, which is why it carries the interleaved-section attribute like
the other arrays the CGRA reads.

The reference is computed with plain Python integers and then range-checked
against int32, so a mismatch in simulation means the fabric is wrong rather
than the golden having silently wrapped. As in strela_v2_gemm, the result goes to
its own array rather than accumulating over C in place.
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
        description="Generate the STRELA v1 GEMM dataset header.",
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

    # The mm kernel reduces three rows of A at once, and a lane cannot be left
    # idle: B's column is forked to all three multipliers, so a row with no
    # data would stall the other two.
    if ni % 3:
        sys.exit(f"error: NI={ni} must be a multiple of 3, the rows one mm "
                 f"run reduces")
    # A memory node's size field is the 16-bit byte span of its stream; the
    # widest one is B's column, NK elements NJ words apart.
    for what, span in (("a column of B", 4 * nj * nk),
                       ("half of matAB", 4 * ((ni * nj + 1) // 2))):
        if span > 0xFFFF:
            sys.exit(f"error: {what} spans {span} bytes, past the 16-bit "
                     f"memory node size field")

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
    # Written by phase 1's output nodes, read back by phase 2's input nodes.
    print_matrix("int32_t", "matAB", ni, nj, [0] * (ni * nj), "NI*NJ", True)
    print("")
    # Checked too: in a chain, the intermediate is what places a failure.
    print_matrix("int32_t", "matAB_expected", ni, nj, ab, "NI*NJ", False)
    print("")
    print_matrix("int32_t", "matD", ni, nj, [0] * (ni * nj), "NI*NJ", False)
    print("")
    print_matrix("int32_t", "matD_expected", ni, nj, d, "NI*NJ", False)


if __name__ == "__main__":
    main()
