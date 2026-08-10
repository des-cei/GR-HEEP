#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data).
DEFAULT_N = 128


def _arg(i, default=DEFAULT_N):
    return int(sys.argv[i]) if len(sys.argv) > i else default


N = _arg(1)


def gemver_polybench(N, alpha, beta, A, u1, v1, u2, v2, w, x, y, z):
    """
    - A: N x N
    - u1, v1, u2, v2, w, x, y, z: N
    """
    A2 = list(A)
    for i in range(N):
        for j in range(N):
            A2[i * N + j] = A2[i * N + j] + u1[i] * v1[j] + u2[i] * v2[j]

    x2 = list(x)
    for i in range(N):
        for j in range(N):
            x2[i] = x2[i] + beta * A2[j * N + i] * y[j]

    for i in range(N):
        x2[i] = x2[i] + z[i]

    w2 = list(w)
    for i in range(N):
        for j in range(N):
            w2[i] = w2[i] + alpha * A2[i * N + j] * x2[j]

    return x2, w2


alpha = int(random.random() * 100.0 - 50.0)
beta = int(random.random() * 100.0 - 50.0)
A = [int(random.random() * 100.0 - 50.0) for _ in range(N * N)]
u1 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
v1 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
u2 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
v2 = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
w = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
x = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
y = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
z = [int(random.random() * 100.0 - 50.0) for _ in range(N)]
expected_x, expected_w = gemver_polybench(N, alpha, beta, A, u1, v1, u2, v2, w, x, y, z)


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
print_array("DATA_TYPE", "u1", "N", u1)
print_array("DATA_TYPE", "v1", "N", v1)
print_array("DATA_TYPE", "u2", "N", u2)
print_array("DATA_TYPE", "v2", "N", v2)
print_array("DATA_TYPE", "w", "N", w)
print_array("DATA_TYPE", "x", "N", x)
print_array("DATA_TYPE", "y", "N", y)
print_array("DATA_TYPE", "z", "N", z)
print_array_continuous("DATA_TYPE", "expected_x", "N", expected_x)
print_array_continuous("DATA_TYPE", "expected_w", "N", expected_w)
