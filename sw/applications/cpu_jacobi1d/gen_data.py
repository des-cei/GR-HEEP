#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data).
DEFAULT_N = 128


def _arg(i, default=DEFAULT_N):
    return int(sys.argv[i]) if len(sys.argv) > i else default


TSTEPS = _arg(1)
N = _arg(2)


def c_trunc_div(a, b):
    """Integer division truncating toward zero, matching C's / operator
    (Python's // floors instead, which differs for negative operands)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def jacobi1d_polybench(TSTEPS, N, A):
    """
    - A: N (boundaries A[0], A[N-1] are never updated)
    """
    A = list(A)
    B = [0] * N
    for _ in range(TSTEPS):
        for i in range(1, N - 1):
            B[i] = c_trunc_div(A[i - 1] + A[i] + A[i + 1], 3)
        for i in range(1, N - 1):
            A[i] = c_trunc_div(B[i - 1] + B[i] + B[i + 1], 3)
    return A


A = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_A = jacobi1d_polybench(TSTEPS, N, A)


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
print("#define TSTEPS {}".format(TSTEPS))
print("#define N {}\n".format(N))

print_array("DATA_TYPE", "A", "N", A)
print_array_continuous("DATA_TYPE", "expected_A", "N", expected_A)
