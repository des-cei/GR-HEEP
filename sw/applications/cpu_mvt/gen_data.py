#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data).
DEFAULT_N = 128


def _arg(i, default=DEFAULT_N):
    return int(sys.argv[i]) if len(sys.argv) > i else default


N = _arg(1)


def mvt_polybench(N, x1, x2, y1, y2, A):
    """
    - A: N x N
    - x1, x2, y1, y2: N
    - x1 := x1 + A*y1
    - x2 := x2 + A^T*y2
    """
    x1n = list(x1)
    for i in range(N):
        for j in range(N):
            x1n[i] += A[i * N + j] * y1[j]

    x2n = list(x2)
    for i in range(N):
        for j in range(N):
            x2n[i] += A[j * N + i] * y2[j]

    return x1n, x2n


A = [int(random.random() * 100.0 - 50.0) for _ in range(N * N)]
x1 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
x2 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
y1 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
y2 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_x1, expected_x2 = mvt_polybench(N, x1, x2, y1, y2, A)


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
print_array("DATA_TYPE", "x1", "N", x1)
print_array("DATA_TYPE", "x2", "N", x2)
print_array("DATA_TYPE", "y_1", "N", y1)
print_array("DATA_TYPE", "y_2", "N", y2)
print_array_continuous("DATA_TYPE", "expected_x1", "N", expected_x1)
print_array_continuous("DATA_TYPE", "expected_x2", "N", expected_x2)
