#!/usr/bin/env python3
"""Generate the STRELA mvt ISE/OSE descriptor tables (two phases, three ops).

PolyBench mvt accumulates one square matrix onto two vectors, once in each
direction:

    phase 0  matvec   t1 = A  @ y_1    lines are rows of A       (mvt_1_hv)
             matvec   t2 = A^T @ y_2   lines are columns of A    (same config)
    phase 1  add      x1 = x1_in + t1  lane 0                    (mvt_2_hv)
                      x2 = x2_in + t2  lane 1

Both bitstreams come from elastic-cgra's committed regression, replayed into
kernel headers without re-running the mapper:

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py mvt_1_hv \\
        --array-name matvec_kernel \\
        -o sw/applications/strela_mvt/matvec_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py mvt_2_hv \\
        --array-name add_kernel \\
        -o sw/applications/strela_mvt/add_kernel.h
    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    cp $CG/regress/4x4-HV/mvt_1_hv/io_map.json \\
        sw/applications/strela_mvt/mvt_1_hv_io_map.json      # and mvt_2_hv
    make gen-app-data PROJECT=strela_mvt   # or: python3 gen_descriptors.py

One io_map per DFG is committed next to the kernel headers so descriptors.h can
be regenerated (it is gitignored) without the mapper. They describe one solve
each; a re-solve reshuffles the engine assignment, so keep each io_map in step
with its header.

**Three operations, two phases.** A@y_1 and A^T@y_2 are not two phases: they
reduce over the same N -- A is square -- and want the same PE settings, so the
transposed product is just more passes of the configuration the first one
already runs under, with one FENCE_SE between them and one loaded copy of
`matvec_kernel`. This is the same lesson strela_gesummv teaches with two
matrices, seen from the transpose side, and the exact counterpoint to
strela_atax and strela_bicg: those run this *identical* pair of products over a
*rectangular* matrix, where the two reductions have different lengths, and since
`delay_value` lives in the bitstream they are forced into two phases with two
patched copies. A phase is a configuration, not an operand and not a direction;
here squareness is what collapses the pair into one.

**The transpose costs nothing but a stride.** t2 never transposes A in memory. A
line of A^T is a column of A -- N elements one row apart -- and a descriptor
already carries an arbitrary stride, so the only difference between the two
products is `stride=ELEM, count=N` for a row versus `stride=N*ELEM, count=N` for
a column. What it *does* cost is span: a column descriptor reaches across the
whole matrix, so its byte count is N*N*4 rather than N*4, and a 16-bit byte
count caps that at 65535. PolyBench SMALL (120x120 -> 57600 bytes) fits with
little room, so `stream_line()` below splits a line across several descriptors
when it has to; consecutive descriptors on one channel are one continuous token
stream as far as the accumulator is concerned, which counts tokens and not
descriptors.

Kernel contracts, which fix what each port means:

  mvt_1_hv (mapper/applications/mvt_1_hv/main.dot -- byte-identical to atax_hv
  and bicg_hv, same io_map)
      input4        the y vector, preloaded into a scratchpad and replayed once
                    per group of four lines; it lands on router 0's *horizontal
                    bus* (MEM_W 0 mode 1), which broadcasts it to all four lanes
                    directly -- unlike gesummv_1_hv there is no
                    constant-multiply PE in the path, so nothing but
                    delay_value has to be patched
      input0..3     one matrix line each, streamed element by element
      output_k      = line(input_k) . y, one word per line, decimated by the
                    lane accumulator's delay_value

  mvt_2_hv (mapper/applications/mvt_2_hv/main.dot), two independent lanes of
  plain addition -- no constants, no accumulators, nothing to patch:
      output0 = input0 + input1
      output1 = input2 + input3
  so lane 0 takes (x1_in, t1) and lane 1 takes (x2_in, t2). Unlike gemm_2_hv the
  two lanes here carry the two *different* output vectors rather than halves of
  one, which is why each lane streams a whole N-element vector.

How the two solves land on the engines (the reason the emission order below is
not arbitrary):

  matvec:  ISE 0  input3 -> north col 0    ISE 1  input1 -> north col 1
           ISE 2  input2 -> north col 2    ISE 3  input4 -> MEM_W 0 mode 1
                                                  + input0 -> north col 3
           OSE 0  output3 -> ver bus 0     OSE 1  output1 -> ver bus 1
           OSE 2  output2 -> ver bus 2     OSE 3  output0 -> ver bus 3
  add:     ISE 0  input1                   ISE 1  input0
           ISE 2  input2                   ISE 3  input3    (all north borders)
           OSE 0  output0 -> ver bus 0     OSE 3  output1 -> ver bus 3

In the matvec phase ISE 3 carries both the scratchpad and a direct stream, so
its `mem()` is emitted before any `stream()`: descriptors within a pass keep
call order, and an engine parked on a stream can no longer release the
scratchpad every lane is waiting on. No output of either kernel is
scratchpad-backed, so this app is free of the OSE-side `strela_memory.sv` race
that caps strela_mm's pass length.

Why there is one fence and not two. A barrier between passes of the same phase
is real: an ISE that ran ahead would reach the next pass's TR_MEM_W while the
fabric is still draining the current replay of y, and `strela_memory.sv`
presents the last word of a replay from S_IDLE, where `ready_o` is already high
-- so the new descriptor is accepted, the FSM leaves for S_WR and that word is
never handed to the fabric. But that is a hazard of *loading*, and the two
products do not load. y_1 and y_2 are 2*N_PAD words together, well inside the
512-word scratchpad, so they are parked once with `iters=0` and each product
re-points the replay with a param-only `mem_param()`: word 0 alone, no bus
traffic and no SRAM write, so there is nothing to clobber `data_out` with and
the ISE simply blocks on the scratchpad's `ready_o` until the replays it is
replacing have been issued. Nothing in the phase writes SRAM after that one
park, so the phase needs no barrier at all -- not between the products and not
between the chunks `--groups-per-pass` splits them into, which are now only a
descriptor-granularity knob. The only FENCE_SE left is the phase boundary, where
it is what makes the reconfiguration safe: an ISE re-entering TR_CONF clears its
`conf_done_o`, which drops the global `conf_reg` and re-gates every fabric
handshake -- and t1/t2 are written by phase 0's OSEs and read straight back by
phase 1's ISEs, which only a FENCE_SE waits on both of.

What orders the products without a barrier is the fabric. y reaches all four
lanes through one fork, so no lane can start reducing a column of A against y_2
while another is still on a row against y_1; the accumulators emit t1's words
before t2's on every lane, which is what lets one output descriptor per product
per lane collect them. Engines that run ahead into t2's lines only queue tokens
with nothing to reduce against yet. Note this is the *opposite* conclusion to
strela_fw, whose per-pivot barriers survive an identical-looking audit finding:
there the next step reads a buffer the current one writes, and no amount of
scratchpad parking removes a read-after-write through main memory.
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
ROWS = 4          # accumulator lanes of mvt_1_hv = matrix lines per group
PRODUCTS = 2      # A@y_1 and A^T@y_2, whose vectors share one scratchpad load
MEM_DEPTH = 512   # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def groups_per_pass(groups):
    """Line groups in one pass: as many as the 8-bit `iters` field can replay
    the y vector for. Nothing else here grows with the pass length -- every
    output is a direct stream, so there is no scratchpad drain to keep short."""
    return max(1, min(groups, MAX_ITERS))


def stream_line(prog, port, sym, index, stride, count, max_bytes=MAX_BYTES):
    """Feed one matrix line to a lane, split if its span overflows 16 bits.

    A row of A is contiguous (stride ELEM) and never needs splitting; a column
    is N elements a row apart, so its span is the whole matrix. The accumulator
    counts tokens, not descriptors, so several back-to-back descriptors on one
    channel reduce exactly as one would.
    """
    per_desc = max(1, max_bytes // stride)
    for first in range(0, count, per_desc):
        block = min(per_desc, count - first)
        prog.stream(port, sym, index + first * (stride // ELEM),
                    stride=stride, count=block)


def matvec(prog, products, n, per_pass, max_bytes=MAX_BYTES):
    """Emit the passes computing `out(n) = lines(a_sym) . y_half` for every
    (out_sym, line_base, line_stride, half) in `products`.

    A "line" is a row of A for t1 and a column for t2; `line_base(l)` gives its
    first element and `line_stride` the byte step between its elements, which is
    the only place the transpose shows up. Because A is square the reduction
    length is n either way, which is what lets both products share one loaded
    bitstream.

    One pass parks both y vectors once, and each product re-points the replay at
    its own half and replays it once per group of ROWS lines; the four lines of
    a group stream down the four lanes, lane k taking line ROWS*g+k and
    producing element ROWS*g+k of the result, so one output descriptor per lane
    walks the whole chunk with a stride of ROWS elements.

    Parking both halves is what makes the two products one pass. A `mem()` per
    product would be a *loading* descriptor, which writes the SRAM from S_WR and
    clobbers the word `data_out` is still presenting from the previous replay --
    the deadlock a FENCE_SE between the products used to guard against.
    `mem_param()` carries word 0 only: no SRAM access, so nothing is clobbered,
    and the ISE just blocks on the scratchpad's `ready_o` until the replays it
    is replacing have been issued. Back-pressure on one scratchpad instead of a
    rendezvous of all eight engines.
    """
    groups = n // ROWS
    for first in range(0, groups, per_pass):
        block = min(per_pass, groups - first)
        with prog:
            # The scratchpad first: ISE 3 must release MEM_W 0 before it blocks
            # on the input0 stream, and y is what every lane waits on. Parked
            # once for the whole phase -- the block never changes, and nothing
            # else in the phase writes SRAM, which is why the passes below need
            # no barrier between them either.
            if not first:
                prog.mem("input4", "vec_y", 0, stride=ELEM, count=PRODUCTS * n,
                         size=PRODUCTS * n, iters=0)

            for out_sym, line_base, line_stride, half in products:
                # Word 0 only: re-point the replay at this product's half.
                prog.mem_param("input4", mem_addr=half * n, size=n, iters=block)

                for lane in range(ROWS):
                    for group in range(first, first + block):
                        stream_line(prog, f"input{lane}", "mat_a",
                                    line_base(ROWS * group + lane),
                                    line_stride, n, max_bytes)

            # One word per group on each lane, the accumulator's delayed output;
            # the lanes interleave, hence the ROWS-element stride.
            for out_sym, _, _, _ in products:
                for lane in range(ROWS):
                    prog.out(f"output{lane}", out_sym, ROWS * first + lane,
                             stride=ROWS * ELEM, count=block)


def build(n_pad, io_map_matvec, io_map_add, per_pass, max_bytes=MAX_BYTES):
    prog = StreamProgram(
        io_map=io_map_matvec,
        kernel="matvec_kernel",
        app="strela_mvt",
        arrays={
            "mat_a": (n_pad * n_pad, ELEM, [n_pad, n_pad]),
            # y_1 then y_2, adjacent so one scratchpad load holds both.
            "vec_y": (PRODUCTS * n_pad, ELEM),
            "vec_x1_in": (n_pad, ELEM),
            "vec_x2_in": (n_pad, ELEM),
            "vec_t1": (n_pad, ELEM),
            "vec_t2": (n_pad, ELEM),
            "vec_x1": (n_pad, ELEM),
            "vec_x2": (n_pad, ELEM),
        },
        sew=SEW, acc_sew=SEW,
    )
    prog.mark_output("vec_t1", "vec_t2", "vec_x1", "vec_x2")
    prog.conf_all()

    # ---- phase 0: both products, one configuration, one pass ---------------
    # t1 walks rows of A, t2 walks columns; same reduction length, so the second
    # is not a phase of its own -- and both y vectors fit one scratchpad load,
    # so it is not a pass of its own either.
    matvec(prog, (
        ("vec_t1", lambda row: row * n_pad, ELEM, 0),
        ("vec_t2", lambda col: col, n_pad * ELEM, 1),
    ), n=n_pad, per_pass=per_pass, max_bytes=max_bytes)

    # ---- phase 1: x1 = x1_in + t1 and x2 = x2_in + t2 ----------------------
    # The add kernel's two lanes are independent, and here they carry the two
    # different output vectors rather than halves of one.
    prog.fence()
    prog.load_kernel("add_kernel", io_map=io_map_add)
    with prog:
        for lane, (acc, term, out) in enumerate((
                ("vec_x1_in", "vec_t1", "vec_x1"),
                ("vec_x2_in", "vec_t2", "vec_x2"))):
            prog.stream(f"input{2 * lane}", acc, 0, stride=ELEM, count=n_pad)
            prog.stream(f"input{2 * lane + 1}", term, 0, stride=ELEM,
                        count=n_pad)
            prog.out(f"output{lane}", out, 0, stride=ELEM, count=n_pad)

    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA mvt ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data PROJECT=strela_mvt`
runs both with no arguments, so change N in both together. It is rounded up to a
multiple of 4 here exactly as it is there.
""")
    parser.add_argument("N", type=int, nargs="?", default=120,
                        help="order of A and length of every vector "
                             "(default: 120, PolyBench SMALL)")
    parser.add_argument("--groups-per-pass", type=int, metavar="G",
                        help="line groups (of 4 lines) per pass, i.e. how many "
                             "times y is replayed from one preload (default: "
                             "the whole matrix, capped by the 8-bit iters field)")
    parser.add_argument("--max-stream-bytes", type=int, default=MAX_BYTES,
                        metavar="B",
                        help="split a matrix line across descriptors past this "
                             "many bytes (default: %(default)s, the 16-bit "
                             "byte count; lower it only to exercise the split)")
    parser.add_argument("--io-map-matvec",
                        default=os.path.join(_HERE, "mvt_1_hv_io_map.json"),
                        help="io_map.json of the matrix-vector kernel "
                             "(default: the copy committed next to its header)")
    parser.add_argument("--io-map-add",
                        default=os.path.join(_HERE, "mvt_2_hv_io_map.json"),
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

    # validate() catches none of these -- size and iters both wrap silently.
    # Both y vectors are parked in one scratchpad, so the pair is what must fit.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if PRODUCTS * n_pad > limit:
        raise SystemExit(f"y_1 and y_2 are {PRODUCTS * n_pad} words together, "
                         f"past the {limit} a 9-bit size field and a "
                         "512-word scratchpad allow; lower N")
    if not 1 <= per_pass <= MAX_ITERS:
        raise SystemExit(f"--groups-per-pass={per_pass}: a pass replays y that "
                         f"many times, which must fit the {MAX_ITERS} the "
                         "8-bit iters field holds")
    for label, nbytes in (
            (f"a lane result ({per_pass} line groups)", per_pass * ROWS * ELEM),
            (f"an add-kernel lane (N_PAD = {n_pad})", n_pad * ELEM)):
        if nbytes > MAX_BYTES:
            raise SystemExit(f"{label} is {nbytes} bytes, past the {MAX_BYTES} "
                             "a 16-bit byte count holds")

    prog = build(n_pad, args.io_map_matvec, args.io_map_add, per_pass,
                 args.max_stream_bytes)
    prog.validate()

    passes = -(-(n_pad // ROWS) // per_pass)
    col_bytes = n_pad * n_pad * ELEM
    split = -(-col_bytes // args.max_stream_bytes)
    note = (f"mvt {n}x{n} (padded to {n_pad}): t1 = A@y_1 and t2 = A^T@y_2 "
            f"under one configuration, {passes} pass(es) of up to "
            f"{per_pass} four-line groups of each, sharing one parked copy of "
            f"y_1|y_2, then behind a FENCE_SE "
            f"x1 = x1_in + t1 and x2 = x2_in + t2; a column of A spans "
            f"{col_bytes} bytes and is streamed as {split} descriptor(s)")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "matvec_kernel.h", "add_kernel.h",
                                 "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
