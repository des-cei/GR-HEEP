#!/usr/bin/env python3
"""Generate the STRELA 2mm ISE/OSE descriptor tables (three chained kernels).

PolyBench 2mm is split across three bitstreams that run back to back in a
single STRELA execution, each pair separated by a FENCE_SE:

    phase 0  mm_ab      matAB  = matA(NI x NK)  * matB(NK x NJ)
    phase 1  mm_abc     matABC = matAB(NI x NJ) * matC(NJ x NL)
    phase 2  scale_add  matD   = alpha * matABC + beta * matDin

Phases 0 and 1 are the *same* DFG and the *same* bitstream (2mm_1_hv, which is
byte-identical to mm_hv, so both are strela_v2_mm's schedule), but they need two
copies of it in RAM: the accumulator delay is the reduction length, NK for the
first product and NJ for the second, and main.c patches each copy separately
before the run. That is also why they cannot share one TR_CONF -- a phase is one
loaded bitstream.

All three bitstreams come from elastic-cgra's committed regression, replayed
into kernel headers without re-running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py 2mm_1_hv \\
        --array-name mm_ab_kernel -o sw/applications/strela_v2_2mm/mm_ab_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py 2mm_1_hv \\
        --array-name mm_abc_kernel -o sw/applications/strela_v2_2mm/mm_abc_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py 2mm_2_hv \\
        --array-name scale_add_kernel \\
        -o sw/applications/strela_v2_2mm/scale_add_kernel.h
    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/2mm_1_hv/io_map.json \\
        sw/applications/strela_v2_2mm/2mm_1_hv_io_map.json   # and likewise 2mm_2_hv
    make gen-app-data PROJECT=strela_v2_2mm      # or: python3 gen_descriptors.py

One io_map per DFG is committed next to the kernel headers so descriptors.h can
be regenerated (it is gitignored) without the mapper; the two matmul phases
share 2mm_1_hv_io_map.json because they share the solve. Keep each io_map in
step with its headers: they describe one solve, and a re-solve reshuffles the
engine assignment.

Why the fences are not optional. An ISE that re-enters TR_CONF clears its
`conf_done_o` (rtl/strela_v2_input_stream_engine.sv), which drops the global
`conf_reg` and re-gates every fabric handshake -- so anything still in flight
when the next phase starts configuring would hang. The fence also makes the
hand-off through memory safe: matAB and matABC are written by one phase's OSEs
and read back by the next phase's ISEs, and FENCE_SE is the only fence that
waits on both kinds of engine. The rising edge of `conf_reg` then pulses
`clr_cgra`, so the fabric is flushed of the previous phase's tokens (including
the accumulators' partial sums) before the next one streams anything.

Kernel contracts, which fix what each port means:

  2mm_1_hv (mapper/applications/2mm_1_hv/main.dot -- the same DFG, io_map and
  bitstream as mm_hv)
      input0..3        four rows of the left operand, one element at a time
      input4, input5   two columns of the right operand, preloaded into a
                       scratchpad and replayed once per row group
      output[j + 4*b]  = row(input_j) . col(input_{4+b})
  so one pass of the fabric computes a 4x2 tile of the product.

  2mm_2_hv (mapper/applications/2mm_2_hv/main.dot -- byte-identical to
  gemm_2_hv), two independent lanes:
      output0 = const(input0) * input0 + const(input1) * input1
      output1 = const(input2) * input2 + const(input3) * input3
  so each lane gets one half of the flat NI*NL array, with the matABC operand
  scaled by alpha and the matDin operand by beta. main.c patches those four
  constants (the DFG ships a placeholder 3).
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_V2_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_V2_SW)

from strela_v2_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
ROWS = 4          # the STRELA shell hard-codes a 4x4 fabric
TILE_COLS = 2     # right-operand columns resident in scratchpads per pass
LANES = 2         # independent lanes of 2mm_2_hv

# Row groups handled by a single pass: the ISE scratchpad replay count, and the
# number of results each OSE-side scratchpad buffers before it is drained.
#
# This is a workaround for a hardware race, not a modelling choice. `output7` --
# the only matmul output that reaches its scratchpad over a router's horizontal
# bus (MEM_W1 in mode 1, see 2mm_1_hv_io_map.json; the other four scratchpad
# outputs are mode 0 PE-border ports) -- loses the *last* word of its drain once
# the drain is long enough for the OSE's obione FIFO to fill. In
# rtl/strela_v2_memory.sv the `valid_out` register that presents that word is
# shared between the two directions, and the S_IDLE arm of its update guard
# accepts either of them, so while the FSM sits in S_IDLE with the last word
# pending, a `hor_ready_i` from the fabric side clears it before the OSE's
# `ose_ready_i` takes it. The OSE then waits forever for its last element: the
# run hangs in that pass with exactly one element of the product unwritten.
#
# The boundary moves with the number of columns of the right operand, which sets
# the OSE's write stride and therefore how fast that FIFO drains, so this is a
# race rather than a clean depth: 8 is a margin, not a proof, and the real fix
# belongs in strela_v2_memory.sv. Keep it in step with strela_v2_mm and strela_v2_gemm --
# all three run the same solve.
MAX_GROUPS = 8


def matmul_phase(prog, out_sym, a_sym, b_sym, ni, nj, nk, max_groups):
    """Emit the passes computing `out(ni x nj) = a(ni x nk) * b(nk x nj)`.

    One pass of the fabric produces a 4x2 tile, so the schedule loops over the
    nj/2 column pairs of `b` and, inside each, over the ni/4 row groups of `a`
    in chunks of at most `max_groups` groups (see MAX_GROUPS).
    """
    row_groups = ni // ROWS
    col_pairs = nj // TILE_COLS

    for pair in range(col_pairs):
        b_cols = (TILE_COLS * pair, TILE_COLS * pair + 1)
        # A column pair is walked in chunks of at most max_groups row groups;
        # each chunk is a pass of its own, which re-preloads the two columns
        # (nk extra reads) and bounds every scratchpad replay.
        for first in range(0, row_groups, max_groups):
            groups = min(max_groups, row_groups - first)
            with prog:
                # Preload the two `b` columns; each is replayed once per row
                # group of this chunk. mem() must come before stream() here:
                # ISE0 and ISE2 carry both a scratchpad port and a row stream,
                # and must release the scratchpad first.
                for b, col in enumerate(b_cols):
                    prog.mem(f"input{4 + b}", b_sym, col,
                             stride=nj * ELEM, count=nk, size=nk, iters=groups)

                # Stream the `a` rows: ISE carrying input_j gets rows j, j+4, ...
                for j in range(ROWS):
                    for group in range(first, first + groups):
                        prog.stream(f"input{j}", a_sym, (ROWS * group + j) * nk,
                                    stride=ELEM, count=nk)

                # One descriptor per output walks the chunk's row groups,
                # ROWS rows apart.
                for b, col in enumerate(b_cols):
                    for j in range(ROWS):
                        prog.out(f"output{j + ROWS * b}", out_sym,
                                 (ROWS * first + j) * nj + col,
                                 stride=ROWS * nj * ELEM, count=groups)


def build(ni, nj, nk, nl, io_map_mm, io_map_scale, max_groups=MAX_GROUPS):
    prog = StreamProgram(
        io_map=io_map_mm,
        kernel="mm_ab_kernel",
        app="strela_v2_2mm",
        arrays={
            "matA": (ni * nk, ELEM, [ni, nk]),
            "matB": (nk * nj, ELEM, [nk, nj]),
            "matC": (nj * nl, ELEM, [nj, nl]),
            "matDin": (ni * nl, ELEM, [ni, nl]),
            "matAB": (ni * nj, ELEM, [ni, nj]),
            "matABC": (ni * nl, ELEM, [ni, nl]),
            "matD": (ni * nl, ELEM, [ni, nl]),
        },
        sew=SEW,
    )
    prog.mark_output("matAB", "matABC", "matD")
    prog.conf_all()

    # ---- phase 0: matAB = matA * matB --------------------------------------
    matmul_phase(prog, "matAB", "matA", "matB", ni, nj, nk, max_groups)

    # ---- phase 1: matABC = matAB * matC ------------------------------------
    # Same DFG and bitstream, second copy in RAM: the accumulator delay is now
    # the reduction length NJ instead of NK.
    prog.fence()                                  # FENCE_SE, all eight engines
    prog.load_kernel("mm_abc_kernel", io_map=io_map_mm)
    matmul_phase(prog, "matABC", "matAB", "matC", ni, nl, nj, max_groups)

    # ---- phase 2: matD = alpha * matABC + beta * matDin ---------------------
    # Lane k owns the k-th slice of the flat array; the two lanes are
    # independent, so any split works as long as the three streams of a lane
    # address the same elements.
    prog.fence()
    prog.load_kernel("scale_add_kernel", io_map=io_map_scale)
    span = ni * nl // LANES
    with prog:
        for k in range(LANES):
            prog.stream(f"input{2 * k}", "matABC", k * span,
                        stride=ELEM, count=span)
            prog.stream(f"input{2 * k + 1}", "matDin", k * span,
                        stride=ELEM, count=span)
            prog.out(f"output{k}", "matD", k * span, stride=ELEM, count=span)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA 2mm ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data PROJECT=strela_v2_2mm`
runs both with no arguments, so change NI/NJ/NK/NL in both together.
""")
    parser.add_argument("NI", type=int, nargs="?", default=40,
                        help="rows of A, AB, ABC and D (default: 40)")
    parser.add_argument("NJ", type=int, nargs="?", default=50,
                        help="cols of B / rows of C (default: 50)")
    parser.add_argument("NK", type=int, nargs="?", default=70,
                        help="cols of A / rows of B (default: 70)")
    parser.add_argument("NL", type=int, nargs="?", default=80,
                        help="cols of C and D (default: 80)")
    parser.add_argument("--io-map-mm",
                        default=os.path.join(_HERE, "2mm_1_hv_io_map.json"),
                        help="io_map.json of the matmul kernel, shared by both "
                             "matmul phases (default: the copy committed next "
                             "to the kernel headers)")
    parser.add_argument("--io-map-scale",
                        default=os.path.join(_HERE, "2mm_2_hv_io_map.json"),
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

    ni, nj, nk, nl = args.NI, args.NJ, args.NK, args.NL
    # Both matmul phases stream NI rows, four at a time; phase 0 tiles NJ
    # columns and phase 1 tiles NL columns, two at a time.
    if ni % ROWS:
        raise SystemExit(f"NI must be a multiple of {ROWS}, one row group per pass")
    if nj % TILE_COLS or nl % TILE_COLS:
        raise SystemExit(f"NJ and NL must be multiples of {TILE_COLS}, the "
                         "columns resident in scratchpads per pass")
    if (ni * nl) % LANES:
        raise SystemExit(f"NI*NL must be a multiple of {LANES}, one slice per "
                         "lane of 2mm_2_hv")
    # validate() catches these too, but fail here with the knob to turn.
    if nk > 511 or nj > 511:
        raise SystemExit(
            f"NK={nk} (matB column) and NJ={nj} (matC column) must fit the "
            "512-word scratchpad")
    for label, nbytes, cap in (
            (f"a matB column preload (NJ*NK = {nj * nk})", nj * nk * ELEM,
             f"max NJ*NK = {((1 << 16) - 1) // ELEM}"),
            (f"a matC column preload (NL*NJ = {nl * nj})", nl * nj * ELEM,
             f"max NL*NJ = {((1 << 16) - 1) // ELEM}"),
            (f"a phase 2 stream (NI*NL/{LANES} = {ni * nl // LANES})",
             ni * nl // LANES * ELEM,
             f"max NI*NL = {LANES * ((1 << 16) - 1) // ELEM}")):
        if nbytes >= 1 << 16:
            raise SystemExit(f"{label} is {nbytes} bytes, past the 65535 the "
                             f"16-bit byte count holds ({cap})")
    if not 1 <= args.max_groups <= 255:
        raise SystemExit(f"--max-groups={args.max_groups}: a pass replays the "
                         "scratchpad that many times, which must fit the 8-bit "
                         "iters field")

    prog = build(ni, nj, nk, nl, args.io_map_mm, args.io_map_scale,
                 args.max_groups)
    prog.validate()

    note = (f"2mm {ni}x{nj}x{nk}x{nl}: matAB = matA*matB, then behind a "
            f"FENCE_SE matABC = matAB*matC, then behind another "
            f"matD = alpha*matABC + beta*matDin")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "mm_ab_kernel.h",
                                 "mm_abc_kernel.h", "scale_add_kernel.h",
                                 "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
