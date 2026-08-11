#!/usr/bin/env python3
"""Generate the STRELA gesummv test data header.

The fabric runs the PolyBench gesummv kernel

    y = alpha * (A @ x) + beta * (B @ x)

for a GESUMMV_M x GESUMMV_N pair of matrices. The DFG
(mapper/applications/gesummv_hv/main.dot) computes two output rows at a time:

    input4 = x        the shared vector, scratchpad-resident and replayed
    input0 = A rows [0, M/2)      input1 = A rows [M/2, M)
    input2 = B rows [0, M/2)      input3 = B rows [M/2, M)
    output0 = y[0, M/2)           output1 = y[M/2, M)

alpha and beta are PE constants (mul8/mul9), and the four accumulator PEs
(add0..add3) carry the reduction length as their delay_value. Both are patched
into the kernel array at runtime by main.c, so this generator's GESUMMV_N /
GESUMMV_ALPHA / GESUMMV_BETA are the single source of truth for all three.

The scaling order matters and is reproduced exactly below: the fabric forms
alpha*x[j] first (a PE constant multiply) and only then multiplies by A[i][j],
so the golden values use the same operand order. Everything is exact integer
arithmetic in int32, so these expected values are what the hardware must
produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_ITERS = 255    # mem_param iters is 8 bits
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
The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_gesummv` runs both with no arguments, so change the two
together. M must be even: the DFG produces two output rows per pass, and the
M/2 rows of each half are streamed through one accumulator chain.
""")
    parser.add_argument("M", type=int, nargs="?", default=8,
                        help="rows of A and B, i.e. length of y (default: 8)")
    parser.add_argument("-n", "--cols", type=int, default=16,
                        help="columns of A and B, i.e. the reduction length "
                             "and the accumulators' delay_value (default: 16)")
    parser.add_argument("--alpha", type=int, default=3,
                        help="scalar on A@x, a PE constant (default: 3)")
    parser.add_argument("--beta", type=int, default=5,
                        help="scalar on B@x, a PE constant (default: 5)")
    parser.add_argument("--range", type=int, default=100,
                        help="max |element| of A, B and x (default: 100)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    m, n = args.M, args.cols
    alpha, beta, rng = args.alpha, args.beta, args.range

    if m < 2 or m % 2:
        sys.exit(f"error: M={m} must be even and at least 2 (the DFG has two "
                 "accumulator chains, each taking M/2 rows)")
    if n < 1:
        sys.exit(f"error: --cols {n} must be at least 1")
    if rng < 1:
        sys.exit(f"error: --range {rng} must be at least 1")

    half = m // 2
    # Each half of A and of B is preloaded whole into one scratchpad.
    if half * n > MAX_SIZE or half * n > MEM_DEPTH:
        sys.exit(f"error: each scratchpad would hold M/2*N = {half * n} words, "
                 f"past the {min(MAX_SIZE, MEM_DEPTH)} a 9-bit size field and a "
                 f"{MEM_DEPTH}-word scratchpad allow; lower M or --cols")
    # x is replayed once per output row of a half.
    if half > MAX_ITERS:
        sys.exit(f"error: x would be replayed {half} times, past the "
                 f"{MAX_ITERS} an 8-bit iters field holds; lower M")
    if n > MAX_DELAY:
        sys.exit(f"error: --cols {n} exceeds the {MAX_DELAY} an accumulator's "
                 "16-bit delay_value holds")

    if args.seed is not None:
        random.seed(args.seed)

    mat_a = [random.randint(-rng, rng) for _ in range(m * n)]
    mat_b = [random.randint(-rng, rng) for _ in range(m * n)]
    vec_x = [random.randint(-rng, rng) for _ in range(n)]

    # Worst case, in the fabric's own operand order.
    bound = (alpha + beta) * rng * rng * n
    if bound > INT32_MAX:
        sys.exit(f"error: |y| can reach {bound}, past int32; lower --range, "
                 f"--cols, --alpha or --beta")

    # Same operand order as the DFG: the scalar scales x, then the matrix row
    # multiplies that product, then the two reductions are summed.
    ax = [alpha * v for v in vec_x]
    bx = [beta * v for v in vec_x]
    vec_y = []
    for i in range(m):
        acc_a = 0
        acc_b = 0
        for j in range(n):
            acc_a += mat_a[i * n + j] * ax[j]
            acc_b += mat_b[i * n + j] * bx[j]
        vec_y.append(acc_a + acc_b)

    span = max(abs(v) for v in vec_y)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define GESUMMV_M       {m}   /* rows of A and B = length of y */")
    print(f"#define GESUMMV_N       {n}  /* columns = reduction length = delay_value */")
    print(f"#define GESUMMV_HALF    {half}   /* = GESUMMV_M/2, rows per accumulator chain */")
    print(f"#define GESUMMV_ALPHA   {alpha}   /* PE constant on mul8 */")
    print(f"#define GESUMMV_BETA    {beta}   /* PE constant on mul9 */")
    print("")
    print(f"/* Elements in [-{rng}, {rng}], max |y| = {span}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "GESUMMV_M * GESUMMV_N",
               interleaved=True)
    print("")
    emit_array("int32_t", "mat_b", mat_b, "GESUMMV_M * GESUMMV_N",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_x", vec_x, "GESUMMV_N", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_y", [0] * m, "GESUMMV_M")
    print("")
    emit_array("int32_t", "vec_y_expected", vec_y, "GESUMMV_M")
    print("")


if __name__ == "__main__":
    main()
