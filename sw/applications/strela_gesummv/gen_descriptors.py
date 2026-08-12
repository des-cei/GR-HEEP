#!/usr/bin/env python3
"""Generate the STRELA gesummv ISE/OSE descriptor tables (two chained kernels).

PolyBench gesummv, `y = alpha*A@x + beta*B@x`, split over the two committed HV
bitstreams that elastic-cgra ships for it:

    phase 0  matvec     vec_ax = A @ x      (gesummv_1_hv)
             matvec     vec_bx = B @ x      (the same configuration, more passes)
    phase 1  scale_add  vec_y  = alpha*vec_ax + beta*vec_bx   (gesummv_2_hv)

The interesting part is that the two matrix-vector products are *not* two
phases. A phase is one loaded bitstream, and both products want the same one:
same reduction length (N, the accumulators' delay_value) and same PE constant,
because alpha and beta are applied by scale_add at the end rather than folded
into the products -- which is also how PolyBench writes the kernel. So B@x is
just more passes of the configuration A@x already runs under, and the run has a
single reconfiguration in it. Contrast strela_2mm, whose two matmul phases *do*
need one loaded bitstream each: their reduction lengths differ, and
`delay_value` lives in the bitstream.

Both bitstreams come from elastic-cgra's committed regression, replayed into
kernel headers without re-running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gesummv_1_hv \\
        --array-name matvec_kernel \\
        -o sw/applications/strela_gesummv/matvec_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gesummv_2_hv \\
        --array-name scale_add_kernel \\
        -o sw/applications/strela_gesummv/scale_add_kernel.h
    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/gesummv_1_hv/io_map.json \\
        sw/applications/strela_gesummv/gesummv_1_hv_io_map.json   # and _2_hv
    make gen-app-data PROJECT=strela_gesummv   # or: python3 gen_descriptors.py

One io_map per DFG is committed next to the kernel headers so descriptors.h can
be regenerated (it is gitignored) without the mapper. They describe one solve
each; a re-solve reshuffles the engine assignment, so keep each io_map in step
with its header.

Kernel contracts, which fix what each port means:

  gesummv_1_hv (mapper/applications/gesummv_1_hv/main.dot)
      input4        x, preloaded into a scratchpad and replayed once per row
                    group; one PE multiplies it by a constant and broadcasts
                    the result to all four lanes over a horizontal bus
      input0..3     one matrix row each, streamed element by element
      output_k      = row(input_k) . x, one word per row, decimated by the
                    lane accumulator's delay_value
  so a pass of the fabric reduces four rows at a time, which is why M must be a
  multiple of 4 (gen_data.py pads it).

  gesummv_2_hv (mapper/applications/gesummv_2_hv/main.dot -- byte-identical to
  2mm_2_hv and gemm_2_hv, same io_map), two independent lanes:
      output0 = const(input0)*input0 + const(input1)*input1
      output1 = const(input2)*input2 + const(input3)*input3
  so each lane takes one half of vec_ax and vec_bx, with the vec_ax operand
  scaled by alpha and the vec_bx operand by beta. main.c patches those four
  constants (the DFG ships a placeholder 3).

How this solve lands on the engines (the reason the emission order below is not
arbitrary):

    ISE 0  input0 -> router 0 vertical bus
    ISE 1  input2 -> north of column 1
    ISE 2  input1 -> north of column 2
    ISE 3  input4 -> MEM_W 0 mode 0  +  input3 -> north of column 3
    OSE 0  output0 -> south of column 0     OSE 1  output2 -> ver bus 1
    OSE 2  output1 -> ver bus 2             OSE 3  output3 -> ver bus 3

ISE 3 carries both the scratchpad and a direct stream, so its mem() is emitted
before its stream()s: descriptors within a pass keep call order, and an engine
parked on a stream can no longer release the scratchpad the fabric is waiting
on. No output is scratchpad-backed here, so this kernel is free of the OSE-side
`strela_memory.sv` race that caps strela_mm's pass length.

Why the fences are not optional. Between passes of the *same* phase, an ISE that
runs ahead would reach the next pass's TR_MEM_W while the fabric is still
draining the current replay of x: `strela_memory.sv` presents the last word of a
replay from S_IDLE, where `ready_o` is already high, so the new mem_param is
accepted, the FSM leaves for S_WR and that word is never handed to the fabric
(the same deadlock strela_gesummv_single documents). Before a *phase*, the fence
is what makes the reconfiguration safe: an ISE re-entering TR_CONF clears its
`conf_done_o`, which drops the global `conf_reg` and re-gates every fabric
handshake, so anything still in flight would hang -- and vec_ax/vec_bx are
written by this phase's OSEs and read back by the next one's ISEs, which only
FENCE_SE waits on both of.
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
ROWS = 4          # accumulator lanes of gesummv_1_hv = rows reduced per group
LANES = 2         # independent lanes of gesummv_2_hv
MEM_DEPTH = 512   # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def groups_per_pass(row_groups):
    """Row groups in one pass: as many as the 8-bit `iters` field can replay x
    for. Nothing else here grows with the pass length -- every output is a
    direct stream, so there is no scratchpad drain to keep short."""
    return max(1, min(row_groups, MAX_ITERS))


def matvec(prog, out_sym, a_sym, n, row_groups, per_pass, fence_first):
    """Emit the passes computing `out(4*row_groups) = a(4*row_groups x n) @ x`.

    One pass preloads x once, replays it once per row group, and streams the
    four rows of each group down the four lanes; lane k takes row 4*g+k and
    produces element 4*g+k of the result, so one output descriptor per lane
    walks the whole chunk with a stride of four elements.
    """
    for first in range(0, row_groups, per_pass):
        block = min(per_pass, row_groups - first)
        if fence_first or first:
            prog.fence()                       # FENCE_SE, all eight engines
        with prog:
            # The scratchpad first: ISE 3 must release MEM_W 0 before it blocks
            # on the input3 stream, and x is the operand every lane waits on.
            prog.mem("input4", "vec_x", 0, stride=ELEM, count=n,
                     size=n, iters=block)

            # One descriptor per row: a row is n contiguous elements, and the
            # jump to the lane's next row is not a stride the DMA can take.
            for lane in range(ROWS):
                for group in range(first, first + block):
                    prog.stream(f"input{lane}", a_sym, (ROWS * group + lane) * n,
                                stride=ELEM, count=n)

            # One word per row group on each lane, the accumulator's delayed
            # output; the lanes interleave, hence the ROWS-element stride.
            for lane in range(ROWS):
                prog.out(f"output{lane}", out_sym, ROWS * first + lane,
                         stride=ROWS * ELEM, count=block)


def build(m_pad, n, io_map_matvec, io_map_scale, per_pass=None):
    row_groups = m_pad // ROWS
    per_pass = per_pass or groups_per_pass(row_groups)

    prog = StreamProgram(
        io_map=io_map_matvec,
        kernel="matvec_kernel",
        app="strela_gesummv",
        arrays={
            "mat_a": (m_pad * n, ELEM, [m_pad, n]),
            "mat_b": (m_pad * n, ELEM, [m_pad, n]),
            "vec_x": (n, ELEM),
            "vec_ax": (m_pad, ELEM),
            "vec_bx": (m_pad, ELEM),
            "vec_y": (m_pad, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("vec_ax", "vec_bx", "vec_y")
    prog.conf_all()

    # ---- phase 0: the two matrix-vector products, one configuration ---------
    matvec(prog, "vec_ax", "mat_a", n, row_groups, per_pass, fence_first=False)
    matvec(prog, "vec_bx", "mat_b", n, row_groups, per_pass, fence_first=True)

    # ---- phase 1: vec_y = alpha*vec_ax + beta*vec_bx ------------------------
    # Lane k owns the k-th half of the vector; the lanes are independent, so any
    # split works as long as the three streams of a lane address the same
    # elements.
    prog.fence()
    prog.load_kernel("scale_add_kernel", io_map=io_map_scale)
    span = m_pad // LANES
    with prog:
        for lane in range(LANES):
            prog.stream(f"input{2 * lane}", "vec_ax", lane * span,
                        stride=ELEM, count=span)
            prog.stream(f"input{2 * lane + 1}", "vec_bx", lane * span,
                        stride=ELEM, count=span)
            prog.out(f"output{lane}", "vec_y", lane * span,
                     stride=ELEM, count=span)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA gesummv ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_data.py's: `make gen-app-data
PROJECT=strela_gesummv` runs both with no arguments, so change M and --cols in
both together. M is rounded up to a multiple of 4 here exactly as it is there.
""")
    parser.add_argument("M", type=int, nargs="?", default=90,
                        help="rows of A and B, i.e. length of y (default: 90, "
                             "PolyBench SMALL)")
    parser.add_argument("-n", "--cols", type=int, default=90,
                        help="columns of A and B (default: 90, PolyBench SMALL)")
    parser.add_argument("--groups-per-pass", type=int, metavar="G",
                        help="row groups (of 4 rows) per pass, i.e. how many "
                             "times x is replayed from one preload (default: "
                             "the whole matrix, capped by the 8-bit iters field)")
    parser.add_argument("--io-map-matvec",
                        default=os.path.join(_HERE, "gesummv_1_hv_io_map.json"),
                        help="io_map.json of the matrix-vector kernel "
                             "(default: the copy committed next to its header)")
    parser.add_argument("--io-map-scale",
                        default=os.path.join(_HERE, "gesummv_2_hv_io_map.json"),
                        help="io_map.json of the scale-and-add kernel")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    m, n = args.M, args.cols
    if m < 1:
        raise SystemExit(f"M={m} must be at least 1")
    if n < 1:
        raise SystemExit(f"--cols {n} must be at least 1")

    m_pad = -(-m // ROWS) * ROWS
    row_groups = m_pad // ROWS
    per_pass = args.groups_per_pass or groups_per_pass(row_groups)

    # validate() catches none of these -- size, iters and the byte counts all
    # wrap silently.
    if n > min(MAX_SIZE, MEM_DEPTH):
        raise SystemExit(f"x is {n} words, past the {min(MAX_SIZE, MEM_DEPTH)} "
                         "a 9-bit size field and a 512-word scratchpad allow; "
                         "lower --cols")
    if not 1 <= per_pass <= MAX_ITERS:
        raise SystemExit(f"--groups-per-pass={per_pass}: a pass replays x that "
                         f"many times, which must fit the {MAX_ITERS} the "
                         "8-bit iters field holds")
    for label, nbytes in (
            (f"a row stream (N = {n})", n * ELEM),
            (f"a lane result ({per_pass} row groups)", per_pass * ROWS * ELEM),
            (f"a scale-add lane (M_PAD/{LANES} = {m_pad // LANES})",
             m_pad // LANES * ELEM)):
        if nbytes > MAX_BYTES:
            raise SystemExit(f"{label} is {nbytes} bytes, past the {MAX_BYTES} "
                             "a 16-bit byte count holds")

    prog = build(m_pad, n, args.io_map_matvec, args.io_map_scale, per_pass)
    prog.validate()

    passes = -(-row_groups // per_pass)
    note = (f"gesummv {m}x{n} (padded to {m_pad} rows): vec_ax = A@x and "
            f"vec_bx = B@x under one configuration, {passes} pass(es) each of "
            f"up to {per_pass} four-row groups, then behind a FENCE_SE "
            f"vec_y = alpha*vec_ax + beta*vec_bx")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "matvec_kernel.h",
                                 "scale_add_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
