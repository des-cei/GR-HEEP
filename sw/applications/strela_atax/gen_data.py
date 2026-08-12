#!/usr/bin/env python3
"""Generate the STRELA atax test data header (PolyBench atax, int32).

    y = A^T @ (A @ x)                A: M x N,  x: N,  y: N,  tmp: M

which PolyBench itself writes as the two products

    tmp = A  @ x                     <- kernel matvec_tmp  (atax_hv)
    y   = A^T @ tmp                  <- kernel matvec_y    (atax_hv, again)

Both products run the *same* bitstream -- one matrix-vector kernel -- but they
are two phases rather than two sets of passes, because their reduction lengths
differ (N for tmp, M for y) and the reduction length *is* the accumulators'
`delay_value`, which lives inside the bitstream. That is why two copies of the
same regress bitstream are committed, one patched per phase. Contrast
strela_gesummv, whose two products reduce over the same N and therefore share a
single configuration. See gen_descriptors.py.

Shapes. The kernel has four independent accumulator lanes, so a pass consumes
four lines of the matrix at a time: M must be a multiple of 4 for the first
product (whose lines are rows of A) and N for the second (whose lines are
columns of A). PolyBench SMALL is 116x124, and although both happen to be
multiples of 4 the arrays are still allocated at ATAX_M_PAD x ATAX_N_PAD so any
shape works. Padding is all zeros -- padded rows/columns of A, padded entries of
x -- so a padded line reduces to exactly 0 and the expected values below cover
the padded shape: tmp[i] = 0 for i >= M and y[j] = 0 for j >= N, which keeps the
check a check instead of an exemption.

tmp is the hand-off between the two phases: written to memory by phase 0's OSEs
and read back by phase 1's ISE as a scratchpad preload, so it carries the
interleaved-section attribute like the arrays the CGRA reads from the start.

Range. atax accumulates *twice* -- y sums M terms each of which already sums N
terms -- so the worst case grows as M*N*range^3, four orders of magnitude faster
than a single matrix-vector product. That is why the default --range is 40 here
and 100 in the single-reduction apps; the bound is checked explicitly below
because int32 would otherwise wrap silently and read as a fabric bug.

The reference is computed with plain Python integers in the fabric's own operand
order, so a mismatch in simulation means the hardware is wrong rather than the
golden having overflowed.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_atax` runs both with no arguments, so change the two together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

ROWS = 4           # accumulator lanes of atax_hv = matrix lines per group

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count
MAX_DELAY = 65535  # set_pe_delay_value writes bits 31:16 of PE word 2


def emit_array(ctype, name, values, size_expr, per_line=8, interleaved=False):
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
        description="Generate the STRELA atax test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Neither dimension needs a particular shape: both are rounded up to a multiple of
4 (the fabric's accumulator lanes) and the padding is zeroed, so the kernel runs
on M_PAD x N_PAD and the padded results are zero by construction.
""")
    parser.add_argument("M", type=int, nargs="?", default=116,
                        help="rows of A = length of tmp (default: 116, "
                             "PolyBench SMALL)")
    parser.add_argument("N", type=int, nargs="?", default=124,
                        help="columns of A = length of x and y (default: 124, "
                             "PolyBench SMALL)")
    parser.add_argument("--range", type=int, default=40,
                        help="max |element| of A and x (default: 40; atax "
                             "reduces twice, so this cubes)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    m, n, rng = args.M, args.N, args.range

    if m < 1:
        sys.exit(f"error: M={m} must be at least 1")
    if n < 1:
        sys.exit(f"error: N={n} must be at least 1")
    if rng < 1:
        sys.exit(f"error: --range {rng} must be at least 1")

    m_pad = -(-m // ROWS) * ROWS
    n_pad = -(-n // ROWS) * ROWS

    # Each phase preloads one vector into a 512-word scratchpad: x (N words) for
    # tmp = A@x, tmp (M words) for y = A^T@tmp.
    limit = min(MAX_SIZE, MEM_DEPTH)
    for label, size in (("x", n_pad), ("tmp", m_pad)):
        if size > limit:
            sys.exit(f"error: {label} is {size} words, past the {limit} a 9-bit "
                     f"size field and a {MEM_DEPTH}-word scratchpad allow; "
                     "lower the corresponding dimension")
    if max(m_pad, n_pad) > MAX_DELAY:
        sys.exit(f"error: a reduction length of {max(m_pad, n_pad)} exceeds the "
                 f"{MAX_DELAY} an accumulator's 16-bit delay_value holds")
    # The second product streams *columns* of A: M_PAD elements a row apart, so
    # one descriptor covers the whole matrix span. This is the real size wall.
    if m_pad * n_pad * 4 > MAX_BYTES:
        sys.exit(f"error: a column of A spans {m_pad * n_pad * 4} bytes, past "
                 f"the {MAX_BYTES} a 16-bit byte count holds; lower M or N "
                 "(gen_descriptors.py splits the column across descriptors, but "
                 "keep the two generators in step)")

    if args.seed is not None:
        random.seed(args.seed)

    # Zero padding in both directions: rows m.. and columns n.. of A.
    mat_a = [random.randint(-rng, rng) if (i // n_pad) < m and (i % n_pad) < n
             else 0
             for i in range(m_pad * n_pad)]
    vec_x = [random.randint(-rng, rng) if j < n else 0 for j in range(n_pad)]

    # Worst case: |tmp| <= N*rng^2, then |y| <= M*rng*|tmp|.
    bound = m * n * rng ** 3
    if bound > INT32_MAX:
        sys.exit(f"error: |y| can reach {bound}, past int32; lower --range "
                 f"(<= {int((INT32_MAX / (m * n)) ** (1 / 3))} for {m}x{n}) or "
                 "the dimensions")

    vec_tmp = [sum(mat_a[i * n_pad + j] * vec_x[j] for j in range(n_pad))
               for i in range(m_pad)]
    vec_y = [sum(mat_a[i * n_pad + j] * vec_tmp[i] for i in range(m_pad))
             for j in range(n_pad)]

    span = max(abs(v) for v in vec_tmp + vec_y)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define ATAX_M       {m}   /* rows of A = length of tmp */")
    print(f"#define ATAX_N       {n}   /* columns of A = length of x and y */")
    print(f"#define ATAX_M_PAD   {m_pad}   /* M rounded up to the fabric's {ROWS} lanes */")
    print(f"#define ATAX_N_PAD   {n_pad}   /* N rounded up to the fabric's {ROWS} lanes */")
    print("")
    pad_note = ", ".join(
        f"{label} {lo}..{hi - 1} of A are zero padding"
        for label, lo, hi in (("rows", m, m_pad), ("columns", n, n_pad))
        if hi > lo) or "no padding needed"
    print(f"/* Elements in [-{rng}, {rng}], max |result| = {span}; {pad_note}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "ATAX_M_PAD * ATAX_N_PAD",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_x", vec_x, "ATAX_N_PAD", interleaved=True)
    print("")

    # Written by phase 0's OSEs, preloaded into a scratchpad by phase 1's ISE,
    # so this one is interleaved too.
    emit_array("int32_t", "vec_tmp", [0] * m_pad, "ATAX_M_PAD", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_y", [0] * n_pad, "ATAX_N_PAD")
    print("")
    emit_array("int32_t", "vec_tmp_expected", vec_tmp, "ATAX_M_PAD")
    print("")
    emit_array("int32_t", "vec_y_expected", vec_y, "ATAX_N_PAD")
    print("")


if __name__ == "__main__":
    main()
