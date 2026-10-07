#!/usr/bin/env python3
"""Generate the STRELA find-two-minima ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it comes from the io_map.json
that pairs with the bitstream, resolved by STRELA's shared binding layer. That
matters here because the four scalar outputs did not land on one kind of port --
output0 drains off PE12's south border while output1/2/3 come back over the
vertical OSE buses of routers 2/1/3 -- so the binding layer picks a different
opcode per port. The bitstream is the one committed in elastic-cgra's
regression, replayed into a kernel header without re-running the mapper:

    CG=hw/vendor/strela-v2/rtl/elastic-cgra
    python3 scripts/regress2kernel.py find2min \\
        -o sw/applications/strela_find2min/find2min_kernel.h
    cp $CG/regress/4x4-HV/find2min/io_map.json \\
       sw/applications/strela_find2min/find2min_io_map.json
    make gen-app-data PROJECT=strela_find2min  # or: python3 gen_descriptors.py

find2min_io_map.json is committed next to the kernel header so descriptors.h can
be regenerated (it is gitignored) without re-running the mapper. Keep the two in
step: they describe the same solve, and a re-solve reshuffles the binding.

Kernel contract (mapper/applications/find2min/main.dot), which fixes what each
port means:

    input0  = x, streamed
    output0 = min1   output1 = min2   output2 = idx1   output3 = idx2

Each output carries exactly *one* token per run, not one per input: the tracker
FUs decimate with a delay counter, so the reduction result appears once the
whole group has gone by. That is why the four `out()` calls below use count=1
while the input streams N elements -- and why the delay counter in the bitstream
has to agree with N, which main.c patches at runtime (see gen_data.py).
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRELA_SW = os.path.abspath(os.path.join(
    _HERE, "..", "..", "..", "hw", "vendor", "strela-v2", "sw"))
sys.path.insert(0, _STRELA_SW)

from strela_desc import StreamProgram  # noqa: E402

ELEM = 4          # int32 samples
SEW = 32
MAX_BYTES = (1 << 16) - 1     # the descriptor byte-count field is 16 bits
MAX_DELAY = (1 << 16) - 1     # set_pe_delay_value packs the delay in 16 bits

# output<k> -> index into the contiguous result[4] array, matching the DFG's own
# output addresses 0x400/0x404/0x408/0x40C.
RESULT_SLOT = {"output0": 0, "output1": 1, "output2": 2, "output3": 3}


def build(n, io_map):
    prog = StreamProgram(
        io_map=io_map,
        kernel="find2min_kernel",
        app="strela_find2min",
        arrays={"x": (n, ELEM), "result": (len(RESULT_SLOT), ELEM)},
        sew=SEW,
    )
    prog.mark_output("result")
    prog.conf_all()

    with prog:
        prog.stream("input0", "x", 0, stride=ELEM, count=n)
        for port, slot in RESULT_SLOT.items():
            prog.out(port, "result", slot, stride=ELEM, count=1)
    return prog


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA find-two-minima ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The default must match gen_data.py's: `make gen-app-data
PROJECT=strela_find2min` runs both with no arguments, so change N in both
together. main.c derives the tracker delay from FIND2MIN_SAMPLES, so N only
needs changing in those two places.
""")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="elements to reduce (default: 4096)")
    parser.add_argument("--io-map",
                        default=os.path.join(_HERE, "find2min_io_map.json"),
                        help="io_map.json for the bitstream (default: the copy "
                             "committed next to the kernel header)")
    parser.add_argument("--streams", metavar="FILE",
                        help="also write the streams.json model for the viewer")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="write descriptors.h here (default: stdout)")
    args = parser.parse_args()

    n = args.N
    if n < 2:
        raise SystemExit(f"N={n} must be at least 2, there are two minima")
    # validate() checks the byte count but knows nothing about the delay field.
    if n * ELEM > MAX_BYTES:
        raise SystemExit(f"N={n} needs {n * ELEM} input bytes, past the "
                         f"{MAX_BYTES} the 16-bit byte count holds "
                         f"(max N = {MAX_BYTES // ELEM})")
    if n + 1 > MAX_DELAY:
        raise SystemExit(f"N={n} needs a tracker delay of {n + 1}, past the "
                         f"{MAX_DELAY} the 16-bit delay field holds")

    prog = build(n, args.io_map)
    prog.validate()

    note = (f"two smallest values and their indices over {n} elements: one ISE "
            f"stream in, four decimated scalar outputs, single pass")
    prog.emit_c_header(args.output or "/dev/stdout",
                       includes=("strela.h", "find2min_kernel.h", "dataset.h"),
                       extra_note=note)
    if args.streams:
        prog.emit_json(args.streams)


if __name__ == "__main__":
    main()
