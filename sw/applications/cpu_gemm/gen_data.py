#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

NI = int(sys.argv[1])
NJ = int(sys.argv[2])
NK = int(sys.argv[3])


def gemm_polybench(NI, NJ, NK, alpha, beta, A, B, C):
    """
    - A: NI x NK
    - B: NK x NJ
    - C: NI x NJ
    """
    R = [0 for _ in range(NI * NJ)]
    for i in range(NI):
        for j in range(NJ):
            tmp = 0  # keep integer accumulation (inputs are ints anyway)
            for k in range(NK):
                tmp += A[i * NK + k] * B[k * NJ + j]
            R[i * NJ + j] = int(alpha * tmp + beta * C[i * NJ + j])
    return R


alpha = int(random.random() * 100.0 - 50.0)
beta = int(random.random() * 100.0 - 50.0)
A = [int(random.random() * 100.0 - 50.0) for _ in range(NI * NK)]
B = [int(random.random() * 100.0 - 50.0) for _ in range(NK * NJ)]
C = [int(random.random() * 100.0 - 50.0) for _ in range(NI * NJ)]
Z = gemm_polybench(NI, NJ, NK, alpha, beta, A, B, C)


def print_array(array_type, array_name, array_sz, pyarr):
    print(
        'volatile {} {}[{}] __attribute__((section(".xheep_data_interleaved"))) = '.format(
            array_type, array_name, array_sz
        )
    )
    print("{")
    print(", ".join(map(str, pyarr)))
    print("};")


def print_array_continuous(array_type, array_name, array_sz, pyarr):
    print("volatile {} {}[{}] = ".format(array_type, array_name, array_sz))
    print("{")
    print(", ".join(map(str, pyarr)))
    print("};")


print("#include <stdint.h>\n")
print("#define DATA_TYPE int32_t\n")
print("#define NI {}".format(NI))
print("#define NJ {}".format(NJ))
print("#define NK {}\n".format(NK))
print("volatile DATA_TYPE alpha = {};".format(alpha))
print("volatile DATA_TYPE beta = {};\n".format(beta))

print_array("DATA_TYPE", "A", "NI*NK", A)
print_array("DATA_TYPE", "B", "NK*NJ", B)
print_array("DATA_TYPE", "C", "NI*NJ", C)
print_array_continuous("DATA_TYPE", "expected_result", "NI*NJ", Z)
