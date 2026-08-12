#!/usr/bin/env python3
"""Generate the STRELA gemver ISE/OSE descriptor tables (four chained phases).

PolyBench gemver's four statements over the three committed HV bitstreams:

    phase 0  update      mat_a2 = A + u1*v1^T + u2*v2^T     (gemver_1_hv)
    phase 1  matvec      vec_tmp = beta*(A2^T @ y)          (gemver_2_hv)
    phase 2  add         vec_x   = vec_tmp + z              (gemver_3_hv)
    phase 3  matvec      vec_w   = alpha*(A2 @ x)           (gemver_2_hv, again)

Four phases for four statements, and the interesting one is why phases 1 and 3
are not one. They run the same bitstream over the same *square* matrix, so they
reduce over the same N -- the thing that forces strela_atax's rectangular pair
apart -- and they are not two operands of one configuration either, which is what
strela_gesummv collapses into extra passes. What separates them is the **PE
constant**: the matrix-vector kernel folds a scalar into the replayed vector
through one constant-multiply PE, gemver needs beta there for x and alpha for w,
and a constant lives in the bitstream exactly like a delay_value does. Hence two
committed copies of one regress bitstream, patched differently by main.c. Folding
the scalars out into a later kernel is what strela_gesummv does to avoid this,
but gemver cannot: its only other kernel is a plain add with no constants at all,
and beta has to be applied before z is added. **A phase is a configuration, and a
constant is part of one.**

All three bitstreams come from elastic-cgra's committed regression, replayed into
kernel headers without re-running the mapper -- the matrix-vector one twice, under
two array names, so each phase has its own patchable copy in RAM:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_1_hv \\
        --array-name update_kernel \\
        -o sw/applications/strela_gemver/update_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_2_hv \\
        --array-name matvec_beta_kernel \\
        -o sw/applications/strela_gemver/matvec_beta_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_2_hv \\
        --array-name matvec_alpha_kernel \\
        -o sw/applications/strela_gemver/matvec_alpha_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_3_hv \\
        --array-name add_kernel \\
        -o sw/applications/strela_gemver/add_kernel.h
    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/gemver_1_hv/io_map.json \\
        sw/applications/strela_gemver/gemver_1_hv_io_map.json      # and _2_hv, _3_hv
    make gen-app-data PROJECT=strela_gemver   # or: python3 gen_descriptors.py

gemver_2_hv is byte-identical to gesummv_1_hv and gemver_3_hv to mvt_2_hv, same
io_map locations, so phases 1-3 are the schedules strela_gesummv and strela_mvt
already run. One io_map per DFG is committed next to the headers so descriptors.h
can be regenerated (it is gitignored) without the mapper; the io_map is per-DFG,
which is why both matvec copies share one.

Kernel contracts, which fix what each port means:

  gemver_1_hv (mapper/applications/gemver_1_hv/main.dot), two independent lanes
  of the rank-2 update, one matrix row each:
      output0 = input0 + input2*input6 + input4*input7      lane a
      output1 = input1 + input3*input6 + input5*input7      lane b
  input6 and input7 are the operands the two lanes *share* -- they land on the
  horizontal buses of rows 0 and 2, which fork one token to both lanes -- and
  input2/input4 (lane a) and input3/input5 (lane b) are per-lane scratchpads.
  That fork is what fixes the schedule below: the shared operands must be the
  ones that vary along the streamed line and the per-lane ones must be constant
  along it, so v1/v2 go on the buses and u1[i]/u2[i] in the PE scratchpads, with
  each lane streaming one row of A.

  gemver_2_hv (byte-identical to gesummv_1_hv, same io_map)
      input4        the vector, preloaded into a scratchpad and replayed once
                    per group of four lines; one PE multiplies it by a constant
                    -- beta or alpha here -- and broadcasts the product to all
                    four lanes over the row-0 horizontal bus
      input0..3     one matrix line each, streamed element by element
      output_k      = line(input_k) . const*vector, one word per line, decimated
                    by the lane accumulator's delay_value
  so a pass reduces four lines at a time, which is why N is padded to a multiple
  of four; the transposed product's lines are columns of A2 and the forward one's
  are rows, which is nothing but a change of stride.

  gemver_3_hv (byte-identical to mvt_2_hv, same io_map), two lanes of plain
  addition -- no constants, no accumulators, nothing to patch:
      output0 = input0 + input1        output1 = input2 + input3
  Here both lanes carry halves of the *same* vector add, x = tmp + z.

**Why one row per lane per pass in phase 0.** A scratchpad replays a fixed block
of words a whole number of times, and the update needs u1[i] held constant across
a whole row while v1[j] runs along it. Over a pass of R rows the u ports would
have to emit [u1[i0] x N, u1[i0+1] x N, ...], which is a run-length pattern and
not a repeat of anything -- so either R is 1, or the u scratchpad is reloaded
mid-pass, which is the `strela_memory.sv` race the fences below exist to avoid.
R=1 it is: two rows per pass, one per lane, and each pass reloads v1 and v2. That
costs one extra word of scratchpad traffic per element updated (2N loaded per 2N
elements), and the alternative -- swapping the roles so the per-lane scratchpads
hold halves of v and the shared buses carry the u scalars -- is strictly worse,
since it reloads 2N words to update only N elements per pass.

How the three solves land on the engines (the reason the emission order below is
not arbitrary):

  update:  ISE 0  input6 -> MEM_E 0 mode 1 (row-0 bus, shared)
           ISE 1  input3 -> MEM_E 1  + input4 -> MEM_W 2  + input0 -> north col 1
           ISE 2  input7 -> MEM_E 2 mode 1 (row-2 bus, shared) + input1 -> north col 2
           ISE 3  input2 -> MEM_W 0  + input5 -> MEM_E 3
           OSE 1  output0 -> ver bus 1     OSE 2  output1 -> ver bus 2
  matvec:  ISE 0  input0 -> ver bus 0      ISE 1  input2 -> north col 1
           ISE 2  input1 -> north col 2    ISE 3  input4 -> MEM_W 0 mode 0
                                                  + input3 -> north col 3
           OSE 0  output0 -> south col 0   OSE 1  output2 -> ver bus 1
           OSE 2  output1 -> ver bus 2     OSE 3  output3 -> ver bus 3
  add:     ISE 0  input1   ISE 1  input0   ISE 2  input2   ISE 3  input3
           OSE 0  output0 -> ver bus 0     OSE 3  output1 -> ver bus 3

ISE 1 and ISE 2 in the update, and ISE 3 in the matvec, carry both a scratchpad
and a direct stream, so their mem() calls are emitted before any stream():
descriptors within a pass keep call order, and an engine parked on a stream can
no longer release the scratchpad the fabric is waiting on. No output of any of
the three kernels is scratchpad-backed, so this app is free of the OSE-side
`strela_memory.sv` race that caps strela_mm's pass length.

Why the fences are not optional. Between passes of the same phase an ISE that ran
ahead would reach the next pass's TR_MEM_* while the fabric is still draining the
current replay: `strela_memory.sv` presents the last word of a replay from S_IDLE,
where `ready_o` is already high, so the new mem_param is accepted, the FSM leaves
for S_WR and that word is never handed to the fabric. Phase 0 reloads a scratchpad
every pass, so it fences every pass. Before a *phase*, the fence is what makes the
reconfiguration safe: an ISE re-entering TR_CONF clears its `conf_done_o`, which
drops the global `conf_reg` and re-gates every fabric handshake -- and this run is
one long chain, mat_a2 -> vec_tmp -> vec_x -> vec_w, where each phase reads back
what the previous phase's OSEs wrote, which only a FENCE_SE waits on both of.
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
ROWS = 4          # accumulator lanes of gemver_2_hv = matrix lines per group
LANES = 2         # independent lanes of gemver_1_hv and gemver_3_hv
MEM_DEPTH = 512   # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def groups_per_pass(line_groups):
    """Line groups in one matrix-vector pass: as many as the 8-bit `iters` field
    can replay the vector for. Nothing else there grows with the pass length --
    every output is a direct stream, so there is no scratchpad drain to keep
    short."""
    return max(1, min(line_groups, MAX_ITERS))


def stream_line(prog, port, sym, index, stride, count, max_bytes=MAX_BYTES):
    """Feed one matrix line to a lane, split if its span overflows 16 bits.

    A row of A2 is contiguous (stride ELEM) and never needs splitting; a column
    is N elements a row apart, so its span is the whole matrix. The accumulator
    counts tokens, not descriptors, so several back-to-back descriptors on one
    channel reduce exactly as one would.
    """
    per_desc = max(1, max_bytes // stride)
    for first in range(0, count, per_desc):
        block = min(per_desc, count - first)
        prog.stream(port, sym, index + first * (stride // ELEM),
                    stride=stride, count=block)


def rank2_update(prog, n):
    """Phase 0: mat_a2 = mat_a + u1*v1^T + u2*v2^T, two rows at a time.

    One pass per row pair: v1 and v2 are reloaded and replayed once each, the two
    u entries of the pair are replayed once per element of their row, and each
    lane streams its row of A and writes its row of A2.
    """
    for pair in range(n // LANES):
        rows = (LANES * pair, LANES * pair + 1)          # lane a, lane b
        if pair:
            prog.fence()                                 # FENCE_SE, all eight
        with prog:
            # The shared operands, on the row-0 and row-2 horizontal buses: one
            # replay of each feeds both lanes, which consume the same token.
            prog.mem("input6", "vec_v1", 0, stride=ELEM, count=n,
                     size=n, iters=1)
            prog.mem("input7", "vec_v2", 0, stride=ELEM, count=n,
                     size=n, iters=1)

            # The per-lane operands: one word each, held constant along the row
            # by replaying it once per element. ISE 1 and ISE 2 also carry a
            # stream below, so every mem() goes first.
            for port, sym, row in (("input2", "vec_u1", rows[0]),
                                   ("input3", "vec_u1", rows[1]),
                                   ("input4", "vec_u2", rows[0]),
                                   ("input5", "vec_u2", rows[1])):
                prog.mem(port, sym, row, stride=ELEM, count=1, size=1, iters=n)

            for lane, row in enumerate(rows):
                prog.stream(f"input{lane}", "mat_a", row * n,
                            stride=ELEM, count=n)
            for lane, row in enumerate(rows):
                prog.out(f"output{lane}", "mat_a2", row * n,
                         stride=ELEM, count=n)


def matvec(prog, out_sym, a_sym, vec_sym, line_base, line_stride, n, per_pass,
           max_bytes=MAX_BYTES):
    """A matrix-vector phase: out(n) = lines(a_sym) . const*vec_sym.

    A "line" is a column of A2 for the transposed product and a row for the
    forward one; `line_base(l)` gives its first element and `line_stride` the
    byte step between its elements, which is the only place the transpose shows
    up. The scalar is not here at all -- it is the constant PE main.c patches.

    One pass preloads the vector once, replays it once per group of ROWS lines,
    and streams the four lines of the group down the four lanes; lane k takes
    line ROWS*g+k and produces element ROWS*g+k of the result, so one output
    descriptor per lane walks the whole chunk with a stride of ROWS elements.
    """
    groups = n // ROWS
    for first in range(0, groups, per_pass):
        block = min(per_pass, groups - first)
        prog.fence()
        with prog:
            # The scratchpad first: ISE 3 must release MEM_W 0 before it blocks
            # on the input3 stream, and the vector is what every lane waits on.
            prog.mem("input4", vec_sym, 0, stride=ELEM, count=n,
                     size=n, iters=block)

            for lane in range(ROWS):
                for group in range(first, first + block):
                    stream_line(prog, f"input{lane}", a_sym,
                                line_base(ROWS * group + lane),
                                line_stride, n, max_bytes)

            # One word per line group on each lane, the accumulator's delayed
            # output; the lanes interleave, hence the ROWS-element stride.
            for lane in range(ROWS):
                prog.out(f"output{lane}", out_sym, ROWS * first + lane,
                         stride=ROWS * ELEM, count=block)


def build(n, io_map_update, io_map_matvec, io_map_add, per_pass,
          max_bytes=MAX_BYTES):
    prog = StreamProgram(
        io_map=io_map_update,
        kernel="update_kernel",
        app="strela_gemver",
        arrays={
            "mat_a": (n * n, ELEM, [n, n]),
            "mat_a2": (n * n, ELEM, [n, n]),
            "vec_u1": (n, ELEM),
            "vec_v1": (n, ELEM),
            "vec_u2": (n, ELEM),
            "vec_v2": (n, ELEM),
            "vec_y": (n, ELEM),
            "vec_z": (n, ELEM),
            "vec_tmp": (n, ELEM),
            "vec_x": (n, ELEM),
            "vec_w": (n, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("mat_a2", "vec_tmp", "vec_x", "vec_w")
    prog.conf_all()

    # ---- phase 0: the rank-2 update ----------------------------------------
    rank2_update(prog, n)

    # ---- phase 1: vec_tmp = beta*(A2^T @ y), lines are columns --------------
    prog.fence()
    prog.load_kernel("matvec_beta_kernel", io_map=io_map_matvec)
    matvec(prog, "vec_tmp", "mat_a2", "vec_y",
           line_base=lambda col: col, line_stride=n * ELEM,
           n=n, per_pass=per_pass, max_bytes=max_bytes)

    # ---- phase 2: vec_x = vec_tmp + z --------------------------------------
    # Two independent lanes over halves of one vector; any split works as long
    # as the three streams of a lane address the same elements.
    prog.fence()
    prog.load_kernel("add_kernel", io_map=io_map_add)
    span = n // LANES
    with prog:
        for lane in range(LANES):
            prog.stream(f"input{2 * lane}", "vec_tmp", lane * span,
                        stride=ELEM, count=span)
            prog.stream(f"input{2 * lane + 1}", "vec_z", lane * span,
                        stride=ELEM, count=span)
            prog.out(f"output{lane}", "vec_x", lane * span,
                     stride=ELEM, count=span)

    # ---- phase 3: vec_w = alpha*(A2 @ x), lines are rows --------------------
    # Same bitstream as phase 1 and the same reduction length; the second copy
    # exists only because the constant differs.
    prog.fence()
    prog.load_kernel("matvec_alpha_kernel", io_map=io_map_matvec)
    matvec(prog, "vec_w", "mat_a2", "vec_x",
           line_base=lambda row: row * n, line_stride=ELEM,
           n=n, per_pass=per_pass, max_bytes=max_bytes)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA gemver ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data PROJECT=strela_gemver`
runs both with no arguments, so change N in both together. It is rounded up to a
multiple of 4 here exactly as it is there.
""")
    parser.add_argument("N", type=int, nargs="?", default=120,
                        help="order of A and length of every vector "
                             "(default: 120, PolyBench SMALL)")
    parser.add_argument("--groups-per-pass", type=int, metavar="G",
                        help="line groups (of 4 lines) per matrix-vector pass, "
                             "i.e. how many times the vector is replayed from "
                             "one preload (default: the whole matrix, capped by "
                             "the 8-bit iters field)")
    parser.add_argument("--max-stream-bytes", type=int, default=MAX_BYTES,
                        metavar="B",
                        help="split a matrix line across descriptors past this "
                             "many bytes (default: %(default)s, the 16-bit "
                             "byte count; lower it only to exercise the split)")
    parser.add_argument("--io-map-update",
                        default=os.path.join(_HERE, "gemver_1_hv_io_map.json"),
                        help="io_map.json of the rank-2 update kernel "
                             "(default: the copy committed next to its header)")
    parser.add_argument("--io-map-matvec",
                        default=os.path.join(_HERE, "gemver_2_hv_io_map.json"),
                        help="io_map.json of the matrix-vector kernel, shared by "
                             "both of its phases (the io_map is per DFG)")
    parser.add_argument("--io-map-add",
                        default=os.path.join(_HERE, "gemver_3_hv_io_map.json"),
                        help="io_map.json of the two-lane add kernel")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n = args.N
    if n < 1:
        raise SystemExit(f"N={n} must be at least 1")
    if not 1 <= args.max_stream_bytes <= MAX_BYTES:
        raise SystemExit(f"--max-stream-bytes must be in 1..{MAX_BYTES}")

    n_pad = -(-n // ROWS) * ROWS
    per_pass = args.groups_per_pass or groups_per_pass(n_pad // ROWS)

    # validate() catches none of these -- size, iters and the byte counts all
    # wrap silently.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n_pad > limit:
        raise SystemExit(f"a preloaded vector is {n_pad} words, past the {limit} "
                         "a 9-bit size field and a 512-word scratchpad allow; "
                         "lower N")
    # Specific to the update: it replays one u entry per element of a row, so the
    # row length itself has to fit the iters field.
    if n_pad > MAX_ITERS:
        raise SystemExit(f"the rank-2 update replays u1[i]/u2[i] {n_pad} times "
                         f"per row, past the {MAX_ITERS} an 8-bit iters field "
                         "holds; lower N")
    if not 1 <= per_pass <= MAX_ITERS:
        raise SystemExit(f"--groups-per-pass={per_pass}: a pass replays the "
                         f"vector that many times, which must fit the "
                         f"{MAX_ITERS} the 8-bit iters field holds")
    for label, nbytes in (
            (f"a row stream (N_PAD = {n_pad})", n_pad * ELEM),
            (f"a lane result ({per_pass} line groups)", per_pass * ROWS * ELEM),
            (f"an add-kernel lane (N_PAD/{LANES} = {n_pad // LANES})",
             n_pad // LANES * ELEM)):
        if nbytes > MAX_BYTES:
            raise SystemExit(f"{label} is {nbytes} bytes, past the {MAX_BYTES} "
                             "a 16-bit byte count holds")

    prog = build(n_pad, args.io_map_update, args.io_map_matvec, args.io_map_add,
                 per_pass, args.max_stream_bytes)
    prog.validate()

    passes = -(-(n_pad // ROWS) // per_pass)
    col_bytes = n_pad * n_pad * ELEM
    split = -(-col_bytes // args.max_stream_bytes)
    note = (f"gemver {n}x{n} (padded to {n_pad}): mat_a2 = A + u1*v1^T + "
            f"u2*v2^T in {n_pad // LANES} two-row passes, then behind FENCE_SEs "
            f"vec_tmp = beta*(A2^T@y) and vec_x = vec_tmp + z and "
            f"vec_w = alpha*(A2@x); each product is {passes} pass(es) of up to "
            f"{per_pass} four-line groups, and a column of A2 spans {col_bytes} "
            f"bytes and is streamed as {split} descriptor(s)")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "update_kernel.h",
                                 "matvec_beta_kernel.h", "add_kernel.h",
                                 "matvec_alpha_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
