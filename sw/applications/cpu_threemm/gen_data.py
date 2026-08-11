#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data):
# PolyBench 4.2.1 SMALL_DATASET (linear-algebra/kernels/3mm).
DEFAULT_NI = 40
DEFAULT_NJ = 50
DEFAULT_NK = 60
DEFAULT_NL = 70
DEFAULT_NM = 80


def _arg(i, default):
    return int(sys.argv[i]) if len(sys.argv) > i else default


NI = _arg(1, DEFAULT_NI)
NJ = _arg(2, DEFAULT_NJ)
NK = _arg(3, DEFAULT_NK)
NL = _arg(4, DEFAULT_NL)
NM = _arg(5, DEFAULT_NM)


def threemm_polybench(NI, NJ, NK, NL, NM, A, B, C, D):
    """
    - A: NI x NK
    - B: NK x NJ
    - C: NJ x NM
    - D: NM x NL
    - E := A*B : NI x NJ
    - F := C*D : NJ x NL
    - G := E*F : NI x NL
    """
    E = [0 for _ in range(NI * NJ)]
    for i in range(NI):
        for j in range(NJ):
            acc = 0
            for k in range(NK):
                acc += A[i * NK + k] * B[k * NJ + j]
            E[i * NJ + j] = acc

    F = [0 for _ in range(NJ * NL)]
    for i in range(NJ):
        for j in range(NL):
            acc = 0
            for k in range(NM):
                acc += C[i * NM + k] * D[k * NL + j]
            F[i * NL + j] = acc

    G = [0 for _ in range(NI * NL)]
    for i in range(NI):
        for j in range(NL):
            acc = 0
            for k in range(NJ):
                acc += E[i * NJ + k] * F[k * NL + j]
            G[i * NL + j] = acc

    return E, F, G


A = [int(random.random() * 100.0 - 50.0) for _ in range(NI * NK)]
B = [int(random.random() * 100.0 - 50.0) for _ in range(NK * NJ)]
C = [int(random.random() * 100.0 - 50.0) for _ in range(NJ * NM)]
D = [int(random.random() * 100.0 - 50.0) for _ in range(NM * NL)]
expected_E, expected_F, expected_G = threemm_polybench(NI, NJ, NK, NL, NM, A, B, C, D)


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
print("#define NK {}".format(NK))
print("#define NL {}".format(NL))
print("#define NM {}\n".format(NM))

print_array("DATA_TYPE", "A", "NI*NK", A)
print_array("DATA_TYPE", "B", "NK*NJ", B)
print_array("DATA_TYPE", "C", "NJ*NM", C)
print_array("DATA_TYPE", "D", "NM*NL", D)
print_array("DATA_TYPE", "E", "NI*NJ", [0] * (NI * NJ))
print_array("DATA_TYPE", "F", "NJ*NL", [0] * (NJ * NL))
print_array("DATA_TYPE", "G", "NI*NL", [0] * (NI * NL))
print_array_continuous("DATA_TYPE", "expected_E", "NI*NJ", expected_E)
print_array_continuous("DATA_TYPE", "expected_F", "NJ*NL", expected_F)
print_array_continuous("DATA_TYPE", "expected_G", "NI*NL", expected_G)
