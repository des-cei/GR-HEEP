#!/usr/bin/env python3
"""Generate the STRELA gemver test data header (PolyBench gemver, int32).

PolyBench gemver is four statements over one square matrix and eight vectors:

    A = A + u1*v1^T + u2*v2^T        rank-2 update, elementwise
    x = beta * (A^T @ y)             matrix-vector, transposed
    x = x + z                        vector add
    w = alpha * (A @ x)              matrix-vector, forward

with x and w starting at zero (PolyBench's init_array does that, which is why
the last two statements read as assignments rather than accumulations here).
The four map onto the three committed HV bitstreams as four phases; see
gen_descriptors.py for why the two matrix-vector products cannot share one.

Shapes. The matrix-vector kernel has four accumulator lanes, so a pass consumes
four lines at a time and N must be a multiple of 4: the forward product's lines
are rows and the transposed one's are columns, so the *same* N covers both. The
rank-2 update instead consumes two rows at a time, one per lane, which N being
even already gives. PolyBench SMALL is 120, a multiple of 4 either way, but the
arrays are still allocated at GEMVER_N_PAD so any N works. Padding is all zeros
-- padded rows and columns of A, padded entries of every vector -- so it
propagates as zero through all four statements and the expected values below
cover the padded shape rather than exempting it.

The intermediates are all hand-offs between phases, written by one phase's OSEs
and read back by the next one's ISEs, so they carry the interleaved-section
attribute exactly like the inputs the CGRA reads from the start. mat_a2 holds the
updated matrix instead of overwriting mat_a: the update is elementwise, so
writing back in place would in fact be safe here, but within a pass the ISE
reading an element and the OSE writing it are not ordered with respect to each
other, and a separate destination keeps that reasoning out of the app.

**Range.** This is the app where int32 runs out of room. gemver is a *triple*
product -- a rank-2 update whose result feeds a matrix-vector product whose
result feeds another one -- so the worst case grows as N^2 * range^5 * (1+2*range)^2,
and at PolyBench SMALL (N=120) that caps |element| at 4, against 40 for the
doubly-reducing strela_atax and 100 for the single-reduction apps. The bound is
checked explicitly below, because int32 would otherwise wrap silently and read as
a fabric bug. It is a worst case (every term aligned in sign), so it is
conservative by a wide margin -- but it is deterministic, and `make gen-app-data`
runs this with no arguments in CI, where a range that only *usually* fits would
be a flaky build. The accumulated values still reach ~10^9, so the datapath is
exercised across the full 32 bits even though the operands are small.

The reference is computed with plain Python integers in the fabric's own operand
order -- beta and alpha scale the *replayed vector* rather than the product,
because that is where the constant-multiply PE sits -- so a mismatch in
simulation means the hardware is wrong rather than the golden having overflowed.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_gemver` runs both with no arguments, so change the two together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

ROWS = 4           # accumulator lanes of gemver_2_hv = matrix lines per group
LANES = 2          # independent lanes of gemver_1_hv and gemver_3_hv

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_ITERS = 255    # mem_param iters is 8 bits (sw/strela.h)
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count
MAX_DELAY = 65535  # set_pe_delay_value writes bits 31:16 of PE word 2


def worst_case(n, rng):
    """Bound on |w|, the deepest of the four statements.

    |A2| <= rng + 2*rng^2 =: a, since each entry takes one A element and two
    products of vector entries. The transposed product then sums N terms of
    |A2| * |beta*y| <= a*rng^2, and z adds one more rng; the forward product
    sums N terms of |A2| * |alpha*x|.
    """
    a = rng + 2 * rng ** 2
    x = n * a * rng ** 2 + rng
    return n * a * rng * x


def max_range(n):
    """Largest --range whose worst case still fits int32, for the error text."""
    rng = 0
    while worst_case(n, rng + 1) <= INT32_MAX:
        rng += 1
    return rng


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
        description="Generate the STRELA gemver test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
N needs no particular shape: it is rounded up to a multiple of 4 (the fabric's
accumulator lanes) and the padding is zeroed, so the kernel runs on N_PAD x N_PAD
and the padded results are zero by construction.
""")
    parser.add_argument("N", type=int, nargs="?", default=120,
                        help="order of A and length of every vector "
                             "(default: 120, PolyBench SMALL)")
    parser.add_argument("--range", type=int, default=4,
                        help="max |element| of A, the four vectors, y, z, alpha "
                             "and beta (default: 4; gemver multiplies three "
                             "deep, so this hits int32 fast)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    n, rng = args.N, args.range

    if n < 1:
        sys.exit(f"error: N={n} must be at least 1")
    if rng < 1:
        sys.exit(f"error: --range {rng} must be at least 1")

    n_pad = -(-n // ROWS) * ROWS

    # Both matrix-vector phases preload a whole vector into a 512-word
    # scratchpad: y for the transposed product, x for the forward one.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n_pad > limit:
        sys.exit(f"error: a preloaded vector is {n_pad} words, past the {limit} "
                 f"a 9-bit size field and a {MEM_DEPTH}-word scratchpad allow; "
                 "lower N")
    # The rank-2 update replays each u entry once per element of its row, so the
    # row length itself lands in the 8-bit iters field. This limit is specific to
    # that kernel -- the matrix-vector phases only replay once per row group.
    if n_pad > MAX_ITERS:
        sys.exit(f"error: the rank-2 update replays u1[i]/u2[i] {n_pad} times "
                 f"per row, past the {MAX_ITERS} an 8-bit iters field holds; "
                 "lower N")
    if n_pad > MAX_DELAY:
        sys.exit(f"error: a reduction length of {n_pad} exceeds the {MAX_DELAY} "
                 "an accumulator's 16-bit delay_value holds")

    bound = worst_case(n, rng)
    if bound > INT32_MAX:
        sys.exit(f"error: |w| can reach {bound}, past int32; lower --range "
                 f"(<= {max_range(n)} for N={n}) or N")

    if args.seed is not None:
        random.seed(args.seed)

    def scalar():
        """alpha and beta, drawn away from 0 and from +-1 when the range allows.
        Both are patched into a constant-multiply PE, and a scale of 0 would turn
        a whole statement into zeros while +-1 would leave the product unchanged
        -- either way the patch would stop being tested."""
        return random.choice([-1, 1]) * random.randint(min(2, rng), rng)

    alpha, beta = scalar(), scalar()

    # Zero padding in both directions: rows/columns n.. of A and entries n.. of
    # every vector.
    mat_a = [random.randint(-rng, rng) if (i // n_pad) < n and (i % n_pad) < n
             else 0
             for i in range(n_pad * n_pad)]
    vec_u1, vec_u2, vec_v1, vec_v2, vec_y, vec_z = (
        [random.randint(-rng, rng) if i < n else 0 for i in range(n_pad)]
        for _ in range(6))

    # A2 = A + u1*v1^T + u2*v2^T, elementwise and in the fabric's operand order.
    mat_a2 = [mat_a[i * n_pad + j]
              + vec_u1[i] * vec_v1[j] + vec_u2[i] * vec_v2[j]
              for i in range(n_pad) for j in range(n_pad)]

    # tmp = beta*(A2^T @ y), then x = tmp + z. The constant-multiply PE scales
    # the replayed vector, not the product, so beta multiplies y here too.
    beta_y = [beta * v for v in vec_y]
    vec_tmp = [sum(mat_a2[j * n_pad + k] * beta_y[j] for j in range(n_pad))
               for k in range(n_pad)]
    vec_x = [vec_tmp[k] + vec_z[k] for k in range(n_pad)]

    # w = alpha*(A2 @ x), the forward product, alpha again scaling the vector.
    alpha_x = [alpha * v for v in vec_x]
    vec_w = [sum(mat_a2[i * n_pad + j] * alpha_x[j] for j in range(n_pad))
             for i in range(n_pad)]

    span = max(abs(v) for v in mat_a2 + vec_tmp + vec_x + vec_w)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define GEMVER_N       {n}   /* order of A, length of every vector */")
    print(f"#define GEMVER_N_PAD   {n_pad}   /* N rounded up to the fabric's {ROWS} lanes */")
    print(f"#define GEMVER_ALPHA   ({alpha})")
    print(f"#define GEMVER_BETA    ({beta})")
    print("")
    pad_note = (f"rows/columns {n}..{n_pad - 1} are zero padding"
                if n_pad > n else "no padding needed")
    print(f"/* Elements in [-{rng}, {rng}], max |result| = {span} "
          f"(worst case {bound}); {pad_note}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "GEMVER_N_PAD * GEMVER_N_PAD",
               interleaved=True)
    print("")
    for name, values in (("vec_u1", vec_u1), ("vec_v1", vec_v1),
                         ("vec_u2", vec_u2), ("vec_v2", vec_v2),
                         ("vec_y", vec_y), ("vec_z", vec_z)):
        emit_array("int32_t", name, values, "GEMVER_N_PAD", interleaved=True)
        print("")

    # Written by one phase's OSEs and read straight back by the next phase's
    # ISEs, so these are interleaved too.
    emit_array("int32_t", "mat_a2", [0] * (n_pad * n_pad),
               "GEMVER_N_PAD * GEMVER_N_PAD", interleaved=True)
    print("")
    for name in ("vec_tmp", "vec_x"):
        emit_array("int32_t", name, [0] * n_pad, "GEMVER_N_PAD",
                   interleaved=True)
        print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_w", [0] * n_pad, "GEMVER_N_PAD")
    print("")
    emit_array("int32_t", "mat_a2_expected", mat_a2,
               "GEMVER_N_PAD * GEMVER_N_PAD")
    print("")
    for name, values in (("vec_tmp_expected", vec_tmp),
                         ("vec_x_expected", vec_x),
                         ("vec_w_expected", vec_w)):
        emit_array("int32_t", name, values, "GEMVER_N_PAD")
        print("")


if __name__ == "__main__":
    main()
