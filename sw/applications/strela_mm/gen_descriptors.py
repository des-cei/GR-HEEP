#!/usr/bin/env python3
"""Generate the STRELA matmul ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that `make map-bitstream PROJECT=mm_hv` writes next to the bitstream, resolved
by STRELA's shared binding layer. Regenerate the bitstream and the descriptors
together:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    make -C $CG map-bitstream PROJECT=mm_hv CGRA_CONFIG=configs/4x4-HV.hjson
    sed -e 's/mm_hv_kernel/matmul_kernel/g' -e 's/MM_HV_KERNEL/MATMUL_KERNEL/g' \\
        $CG/build/bitstream/mm_hv_kernel.h > sw/applications/strela_mm/matmul.h
    cp $CG/build/bitstream/mm_hv_io_map.json sw/applications/strela_mm/mm_hv_io_map.json
    make gen-app-data PROJECT=strela_mm        # or: python3 gen_descriptors.py

mm_hv_io_map.json is committed next to matmul.h so descriptors.h can be
regenerated (it is gitignored) without re-running the mapper. Keep the two in
step: they describe the same solve.

Kernel contract (mapper/applications/mm_hv/main.dot), which fixes what each
port means:

    input0..3        four rows of A, streamed one element at a time
    input4, input5   two columns of B, preloaded into a scratchpad and replayed
    output[j + 4*b]  = A_row(input_j) . B_col(input_{4+b})

so one pass of the fabric computes a 4x2 tile of C. The tables loop over the
N/2 column pairs of B and, inside each, over the M/4 row groups of A -- in
chunks of at most MAX_GROUPS row groups, one pass each, because a longer pass
trips the strela_memory.sv race described at MAX_GROUPS below.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

DTYPE_INFO = {
    "int8": {"sew": 8, "ctype": "int8_t"},
    "int16": {"sew": 16, "ctype": "int16_t"},
    "int32": {"sew": 32, "ctype": "int32_t"},
}

ROWS = 4          # the STRELA shell hard-codes a 4x4 fabric
TILE_COLS = 2     # B columns resident in scratchpads per pass

# Row groups handled by a single pass: the ISE scratchpad replay count, and the
# number of results each OSE-side scratchpad buffers before it is drained.
#
# This is a workaround for a hardware race, not a modelling choice. `output7` --
# the only output of this kernel that reaches its scratchpad over a router's
# horizontal bus (MEM_W1 in mode 1, see mm_hv_io_map.json; the other four
# scratchpad outputs are mode 0 PE-border ports) -- loses the *last* word of its
# drain once the drain is long enough for the OSE's obione FIFO to fill. In
# rtl/strela_memory.sv the `valid_out` register that presents that word is
# shared between the two directions, and the S_IDLE arm of its update guard
# accepts either of them, so while the FSM sits in S_IDLE with the last word
# pending, a `hor_ready_i` from the fabric side clears it before the OSE's
# `ose_ready_i` takes it. The OSE then waits forever for its last element: the
# run hangs in that pass with exactly one element of matC unwritten.
#
# strela_gemm hits exactly the same race -- gemm_1_hv is byte-identical to
# mm_hv, the same solve -- and its MAX_GROUPS comment records the Verilator
# sweep: 13 groups per pass are fine at K = 16, 56, 64 and 400; 14 hang at
# K = 16, 56 and 64. N moves the boundary too, since it sets the OSE's write
# stride and therefore how fast that FIFO drains, so this is a race rather than
# a clean depth, and 8 is a margin rather than a proof. Keep the two apps'
# values in step; the real fix belongs in strela_memory.sv.
MAX_GROUPS = 8


def build(m, k, n, in_sew, in_bytes, acc_sew, acc_bytes, io_map,
          max_groups=MAX_GROUPS):
    prog = StreamProgram(
        io_map=io_map,
        kernel="matmul_kernel",
        app="strela_mm",
        arrays={
            "matA": (m * k, in_bytes, [m, k]),
            "matB": (k * n, in_bytes, [k, n]),
            "matC": (m * n, acc_bytes, [m, n]),
        },
        sew=in_sew, acc_sew=acc_sew,
    )
    prog.mark_output("matC")
    prog.conf_all()

    row_groups = m // ROWS        # A row groups per B column pair
    col_pairs = n // TILE_COLS

    for pair in range(col_pairs):
        b_cols = (TILE_COLS * pair, TILE_COLS * pair + 1)
        # A column pair is walked in chunks of at most MAX_GROUPS row groups;
        # each chunk is a pass of its own, which re-preloads the two B columns
        # (k extra reads) and bounds every scratchpad replay.
        for first in range(0, row_groups, max_groups):
            groups = min(max_groups, row_groups - first)
            with prog:
                # Preload the two B columns; each is replayed once per row group
                # of this chunk.
                for b, col in enumerate(b_cols):
                    prog.mem(f"input{4 + b}", "matB", col,
                             stride=n * in_bytes, count=k, size=k, iters=groups)

                # Stream the A rows: ISE carrying input_j gets rows j, j+4, ...
                for j in range(ROWS):
                    for group in range(first, first + groups):
                        prog.stream(f"input{j}", "matA", (ROWS * group + j) * k,
                                    stride=in_bytes, count=k)

                # One descriptor per output walks the chunk's row groups,
                # ROWS rows apart.
                for b, col in enumerate(b_cols):
                    for j in range(ROWS):
                        prog.out(f"output{j + ROWS * b}", "matC",
                                 (ROWS * first + j) * n + col,
                                 stride=ROWS * n * acc_bytes, count=groups)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA matmul ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
SEW selection:
  ISE opcodes always use the --dtype SEW (matA/matB are stored as that type).
  OSE opcodes use:
    accum32  -> 32-bit  (matC is int32, regardless of input dtype)
    same     -> --dtype (matC is the same type as matA/matB)
""",
    )
    parser.add_argument("M", type=int, nargs="?", default=64,
                        help="rows of A and C (default: 64)")
    parser.add_argument("K", type=int, nargs="?", default=64,
                        help="cols of A / rows of B (default: 64)")
    parser.add_argument("N", type=int, nargs="?", default=64,
                        help="cols of B and C (default: 64)")
    parser.add_argument("--max-groups", type=int, default=MAX_GROUPS,
                        metavar="G",
                        help=f"row groups per pass (default: {MAX_GROUPS}); "
                             "see MAX_GROUPS for why this is capped")
    parser.add_argument("--io-map", default=os.path.join(_HERE, "mm_hv_io_map.json"),
                        help="io_map.json from map-bitstream "
                             "(default: the copy committed next to matmul.h)")
    parser.add_argument("--dtype", choices=list(DTYPE_INFO), default="int32",
                        help="element type for matA/matB (default: int32)")
    parser.add_argument("--mode", choices=["accum32", "same"], default="accum32",
                        help="matC accumulator strategy (default: accum32)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    m, k, n = args.M, args.K, args.N
    if m % ROWS or n % TILE_COLS:
        raise SystemExit(f"M must be a multiple of {ROWS} and N of {TILE_COLS}")
    # validate() catches these too, but fail here with the knob to turn.
    if k > 511:
        raise SystemExit(f"K={k} exceeds the 512-word scratchpad (a B column)")
    if not 1 <= args.max_groups <= 255:
        raise SystemExit(f"--max-groups={args.max_groups}: a pass replays the "
                         "scratchpad that many times, which must fit the 8-bit "
                         "iters field")

    info = DTYPE_INFO[args.dtype]
    in_sew, in_bytes = info["sew"], info["sew"] // 8
    if args.mode == "accum32":
        acc_sew, acc_ctype = 32, "int32_t"
    else:
        acc_sew, acc_ctype = in_sew, info["ctype"]

    prog = build(m, k, n, in_sew, in_bytes, acc_sew, acc_sew // 8, args.io_map,
                 args.max_groups)
    prog.validate()

    note = (f"dtype={args.dtype}, mode={args.mode}: ISE SEW={in_sew}, "
            f"OSE SEW={acc_sew} (matC is {acc_ctype})")
    out = args.output or "/dev/stdout"
    prog.emit_c_header(out, includes=("strela.h", "matmul.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
