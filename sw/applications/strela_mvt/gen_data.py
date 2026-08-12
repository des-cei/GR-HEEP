#!/usr/bin/env python3
"""Generate the STRELA mvt test data header (PolyBench mvt, int32).

Matrix-vector product transposed: one square matrix traversed both ways, each
product accumulated onto a vector that is already there.

    x1 = x1 + A  @ y_1               A: N x N,  every vector: N
    x2 = x2 + A^T @ y_2

split over the two committed HV bitstreams elastic-cgra ships for it:

    t1 = A  @ y_1                    <- kernel matvec  (mvt_1_hv)
    t2 = A^T @ y_2                   <- kernel matvec  (mvt_1_hv, same config)
    x1 = x1_in + t1                  <- kernel add     (mvt_2_hv), lane 0
    x2 = x2_in + t2                  <- kernel add     (mvt_2_hv), lane 1

The run therefore has **two** phases for three operations, and that is the point
of this app: A@y_1 and A^T@y_2 reduce over the same N and want the same PE
settings, so the transposed product is simply more passes of the configuration
the first one already runs under -- a phase is a configuration, not an operand
and not a direction. Contrast strela_atax and strela_bicg, which run the exact
same pair of products over a *rectangular* matrix: there the two reductions have
different lengths, `delay_value` lives in the bitstream, and the same pair needs
two phases. Squareness is what buys the saving here.

Shapes. The matvec kernel has four independent accumulator lanes, so a pass
consumes four lines of A at a time and N must be a multiple of 4 in both
directions. PolyBench SMALL is 120, which is, but the arrays are still allocated
at MVT_N_PAD so any shape works: the padded rows and columns of A and the padded
entries of y_1, y_2, x1 and x2 are zero, a padded line reduces to exactly 0, and
the expected values below cover the padded shape (x1[i] = x2[i] = 0 for i >= N),
which keeps the check a check instead of an exemption.

t1 and t2 are hand-offs between kernels -- written to memory by the matvec
phase's OSEs and streamed back in by the add phase's ISEs -- so they carry the
interleaved-section attribute like the arrays the CGRA reads from the start.
x1_in and x2_in are read by the CGRA too: PolyBench accumulates onto the
incoming x, and the add kernel is what does it.

The reference is computed with plain Python integers in the fabric's own operand
order and range-checked against int32, so a mismatch in simulation means the
hardware is wrong rather than the golden having silently wrapped.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_mvt` runs both with no arguments, so change the two together.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

ROWS = 4           # accumulator lanes of mvt_1_hv = matrix lines per group
LANES = 2          # independent lanes of mvt_2_hv, one per output vector

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
        description="Generate the STRELA mvt test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
N needs no particular shape: it is rounded up to a multiple of 4 (the fabric's
accumulator lanes) and the padding is zeroed, so the kernel runs on N_PAD x
N_PAD and the last N_PAD-N results of both vectors are zero by construction.
""")
    parser.add_argument("N", type=int, nargs="?", default=120,
                        help="order of A and length of every vector "
                             "(default: 120, PolyBench SMALL)")
    parser.add_argument("--range", type=int, default=100,
                        help="max |element| of A, y_1, y_2, x1 and x2 "
                             "(default: 100)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    n, rng = args.N, args.range

    if n < 1:
        sys.exit(f"error: N={n} must be at least 1")
    if rng < 1:
        sys.exit(f"error: --range {rng} must be at least 1")

    n_pad = -(-n // ROWS) * ROWS

    # Each product preloads its y vector into a 512-word scratchpad and replays
    # it, so one vector is what has to fit -- not the matrix.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n_pad > limit:
        sys.exit(f"error: y is {n_pad} words, past the {limit} a 9-bit size "
                 f"field and a {MEM_DEPTH}-word scratchpad allow; lower N")
    if n_pad > MAX_DELAY:
        sys.exit(f"error: N={n_pad} exceeds the {MAX_DELAY} an accumulator's "
                 "16-bit delay_value holds")
    # The transposed product streams *columns* of A: N_PAD elements a row apart,
    # so one descriptor covers the whole matrix span. This is the real size wall.
    if n_pad * n_pad * 4 > MAX_BYTES:
        sys.exit(f"error: a column of A spans {n_pad * n_pad * 4} bytes, past "
                 f"the {MAX_BYTES} a 16-bit byte count holds; lower N "
                 "(gen_descriptors.py splits the column across descriptors, but "
                 "keep the two generators in step)")
    if n_pad * 4 > MAX_BYTES:
        sys.exit(f"error: an add-kernel lane streams {n_pad} words = "
                 f"{n_pad * 4} bytes, past the {MAX_BYTES} a descriptor holds; "
                 "lower N")

    if args.seed is not None:
        random.seed(args.seed)

    def vector():
        return [random.randint(-rng, rng) if i < n else 0 for i in range(n_pad)]

    # Zero padding in both directions: rows n.. and columns n.. of A.
    mat_a = [random.randint(-rng, rng) if (i // n_pad) < n and (i % n_pad) < n
             else 0
             for i in range(n_pad * n_pad)]
    vec_y1, vec_y2 = vector(), vector()
    vec_x1_in, vec_x2_in = vector(), vector()

    bound = rng + n * rng * rng
    if bound > INT32_MAX:
        sys.exit(f"error: |x1| can reach {bound}, past int32; lower --range "
                 "or N")

    vec_t1 = [sum(mat_a[i * n_pad + j] * vec_y1[j] for j in range(n_pad))
              for i in range(n_pad)]
    vec_t2 = [sum(mat_a[i * n_pad + j] * vec_y2[i] for i in range(n_pad))
              for j in range(n_pad)]
    vec_x1 = [vec_x1_in[i] + vec_t1[i] for i in range(n_pad)]
    vec_x2 = [vec_x2_in[i] + vec_t2[i] for i in range(n_pad)]

    span = max(abs(v) for v in vec_t1 + vec_t2 + vec_x1 + vec_x2)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print(f"#define MVT_N       {n}   /* order of A = length of every vector */")
    print(f"#define MVT_N_PAD   {n_pad}   /* N rounded up to the fabric's {ROWS} lanes */")
    print("")
    pad = (f"rows and columns {n}..{n_pad - 1} of A are zero padding"
           if n_pad > n else "no padding needed")
    print(f"/* Elements in [-{rng}, {rng}], max |result| = {span}; {pad}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "mat_a", mat_a, "MVT_N_PAD * MVT_N_PAD",
               interleaved=True)
    print("")
    emit_array("int32_t", "vec_y1", vec_y1, "MVT_N_PAD", interleaved=True)
    print("")
    emit_array("int32_t", "vec_y2", vec_y2, "MVT_N_PAD", interleaved=True)
    print("")
    emit_array("int32_t", "vec_x1_in", vec_x1_in, "MVT_N_PAD", interleaved=True)
    print("")
    emit_array("int32_t", "vec_x2_in", vec_x2_in, "MVT_N_PAD", interleaved=True)
    print("")

    # Written by the matvec phase's OSEs, read back by the add phase's ISEs, so
    # these are interleaved too.
    emit_array("int32_t", "vec_t1", [0] * n_pad, "MVT_N_PAD", interleaved=True)
    print("")
    emit_array("int32_t", "vec_t2", [0] * n_pad, "MVT_N_PAD", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "vec_x1", [0] * n_pad, "MVT_N_PAD")
    print("")
    emit_array("int32_t", "vec_x2", [0] * n_pad, "MVT_N_PAD")
    print("")
    emit_array("int32_t", "vec_t1_expected", vec_t1, "MVT_N_PAD")
    print("")
    emit_array("int32_t", "vec_t2_expected", vec_t2, "MVT_N_PAD")
    print("")
    emit_array("int32_t", "vec_x1_expected", vec_x1, "MVT_N_PAD")
    print("")
    emit_array("int32_t", "vec_x2_expected", vec_x2, "MVT_N_PAD")
    print("")


if __name__ == "__main__":
    main()
