#!/usr/bin/env python3
"""Generate the STRELA doitgen ISE/OSE descriptor tables.

PolyBench doitgen multiplies a 3-D tensor by a 2-D matrix:

    sum[r][q][p] = SUM_s A[r][q][s] * C4[s][p]

but the r and q loops are independent and only ever address whole rows of the
last axis, and `A` is row-major -- so the (NR x NQ x NP) tensor *is* an
(NR*NQ) x NP matrix in memory, with one (r, q) pair per row and no copy. The
tensor rank never reaches the descriptors: this app is a single matrix product,

    matSum(NROWS x NP) = matA(NROWS x NP) * matC4(NP x NP),   NROWS = NR*NQ

run in one phase over the doitgen_hv bitstream. That bitstream, its DFG and its
io_map are byte-identical to mm_hv's, so the schedule below is strela_mm's; the
only doitgen-specific parts are the flattened row count and the fact that the
right operand is square, which makes the reduction length and the tiled column
count both NP.

The bitstream comes from elastic-cgra's committed regression, replayed into a
kernel header without re-running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py doitgen_hv \\
        -o sw/applications/strela_doitgen/doitgen_hv_kernel.h
    cp hw/vendor/ceimm_upm_strela/rtl/elastic-cgra/regress/4x4-HV/doitgen_hv/io_map.json \\
        sw/applications/strela_doitgen/doitgen_hv_io_map.json
    make gen-app-data PROJECT=strela_doitgen  # or: python3 gen_descriptors.py

The io_map is committed next to the kernel header so descriptors.h can be
regenerated (it is gitignored) without the mapper. Keep the pair in step: they
describe one solve, and a re-solve reshuffles the engine assignment.

Kernel contract (mapper/applications/doitgen_hv/main.dot -- the same DFG,
io_map and bitstream as mm_hv), which fixes what each port means:

    input0..3        four rows of matA, streamed one element at a time
    input4, input5   two columns of matC4, preloaded into a scratchpad and
                     replayed once per row group
    output[j + 4*b]  = row(input_j) . col(input_{4+b})

so one pass of the fabric computes a 4x2 tile of matSum.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
ROWS = 4          # the STRELA shell hard-codes a 4x4 fabric
TILE_COLS = 2     # matC4 columns resident in scratchpads per pass

# Row groups handled by a single pass: the ISE scratchpad replay count, and the
# number of results each OSE-side scratchpad buffers before it is drained.
#
# This is a workaround for a hardware race, not a modelling choice. `output7` --
# the only output of this kernel that reaches its scratchpad over a router's
# horizontal bus (MEM_W1 in mode 1, see doitgen_hv_io_map.json; the other four
# scratchpad outputs are mode 0 PE-border ports) -- loses the *last* word of its
# drain once the drain is long enough for the OSE's obione FIFO to fill. In
# rtl/strela_memory.sv the `valid_out` register that presents that word is
# shared between the two directions, and the S_IDLE arm of its update guard
# accepts either of them, so while the FSM sits in S_IDLE with the last word
# pending, a `hor_ready_i` from the fabric side clears it before the OSE's
# `ose_ready_i` takes it. The OSE then waits forever for its last element: the
# run hangs in that pass with exactly one element of matSum unwritten.
#
# The boundary moves with the number of columns of the right operand, which sets
# the OSE's write stride and therefore how fast that FIFO drains, so this is a
# race rather than a clean depth: 8 is a margin, not a proof, and the real fix
# belongs in strela_memory.sv. Keep it in step with strela_mm, strela_gemm,
# strela_2mm and strela_3mm -- they all run the same solve.
MAX_GROUPS = 8


def build(nrows, np_, io_map, max_groups=MAX_GROUPS):
    """matSum(nrows x np_) = matA(nrows x np_) * matC4(np_ x np_).

    One pass of the fabric produces a 4x2 tile, so the schedule loops over the
    np_/2 column pairs of matC4 and, inside each, over the nrows/4 row groups
    of matA in chunks of at most `max_groups` groups (see MAX_GROUPS).
    """
    prog = StreamProgram(
        io_map=io_map,
        kernel="doitgen_hv_kernel",
        app="strela_doitgen",
        arrays={
            "matA": (nrows * np_, ELEM, [nrows, np_]),
            "matC4": (np_ * np_, ELEM, [np_, np_]),
            "matSum": (nrows * np_, ELEM, [nrows, np_]),
        },
        sew=SEW,
    )
    prog.mark_output("matSum")
    prog.conf_all()

    row_groups = nrows // ROWS
    col_pairs = np_ // TILE_COLS

    for pair in range(col_pairs):
        b_cols = (TILE_COLS * pair, TILE_COLS * pair + 1)
        # A column pair is walked in chunks of at most max_groups row groups;
        # each chunk is a pass of its own, which re-preloads the two matC4
        # columns (np_ extra reads) and bounds every scratchpad replay.
        for first in range(0, row_groups, max_groups):
            groups = min(max_groups, row_groups - first)
            with prog:
                # Preload the two matC4 columns; each is replayed once per row
                # group of this chunk. mem() must come before stream() here:
                # ISE0 and ISE2 carry both a scratchpad port and a row stream,
                # and must release the scratchpad first.
                for b, col in enumerate(b_cols):
                    prog.mem(f"input{4 + b}", "matC4", col,
                             stride=np_ * ELEM, count=np_, size=np_,
                             iters=groups)

                # Stream the matA rows: ISE carrying input_j gets rows
                # j, j+4, ... Each row is one (r, q) pair's length-NP vector.
                for j in range(ROWS):
                    for group in range(first, first + groups):
                        prog.stream(f"input{j}", "matA",
                                    (ROWS * group + j) * np_,
                                    stride=ELEM, count=np_)

                # One descriptor per output walks the chunk's row groups,
                # ROWS rows apart.
                for b, col in enumerate(b_cols):
                    for j in range(ROWS):
                        prog.out(f"output{j + ROWS * b}", "matSum",
                                 (ROWS * first + j) * np_ + col,
                                 stride=ROWS * np_ * ELEM, count=groups)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA doitgen ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data
PROJECT=strela_doitgen` runs both with no arguments, so change NR/NQ/NP in both
together.
""")
    parser.add_argument("NR", type=int, nargs="?", default=25,
                        help="outer tensor dimension (default: 25)")
    parser.add_argument("NQ", type=int, nargs="?", default=20,
                        help="middle tensor dimension (default: 20)")
    parser.add_argument("NP", type=int, nargs="?", default=30,
                        help="contracted dimension, and the side of C4 "
                             "(default: 30)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "doitgen_hv_io_map.json"),
                        help="io_map.json of the kernel (default: the copy "
                             "committed next to the kernel header)")
    parser.add_argument("--max-groups", type=int, default=MAX_GROUPS,
                        metavar="N",
                        help=f"row groups per pass (default: {MAX_GROUPS}); "
                             "see MAX_GROUPS for why this is capped")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    nr, nq, np_ = args.NR, args.NQ, args.NP
    nrows = nr * nq

    # Only the *product* NR*NQ has to tile: r and q are flattened into one row
    # index, so either alone may be odd.
    if nrows % ROWS:
        raise SystemExit(f"NR*NQ = {nrows} must be a multiple of {ROWS}, one "
                         "row group per pass (NR and NQ are flattened into a "
                         "single row index, so only their product matters)")
    if np_ % TILE_COLS:
        raise SystemExit(f"NP must be a multiple of {TILE_COLS}, the matC4 "
                         "columns resident in scratchpads per pass")
    # validate() catches these too, but fail here with the knob to turn.
    if np_ > 511:
        raise SystemExit(f"NP={np_} exceeds the 512-word scratchpad "
                         "(a matC4 column)")
    if np_ * np_ * ELEM >= 1 << 16:
        raise SystemExit(
            f"NP={np_}: a matC4 column preload is {np_ * np_ * ELEM} bytes, "
            "past the 65535 the 16-bit byte count holds (max NP = "
            f"{int((((1 << 16) - 1) // ELEM) ** 0.5)})")
    if not 1 <= args.max_groups <= 255:
        raise SystemExit(f"--max-groups={args.max_groups}: a pass replays the "
                         "scratchpad that many times, which must fit the 8-bit "
                         "iters field")

    prog = build(nrows, np_, args.io_map, args.max_groups)
    prog.validate()

    note = (f"doitgen {nr}x{nq}x{np_}: matSum = matA * matC4, the tensor "
            f"flattened to {nrows} x {np_} rows against the {np_}x{np_} matrix")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "doitgen_hv_kernel.h",
                                 "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
