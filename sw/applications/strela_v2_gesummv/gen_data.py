#!/usr/bin/env python3
"""Generate the STRELA gesummv test data header (PolyBench gesummv, int32).

    y = alpha * (A @ x) + beta * (B @ x)          A, B: M x N,  x: N,  y: M

split over the two committed HV bitstreams the way PolyBench itself writes it --
the two matrix-vector products first, the scaling and the sum last:

    vec_ax = A @ x                   <- kernel matvec     (gesummv_1_hv)
    vec_bx = B @ x                   <- kernel matvec     (gesummv_1_hv, again)
    vec_y  = alpha*vec_ax + beta*vec_bx  <- kernel scale_add (gesummv_2_hv)

Keeping alpha and beta out of the matrix-vector kernel is what lets both
products run the *same* configuration of the fabric (see gen_descriptors.py):
the scalars are applied once, at the end, by the four `mul` PEs of scale_add.

Shapes. gesummv_1_hv has four independent accumulator lanes, so a pass consumes
four rows of a matrix at a time and M has to be a multiple of 4. PolyBench SMALL
is 90, which is not, so the arrays are allocated with GESUMMV_M_PAD rows (M
rounded up), the added rows of A and B are zeroed, and the expected values cover
the padding too -- a padded row reduces to 0, so `vec_y` must be exactly zero
there and the check stays a check.

vec_ax and vec_bx are hand-offs between kernels: written to memory by one
phase's OSEs and streamed back in by the next phase's ISEs, which is why they
carry the interleaved-section attribute like the arrays the CGRA reads from the
start.

The reference is computed with plain Python integers in the fabric's own operand
order and then range-checked against int32, so a mismatch in simulation means
the hardware is wrong rather than the golden having silently wrapped.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_v2_gesummv` runs both with no arguments, so change the two together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

ROWS = 4           # accumulator lanes of gesummv_1_hv = rows consumed per group

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaV2MemDepth, rtl/strela_v2_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela_v2.h)
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
        description="Generate the STRELA gesummv test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
M needs no particular shape: it is rounded up to a multiple of 4 (the fabric's
accumulator lanes) and the padding rows are zeroed, so the kernel runs on
M_PAD rows and the last M_PAD-M results are zero by construction.
""")
    parser.add_argument("M", type=int, nargs="?", default=90,
                        help="rows of A and B, i.e. length of y (default: 90, "
                             "PolyBench SMALL)")
    parser.add_argument("-n", "--cols", type=int, default=90,
                        help="columns of A and B, i.e. the reduction length "
                             "and the accumulators' delay_value (default: 90, "
                             "PolyBench SMALL)")
    parser.add_argument("--alpha", type=int, default=3,
                        help="scalar on A@x, a PE constant of scale_add "
                             "(default: 3)")
    parser.add_argument("--beta", type=int, default=5,
                        help="scalar on B@x, a PE constant of scale_add "
                             "(default: 5)")
    parser.add_argument("--range", type=int, default=100,
                        help="max |element| of A, B and x (default: 100)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    m, n = args.M, args.cols
    alpha, beta, rng = args.alpha, args.beta, args.range

    if m < 1:
        sys.exit(f"error: M={m} must be at least 1")
    if n < 1:
        sys.exit(f"error: --cols {n} must be at least 1")
    if rng < 1:
        sys.exit(f"error: --range {rng} must be at least 1")

    m_pad = -(-m // ROWS) * ROWS        # the fabric consumes four rows per group
    half = m_pad // 2                   # one lane of the scale-add kernel

    # x is preloaded into a 512-word scratchpad and replayed, so one *row* is
    # what has to fit -- not a matrix.
    if n > min(MAX_SIZE, MEM_DEPTH):
        sys.exit(f"error: x is {n} words, past the {min(MAX_SIZE, MEM_DEPTH)} a "
                 f"9-bit size field and a {MEM_DEPTH}-word scratchpad allow; "
                 "lower --cols")
    if n > MAX_DELAY:
        sys.exit(f"error: --cols {n} exceeds the {MAX_DELAY} an accumulator's "
                 "16-bit delay_value holds")
    if half * 4 > MAX_BYTES:
        sys.exit(f"error: a scale-add lane streams {half} words = {half * 4} "
                 f"bytes, past the {MAX_BYTES} a descriptor holds; lower M")

    if args.seed is not None:
        random.seed(args.seed)

    # Padding rows are zero, so they contribute nothing to y and the expected
    # values below stay exact for the whole padded shape.
    def matrix():
        return [random.randint(-rng, rng) if i < m * n else 0
                for i in range(m_pad * n)]

    mat_a = matrix()
    mat_b = matrix()
    vec_x = [random.randint(-rng, rng) for _ in range(n)]

    # Worst case in the fabric's own operand order: each product accumulates N
    # terms, then the two reductions are scaled and summed.
    bound = (abs(alpha) + abs(beta)) * rng * rng * n
    if bound > INT32_MAX:
        sys.exit(f"error: |y| can reach {bound}, past int32; lower --range, "
                 f"--cols, --alpha or --beta")

    vec_ax, vec_bx = [], []
    for i in range(m_pad):
        vec_ax.append(sum(mat_a[i * n + j] * vec_x[j] for j in range(n)))
        vec_bx.append(sum(mat_b[i * n + j] * vec_x[j] for j in range(n)))
    vec_y = [alpha * vec_ax[i] + beta * vec_bx[i] for i in range(m_pad)]

    span = max(abs(v) for v in vec_ax + vec_bx + vec_y)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define GESUMMV_M       {m}   /* rows of A and B = length of y */")
    print(f"#define GESUMMV_N       {n}   /* columns = reduction length = delay_value */")
    print(f"#define GESUMMV_M_PAD   {m_pad}   /* M rounded up to the fabric's {ROWS} lanes */")
    print(f"#define GESUMMV_HALF    {half}   /* = GESUMMV_M_PAD/2, one scale-add lane */")
    print(f"#define GESUMMV_ALPHA   {alpha}   /* PE constant on scale_add's A@x muls */")
    print(f"#define GESUMMV_BETA    {beta}   /* PE constant on scale_add's B@x muls */")
    print("")
    print(f"/* Elements in [-{rng}, {rng}], max |result| = {span}; rows "
          f"{m}..{m_pad - 1} of A and B are zero padding. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "GESUMMV_M_PAD * GESUMMV_N",
               interleaved=True)
    print("")
    emit_array("int32_t", "mat_b", mat_b, "GESUMMV_M_PAD * GESUMMV_N",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_x", vec_x, "GESUMMV_N", interleaved=True)
    print("")

    # Written by the matvec phase's OSEs, read back by the scale-add phase's
    # ISEs, so these are interleaved too.
    emit_array("int32_t", "vec_ax", [0] * m_pad, "GESUMMV_M_PAD",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_bx", [0] * m_pad, "GESUMMV_M_PAD",
               interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_y", [0] * m_pad, "GESUMMV_M_PAD")
    print("")
    emit_array("int32_t", "vec_ax_expected", vec_ax, "GESUMMV_M_PAD")
    print("")
    emit_array("int32_t", "vec_bx_expected", vec_bx, "GESUMMV_M_PAD")
    print("")
    emit_array("int32_t", "vec_y_expected", vec_y, "GESUMMV_M_PAD")
    print("")


if __name__ == "__main__":
    main()
