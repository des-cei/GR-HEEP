#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data):
# PolyBench 4.2.1 SMALL_DATASET (linear-algebra/kernels/bicg), the same shape as
# strela_bicg so the two are directly comparable. A is N x M -- the two are not
# interchangeable, and the argument order (M then N) follows PolyBench's.
DEFAULT_M = 116
DEFAULT_N = 124


def _arg(i, default):
    return int(sys.argv[i]) if len(sys.argv) > i else default


M = _arg(1, DEFAULT_M)
N = _arg(2, DEFAULT_N)


def bicg_polybench(M, N, A, p, r):
    """
    - A: N x M
    - p: M, r: N
    - returns q = A * p (size N) and s = A^T * r (size M)

    The two products are independent: both read A and an input vector, and
    neither consumes the other's result.
    """
    s = [0 for _ in range(M)]
    q = [0 for _ in range(N)]
    for i in range(N):
        for j in range(M):
            s[j] += r[i] * A[i * M + j]
            q[i] += A[i * M + j] * p[j]
    return q, s


A = [int(random.random() * 100.0 - 50.0) for _ in range(N * M)]
p = [int(random.random() * 100.0 - 50.0) for _ in range(M)]
r = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_q, expected_s = bicg_polybench(M, N, A, p, r)


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
print("#define M {}".format(M))
print("#define N {}\n".format(N))

print_array("DATA_TYPE", "A", "N*M", A)
print_array("DATA_TYPE", "p", "M", p)
print_array("DATA_TYPE", "r", "N", r)
print_array("DATA_TYPE", "q", "N", [0] * N)
print_array("DATA_TYPE", "s", "M", [0] * M)
print_array_continuous("DATA_TYPE", "expected_q", "N", expected_q)
print_array_continuous("DATA_TYPE", "expected_s", "M", expected_s)
