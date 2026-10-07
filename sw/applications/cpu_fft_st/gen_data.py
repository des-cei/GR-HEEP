#!/usr/bin/env python3
"""Generate the CPU stationary-twiddle FFT butterfly test data header.

The software twin of strela_v2_fft_st: same stage, same layout, same defaults, so
the cycle counts of the two apps are directly comparable.

One radix-2 butterfly stage with a single twiddle factor w = (W_RE, W_IM): for
every butterfly

    t = w * b        t_re = b_re*W_RE - b_im*W_IM , t_im = b_re*W_IM + b_im*W_RE
    x = a + t        y = a - t

Layout, x-trela's cpu_fft_nt/strela_fft_nt one: N complex points held in place
in real[]/imag[], the `a` operands in the first half and the `b` operands in the
second, 8-bit samples, and the results written back over the same halves (x
over a, y over b). On the CPU the in-place layout buys nothing on its own; it is
kept so that both apps move the same words in the same order and the comparison
is honest. There is no twiddle table and no Q format: the twiddle is the pair
of integer constants the DFG (mapper/applications/fft_st/main.dot) gives its
four mul PEs, and the CPU version deliberately keeps the same integer kernel.

Everything is exact integer arithmetic, so the expected values below are what
the CPU must produce bit for bit.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

# The stationary twiddle: mul0/mul3 carry constant=5 (w_re) and mul1/mul2
# constant=3 (w_im) in fft_st/main.dot. Change the DFG and these together.
W_RE = 5
W_IM = 3


def butterfly(a_re, a_im, b_re, b_im):
    """One radix-2 butterfly, in the same integer order as the DFG."""
    t_re = b_re * W_RE - b_im * W_IM
    t_im = b_re * W_IM + b_im * W_RE
    return (a_re + t_re, a_im + t_im,      # x
            a_re - t_re, a_im - t_im)      # y


def emit_array(ctype, name, values, size_expr, per_line=8, interleaved=False):
    section = (' __attribute__((section(".xheep_data_interleaved")))'
               if interleaved else "")
    print(f"volatile {ctype} {name}[{size_expr}]{section} =")
    print("{")
    width = max((len(str(v)) for v in values), default=1)
    for start in range(0, len(values), per_line):
        row = values[start:start + per_line]
        print("    " + ", ".join(f"{v:>{width}d}" for v in row)
              + ("," if start + per_line < len(values) else ""))
    print("};")


def main():
    parser = argparse.ArgumentParser(
        description="Generate the CPU stationary-twiddle FFT butterfly test "
                    "data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_v2_fft_st's -- x-trela's strela_fft_nt shape: 512
complex points, i.e. 256 butterflies pairing real/imag[i] with
real/imag[i + 256], on 8-bit samples. Changing them here means the CPU
baseline no longer measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=512,
                        help="complex points, two per butterfly (default: 512)")
    parser.add_argument("--amplitude", type=int, default=127,
                        help="max |sample| of the input signal (default: 127)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    n, amp = args.N, args.amplitude
    if n < 2 or n % 2:
        sys.exit(f"error: N={n} must be even and at least 2")

    if args.seed is not None:
        random.seed(args.seed)

    g = n // 2                   # butterflies, and the a/b split point

    # Worst case |x| = |a| + |b|*(|W_RE| + |W_IM|).
    bound = amp + amp * (abs(W_RE) + abs(W_IM))
    if bound > INT32_MAX:
        sys.exit(f"error: |result| can reach {bound}, past int32; lower "
                 f"--amplitude")

    real = [random.randint(-amp, amp) for _ in range(n)]
    imag = [random.randint(-amp, amp) for _ in range(n)]

    # Butterfly i pairs sample i (`a`) with sample i + g (`b`); x lands on a's
    # slot and y on b's, which is what lets the fabric work in place.
    exp_re, exp_im = [0] * n, [0] * n
    for i in range(g):
        xr, xi, yr, yi = butterfly(real[i], imag[i], real[i + g], imag[i + g])
        exp_re[i], exp_im[i] = xr, xi
        exp_re[i + g], exp_im[i + g] = yr, yi

    span = max(abs(v) for v in exp_re + exp_im)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE       int32_t")
    print(f"#define DATA_SIZE       {n}   /* complex points */")
    print(f"#define FFT_BFLIES      {g}   /* = DATA_SIZE/2 butterflies */")
    print(f"#define FFT_W_RE        {W_RE}   /* stationary twiddle, in the bitstream */")
    print(f"#define FFT_W_IM        {W_IM}")
    print("")
    print(f"/* Signal range [-{amp}, {amp}], max |result| = {span}. */")
    print("")

    # Read and then overwritten by the kernel: interleaved banks, as in the
    # STRELA twin.
    emit_array("DATA_TYPE", "real", real, "DATA_SIZE", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "imag", imag, "DATA_SIZE", interleaved=True)
    print("")

    # The golden values to compare against.
    emit_array("DATA_TYPE", "expected_real", exp_re, "DATA_SIZE")
    print("")
    emit_array("DATA_TYPE", "expected_imag", exp_im, "DATA_SIZE")
    print("")


if __name__ == "__main__":
    main()
