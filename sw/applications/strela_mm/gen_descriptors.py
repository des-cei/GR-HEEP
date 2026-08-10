#!/usr/bin/env python3
"""Generate the STRELA matmul ISE/OSE descriptor tables.

The engine/opcode assignment is *not* hard-coded: it is derived from the
io_map.json that `make map-bitstream PROJECT=mm_hv` writes next to the
bitstream, so the descriptors always follow the channels the mapper actually
picked. Regenerate the bitstream and the descriptors together:

    CG=hw/vendor/ceimm_upm_strela/rtl/elastic-cgra
    make -C $CG map-bitstream PROJECT=mm_hv CGRA_CONFIG=configs/4x4-HV.hjson
    sed -e 's/mm_hv_kernel/matmul_kernel/g' -e 's/MM_HV_KERNEL/MATMUL_KERNEL/g' \\
        $CG/build/bitstream/mm_hv_kernel.h > sw/applications/strela_mm/matmul.h
    cp $CG/build/bitstream/io_map.json sw/applications/strela_mm/mm_hv_io_map.json
    python3 gen_descriptors.py 8 8 8 > descriptors.h

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
import json
import os


DTYPE_INFO = {
    "int8": {"sew": "8", "ctype": "int8_t"},
    "int16": {"sew": "16", "ctype": "int16_t"},
    "int32": {"sew": "32", "ctype": "int32_t"},
}

# Fabric geometry. The STRELA shell hard-codes a 4x4 fabric.
ROWS = COLS = 4


class Binding:
    """One io_map port resolved to the stream engine that serves it.

    engine  : index of the ISE (inputs) or OSE (outputs)
    kind    : "stream" for a direct border/bus transfer, "mem" for one that
              goes through a scratchpad
    op      : opcode base name, completed with the SEW by opcode()
    mode    : scratchpad fabric-side port, 0 = PE border, 1 = router bus
              (None for direct stream transfers)
    """

    def __init__(self, engine, kind, op, mode=None):
        self.engine = engine
        self.kind = kind
        self.op = op
        self.mode = mode

    def opcode(self, sew, se):
        return f"{self.op}_{sew}_{se}"


def resolve_input(loc):
    """Map an io_map input location to the ISE that drives it.

    ISE i feeds column i's north port and router i's vertical bus, and writes
    MEM WEST 3-i / MEM EAST i. MEM WEST k / MEM EAST k sit on fabric row k.
    """
    where, index, port = loc[0], loc[1], loc[2]
    if where == "pe":
        row, col = divmod(index, COLS)
        if port == "north":
            return Binding(col, "stream", "TR_NORTH")
        if port == "west":
            return Binding(ROWS - 1 - row, "mem", "TR_MEM_W", mode=0)
        if port == "east":
            return Binding(row, "mem", "TR_MEM_E", mode=0)
    elif where == "bus":
        if port in ("ver_ise", "ver_north"):
            return Binding(index, "stream", "TR_VER")
        if port == "hor_west":
            return Binding(ROWS - 1 - index, "mem", "TR_MEM_W", mode=1)
        if port == "hor_east":
            return Binding(index, "mem", "TR_MEM_E", mode=1)
    raise ValueError(f"input location not reachable from an ISE: {loc}")


def resolve_output(loc):
    """Map an io_map output location to the OSE that drains it.

    OSE i reads column i's south port and router i's vertical bus, and reads
    MEM WEST i / MEM EAST 3-i.
    """
    where, index, port = loc[0], loc[1], loc[2]
    if where == "pe":
        row, col = divmod(index, COLS)
        if port == "south":
            return Binding(col, "stream", "TR_SOUTH")
        if port == "west":
            return Binding(row, "mem", "TR_MEM_W", mode=0)
        if port == "east":
            return Binding(ROWS - 1 - row, "mem", "TR_MEM_E", mode=0)
    elif where == "bus":
        if port in ("ver_ose", "ver_south"):
            return Binding(index, "stream", "TR_VER")
        if port == "hor_west":
            return Binding(index, "mem", "TR_MEM_W", mode=1)
        if port == "hor_east":
            return Binding(ROWS - 1 - index, "mem", "TR_MEM_E", mode=1)
    raise ValueError(f"output location not reachable from an OSE: {loc}")


def load_io_map(path):
    with open(path, encoding="utf-8") as handle:
        io_map = json.load(handle)

    inputs = {n: resolve_input(b["location"]) for n, b in io_map["inputs"].items()}
    outputs = {n: resolve_output(b["location"]) for n, b in io_map["outputs"].items()}

    missing = [n for n in (f"input{i}" for i in range(6)) if n not in inputs]
    missing += [n for n in (f"output{i}" for i in range(8)) if n not in outputs]
    if missing:
        raise SystemExit(f"io_map is missing the mm_hv ports: {', '.join(missing)}")

    # A scratchpad can only be armed once per pass, so two ports must never
    # land on the same engine *and* the same memory.
    for label, table in (("input", inputs), ("output", outputs)):
        seen = {}
        for name, b in table.items():
            if b.kind != "mem":
                continue
            key = (b.engine, b.op)
            if key in seen:
                raise SystemExit(
                    f"{label}s {seen[key]} and {name} share {b.op} on engine "
                    f"{b.engine}; this kernel cannot be expressed as descriptors"
                )
            seen[key] = name

    return inputs, outputs


def mem_param(binding, size, iters=None):
    parts = []
    if iters is not None:
        parts.append(f"{iters} << MEM_PARAM_ITER_OFFSET")
    parts.append(f"{binding.mode} << MEM_PARAM_MODE_OFFSET")
    parts.append(f"{size} << MEM_PARAM_SIZE_OFFSET")
    parts.append("0 << MEM_PARAM_ADDR_OFFSET")
    return " | ".join(parts)


def emit_table(name, comment, rows):
    print(f"// {comment}")
    print(
        f"volatile memory_node_t {name}[] "
        '__attribute__((section(".xheep_data_interleaved"))) = {'
    )
    for row in rows:
        print(f"    {row},")
    print("    {IDLE_SE, 0, 0}")
    print("};")
    print("")


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
    parser.add_argument("M", type=int, help="rows of A and C")
    parser.add_argument("K", type=int, help="cols of A / rows of B")
    parser.add_argument("N", type=int, help="cols of B and C")
    parser.add_argument(
        "--io-map",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "mm_hv_io_map.json"),
        help="io_map.json emitted next to the bitstream by map-bitstream "
             "(default: the copy committed next to matmul.h)",
    )
    parser.add_argument(
        "--dtype", choices=list(DTYPE_INFO), default="int32",
        help="element type for matA/matB (default: int32)",
    )
    parser.add_argument(
        "--mode", choices=["accum32", "same"], default="accum32",
        help="matC accumulator strategy (default: accum32)",
    )
    args = parser.parse_args()

    m, k, n = args.M, args.K, args.N
    if m % ROWS or n % 2:
        raise SystemExit(f"M must be a multiple of {ROWS} and N a multiple of 2")

    in_info = DTYPE_INFO[args.dtype]
    in_sew, in_ctype = in_info["sew"], in_info["ctype"]
    if args.mode == "accum32":
        acc_sew, acc_ctype = "32", "int32_t"
    else:
        acc_sew, acc_ctype = in_sew, in_info["ctype"]

    inputs, outputs = load_io_map(args.io_map)

    row_groups = m // ROWS      # A row groups per B column pair
    col_pairs = n // 2          # B column pairs

    ise_rows = {i: [] for i in range(COLS)}
    ose_rows = {i: [] for i in range(COLS)}

    # Every ISE must issue its own TR_CONF, including ones with no data work:
    # the CGRA input handshakes stay gated until all four have finished. This
    # is conf_node() spelled out, because these tables are statically
    # initialised and conf_node() returns a struct. CONFIG_COL_BYTES -- not
    # CONFIG_BYTES -- is the size: an ISE shifts one column, and the two only
    # happen to be equal on a 4x4 fabric.
    for i in range(COLS):
        ise_rows[i].append(
            f"{{TR_CONF_ISE, (uintptr_t)&matmul_kernel[{i} * CONFIG_COL_WORDS], "
            "4 << 16 | CONFIG_COL_BYTES}"
        )

    for pair in range(col_pairs):
        b_cols = (2 * pair, 2 * pair + 1)

        # --- ISEs: preload the two B columns, then stream the A rows --------
        for b, col in enumerate(b_cols):
            binding = inputs[f"input{4 + b}"]
            param = mem_param(binding, size=k, iters=row_groups)
            ise_rows[binding.engine].append(
                f"{{{param} | {binding.opcode(in_sew, 'ISE')}, "
                f"(uintptr_t)&matB[{col}], "
                f"sizeof({in_ctype}) * {n} << 16 | sizeof({in_ctype}) * {k * n}}}"
            )

        for j in range(ROWS):
            binding = inputs[f"input{j}"]
            for group in range(row_groups):
                a_row = ROWS * group + j
                ise_rows[binding.engine].append(
                    f"{{{binding.opcode(in_sew, 'ISE')}, "
                    f"(uintptr_t)&matA[{a_row * k}], "
                    f"sizeof({in_ctype}) << 16 | sizeof({in_ctype}) * {k}}}"
                )

        # --- OSEs: arm the scratchpads, drain the direct streams, then the
        #     scratchpads. C[(ROWS*g + j) * N + col] for g in row_groups, so
        #     one descriptor walks the row groups with a ROWS-row stride.
        stride = f"sizeof({acc_ctype}) * {ROWS * n}"
        total = f"sizeof({acc_ctype}) * {ROWS * n * row_groups}"

        arms = {i: [] for i in range(COLS)}
        streams = {i: [] for i in range(COLS)}
        drains = {i: [] for i in range(COLS)}

        for b, col in enumerate(b_cols):
            for j in range(ROWS):
                binding = outputs[f"output{j + ROWS * b}"]
                base = f"(uintptr_t)&matC[{j * n + col}]"
                params = f"({stride}) << 16 | {total}"
                if binding.kind == "stream":
                    streams[binding.engine].append(
                        f"{{{binding.opcode(acc_sew, 'OSE')}, {base}, {params}}}"
                    )
                else:
                    cfg_op = "CFG_MEM_W_OSE" if binding.op.endswith("_W") \
                        else "CFG_MEM_E_OSE"
                    param = mem_param(binding, size=row_groups)
                    arms[binding.engine].append(f"{{{param} | {cfg_op}, 0, 0}}")
                    drains[binding.engine].append(
                        f"{{{param} | {binding.opcode(acc_sew, 'OSE')}, "
                        f"{base}, {params}}}"
                    )

        for i in range(COLS):
            ose_rows[i].extend(arms[i] + streams[i] + drains[i])

    print("#include <stdint.h>")
    print('#include "strela.h"')
    print('#include "matmul.h"')
    print('#include "dataset.h"')
    print("")
    print(
        f"/* Generated with dtype={args.dtype}, mode={args.mode}: "
        f"ISE SEW={in_sew}, OSE SEW={acc_sew} */"
    )
    print(f"/* Engine assignment derived from {os.path.basename(args.io_map)} */")
    print("")

    for i in range(COLS):
        emit_table(
            f"ise_{i}_table",
            f"ISE {i}, MEM_W {ROWS - 1 - i}, MEM_E {i}",
            ise_rows[i],
        )
    for i in range(COLS):
        emit_table(
            f"ose_{i}_table",
            f"OSE {i}, MEM_W {i}, MEM_E {ROWS - 1 - i}",
            ose_rows[i],
        )


if __name__ == "__main__":
    main()
