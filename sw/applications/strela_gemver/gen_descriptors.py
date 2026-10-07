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

Phases 1-3 come from elastic-cgra's committed regression, replayed into kernel
headers without re-running the mapper -- the matrix-vector one twice, under two
array names, so each phase has its own patchable copy in RAM.

**update_kernel.h does not.** `regress/4x4-HV/gemver_1_hv/` holds the original
unpinned solve, which puts lane a's A stream on ISE 1 -- the engine that also
owns two of the u scratchpads. This app needs it on ISE 0, so the DFG carries
`at=` pins (see mapper.py's parse_pin) and the header comes from a mapper run:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    scripts/gr_heep_env.sh make -C $CG map-bitstream PROJECT=gemver_1_hv \\
        CGRA_CONFIG=configs/4x4-HV.hjson \\
        ARRAY_NAME=update_kernel CHEADER=build/bitstream/update_kernel.h
    cp $CG/build/bitstream/update_kernel.h \\
        sw/applications/strela_gemver/update_kernel.h
    cp $CG/build/bitstream/gemver_1_hv_io_map.json \\
        sw/applications/strela_gemver/gemver_1_hv_io_map.json

Do **not** replay gemver_1_hv through `regress2kernel.py`: it would silently
restore the ISE 1 binding, the schedule below would put four descriptors per row
on an engine that is also streaming, and the phase would slow by ~20% (it still
runs -- this is a performance trap, not a correctness one). The pins make the
solve reproducible: re-running it reproduces every binding of the committed
regress solve except input0, which is the one that had to move.

    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_2_hv \\
        --array-name matvec_beta_kernel \\
        -o sw/applications/strela_gemver/matvec_beta_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_2_hv \\
        --array-name matvec_alpha_kernel \\
        -o sw/applications/strela_gemver/matvec_alpha_kernel.h
    scripts/gr_heep_env.sh python3 scripts/regress2kernel.py gemver_3_hv \\
        --array-name add_kernel \\
        -o sw/applications/strela_gemver/add_kernel.h
    cp $CG/regress/4x4-HV/gemver_2_hv/io_map.json \\
        sw/applications/strela_gemver/gemver_2_hv_io_map.json      # and _3_hv
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

**Why one row per lane per step in phase 0, and why that costs nothing.** A
scratchpad replays a fixed block of words a whole number of times, and the update
needs u1[i] held constant across a whole row while v1[j] runs along it. Over R
rows the u ports would have to emit [u1[i0] x N, u1[i0+1] x N, ...], which is a
run-length pattern and not a repeat of anything, so R = 1: the u address changes
once per row and there is no way around it. (The mirror -- per-lane scratchpads
holding halves of v, the u scalars on the shared buses -- is strictly worse: it
would reload 2N words to update only N elements.)

Changing the address, though, is not the same as reloading the block. The whole u
vector is parked in each of the four per-lane scratchpads once, with `iters=0` so
S_WR returns to S_IDLE without replaying it, and each step re-points the replay
with a `mem_param()` descriptor whose address and byte-count words are zero:
word 0 alone, no bus traffic, no SRAM access. The shared v1/v2 blocks meanwhile
never move at all -- a lane consumes one replay of v per row, so a single load
with `iters = N/2` feeds every step of the phase.

That is what removes the fences (below) and it is what makes the lane split free.
Since each lane now walks a contiguous half of the rows rather than alternating
ones, and since the `at=` pins keep every per-row descriptor off the four engines
that stream, all four of them move their whole half of the matrix in a single
descriptor. At the default shape the eight tables hold 430 descriptors where the
fenced, reloading schedule needed 1392; measured TOT 25521 -> 16325, with TAB
falling from 40% of the run to 19% and STL from 1351 to 327.

How the three solves land on the engines (the reason the emission order below is
not arbitrary):

  update:  ISE 0  input6 -> MEM_E 0 mode 1 (row-0 bus, shared) + input0 -> north col 0
           ISE 1  input3 -> MEM_E 1  + input4 -> MEM_W 2
           ISE 2  input7 -> MEM_E 2 mode 1 (row-2 bus, shared) + input1 -> north col 2
           ISE 3  input2 -> MEM_W 0  + input5 -> MEM_E 3
           OSE 1  output0 -> ver bus 1     OSE 2  output1 -> ver bus 2
           ISE 0 and ISE 2 are deliberately symmetric: one preloaded vector and
           one streamed half of A each, so the two engines that feed the fabric
           continuously carry no per-row descriptors at all. That is what the
           `at=` pins in main.dot are for; see the performance note below.
  matvec:  ISE 0  input0 -> ver bus 0      ISE 1  input2 -> north col 1
           ISE 2  input1 -> north col 2    ISE 3  input4 -> MEM_W 0 mode 0
                                                  + input3 -> north col 3
           OSE 0  output0 -> south col 0   OSE 1  output2 -> ver bus 1
           OSE 2  output1 -> ver bus 2     OSE 3  output3 -> ver bus 3
  add:     ISE 0  input1   ISE 1  input0   ISE 2  input2   ISE 3  input3
           OSE 0  output0 -> ver bus 0     OSE 3  output1 -> ver bus 3

ISE 0 and ISE 2 in the update, and ISE 3 in the matvec, carry both a scratchpad
and a direct stream, so their mem() calls are emitted before any stream():
descriptors keep call order within an engine, and an engine parked on a stream
can no longer re-point the scratchpad the fabric is waiting on. No output of any
of the three kernels is scratchpad-backed, so this app is free of the OSE-side
`strela_memory.sv` race that caps strela_mm's pass length.

**Which fences are left, and why the other 59 went.** The three that remain are
the phase boundaries, and those are not about scratchpads at all: an ISE
re-entering TR_CONF clears its `conf_done_o`, which drops the global `conf_reg`
and re-gates every fabric handshake, and this run is one long chain
(mat_a2 -> vec_tmp -> vec_x -> vec_w) where each phase reads back what the
previous phase's OSEs wrote -- only a FENCE_SE waits on both kinds of engine.

Inside phase 0 there is now nothing to fence *for*. The fence was there because
an ISE that ran ahead would reach the next step's TR_MEM_* while the fabric was
still draining the current replay, and a *loading* descriptor destroys what is in
flight: it writes the SRAM from S_WR, clobbering the `data_out` register that is
still presenting the last word of the previous replay, so that word never reaches
the fabric and the lane waits for it forever. A `mem_param()` descriptor issues no
SRAM access, so `data_out` survives S_WR and `valid_out` is re-presented on the
way into S_WR_CGRA -- and the ISE cannot get ahead in the first place, because
`ready_o` is low for the whole of S_WR_CGRA, which blocks it until the replay it
is about to replace has been issued. Back-pressure does what the barrier did, per
scratchpad instead of across all eight engines.

The one thing to check when copying this is that the blocking cannot close a
cycle. Here it cannot: ISE 1 blocks on lane b's u1 while holding nothing lane b
needs, ISE 3 blocks on lane a's u1 while holding nothing lane a needs, and each
lane's other three operands were all issued in the step that is still running.
So no engine is ever more than one step ahead and none of them waits on a stream
that a blocked engine still owes.
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
ROWS = 4          # accumulator lanes of gemver_2_hv = matrix lines per group
LANES = 2         # independent lanes of gemver_1_hv and gemver_3_hv
MEM_DEPTH = 512   # scratchpad words (StrelaMemDepth, rtl/strela_pkg.sv)
MAX_SIZE = 511    # mem_param size is 9 bits
MAX_ITERS = 255   # mem_param iters is 8 bits
MAX_BYTES = 65535  # descriptor word 2 carries a 16-bit byte count


def split(emit, sym, index, stride, count, max_bytes=MAX_BYTES):
    """Emit one logical transfer, split if its span overflows the 16-bit count.

    A contiguous run (stride ELEM) only splits past 16383 elements; a column is
    N elements a row apart, so its span is the whole matrix and it splits far
    sooner. Several back-to-back descriptors on one channel behave exactly as
    one would -- the fabric counts tokens, not descriptors.
    """
    per_desc = max(1, max_bytes // stride)
    for first in range(0, count, per_desc):
        emit(sym, index + first * (stride // ELEM), stride,
             min(per_desc, count - first))


def stream_run(prog, port, sym, index, stride, count, max_bytes=MAX_BYTES):
    split(lambda s, i, st, c: prog.stream(port, s, i, stride=st, count=c),
          sym, index, stride, count, max_bytes)


def out_run(prog, port, sym, index, stride, count, max_bytes=MAX_BYTES):
    split(lambda s, i, st, c: prog.out(port, s, i, stride=st, count=c),
          sym, index, stride, count, max_bytes)


def rank2_update(prog, n, max_bytes=MAX_BYTES):
    """Phase 0: mat_a2 = mat_a + u1*v1^T + u2*v2^T, one row per lane per step.

    Lane a takes the first half of the rows and lane b the second half, so each
    lane's slice of A and of A2 is one contiguous run and the engines that carry
    nothing else can move it in a single descriptor.

    Nothing is loaded into a scratchpad more than once and there is no fence in
    the phase at all. Both properties come from the same place: the ISE stalls
    on a scratchpad's `ready_o` while it is replaying, so a descriptor that only
    re-points the replay is self-synchronising.

      * v1 and v2 are the operands the two lanes share, on the row-0 and row-2
        horizontal buses. Each is replayed once per row a lane processes, so one
        load with `iters = N/2` feeds the whole phase -- the loop below never
        touches them again.
      * u1 and u2 are the per-lane operands, one entry held constant along a
        whole row. That is a run-length pattern, which a scratchpad cannot
        produce, so the address has to change once per row. It does *not* have
        to be reloaded to change: the whole vector is parked in each of the four
        scratchpads once (`iters=0`, load without replaying), and each step
        re-points the replay at one word of it with a `mem_param()` descriptor
        that moves no data.

    Reloading is what made the old schedule fence: a loading descriptor writes
    the SRAM from S_WR, which destroys the `data_out` still presenting the
    previous replay's last word. A param-only descriptor issues no SRAM access,
    so that word survives and is re-presented on the way into S_WR_CGRA -- see
    `strela_desc.mem_param()`.
    """
    half = n // LANES

    # ---- loaded once for the whole phase ---------------------------------
    # The shared operands. One replay of each serves both lanes, and a lane
    # consumes one replay per row, so `iters` is the row count of a lane.
    prog.mem("input6", "vec_v1", 0, stride=ELEM, count=n, size=n, iters=half)
    prog.mem("input7", "vec_v2", 0, stride=ELEM, count=n, size=n, iters=half)

    # The per-lane operands, parked whole. ISE 1 and ISE 3 carry nothing else
    # in this phase, which is what the `at=` pins in the DFG buy.
    for port, sym in (("input3", "vec_u1"), ("input4", "vec_u2"),   # ISE 1
                      ("input2", "vec_u1"), ("input5", "vec_u2")):  # ISE 3
        prog.mem(port, sym, 0, stride=ELEM, count=n, size=n, iters=0)

    # ---- one step per row of each lane -----------------------------------
    # Four re-points and nothing else. The two engines take one lane each way
    # round, so neither lane waits for both of its u operands: ISE 1 pushes
    # lane b's u1 before lane a's u2, ISE 3 lane a's u1 before lane b's u2.
    for step in range(half):
        row_a, row_b = step, half + step
        prog.mem_param("input3", mem_addr=row_b, size=1, iters=n)   # ISE 1
        prog.mem_param("input4", mem_addr=row_a, size=1, iters=n)   # ISE 1
        prog.mem_param("input2", mem_addr=row_a, size=1, iters=n)   # ISE 3
        prog.mem_param("input5", mem_addr=row_b, size=1, iters=n)   # ISE 3

    # ---- the four contiguous halves --------------------------------------
    # ISE 0 and ISE 2 carry one preloaded vector each and then nothing, and the
    # two OSEs nothing at all, so each moves its whole half of the matrix in a
    # single descriptor and never stops mid-phase.
    stream_run(prog, "input0", "mat_a", 0, ELEM, half * n, max_bytes)
    stream_run(prog, "input1", "mat_a", half * n, ELEM, half * n, max_bytes)
    out_run(prog, "output0", "mat_a2", 0, ELEM, half * n, max_bytes)
    out_run(prog, "output1", "mat_a2", half * n, ELEM, half * n, max_bytes)


def matvec(prog, out_sym, a_sym, vec_sym, line_base, line_stride, n,
           max_bytes=MAX_BYTES):
    """A matrix-vector phase: out(n) = lines(a_sym) . const*vec_sym.

    A "line" is a column of A2 for the transposed product and a row for the
    forward one; `line_base(l)` gives its first element and `line_stride` the
    byte step between its elements, which is the only place the transpose shows
    up. The scalar is not here at all -- it is the constant PE main.c patches.

    The vector is preloaded once and replayed once per line a lane reduces, so
    the whole product is one pass: `iters` is N/ROWS, well inside the 8-bit
    field, and there is nothing to reload and so nothing to fence between.

    Lane k takes a *contiguous* quarter of the lines rather than every fourth
    one. The four lanes still advance in lockstep -- one replay of the vector
    serves one line on each of them -- but each lane's results are then
    contiguous too, so its output is a single descriptor, and for the forward
    product its lines are contiguous rows, i.e. a single input descriptor as
    well. Interleaving the lanes would cost N descriptors instead of ROWS.
    """
    lines = n // ROWS               # lines per lane
    with prog:
        # The scratchpad first: ISE 3 must release MEM_W 0 before it blocks on
        # the input3 stream, and the vector is what every lane waits on.
        prog.mem("input4", vec_sym, 0, stride=ELEM, count=n, size=n,
                 iters=lines)

        for lane in range(ROWS):
            first = lane * lines
            if line_stride == ELEM:
                # Rows: the lane's whole slice is one contiguous run.
                stream_run(prog, f"input{lane}", a_sym, line_base(first),
                           ELEM, lines * n, max_bytes)
            else:
                for line in range(first, first + lines):
                    stream_run(prog, f"input{lane}", a_sym, line_base(line),
                               line_stride, n, max_bytes)

        # One word per line on each lane, the accumulator's delayed output.
        for lane in range(ROWS):
            prog.out(f"output{lane}", out_sym, lane * lines,
                     stride=ELEM, count=lines)


def build(n, io_map_update, io_map_matvec, io_map_add, max_bytes=MAX_BYTES):
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
    rank2_update(prog, n, max_bytes)

    # ---- phase 1: vec_tmp = beta*(A2^T @ y), lines are columns --------------
    prog.fence()
    prog.load_kernel("matvec_beta_kernel", io_map=io_map_matvec)
    matvec(prog, "vec_tmp", "mat_a2", "vec_y",
           line_base=lambda col: col, line_stride=n * ELEM,
           n=n, max_bytes=max_bytes)

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
           n=n, max_bytes=max_bytes)

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

    # validate() catches none of these -- size, iters and the byte counts all
    # wrap silently.
    limit = min(MAX_SIZE, MEM_DEPTH)
    if n_pad > limit:
        raise SystemExit(f"a preloaded vector is {n_pad} words, past the {limit} "
                         "a 9-bit size field and a 512-word scratchpad allow; "
                         "lower N")
    # Specific to the update: it replays one u entry per element of a row, so the
    # row length itself has to fit the iters field. The whole u vector is also
    # resident in a scratchpad, but that is the same `limit` as above.
    if n_pad > MAX_ITERS:
        raise SystemExit(f"the rank-2 update replays u1[i]/u2[i] {n_pad} times "
                         f"per row, past the {MAX_ITERS} an 8-bit iters field "
                         "holds; lower N")

    prog = build(n_pad, args.io_map_update, args.io_map_matvec, args.io_map_add,
                 args.max_stream_bytes)
    prog.validate()

    col_bytes = n_pad * n_pad * ELEM
    col_descs = -(-col_bytes // args.max_stream_bytes)
    descs = sum(len(t) for t in prog.tables.values())
    note = (f"gemver {n}x{n} (padded to {n_pad}): mat_a2 = A + u1*v1^T + "
            f"u2*v2^T in {n_pad // LANES} fenceless steps of one row per lane, "
            f"then behind FENCE_SEs vec_tmp = beta*(A2^T@y) and "
            f"vec_x = vec_tmp + z and vec_w = alpha*(A2@x); each product is one "
            f"pass of {n_pad // ROWS} lines per lane, a column of A2 spans "
            f"{col_bytes} bytes and is streamed as {col_descs} descriptor(s), "
            f"and the eight tables hold {descs} descriptors in total")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "update_kernel.h",
                                 "matvec_beta_kernel.h", "add_kernel.h",
                                 "matvec_alpha_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
