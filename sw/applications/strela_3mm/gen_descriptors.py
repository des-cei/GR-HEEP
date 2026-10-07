#!/usr/bin/env python3
"""Generate the STRELA 3mm ISE/OSE descriptor tables (three chained kernels).

PolyBench 3mm is three matrix products, each of which runs the *same* bitstream
(3mm_hv, byte-identical to mm_hv, so every phase is strela_mm's schedule) back
to back in a single STRELA execution, separated by FENCE_SEs:

    phase 0  mm_e   matE = matA(NI x NK)  * matB(NK x NJ)      reduction NK
    phase 1  mm_f   matF = matC(NJ x NM)  * matD(NM x NL)      reduction NM
    phase 2  mm_g   matG = matE(NI x NJ)  * matF(NJ x NL)      reduction NJ

The one bitstream is held in RAM three times because the accumulator delay *is*
the reduction length, and the three products reduce over NK, NM and NJ; main.c
patches each copy separately before the run. That is also why the three phases
cannot share one TR_CONF -- a phase is one loaded bitstream.

Row padding. One pass consumes four rows of the left operand, so every phase
needs a row count that is a multiple of 4. NI is, but phase 1's left operand
matC has NJ rows (50 by default), so matC and matF are allocated with NJ_PAD
rows (52) and the added rows of matC zeroed by gen_data.py. Phase 1 computes
all NJ_PAD rows -- the destination-coverage check wants matF written exactly
once, and the padding rows come out zero -- while phase 2 reduces over the
first NJ rows of matF only, so the padding never reaches matG.

All three copies come from elastic-cgra's committed regression, replayed into
kernel headers without re-running the mapper:

    for k in e f g; do
      scripts/gr_heep_env.sh python3 scripts/regress2kernel.py 3mm_hv \\
          --array-name mm_${k}_kernel \\
          -o sw/applications/strela_3mm/mm_${k}_kernel.h
    done
    cp hw/vendor/strela-v2/rtl/elastic-cgra/regress/4x4-HV/3mm_hv/io_map.json \\
        sw/applications/strela_3mm/3mm_hv_io_map.json
    make gen-app-data PROJECT=strela_3mm      # or: python3 gen_descriptors.py

The single io_map is committed next to the kernel headers so descriptors.h can
be regenerated (it is gitignored) without the mapper; all three phases share it
because they share the solve. Keep it in step with the headers: a re-solve
reshuffles the engine assignment.

Why the fences are not optional. An ISE that re-enters TR_CONF clears its
`conf_done_o` (rtl/strela_input_stream_engine.sv), which drops the global
`conf_reg` and re-gates every fabric handshake -- so anything still in flight
when the next phase starts configuring would hang. The fence also makes the
hand-off through memory safe: matE and matF are written by one phase's OSEs and
read back by phase 2's ISEs, and FENCE_SE is the only fence that waits on both
kinds of engine. The rising edge of `conf_reg` then pulses `clr_cgra`, so the
fabric is flushed of the previous phase's tokens (including the accumulators'
partial sums) before the next one streams anything.

Kernel contract (mapper/applications/3mm_hv/main.dot -- the same DFG, io_map
and bitstream as mm_hv), which fixes what each port means:

    input0..3        four rows of the left operand, one element at a time
    input4, input5   two columns of the right operand, preloaded into a
                     scratchpad and replayed once per row group
    output[j + 4*b]  = row(input_j) . col(input_{4+b})

so one pass of the fabric computes a 4x2 tile of the product.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 everywhere
SEW = 32
ROWS = 4          # the STRELA shell hard-codes a 4x4 fabric
TILE_COLS = 2     # right-operand columns resident in scratchpads per pass

# Row groups handled by a single pass: the ISE scratchpad replay count, and the
# number of results each OSE-side scratchpad buffers before it is drained.
#
# This is a workaround for a hardware race, not a modelling choice. `output7` --
# the only matmul output that reaches its scratchpad over a router's horizontal
# bus (MEM_W1 in mode 1, see 3mm_hv_io_map.json; the other four scratchpad
# outputs are mode 0 PE-border ports) -- loses the *last* word of its drain once
# the drain is long enough for the OSE's obione FIFO to fill. In
# rtl/strela_memory.sv the `valid_out` register that presents that word is
# shared between the two directions, and the S_IDLE arm of its update guard
# accepts either of them, so while the FSM sits in S_IDLE with the last word
# pending, a `hor_ready_i` from the fabric side clears it before the OSE's
# `ose_ready_i` takes it. The OSE then waits forever for its last element: the
# run hangs in that pass with exactly one element of the product unwritten.
#
# The boundary moves with the number of columns of the right operand, which sets
# the OSE's write stride and therefore how fast that FIFO drains, so this is a
# race rather than a clean depth: 8 is a margin, not a proof, and the real fix
# belongs in strela_memory.sv. Keep it in step with strela_mm, strela_gemm and
# strela_2mm -- they all run the same solve.
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


def build(ni, nj, nk, nl, nm, nj_pad, io_map, max_groups=MAX_GROUPS):
    prog = StreamProgram(
        io_map=io_map,
        kernel="mm_e_kernel",
        app="strela_3mm",
        arrays={
            "matA": (ni * nk, ELEM, [ni, nk]),
            "matB": (nk * nj, ELEM, [nk, nj]),
            "matC": (nj_pad * nm, ELEM, [nj_pad, nm]),
            "matD": (nm * nl, ELEM, [nm, nl]),
            "matE": (ni * nj, ELEM, [ni, nj]),
            "matF": (nj_pad * nl, ELEM, [nj_pad, nl]),
            "matG": (ni * nl, ELEM, [ni, nl]),
        },
        sew=SEW,
    )
    prog.mark_output("matE", "matF", "matG")
    prog.conf_all()

    # ---- phase 0: matE = matA * matB, reduction NK --------------------------
    matmul_phase(prog, "matE", "matA", "matB", ni, nj, nk, max_groups)

    # ---- phase 1: matF = matC * matD, reduction NM --------------------------
    # NJ_PAD rows, so the last group is the zero padding; phase 2 reads only
    # the first NJ rows back.
    prog.fence()                                  # FENCE_SE, all eight engines
    prog.load_kernel("mm_f_kernel", io_map=io_map)
    matmul_phase(prog, "matF", "matC", "matD", nj_pad, nl, nm, max_groups)

    # ---- phase 2: matG = matE * matF, reduction NJ --------------------------
    prog.fence()
    prog.load_kernel("mm_g_kernel", io_map=io_map)
    matmul_phase(prog, "matG", "matE", "matF", ni, nl, nj, max_groups)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA 3mm ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data PROJECT=strela_3mm`
runs both with no arguments, so change the dimensions in both together.
""")
    parser.add_argument("NI", type=int, nargs="?", default=40,
                        help="rows of A, E and G (default: 40)")
    parser.add_argument("NJ", type=int, nargs="?", default=50,
                        help="cols of B / rows of C and F (default: 50)")
    parser.add_argument("NK", type=int, nargs="?", default=60,
                        help="cols of A / rows of B (default: 60)")
    parser.add_argument("NL", type=int, nargs="?", default=70,
                        help="cols of D, F and G (default: 70)")
    parser.add_argument("NM", type=int, nargs="?", default=80,
                        help="cols of C / rows of D (default: 80)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "3mm_hv_io_map.json"),
                        help="io_map.json of the matmul kernel, shared by all "
                             "three phases (default: the copy committed next "
                             "to the kernel headers)")
    parser.add_argument("--max-groups", type=int, default=MAX_GROUPS,
                        metavar="N",
                        help=f"row groups per pass (default: {MAX_GROUPS}); "
                             "see MAX_GROUPS for why this is capped")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    ni, nj, nk, nl, nm = args.NI, args.NJ, args.NK, args.NL, args.NM
    nj_pad = -(-nj // ROWS) * ROWS      # must match gen_data.py's NJ_PAD

    # Phases 0 and 2 stream NI rows and phase 1 streams NJ_PAD, four at a time;
    # the tiled column counts are NJ (phase 0) and NL (phases 1 and 2).
    if ni % ROWS:
        raise SystemExit(f"NI must be a multiple of {ROWS}, one row group per "
                         "pass (NJ needs no such constraint: matC and matF are "
                         f"padded to NJ_PAD = {nj_pad} rows)")
    if nj % TILE_COLS or nl % TILE_COLS:
        raise SystemExit(f"NJ and NL must be multiples of {TILE_COLS}, the "
                         "columns resident in scratchpads per pass")
    # validate() catches these too, but fail here with the knob to turn.
    for label, depth in ((f"NK={nk} (matB column)", nk),
                         (f"NM={nm} (matD column)", nm),
                         (f"NJ={nj} (matF column)", nj)):
        if depth > 511:
            raise SystemExit(f"{label} must fit the 512-word scratchpad")
    for label, nbytes, cap in (
            (f"a matB column preload (NJ*NK = {nj * nk})", nj * nk * ELEM,
             "NJ*NK"),
            (f"a matD column preload (NL*NM = {nl * nm})", nl * nm * ELEM,
             "NL*NM"),
            (f"a matF column preload (NL*NJ = {nl * nj})", nl * nj * ELEM,
             "NL*NJ")):
        if nbytes >= 1 << 16:
            raise SystemExit(
                f"{label} is {nbytes} bytes, past the 65535 the 16-bit byte "
                f"count holds (max {cap} = {((1 << 16) - 1) // ELEM})")
    if not 1 <= args.max_groups <= 255:
        raise SystemExit(f"--max-groups={args.max_groups}: a pass replays the "
                         "scratchpad that many times, which must fit the 8-bit "
                         "iters field")

    prog = build(ni, nj, nk, nl, nm, nj_pad, args.io_map, args.max_groups)
    prog.validate()

    note = (f"3mm {ni}x{nj}x{nk}x{nl}x{nm} (matC/matF padded to {nj_pad} rows): "
            f"matE = matA*matB, then behind a FENCE_SE matF = matC*matD, then "
            f"behind another matG = matE*matF")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "mm_e_kernel.h", "mm_f_kernel.h",
                                 "mm_g_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
