#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys
import random

# Default problem size, so the script runs with no arguments (make gen-app-data):
# PolyBench 4.2.1 SMALL_DATASET (linear-algebra/kernels/doitgen), the same shape
# as strela_v2_doitgen so the two are directly comparable. Positional arguments are
# NR NQ NP, in that order, matching strela_v2_doitgen's.
DEFAULT_NR = 25
DEFAULT_NQ = 20
DEFAULT_NP = 30

INT32_MAX = (1 << 31) - 1

ELEM_CAP = 50  # the house range of the other cpu_* apps, when it is safe


def _arg(i, default):
    return int(sys.argv[i]) if len(sys.argv) > i else default


NR = _arg(1, DEFAULT_NR)
NQ = _arg(2, DEFAULT_NQ)
NP = _arg(3, DEFAULT_NP)


def safe_elem_limit(NP):
    """Largest |v| such that |sum| = NP*v^2 fits int32."""
    lim = 0
    while NP * (lim + 1) ** 2 <= INT32_MAX:
        lim += 1
    return lim


LIMIT = min(ELEM_CAP, safe_elem_limit(NP))
if LIMIT < 1:
    sys.exit("error: NP={} leaves no element range that keeps A*C4 "
             "inside int32".format(NP))


def doitgen_polybench(NR, NQ, NP, A, C4):
    """
    - A: NR x NQ x NP, overwritten in place
    - C4: NP x NP
    - sum[r][q][p] = SUM_s A[r][q][s] * C4[s][p]

    The (r, q) pairs are independent and each contracts one contiguous length-NP
    row of A, so this is really an (NR*NQ) x NP by NP x NP matrix product -- which
    is exactly what strela_v2_doitgen runs on the fabric.
    """
    out = list(A)
    for r in range(NR):
        for q in range(NQ):
            base = (r * NQ + q) * NP
            row = [0 for _ in range(NP)]
            for p in range(NP):
                acc = 0
                for s in range(NP):
                    acc += A[base + s] * C4[s * NP + p]
                row[p] = acc
            out[base:base + NP] = row
    return out


A = [random.randint(-LIMIT, LIMIT) for _ in range(NR * NQ * NP)]
C4 = [random.randint(-LIMIT, LIMIT) for _ in range(NP * NP)]
expected_A = doitgen_polybench(NR, NQ, NP, A, C4)

span = max(abs(v) for v in expected_A)
if span > INT32_MAX:
    sys.exit("internal error: |A| reaches {}, past int32".format(span))


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
print("#define NR {}".format(NR))
print("#define NQ {}".format(NQ))
print("#define NP {}\n".format(NP))
print("/* Elements in [-{}, {}], max |result| = {}. */\n".format(LIMIT, LIMIT, span))

print_array("DATA_TYPE", "A", "NR*NQ*NP", A)
print_array("DATA_TYPE", "C4", "NP*NP", C4)
print_array_continuous("DATA_TYPE", "expected_A", "NR*NQ*NP", expected_A)
