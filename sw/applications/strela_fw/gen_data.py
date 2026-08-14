#!/usr/bin/env python3
"""Generate the STRELA Floyd-Warshall test data header (PolyBench floyd-warshall, int32).

PolyBench floyd-warshall is one statement swept N times over a distance matrix:

    for k: for i: for j:
        path[i][j] = min(path[i][j], path[i][k] + path[k][j])

which is the app's single bitstream run N*N_PAD/4 times; see gen_descriptors.py
for why a pivot costs N_PAD/4 passes rather than one.

**Data is PolyBench's own init_array, not random.** Every other generator here
draws uniform values in +-range, but this kernel needs two things that random
data gives badly. First, the schedule's correctness argument rests on the entries
being *non-negative* -- the pivot row and column must be unchanged during their
own iteration, which holds because

    path[k][j] <- min(path[k][j], path[k][k] + path[k][j]) = path[k][j]
    path[i][k] <- min(path[i][k], path[i][k] + path[k][k]) = path[i][k]

whenever path[k][k] >= 0 -- and min and + preserve non-negativity, so checking
the initial matrix is enough. Second, a matrix of small uniform values collapses
after one pivot into near-constant minima, and would pass even with the pivot
column wired wrong. PolyBench's init mixes short edges with a 999 "no edge"
sentinel, so the sweep has real shortest-path structure to find; the comment
emitted below reports how many entries the sweep actually changes, which is the
number to look at before trusting a pass.

**Shapes.** The fabric has four lanes and a pass takes one line each, so the
number of *rows* is padded up to a multiple of 4; the row length stays N, since j
and k only ever run over the real matrix. Padding rows are zero and stay zero:
min(0, 0 + path[k][j]) = 0 for non-negative entries, they are never a pivot
(k < N) and no real row reads them. The expected values cover the padded shape
rather than exempting it, so main.c compares the whole allocation.

**Two buffers.** path_a and path_b ping-pong, one pivot each, because PolyBench
relaxes in place and within a pass the ISE reading an element and the OSE writing
it are not ordered. Both are read and written by the CGRA, so both carry the
interleaved-section attribute. The reference below is computed *both* ways -- the
in-place PolyBench loop and the alternating-buffer one the hardware runs -- and
they are asserted equal on the actual data, which is what turns the argument
above into a check. With N pivots the result ends in path_a for even N and
path_b for odd N.

**Range.** Nothing here can overflow and the guard is nearly free: entries are
non-negative and the relaxation only ever lowers them, so every value stays
within the initial maximum V. The fabric adds two of them and subtracts a third,
so the widest intermediate is 2*V and the widest ALU result 2*V again; V is 999
for PolyBench's init, six orders of magnitude inside int32.

The defaults must match gen_descriptors.py's. `make gen-app-data
PROJECT=strela_fw` runs both with no arguments, so change the two together.
"""

import argparse
import sys

INT32_MAX = (1 << 31) - 1

LANES = 4          # independent lanes of fw_hv = matrix lines per pass

# Descriptor/fabric limits that would otherwise wrap silently.
MEM_DEPTH = 512    # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511     # mem_param size is 9 bits (sw/strela.h)
MAX_ITERS = 255    # mem_param iters is 8 bits (sw/strela.h)
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def init_array(n, n_pad):
    """PolyBench's init_array, laid out as n_pad rows of n with zero padding."""
    path = [0] * (n_pad * n)
    for i in range(n):
        for j in range(n):
            value = i * j % 7 + 1
            if (i + j) % 13 == 0 or (i + j) % 7 == 0 or (i + j) % 11 == 0:
                value = 999
            path[i * n + j] = value
    return path


def floyd_warshall_inplace(path, n, n_pad):
    """PolyBench's kernel, relaxing in place."""
    out = list(path)
    for k in range(n):
        for i in range(n_pad):
            through = out[i * n + k]
            for j in range(n):
                candidate = through + out[k * n + j]
                if candidate < out[i * n + j]:
                    out[i * n + j] = candidate
    return out


def floyd_warshall_pingpong(path, n, n_pad):
    """What the descriptors run: each pivot reads one buffer and writes the
    other, in the fabric's own operand order (a sub, a signed >0 compare, and a
    mux that takes the candidate when the compare fires)."""
    src = list(path)
    for k in range(n):
        dst = [0] * (n_pad * n)
        for i in range(n_pad):
            through = src[i * n + k]
            for j in range(n):
                cur = src[i * n + j]
                candidate = through + src[k * n + j]
                dst[i * n + j] = candidate if cur - candidate > 0 else cur
        src = dst
    return src


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
        description="Generate the STRELA Floyd-Warshall test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
N needs no particular shape: the row count is rounded up to a multiple of 4 (the
fabric's lanes) and the padding rows are zeroed, so the kernel runs on N_PAD x N
and the padded rows stay zero by construction.
""")
    parser.add_argument("N", type=int, nargs="?", default=60,
                        help="order of the distance matrix "
                             "(default: 60, PolyBench MINI -- SMALL is 180, "
                             "whose descriptor tables do not fit, see "
                             "gen_descriptors.py)")
    args = parser.parse_args()

    n = args.N
    if n < 1:
        sys.exit(f"error: N={n} must be at least 1")

    n_pad = -(-n // LANES) * LANES

    # A line is preloaded into a scratchpad and the pivot column entry is
    # replayed once per element of it; both fields wrap silently.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n > limit:
        sys.exit(f"error: a line is {n} words, past the {limit} a 9-bit size "
                 f"field and a {MEM_DEPTH}-word scratchpad allow; lower N")
    if n > MAX_ITERS:
        sys.exit(f"error: the pivot column entry is replayed {n} times per "
                 f"line, past the {MAX_ITERS} an 8-bit iters field holds; "
                 "lower N")
    if n * 4 > MAX_BYTES:
        sys.exit(f"error: a line is {n * 4} bytes, past the {MAX_BYTES} a "
                 "16-bit byte count holds")

    path = init_array(n, n_pad)

    if min(path) < 0:
        sys.exit("error: the initial matrix has a negative entry; the schedule "
                 "relies on the pivot row and column being unchanged during "
                 "their own iteration, which needs path[k][k] >= 0")
    peak = max(path)
    if 2 * peak > INT32_MAX:
        sys.exit(f"error: two entries sum to {2 * peak}, past int32; the "
                 "relaxation only lowers values, so the initial maximum bounds "
                 "every intermediate")

    expected = floyd_warshall_inplace(path, n, n_pad)
    if expected != floyd_warshall_pingpong(path, n, n_pad):
        sys.exit("internal error: relaxing in place and ping-ponging the two "
                 "buffers disagree, so the schedule is not PolyBench's kernel")
    if min(expected) < 0 or max(expected) > peak:
        sys.exit("internal error: the relaxation left [0, initial max]")

    changed = sum(1 for a, b in zip(path, expected) if a != b)

    print("#include <stdint.h>")
    print("")
    print(f"#define FW_N       {n}   /* order of the distance matrix */")
    print(f"#define FW_N_PAD   {n_pad}   /* rows rounded up to the fabric's {LANES} lanes */")
    print("")
    pad_note = (f"rows {n}..{n_pad - 1} are zero padding"
                if n_pad > n else "no padding needed")
    print(f"/* PolyBench init_array, entries in [{min(path)}, {peak}]; the sweep "
          f"lowers {changed}/{n * n} of them. */")
    print(f"/* {pad_note}. Result lands in "
          f"path_{'b' if n % 2 else 'a'} after {n} pivots. */")
    print("")

    # Both buffers are read and written by the CGRA: interleaved banks. path_b
    # starts at zero and is fully overwritten by the first pivot.
    emit_array("int32_t", "path_a", path, "FW_N_PAD * FW_N", interleaved=True)
    print("")
    emit_array("int32_t", "path_b", [0] * (n_pad * n), "FW_N_PAD * FW_N",
               interleaved=True)
    print("")
    emit_array("int32_t", "path_expected", expected, "FW_N_PAD * FW_N")
    print("")


if __name__ == "__main__":
    main()
