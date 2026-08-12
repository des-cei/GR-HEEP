#!/usr/bin/env python3
"""Generate the STRELA 2mm dataset (PolyBench 2mm, int32).

PolyBench computes `matD := alpha*matA*matB*matC + beta*matD`, which this app
splits over three chained kernels:

    matAB  = matA(NI x NK) * matB(NK x NJ)         <- kernel mm_ab     (2mm_1_hv)
    matABC = matAB(NI x NJ) * matC(NJ x NL)        <- kernel mm_abc    (2mm_1_hv)
    matD   = alpha * matABC + beta * matDin        <- kernel scale_add (2mm_2_hv)

PolyBench folds alpha into the first product (`tmp := alpha*A*B`) and scales D
in place; alpha is a scalar, so pulling it out to the last kernel gives the same
result and keeps both matmul phases running the unmodified mm bitstream.

matAB and matABC are the hand-offs between kernels: written to system memory by
one phase's OSEs and streamed back in by the next phase's ISEs, which is why
they carry the interleaved-section attribute like the other arrays the CGRA
reads. matDin is PolyBench's initial D, kept separate from the result because
within a pass the ISE reading an element and the OSE writing it are not ordered
with respect to each other.

The reference is computed with plain Python integers and then range-checked
against int32, so a mismatch in simulation means the fabric is wrong rather than
the golden having silently wrapped.

The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_2mm` runs both with no arguments, so change NI/NJ/NK/NL in both
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
ELEM_CAP = 50          # readability cap, tightened when NK*NJ makes it unsafe


def matmul(a, b, ni, nk, nj):
    out = [0] * (ni * nj)
    for i in range(ni):
        for k in range(nk):
            aik = a[i * nk + k]
            for j in range(nj):
                out[i * nj + j] += aik * b[k * nj + j]
    return out


def safe_elem_limit(nk, nj):
    """Largest |v| such that |alpha|*(NK*NJ*v^3) + |beta|*v fits int32.

    NK*NJ*v^3 bounds |matABC| (each of its NJ terms is a dot product of NK
    products), which also bounds the smaller |matAB| = NK*v^2.
    """
    lim = 0
    while (SCALE_CAP * (nk * nj * (lim + 1) ** 3)
           + SCALE_CAP * (lim + 1)) <= INT32_MAX:
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
        description="Generate the STRELA 2mm dataset header.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("NI", type=int, nargs="?", default=40,
                        help="rows of A, AB, ABC and D (default: 40, PolyBench SMALL)")
    parser.add_argument("NJ", type=int, nargs="?", default=50,
                        help="cols of B / rows of C (default: 50, PolyBench SMALL)")
    parser.add_argument("NK", type=int, nargs="?", default=70,
                        help="cols of A / rows of B (default: 70, PolyBench SMALL)")
    parser.add_argument("NL", type=int, nargs="?", default=80,
                        help="cols of C and D (default: 80, PolyBench SMALL)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ni, nj, nk, nl = args.NI, args.NJ, args.NK, args.NL

    limit = min(ELEM_CAP, safe_elem_limit(nk, nj))
    if limit < 1:
        sys.exit(f"error: NK={nk}, NJ={nj} leave no element range that keeps "
                 f"alpha*(A*B*C) + beta*D inside int32; lower NK/NJ or SCALE_CAP")

    scales = [v for v in range(-SCALE_CAP, SCALE_CAP + 1) if v]
    alpha = random.choice(scales)
    beta = random.choice(scales)
    a = [random.randint(-limit, limit) for _ in range(ni * nk)]
    b = [random.randint(-limit, limit) for _ in range(nk * nj)]
    c = [random.randint(-limit, limit) for _ in range(nj * nl)]
    din = [random.randint(-limit, limit) for _ in range(ni * nl)]

    ab = matmul(a, b, ni, nk, nj)
    abc = matmul(ab, c, ni, nj, nl)
    d = [alpha * abc[i] + beta * din[i] for i in range(ni * nl)]

    for name, values in (("matAB", ab), ("matABC", abc), ("matD", d)):
        lo, hi = min(values), max(values)
        if lo < INT32_MIN or hi > INT32_MAX:
            sys.exit(f"internal error: {name} range [{lo}, {hi}] does not fit "
                     f"int32 (bug in safe_elem_limit for NK={nk}, NJ={nj})")

    print("#include <stdint.h>")
    print("")
    print(f"#define NI {ni}")
    print(f"#define NJ {nj}")
    print(f"#define NK {nk}")
    print(f"#define NL {nl}")
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
    print_matrix("int32_t", "matC", nj, nl, c, "NJ*NL", True)
    print("")
    print_matrix("int32_t", "matDin", ni, nl, din, "NI*NL", True)
    print("")
    # Written by phase 0's OSEs, read back by phase 1's ISEs.
    print_matrix("int32_t", "matAB", ni, nj, [0] * (ni * nj), "NI*NJ", True)
    print("")
    # Written by phase 1's OSEs, read back by phase 2's ISEs.
    print_matrix("int32_t", "matABC", ni, nl, [0] * (ni * nl), "NI*NL", True)
    print("")
    print_matrix("int32_t", "matD", ni, nl, [0] * (ni * nl), "NI*NL", False)
    print("")
    print_matrix("int32_t", "matAB_expected", ni, nj, ab, "NI*NJ", False)
    print("")
    print_matrix("int32_t", "matABC_expected", ni, nl, abc, "NI*NL", False)
    print("")
    print_matrix("int32_t", "matD_expected", ni, nl, d, "NI*NL", False)


if __name__ == "__main__":
    main()
