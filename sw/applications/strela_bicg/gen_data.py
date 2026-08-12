#!/usr/bin/env python3
"""Generate the STRELA bicg test data header (PolyBench bicg, int32).

BiCG's two matrix-vector products over the *same* matrix, one in each direction:

    q = A  @ p                       A: N x M,  p: M,  q: N
    s = A^T @ r                                 r: N,  s: M

    <- kernel matvec_q  (bicg_hv)    <- kernel matvec_s  (bicg_hv, again)

Unlike strela_atax -- which runs the identical pair of products, but chained,
its second consuming the first's output -- bicg's two products are completely
independent: both read A and a vector that is an input of the kernel, and
neither waits on the other's result. They are still two *phases* rather than two
sets of passes, because their reduction lengths differ (M for q, N for s) and
the reduction length is the accumulators' `delay_value`, which lives inside the
bitstream. See gen_descriptors.py.

Shapes. The kernel has four independent accumulator lanes, so a pass consumes
four lines of the matrix at a time: N must be a multiple of 4 for q (whose lines
are rows of A) and M for s (whose lines are columns). PolyBench SMALL is
M=116, N=124, and although both happen to be multiples of 4 the arrays are still
allocated at BICG_N_PAD x BICG_M_PAD so any shape works. Padding is all zeros --
padded rows and columns of A, padded entries of p and r -- so a padded line
reduces to exactly 0 and the expected values below cover the padded shape:
q[i] = 0 for i >= N and s[j] = 0 for j >= M, which keeps the check a check
instead of an exemption.

Both products reduce only once, so unlike atax the default --range can stay at
100; the bound is checked explicitly anyway because int32 would otherwise wrap
silently and read as a fabric bug.

The reference is computed with plain Python integers in the fabric's own operand
order, so a mismatch in simulation means the hardware is wrong rather than the
golden having overflowed.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_bicg` runs both with no arguments, so change the two together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

ROWS = 4           # accumulator lanes of bicg_hv = matrix lines per group

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
        description="Generate the STRELA bicg test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Neither dimension needs a particular shape: both are rounded up to a multiple of
4 (the fabric's accumulator lanes) and the padding is zeroed, so the kernel runs
on N_PAD x M_PAD and the padded results are zero by construction. A is N x M, so
the two arguments are not interchangeable.
""")
    parser.add_argument("M", type=int, nargs="?", default=116,
                        help="columns of A = length of p and s (default: 116, "
                             "PolyBench SMALL)")
    parser.add_argument("N", type=int, nargs="?", default=124,
                        help="rows of A = length of r and q (default: 124, "
                             "PolyBench SMALL)")
    parser.add_argument("--range", type=int, default=100,
                        help="max |element| of A, p and r (default: 100)")
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

    # Each phase preloads one vector into a 512-word scratchpad: p (M words) for
    # q = A@p, r (N words) for s = A^T@r.
    limit = min(MAX_SIZE, MEM_DEPTH)
    for label, size in (("p", m_pad), ("r", n_pad)):
        if size > limit:
            sys.exit(f"error: {label} is {size} words, past the {limit} a 9-bit "
                     f"size field and a {MEM_DEPTH}-word scratchpad allow; "
                     "lower the corresponding dimension")
    if max(m_pad, n_pad) > MAX_DELAY:
        sys.exit(f"error: a reduction length of {max(m_pad, n_pad)} exceeds the "
                 f"{MAX_DELAY} an accumulator's 16-bit delay_value holds")
    # s streams *columns* of A: N_PAD elements a row apart, so one descriptor
    # covers the whole matrix span. This is the real size wall.
    if m_pad * n_pad * 4 > MAX_BYTES:
        sys.exit(f"error: a column of A spans {m_pad * n_pad * 4} bytes, past "
                 f"the {MAX_BYTES} a 16-bit byte count holds; lower M or N "
                 "(gen_descriptors.py splits the column across descriptors, but "
                 "keep the two generators in step)")

    if args.seed is not None:
        random.seed(args.seed)

    # Zero padding in both directions: rows n.. and columns m.. of A.
    mat_a = [random.randint(-rng, rng) if (i // m_pad) < n and (i % m_pad) < m
             else 0
             for i in range(n_pad * m_pad)]
    vec_p = [random.randint(-rng, rng) if j < m else 0 for j in range(m_pad)]
    vec_r = [random.randint(-rng, rng) if i < n else 0 for i in range(n_pad)]

    bound = max(m, n) * rng * rng
    if bound > INT32_MAX:
        sys.exit(f"error: |q| or |s| can reach {bound}, past int32; lower "
                 "--range or the dimensions")

    vec_q = [sum(mat_a[i * m_pad + j] * vec_p[j] for j in range(m_pad))
             for i in range(n_pad)]
    vec_s = [sum(mat_a[i * m_pad + j] * vec_r[i] for i in range(n_pad))
             for j in range(m_pad)]

    span = max(abs(v) for v in vec_q + vec_s)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define BICG_M       {m}   /* columns of A = length of p and s */")
    print(f"#define BICG_N       {n}   /* rows of A = length of r and q */")
    print(f"#define BICG_M_PAD   {m_pad}   /* M rounded up to the fabric's {ROWS} lanes */")
    print(f"#define BICG_N_PAD   {n_pad}   /* N rounded up to the fabric's {ROWS} lanes */")
    print("")
    pad_note = ", ".join(
        f"{label} {lo}..{hi - 1} of A are zero padding"
        for label, lo, hi in (("rows", n, n_pad), ("columns", m, m_pad))
        if hi > lo) or "no padding needed"
    print(f"/* Elements in [-{rng}, {rng}], max |result| = {span}; {pad_note}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "BICG_N_PAD * BICG_M_PAD",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_p", vec_p, "BICG_M_PAD", interleaved=True)
    print("")
    emit_array("int32_t", "vec_r", vec_r, "BICG_N_PAD", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_q", [0] * n_pad, "BICG_N_PAD")
    print("")
    emit_array("int32_t", "vec_s", [0] * m_pad, "BICG_M_PAD")
    print("")
    emit_array("int32_t", "vec_q_expected", vec_q, "BICG_N_PAD")
    print("")
    emit_array("int32_t", "vec_s_expected", vec_s, "BICG_M_PAD")
    print("")


if __name__ == "__main__":
    main()
