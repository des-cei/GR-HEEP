#!/usr/bin/env python3
"""Generate the STRELA GEMM ISE/OSE descriptor tables (two chained kernels).

PolyBench gemm is split across two bitstreams that run back to back in a single
STRELA execution, separated by a FENCE_SE:

    phase 0  gemm_1_hv   matAB = matA(NI x NK) * matB(NK x NJ)
    phase 1  gemm_2_hv   matD  = alpha * matAB + beta * matC

Both bitstreams come from elastic-cgra's committed regression, replayed into
kernel headers without re-running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemm_1_hv \\
        -o sw/applications/strela_gemm/gemm_1_hv_kernel.h
    cp $CG/regress/4x4-HV/gemm_1_hv/io_map.json \\
        sw/applications/strela_gemm/gemm_1_hv_io_map.json   # and likewise gemm_2_hv
    make gen-app-data PROJECT=strela_gemm       # or: python3 gen_descriptors.py

Each io_map is committed next to its kernel header so descriptors.h can be
regenerated (it is gitignored) without the mapper. Keep each pair in step: they
describe one solve, and a re-solve reshuffles the engine assignment.

Why the fence is not optional. An ISE that re-enters TR_CONF clears its
`conf_done_o` (rtl/strela_input_stream_engine.sv), which drops the global
`conf_reg` and re-gates every fabric handshake -- so anything still in flight
when phase 1 starts configuring would hang. The fence also makes the hand-off
through memory safe: matAB is written by phase 0's OSEs and read back by phase
1's ISEs, and FENCE_SE is the only fence that waits on both kinds of engine.
The rising edge of `conf_reg` then pulses `clr_cgra`, so the fabric is flushed
of phase 0's tokens before phase 1 streams anything.

Kernel contracts, which fix what each port means:

  gemm_1_hv (mapper/applications/gemm_1_hv/main.dot -- the same DFG, io_map and
  bitstream as mm_hv, so this half is strela_mm's schedule writing matAB)
      input0..3        four rows of matA, streamed one element at a time
      input4, input5   two columns of matB, preloaded into a scratchpad
      output[j + 4*b]  = A_row(input_j) . B_col(input_{4+b})

  gemm_2_hv (mapper/applications/gemm_2_hv/main.dot), two independent lanes:
      output0 = const(input0) * input0 + const(input1) * input1
      output1 = const(input2) * input2 + const(input3) * input3
  so each lane gets one half of the flat NI*NJ array, with the matAB operand
  scaled by alpha and the matC operand by beta. main.c patches those four
  constants (the DFG ships a placeholder 3).
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
TILE_COLS = 2     # matB columns resident in scratchpads per pass
LANES = 2         # independent lanes of gemm_2_hv

# Row groups handled by a single pass: the ISE scratchpad replay count, and the
# number of results each OSE-side scratchpad buffers before it is drained.
#
# This is a workaround for a hardware race, not a modelling choice. `output7` --
# the only gemm_1_hv output that reaches its scratchpad over a router's
# horizontal bus (MEM_W1 in mode 1, see gemm_1_hv_io_map.json; the other four
# scratchpad outputs are mode 0 PE-border ports) -- loses the *last* word of its
# drain once the drain is long enough for the OSE's obione FIFO to fill. In
# rtl/strela_memory.sv the `valid_out` register that presents that word is
# shared between the two directions, and the S_IDLE arm of its update guard
# accepts either of them, so while the FSM sits in S_IDLE with the last word
# pending, a `hor_ready_i` from the fabric side clears it before the OSE's
# `ose_ready_i` takes it. The OSE then waits forever for its last element:
# gemm hangs in the pass with exactly one element of matAB unwritten.
#
# Measured on Verilator, sweeping NI (= 4 * row groups), NJ and NK: 13 groups
# per pass are fine at NK = 16, 56, 64 and 400; 14 hang at NK = 16, 56 and 64;
# 20 and 50 hang at NK = 16. NJ moves the boundary as well, since it sets the
# OSE's write stride and therefore how fast that FIFO drains -- so this is a
# race, not a clean depth, and 8 is a margin rather than a proof. The real fix
# belongs in strela_memory.sv.
MAX_GROUPS = 8


def build(ni, nj, nk, io_map_1, io_map_2, max_groups=MAX_GROUPS):
    prog = StreamProgram(
        io_map=io_map_1,
        kernel="gemm_1_hv_kernel",
        app="strela_gemm",
        arrays={
            "matA": (ni * nk, ELEM, [ni, nk]),
            "matB": (nk * nj, ELEM, [nk, nj]),
            "matC": (ni * nj, ELEM, [ni, nj]),
            "matAB": (ni * nj, ELEM, [ni, nj]),
            "matD": (ni * nj, ELEM, [ni, nj]),
        },
        sew=SEW,
    )
    prog.mark_output("matAB", "matD")
    prog.conf_all()

    # ---- phase 0: matAB = matA * matB, one 4x2 tile of matAB per pass -------
    row_groups = ni // ROWS       # matA row groups per matB column pair
    col_pairs = nj // TILE_COLS

    for pair in range(col_pairs):
        b_cols = (TILE_COLS * pair, TILE_COLS * pair + 1)
        # A column pair is walked in chunks of at most MAX_GROUPS row groups;
        # each chunk is a pass of its own, which re-preloads the two matB
        # columns (nk extra reads) and bounds every scratchpad replay.
        for first in range(0, row_groups, max_groups):
            groups = min(max_groups, row_groups - first)
            with prog:
                # Preload the two matB columns; each is replayed once per row
                # group of this chunk.
                for b, col in enumerate(b_cols):
                    prog.mem(f"input{4 + b}", "matB", col,
                             stride=nj * ELEM, count=nk, size=nk, iters=groups)

                # Stream the matA rows: ISE carrying input_j gets rows j, j+4, ...
                for j in range(ROWS):
                    for group in range(first, first + groups):
                        prog.stream(f"input{j}", "matA", (ROWS * group + j) * nk,
                                    stride=ELEM, count=nk)

                # One descriptor per output walks the chunk's row groups,
                # ROWS rows apart.
                for b, col in enumerate(b_cols):
                    for j in range(ROWS):
                        prog.out(f"output{j + ROWS * b}", "matAB",
                                 (ROWS * first + j) * nj + col,
                                 stride=ROWS * nj * ELEM, count=groups)

    # ---- the hand-off -------------------------------------------------------
    prog.fence()                                  # FENCE_SE, all eight engines
    prog.load_kernel("gemm_2_hv_kernel", io_map=io_map_2)

    # ---- phase 1: matD = alpha * matAB + beta * matC ------------------------
    # Lane k owns the k-th slice of the flat array; the two lanes are
    # independent, so any split works as long as the three streams of a lane
    # address the same elements.
    span = ni * nj // LANES
    with prog:
        for k in range(LANES):
            prog.stream(f"input{2 * k}", "matAB", k * span,
                        stride=ELEM, count=span)
            prog.stream(f"input{2 * k + 1}", "matC", k * span,
                        stride=ELEM, count=span)
            prog.out(f"output{k}", "matD", k * span, stride=ELEM, count=span)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA GEMM ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data PROJECT=strela_gemm`
runs both with no arguments, so change NI/NJ/NK in both together.
""")
    parser.add_argument("NI", type=int, nargs="?", default=60,
                        help="rows of matA, matC and matD (default: 60)")
    parser.add_argument("NJ", type=int, nargs="?", default=70,
                        help="cols of matB, matC and matD (default: 70)")
    parser.add_argument("NK", type=int, nargs="?", default=80,
                        help="cols of matA / rows of matB (default: 80)")
    parser.add_argument("--io-map-1",
                        default=os.path.join(_HERE, "gemm_1_hv_io_map.json"),
                        help="io_map.json of the matmul kernel (default: the "
                             "copy committed next to the kernel header)")
    parser.add_argument("--io-map-2",
                        default=os.path.join(_HERE, "gemm_2_hv_io_map.json"),
                        help="io_map.json of the scale-and-add kernel")
    parser.add_argument("--max-groups", type=int, default=MAX_GROUPS,
                        metavar="N",
                        help=f"row groups per pass (default: {MAX_GROUPS}); "
                             "see MAX_GROUPS for why this is capped")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    ni, nj, nk = args.NI, args.NJ, args.NK
    if ni % ROWS or nj % TILE_COLS:
        raise SystemExit(f"NI must be a multiple of {ROWS} and NJ of {TILE_COLS}")
    if (ni * nj) % LANES:
        raise SystemExit(f"NI*NJ must be a multiple of {LANES}, one slice per "
                         "lane of gemm_2_hv")
    # validate() catches these too, but fail here with the knob to turn.
    if nj * nk * ELEM >= 1 << 16:
        raise SystemExit(
            f"NJ*NK = {nj * nk}: a matB column preload is {nj * nk * ELEM} "
            "bytes, past the 65535 the 16-bit byte count holds (max NJ*NK = "
            f"{((1 << 16) - 1) // ELEM})")
    if ni * nj * ELEM >= 1 << 16:
        raise SystemExit(
            f"NI*NJ = {ni * nj}: a matAB output descriptor is "
            f"{ni * nj * ELEM} bytes, past the 65535 the 16-bit byte count "
            f"holds (max NI*NJ = {((1 << 16) - 1) // ELEM})")
    if nk > 511:
        raise SystemExit(f"NK={nk} exceeds the 512-word scratchpad (matB column)")
    if not 1 <= args.max_groups <= 255:
        raise SystemExit(f"--max-groups={args.max_groups}: a pass replays the "
                         "scratchpad that many times, which must fit the 8-bit "
                         "iters field")

    prog = build(ni, nj, nk, args.io_map_1, args.io_map_2, args.max_groups)
    prog.validate()

    note = (f"gemm {ni}x{nj}x{nk}: gemm_1_hv (matAB = matA*matB) then, behind a "
            f"FENCE_SE, gemm_2_hv (matD = alpha*matAB + beta*matC)")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "gemm_1_hv_kernel.h",
                                 "gemm_2_hv_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
