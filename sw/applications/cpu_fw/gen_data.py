#!/usr/bin/env python3
# Python 3.8.20 compatible

import sys

# Default problem size, so the script runs with no arguments (make gen-app-data):
# PolyBench 4.2.1 MINI_DATASET (medley/floyd-warshall). This is the one CPU app
# that is not on SMALL: it exists as the baseline for strela_fw, whose
# descriptor tables do not fit at SMALL (see strela_fw/gen_descriptors.py), and
# a baseline is only worth reading at the shape the accelerator actually runs.
DEFAULT_N = 60


def _arg(i, default):
    return int(sys.argv[i]) if len(sys.argv) > i else default


N = _arg(1, DEFAULT_N)


def init_array(n):
    """PolyBench's own init_array, not a random draw.

    Two reasons, both inherited from strela_fw. The relaxation only ever lowers
    entries, so a non-negative initial matrix keeps every intermediate
    non-negative and bounded by its maximum; and PolyBench's mix of short edges
    with a 999 "no edge" sentinel leaves real shortest-path structure for the
    sweep to find, where a uniform random matrix collapses to near-constant
    minima after a single pivot.
    """
    path = [0] * (n * n)
    for i in range(n):
        for j in range(n):
            value = i * j % 7 + 1
            if (i + j) % 13 == 0 or (i + j) % 7 == 0 or (i + j) % 11 == 0:
                value = 999
            path[i * n + j] = value
    return path


def floyd_warshall(path, n):
    """PolyBench's kernel, relaxing in place."""
    out = list(path)
    for k in range(n):
        for i in range(n):
            through = out[i * n + k]
            for j in range(n):
                candidate = through + out[k * n + j]
                if candidate < out[i * n + j]:
                    out[i * n + j] = candidate
    return out


path = init_array(N)
expected_path = floyd_warshall(path, N)

# How much work the sweep actually does. A pass that changes nothing would
# succeed against a golden that also changed nothing, so this is the number to
# look at before trusting a result.
changed = sum(1 for a, b in zip(path, expected_path) if a != b)


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
print("/* PolyBench init_array; the sweep changes {} of {} entries. */\n".format(
    changed, N * N))

print_array("DATA_TYPE", "path", "N*N", path)
print_array_continuous("DATA_TYPE", "expected_path", "N*N", expected_path)
