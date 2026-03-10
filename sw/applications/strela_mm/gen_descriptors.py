import sys

# Matrix sizes
m = int(sys.argv[1])
k = int(sys.argv[2])
n = int(sys.argv[3])

def gen_ise(index, in_n, in_k, in_m):
    # Always
    print(f"// ISE {index}, MEM_W {3 - index}, MEM_E {index}")
    print(f'volatile memory_node_t ise_{index}_table[] __attribute__((section(".xheep_data_interleaved"))) = {{')
    print(f"    {{TR_CONF_ISE, (uintptr_t)&matmul_kernel[{index * 21}], 4 << 16 | CONFIG_SIZE}},")

    # Size dependent
    chunks = int(in_n*in_k/4)
    rows = int(in_n/4)
    cols = int(in_k/2)

    for col in range(cols):
        if index % 2 != 0:
            if index == 1:
                addrB = 2*col + 1
            else:
                addrB = 2*col
            print(f"    {{{rows} << 25 | 1 << 24 | {in_k} << 14 | 0 << 4 | TR_MEM_W_ISE, (uintptr_t)&matB[{addrB}], sizeof(uint32_t) * {in_m} << 16 | sizeof(uint32_t) * {in_m * in_k}}},")
        for row in range(rows):
            print(f"    {{TR_VER_ISE, (uintptr_t)&matA[{row*4*in_k + index*in_k}], sizeof(uint32_t) << 16 | sizeof(uint32_t) * {in_k}}},")
        
    print("    {IDLE_SE, 0, 0}")
    print("};")
    print("")

def gen_ose(index, in_n, in_k, in_m):
    # Always
    print(f"// OSE {index}, MEM_W {index}, MEM_E {3 - index}")
    print(f'volatile memory_node_t ose_{index}_table[] __attribute__((section(".xheep_data_interleaved"))) = {{')

    # Size dependent
    rows = int(in_n/4)
    cols = int(in_m/2)

    for col in range(cols):
        if index == 1:
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | CFG_MEM_E_OSE, 0, 0}},")
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | CFG_MEM_W_OSE, 0, 0}},")
        elif index == 2 or index == 3:
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | CFG_MEM_E_OSE, 0, 0}},")
        print(f"    {{TR_SOUTH_OSE, (uintptr_t)&matC[{(2*col+1)+in_m*index}], (sizeof(uint32_t) * {4*in_m}) << 16 | sizeof(uint32_t) * {4*in_m*rows}}},")
        if index == 1:
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | TR_MEM_E_OSE, (uintptr_t)&matC[{(2*col)+in_m*index}], (sizeof(uint32_t) * {4*in_m}) << 16 | sizeof(uint32_t) * {4*in_m*rows}}},")
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | TR_MEM_W_OSE, (uintptr_t)&matC[{(2*col)+in_m*(index-1)}], (sizeof(uint32_t) * {4*in_m}) << 16 | sizeof(uint32_t) * {4*in_m*rows}}},")
        elif index == 2 or index == 3:
            print(f"    {{0 << 24 | {rows} << 14 | 0 << 4 | TR_MEM_E_OSE, (uintptr_t)&matC[{(2*col)+in_m*index}], (sizeof(uint32_t) * {4*in_m}) << 16 | sizeof(uint32_t) * {4*in_m*rows}}},")

    print("    {IDLE_SE, 0, 0}")
    print("};")
    print("")


print("#include <stdint.h>")
print("#include \"strela.h\"")
print("#include \"matmul.h\"")
print("#include \"dataset.h\"")
print("")

gen_ise(0, n, k, m)
gen_ise(1, n, k, m)
gen_ise(2, n, k, m)
gen_ise(3, n, k, m)

gen_ose(0, n, k, m)
gen_ose(1, n, k, m)
gen_ose(2, n, k, m)
gen_ose(3, n, k, m)