#!/usr/bin/env python3
"""Generate the STRELA matmul ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that `make map-bitstream PROJECT=mm_hv` writes next to the bitstream, resolved
by STRELA's shared binding layer. Regenerate the bitstream and the descriptors
together:

    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
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
N/2 column pairs of B and, inside each, over the M/4 row groups of A.
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "ceimm_upm_strela", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

DTYPE_INFO = {
    "int8": {"sew": 8, "ctype": "int8_t"},
    "int16": {"sew": 16, "ctype": "int16_t"},
    "int32": {"sew": 32, "ctype": "int32_t"},
}

ROWS = 4          # the STRELA shell hard-codes a 4x4 fabric
TILE_COLS = 2     # B columns resident in scratchpads per pass


def build(m, k, n, in_sew, in_bytes, acc_sew, acc_bytes, io_map):
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
        with prog:
            # Preload the two B columns; each is replayed once per row group.
            for b, col in enumerate(b_cols):
                prog.mem(f"input{4 + b}", "matB", col,
                         stride=n * in_bytes, count=k, size=k, iters=row_groups)

            # Stream the A rows: ISE carrying input_j gets rows j, j+4, ...
            for j in range(ROWS):
                for group in range(row_groups):
                    prog.stream(f"input{j}", "matA", (ROWS * group + j) * k,
                                stride=in_bytes, count=k)

            # One descriptor per output walks the row groups, ROWS rows apart.
            for b, col in enumerate(b_cols):
                for j in range(ROWS):
                    prog.out(f"output{j + ROWS * b}", "matC", j * n + col,
                             stride=ROWS * n * acc_bytes, count=row_groups)
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
                        help="rows of A and C (default: 8)")
    parser.add_argument("K", type=int, nargs="?", default=64,
                        help="cols of A / rows of B (default: 8)")
    parser.add_argument("N", type=int, nargs="?", default=64,
                        help="cols of B and C (default: 8)")
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

    info = DTYPE_INFO[args.dtype]
    in_sew, in_bytes = info["sew"], info["sew"] // 8
    if args.mode == "accum32":
        acc_sew, acc_ctype = 32, "int32_t"
    else:
        acc_sew, acc_ctype = in_sew, info["ctype"]

    prog = build(m, k, n, in_sew, in_bytes, acc_sew, acc_sew // 8, args.io_map)
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
