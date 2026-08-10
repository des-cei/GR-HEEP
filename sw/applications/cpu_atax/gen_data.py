#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

N = int(sys.argv[1])


def atax_polybench(N, M, A, x):
    """
    - A: M x N
    - x: N
    - returns y = A^T * (A * x), size N
    """
    tmp = [0 for _ in range(M)]
    y = [0 for _ in range(N)]
    for i in range(M):
        for j in range(N):
            tmp[i] += A[i * N + j] * x[j]
        for j in range(N):
            y[j] += A[i * N + j] * tmp[i]
    return y


A = [int(random.random() * 100.0 - 50.0) for _ in range(N * N)]
x = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_y = atax_polybench(N, N, A, x)


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
print("#define N {}\n".format(N))

print_array("DATA_TYPE", "A", "N*N", A)
print_array("DATA_TYPE", "x", "N", x)
print_array_continuous("DATA_TYPE", "expected_y", "N", expected_y)
