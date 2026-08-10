#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data).
DEFAULT_N = 128


def _arg(i, default=DEFAULT_N):
    return int(sys.argv[i]) if len(sys.argv) > i else default


N = _arg(1)


def gesummv_polybench(N, alpha, beta, A, B, x):
    """
    - A, B: N x N
    - x: N
    """
    y = [0 for _ in range(N)]
    for i in range(N):
        tmp = 0
        for j in range(N):
            tmp = A[i * N + j] * x[j] + tmp
            y[i] = B[i * N + j] * x[j] + y[i]
        y[i] = alpha * tmp + beta * y[i]
    return y


alpha = int(random.random() * 100.0 - 50.0)
beta = int(random.random() * 100.0 - 50.0)
A = [int(random.random() * 100.0 - 50.0) for _ in range(N * N)]
B = [int(random.random() * 100.0 - 50.0) for _ in range(N * N)]
x = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_y = gesummv_polybench(N, alpha, beta, A, B, x)


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
print("volatile DATA_TYPE alpha = {};".format(alpha))
print("volatile DATA_TYPE beta = {};\n".format(beta))

print_array("DATA_TYPE", "A", "N*N", A)
print_array("DATA_TYPE", "B", "N*N", B)
print_array("DATA_TYPE", "x", "N", x)
print_array_continuous("DATA_TYPE", "expected_y", "N", expected_y)
