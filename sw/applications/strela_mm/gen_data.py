import argparse
import random
import sys


# Perform matrix multiplication: C = A(MxK) * B(KxN)
def matmul(A, B, M, K, N):
    C = [0] * (M * N)
    for i in range(M):
        for k in range(K):
            aik = A[i * K + k]
            base_B = k * N
            base_C = i * N
            for j in range(N):
                C[base_C + j] += aik * B[base_B + j]
    return C


DTYPE_INFO = {
    "int8":  {"bits":  8, "ctype": "int8_t",  "macro": "STRELA_MM_DTYPE_INT8",  "id": 1},
    "int16": {"bits": 16, "ctype": "int16_t", "macro": "STRELA_MM_DTYPE_INT16", "id": 2},
    "int32": {"bits": 32, "ctype": "int32_t", "macro": "STRELA_MM_DTYPE_INT32", "id": 4},
}


def signed_max(bits):
    return (1 << (bits - 1)) - 1


def signed_min(bits):
    return -(1 << (bits - 1))


def safe_elem_limit(K, c_bits):
    """Largest |v| such that K*v*v fits in a signed c_bits accumulator."""
    cmax = signed_max(c_bits)
    limit = int((cmax // max(K, 1)) ** 0.5)
    while K * limit * limit > cmax:
        limit -= 1
    return limit


def gen_random_array(n, lo, hi):
    return [random.randint(lo, hi) for _ in range(n)]


# Format a flat Python array as a visually aligned matrix
def format_matrix(pyarr, rows, cols, indent="    "):
    if not pyarr:
        return ""
    width = max(len(str(v)) for v in pyarr)
    lines = []
    for i in range(rows):
        row_vals = [f"{pyarr[i * cols + j]:>{width}d}" for j in range(cols)]
        lines.append(indent + ", ".join(row_vals))
    return ",\n".join(lines)


def print_matrix_interleaved(array_type, array_name, rows, cols, pyarr, size_expr):
    print(f'volatile {array_type} {array_name}[{size_expr}] __attribute__((section(".xheep_data_interleaved"))) =')
    print("{")
    print(format_matrix(pyarr, rows, cols))
    print("\n};")


def print_matrix(array_type, array_name, rows, cols, pyarr, size_expr):
    print(f'volatile {array_type} {array_name}[{size_expr}] =')
    print("{")
    print(format_matrix(pyarr, rows, cols))
    print("\n};")


def main():
    parser = argparse.ArgumentParser(
        description="Generate STRELA matmul test data header (matA, matB, matC, matC_expected).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Modes:
  accum32  matA/matB use the selected dtype, matC/matC_expected stay int32_t
           (TFLM-style: input activations narrow, accumulator wide).
  same     matA/matB/matC all use the selected dtype. Values of matA and matB
           are clamped so that K * max_A * max_B fits in the dtype range —
           result never overflows.""")
    parser.add_argument("M", type=int, help="rows of A and C")
    parser.add_argument("K", type=int, help="cols of A / rows of B")
    parser.add_argument("N", type=int, help="cols of B and C")
    parser.add_argument("--dtype", choices=list(DTYPE_INFO.keys()), default="int32",
                        help="element type for matA/matB (default: int32)")
    parser.add_argument("--mode", choices=["accum32", "same"], default="accum32",
                        help="accumulator strategy (default: accum32)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    M, K, N = args.M, args.K, args.N
    info = DTYPE_INFO[args.dtype]
    in_bits = info["bits"]
    in_ctype = info["ctype"]

    if args.mode == "accum32":
        acc_bits = 32
        acc_ctype = "int32_t"
    else:
        acc_bits = in_bits
        acc_ctype = in_ctype

    # Element range: bounded by both the input dtype range and the accumulator
    # capacity (so K * max_A * max_B fits in acc_bits).
    elem_cap = safe_elem_limit(K, acc_bits)
    in_cap = signed_max(in_bits)
    elem_limit = min(elem_cap, in_cap)

    if elem_limit < 1:
        sys.exit(f"error: K={K} too large for dtype={args.dtype} mode={args.mode}; "
                 f"no non-zero values fit without overflow")

    lo, hi = -elem_limit, elem_limit

    X = gen_random_array(M * K, lo, hi)
    Y = gen_random_array(K * N, lo, hi)
    Z = matmul(X, Y, M, K, N)
    S = [0] * (M * N)

    # Sanity check: result must fit in accumulator type
    zmin, zmax = (min(Z), max(Z)) if Z else (0, 0)
    if zmin < signed_min(acc_bits) or zmax > signed_max(acc_bits):
        sys.exit(f"internal error: matmul result range [{zmin}, {zmax}] does not fit "
                 f"in {acc_ctype} (bug in element-limit computation)")

    # Header output
    print("#include <stdint.h>")
    print("")
    print(f"#define STRELA_MM_DTYPE_INT8  1")
    print(f"#define STRELA_MM_DTYPE_INT16 2")
    print(f"#define STRELA_MM_DTYPE_INT32 4")
    print(f"#define STRELA_MM_DTYPE {info['macro']}")
    print("")
    print(f"#define M {M}")
    print(f"#define K {K}")
    print(f"#define N {N}")
    print("")
    print(f"typedef {in_ctype}  strela_mm_data_t;   /* matA, matB element type */")
    print(f"typedef {acc_ctype} strela_mm_acc_t;    /* matC accumulator type */")
    print("")
    print(f"/* Generated with mode={args.mode}, dtype={args.dtype}, "
          f"element range=[{lo},{hi}], result range=[{zmin},{zmax}] */")
    print("")

    print_matrix_interleaved("strela_mm_data_t", "matA", M, K, X, "M*K")
    print("")
    print_matrix_interleaved("strela_mm_data_t", "matB", K, N, Y, "K*N")
    print("")
    print_matrix("strela_mm_acc_t", "matC", M, N, S, "M*N")
    print("")
    print_matrix("strela_mm_acc_t", "matC_expected", M, N, Z, "M*N")


if __name__ == "__main__":
    main()
