#!/usr/bin/env python3
"""Generate the STRELA 3mm dataset (PolyBench 3mm, int32).

PolyBench computes three chained products, which this app runs as three phases
of one STRELA execution:

    matE = matA(NI x NK) * matB(NK x NJ)      <- kernel mm_e (3mm_hv)
    matF = matC(NJ x NM) * matD(NM x NL)      <- kernel mm_f (3mm_hv)
    matG = matE(NI x NJ) * matF(NJ x NL)      <- kernel mm_g (3mm_hv)

matE and matF are hand-offs between kernels: written to system memory by one
phase's OSEs and streamed back in by a later phase's ISEs, which is why they
carry the interleaved-section attribute like the other arrays the CGRA reads.

Row padding. The fabric consumes four rows of the left operand at a time, so
every phase needs a row count that is a multiple of 4. NI is, but matC has NJ
rows (50 by default), so matC and matF are allocated with their row count
rounded up to NJ_PAD and the added rows of matC zeroed. Those rows cost one
extra pass of phase 1 and produce zero rows of matF that phase 2 never reads --
it reduces over the first NJ rows only. Keep this in step with
gen_descriptors.py, which derives NJ_PAD the same way.

The reference is computed with plain Python integers and then range-checked
against int32, so a mismatch in simulation means the fabric is wrong rather
than the golden having silently wrapped.

The defaults match cpu_threemm's (PolyBench 4.2.1 SMALL_DATASET), so the two
apps are directly comparable; `make gen-app-data PROJECT=strela_3mm` runs this
and gen_descriptors.py with no arguments, so change the dimensions in both
together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1
INT32_MIN = -(1 << 31)

ROWS = 4               # the STRELA shell hard-codes a 4x4 fabric
ELEM_CAP = 50          # readability cap, tightened when the shape makes it unsafe


def matmul(a, b, ni, nk, nj):
    out = [0] * (ni * nj)
    for i in range(ni):
        for k in range(nk):
            aik = a[i * nk + k]
            for j in range(nj):
                out[i * nj + j] += aik * b[k * nj + j]
    return out


def safe_elem_limit(nj, nk, nm):
    """Largest |v| such that |matG| = NJ*NK*NM*v^4 fits int32.

    Each matG element sums NJ products of a matE element (NK products of two
    elements) and a matF element (NM products), so that fourth-power bound
    dominates the two intermediates as well.
    """
    lim = 0
    while nj * nk * nm * (lim + 1) ** 4 <= INT32_MAX:
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
        description="Generate the STRELA 3mm dataset header.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("NI", type=int, nargs="?", default=40,
                        help="rows of A, E and G (default: 40, PolyBench SMALL)")
    parser.add_argument("NJ", type=int, nargs="?", default=50,
                        help="cols of B / rows of C and F (default: 50)")
    parser.add_argument("NK", type=int, nargs="?", default=60,
                        help="cols of A / rows of B (default: 60)")
    parser.add_argument("NL", type=int, nargs="?", default=70,
                        help="cols of D, F and G (default: 70)")
    parser.add_argument("NM", type=int, nargs="?", default=80,
                        help="cols of C / rows of D (default: 80)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ni, nj, nk, nl, nm = args.NI, args.NJ, args.NK, args.NL, args.NM
    nj_pad = -(-nj // ROWS) * ROWS      # rows of matC/matF, rounded up to 4

    limit = min(ELEM_CAP, safe_elem_limit(nj, nk, nm))
    if limit < 1:
        sys.exit(f"error: NJ={nj}, NK={nk}, NM={nm} leave no element range that "
                 f"keeps (A*B)*(C*D) inside int32; lower one of them")

    a = [random.randint(-limit, limit) for _ in range(ni * nk)]
    b = [random.randint(-limit, limit) for _ in range(nk * nj)]
    # The padding rows of C are zero, so the padding rows of F are zero too --
    # deterministic, and checkable against the golden like every other row.
    c = ([random.randint(-limit, limit) for _ in range(nj * nm)]
         + [0] * ((nj_pad - nj) * nm))
    d = [random.randint(-limit, limit) for _ in range(nm * nl)]

    e = matmul(a, b, ni, nk, nj)
    f = matmul(c, d, nj_pad, nm, nl)
    g = matmul(e, f, ni, nj, nl)

    for name, values in (("matE", e), ("matF", f), ("matG", g)):
        lo, hi = min(values), max(values)
        if lo < INT32_MIN or hi > INT32_MAX:
            sys.exit(f"internal error: {name} range [{lo}, {hi}] does not fit "
                     f"int32 (bug in safe_elem_limit for NJ={nj}, NK={nk}, "
                     f"NM={nm})")

    print("#include <stdint.h>")
    print("")
    print(f"#define NI {ni}")
    print(f"#define NJ {nj}")
    print(f"#define NK {nk}")
    print(f"#define NL {nl}")
    print(f"#define NM {nm}")
    print("")
    print(f"/* Rows of matC and matF, rounded up to the fabric's {ROWS}-row")
    print(" * group; the added rows of matC are zero and matG never reads the")
    print(" * matching rows of matF. */")
    print(f"#define NJ_PAD {nj_pad}")
    print("")
    print(f"/* elements in [{-limit}, {limit}] */")
    print("")

    print_matrix("int32_t", "matA", ni, nk, a, "NI*NK", True)
    print("")
    print_matrix("int32_t", "matB", nk, nj, b, "NK*NJ", True)
    print("")
    print_matrix("int32_t", "matC", nj_pad, nm, c, "NJ_PAD*NM", True)
    print("")
    print_matrix("int32_t", "matD", nm, nl, d, "NM*NL", True)
    print("")
    # Written by phase 0's OSEs, read back by phase 2's ISEs.
    print_matrix("int32_t", "matE", ni, nj, [0] * (ni * nj), "NI*NJ", True)
    print("")
    # Written by phase 1's OSEs, read back by phase 2's ISEs.
    print_matrix("int32_t", "matF", nj_pad, nl, [0] * (nj_pad * nl),
                 "NJ_PAD*NL", True)
    print("")
    print_matrix("int32_t", "matG", ni, nl, [0] * (ni * nl), "NI*NL", False)
    print("")
    print_matrix("int32_t", "matE_expected", ni, nj, e, "NI*NJ", False)
    print("")
    print_matrix("int32_t", "matF_expected", nj_pad, nl, f, "NJ_PAD*NL", False)
    print("")
    print_matrix("int32_t", "matG_expected", ni, nl, g, "NI*NL", False)


if __name__ == "__main__":
    main()
