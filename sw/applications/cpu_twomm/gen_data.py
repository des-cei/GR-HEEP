#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data):
# PolyBench 4.2.1 SMALL_DATASET (linear-algebra/kernels/2mm), the same shape as
# strela_v2_2mm so the two are directly comparable.
DEFAULT_NI = 40
DEFAULT_NJ = 50
DEFAULT_NK = 70
DEFAULT_NL = 80

INT32_MAX = (1 << 31) - 1

# alpha and beta are drawn from [-SCALE_CAP, SCALE_CAP] \ {0}; the element limit
# below is derived from this, so raising one lowers the other.
SCALE_CAP = 16
ELEM_CAP = 50  # the house range of the other cpu_* apps, when it is safe


def _arg(i, default):
    return int(sys.argv[i]) if len(sys.argv) > i else default


NI = _arg(1, DEFAULT_NI)
NJ = _arg(2, DEFAULT_NJ)
NK = _arg(3, DEFAULT_NK)
NL = _arg(4, DEFAULT_NL)


def safe_elem_limit(NK, NJ):
    """Largest |v| such that |alpha|*(NK*NJ*v^3) + |beta|*v fits int32.

    2mm multiplies twice, so unlike the single-product apps the element range
    cannot just be the house +-50: NK*NJ*v^3 bounds |A*B*C| (each of its NJ
    terms is a dot product of NK products), which also bounds the smaller
    |A*B| = NK*v^2. Without this the golden would wrap silently and the app
    would be checking the CPU against an overflowed reference.
    """
    lim = 0
    while (SCALE_CAP * (NK * NJ * (lim + 1) ** 3) + SCALE_CAP * (lim + 1)) <= INT32_MAX:
        lim += 1
    return lim


LIMIT = min(ELEM_CAP, safe_elem_limit(NK, NJ))
if LIMIT < 1:
    sys.exit(
        "error: NK={} NJ={} leave no element range that keeps "
        "alpha*A*B*C + beta*D inside int32".format(NK, NJ)
    )


def twomm_polybench(NI, NJ, NK, NL, alpha, beta, A, B, C, D):
    """
    - A: NI x NK
    - B: NK x NJ
    - C: NJ x NL
    - D: NI x NL, read and overwritten
    - tmp := alpha*A*B : NI x NJ
    - D := tmp*C + beta*D
    """
    tmp = [0 for _ in range(NI * NJ)]
    for i in range(NI):
        for j in range(NJ):
            acc = 0
            for k in range(NK):
                acc += alpha * A[i * NK + k] * B[k * NJ + j]
            tmp[i * NJ + j] = acc

    R = [0 for _ in range(NI * NL)]
    for i in range(NI):
        for j in range(NL):
            acc = beta * D[i * NL + j]
            for k in range(NJ):
                acc += tmp[i * NJ + k] * C[k * NL + j]
            R[i * NL + j] = acc
    return R


def _nonzero_scale():
    v = 0
    while v == 0:
        v = random.randint(-SCALE_CAP, SCALE_CAP)
    return v


alpha = _nonzero_scale()
beta = _nonzero_scale()
A = [random.randint(-LIMIT, LIMIT) for _ in range(NI * NK)]
B = [random.randint(-LIMIT, LIMIT) for _ in range(NK * NJ)]
C = [random.randint(-LIMIT, LIMIT) for _ in range(NJ * NL)]
D = [random.randint(-LIMIT, LIMIT) for _ in range(NI * NL)]
expected_D = twomm_polybench(NI, NJ, NK, NL, alpha, beta, A, B, C, D)

span = max(abs(v) for v in expected_D)
if span > INT32_MAX:
    sys.exit("internal error: |D| reaches {}, past int32".format(span))


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
print("#define NL {}\n".format(NL))
print("/* Elements in [-{}, {}], scales in [-{}, {}], max |D| = {}. */\n".format(
    LIMIT, LIMIT, SCALE_CAP, SCALE_CAP, span))
print("volatile DATA_TYPE alpha = {};".format(alpha))
print("volatile DATA_TYPE beta = {};\n".format(beta))

print_array("DATA_TYPE", "A", "NI*NK", A)
print_array("DATA_TYPE", "B", "NK*NJ", B)
print_array("DATA_TYPE", "C", "NJ*NL", C)
print_array("DATA_TYPE", "D", "NI*NL", D)
print_array_continuous("DATA_TYPE", "expected_D", "NI*NL", expected_D)
