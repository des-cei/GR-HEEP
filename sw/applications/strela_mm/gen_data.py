import sys
import random

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

# Read dimensions from command-line arguments
m = int(sys.argv[1])
k = int(sys.argv[2])
n = int(sys.argv[3])

# Generate random matrices with values in range [-50, 50)
X = [int(random.random() * 100.0 - 50.0) for _ in range(m * k)]
Y = [int(random.random() * 100.0 - 50.0) for _ in range(k * n)]
Z = matmul(X, Y, m, k, n)
S = [0 for _ in range(m * n)]

# Format a flat Python array as a visually aligned matrix
def format_matrix(pyarr, rows, cols, indent="    "):
    """Return the matrix formatted with aligned columns for readability."""
    if not pyarr:
        return ""
    width = max(len(str(v)) for v in pyarr)  # Max width including sign
    lines = []
    for i in range(rows):
        row_vals = []
        for j in range(cols):
            v = pyarr[i * cols + j]
            row_vals.append(f"{v:>{width}d}")   # Right-aligned to max width
        lines.append(indent + ", ".join(row_vals))
    return ",\n".join(lines)

# Print aligned matrix stored in interleaved memory section
def print_matrix_interleaved(array_type, array_name, rows, cols, pyarr, size_expr):
    """Print a C array with aligned matrix formatting in a custom memory section."""
    print(f'volatile {array_type} {array_name}[{size_expr}] __attribute__((section(".xheep_data_interleaved"))) =')
    print("{")
    print(format_matrix(pyarr, rows, cols))
    print("\n};")

# Print aligned matrix in normal C array
def print_matrix(array_type, array_name, rows, cols, pyarr, size_expr):
    """Print a C array with aligned matrix formatting."""
    print(f'volatile {array_type} {array_name}[{size_expr}] =')
    print("{")
    print(format_matrix(pyarr, rows, cols))
    print("\n};")

# Print a scalar variable
def print_scalar(scalar_type, scalar_name, pyscalar):
    """Print a scalar variable declaration."""
    print(f"{scalar_type} {scalar_name} = {pyscalar};")

# Generate header output
print("#include <stdint.h>")
print(f"#define M {m}")
print(f"#define K {k}")
print(f"#define N {n}")
print("")

# Print matrices
print_matrix_interleaved("int32_t", "matA", m, k, X, "M*K")
print("")
print_matrix_interleaved("int32_t", "matB", k, n, Y, "K*N")
print("")
print_matrix("int32_t", "matC", m, n, S, "M*N")
print("")
print_matrix("int32_t", "matC_expected", m, n, Z, "M*N")
