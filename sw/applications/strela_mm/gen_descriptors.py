import argparse


DTYPE_INFO = {
    "int8":  {"sew": "8",  "ctype": "int8_t"},
    "int16": {"sew": "16", "ctype": "int16_t"},
    "int32": {"sew": "32", "ctype": "int32_t"},
}


def gen_ise(index, in_n, in_k, in_m, in_sew, in_ctype):
    print(f"// ISE {index}, MEM_W {3 - index}, MEM_E {index}")
    print(f'volatile memory_node_t ise_{index}_table[] __attribute__((section(".xheep_data_interleaved"))) = {{')
    print(f"    {{TR_CONF_ISE, (uintptr_t)&matmul_kernel[{index * 21}], 4 << 16 | CONFIG_SIZE}},")

    rows = int(in_n / 4)
    cols = int(in_k / 2)

    for col in range(cols):
        if index % 2 != 0:
            if index == 1:
                addrB = 2 * col + 1
            else:
                addrB = 2 * col
            print(f"    {{{rows} << MEM_PARAM_ITER_OFFSET | 1 << MEM_PARAM_MODE_OFFSET | {in_k} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | TR_MEM_W_{in_sew}_ISE, (uintptr_t)&matB[{addrB}], sizeof({in_ctype}) * {in_m} << 16 | sizeof({in_ctype}) * {in_m * in_k}}},")
        for row in range(rows):
            print(f"    {{TR_VER_{in_sew}_ISE, (uintptr_t)&matA[{row*4*in_k + index*in_k}], sizeof({in_ctype}) << 16 | sizeof({in_ctype}) * {in_k}}},")

    print("    {IDLE_SE, 0, 0}")
    print("};")
    print("")


def gen_ose(index, in_n, in_k, in_m, acc_sew, acc_ctype):
    print(f"// OSE {index}, MEM_W {index}, MEM_E {3 - index}")
    print(f'volatile memory_node_t ose_{index}_table[] __attribute__((section(".xheep_data_interleaved"))) = {{')

    rows = int(in_n / 4)
    cols = int(in_m / 2)

    for col in range(cols):
        if index == 1:
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | CFG_MEM_E_OSE, 0, 0}},")
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | CFG_MEM_W_OSE, 0, 0}},")
        elif index == 2 or index == 3:
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | CFG_MEM_E_OSE, 0, 0}},")
        print(f"    {{TR_SOUTH_{acc_sew}_OSE, (uintptr_t)&matC[{(2*col+1)+in_m*index}], (sizeof({acc_ctype}) * {4*in_m}) << 16 | sizeof({acc_ctype}) * {4*in_m*rows}}},")
        if index == 1:
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | TR_MEM_E_{acc_sew}_OSE, (uintptr_t)&matC[{(2*col)+in_m*index}], (sizeof({acc_ctype}) * {4*in_m}) << 16 | sizeof({acc_ctype}) * {4*in_m*rows}}},")
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | TR_MEM_W_{acc_sew}_OSE, (uintptr_t)&matC[{(2*col)+in_m*(index-1)}], (sizeof({acc_ctype}) * {4*in_m}) << 16 | sizeof({acc_ctype}) * {4*in_m*rows}}},")
        elif index == 2 or index == 3:
            print(f"    {{0 << MEM_PARAM_MODE_OFFSET | {rows} << MEM_PARAM_SIZE_OFFSET | 0 << MEM_PARAM_ADDR_OFFSET | TR_MEM_E_{acc_sew}_OSE, (uintptr_t)&matC[{(2*col)+in_m*index}], (sizeof({acc_ctype}) * {4*in_m}) << 16 | sizeof({acc_ctype}) * {4*in_m*rows}}},")

    print("    {IDLE_SE, 0, 0}")
    print("};")
    print("")


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA matmul ISE/OSE descriptor tables.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
SEW selection:
  ISE opcodes (TR_VER_*, TR_MEM_W_*) always use the --dtype SEW
  (matA/matB are stored in the system as the input dtype).

  OSE opcodes (TR_SOUTH_*, TR_MEM_W_*, TR_MEM_E_*) use:
    accum32  -> 32-bit  (matC is int32, regardless of input dtype)
    same     -> --dtype (matC is the same type as matA/matB)
""")
    parser.add_argument("M", type=int, help="rows of A and C")
    parser.add_argument("K", type=int, help="cols of A / rows of B")
    parser.add_argument("N", type=int, help="cols of B and C")
    parser.add_argument("--dtype", choices=list(DTYPE_INFO.keys()), default="int32",
                        help="element type for matA/matB (default: int32)")
    parser.add_argument("--mode", choices=["accum32", "same"], default="accum32",
                        help="matC accumulator strategy (default: accum32)")
    args = parser.parse_args()

    m, k, n = args.M, args.K, args.N
    in_info = DTYPE_INFO[args.dtype]
    in_sew, in_ctype = in_info["sew"], in_info["ctype"]

    if args.mode == "accum32":
        acc_sew, acc_ctype = "32", "int32_t"
    else:
        acc_sew, acc_ctype = in_sew, in_ctype

    print("#include <stdint.h>")
    print("#include \"strela.h\"")
    print("#include \"matmul.h\"")
    print("#include \"dataset.h\"")
    print("")
    print(f"/* Generated with dtype={args.dtype}, mode={args.mode}: "
          f"ISE SEW={in_sew}, OSE SEW={acc_sew} */")
    print("")

    for i in range(4):
        gen_ise(i, n, k, m, in_sew, in_ctype)
    for i in range(4):
        gen_ose(i, n, k, m, acc_sew, acc_ctype)


if __name__ == "__main__":
    main()
