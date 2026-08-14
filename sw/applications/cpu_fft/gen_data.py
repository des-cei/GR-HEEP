#!/usr/bin/env python3
"""Generate the CPU FFT butterfly test data header.

The software twin of strela_fft: same stage, same layout, same defaults, so the
cycle counts of the two apps are directly comparable.

One radix-2 DIT stage of an FFT_POINTS-point transform. For every butterfly:

    t = w * b        t_re = b_re*w_re - b_im*w_im , t_im = b_re*w_im + b_im*w_re
    x = a + t        y = a - t

Layout: the two operands of each butterfly are presented as separate,
deinterleaved arrays, exactly as strela_fft needs them -- within block k of
FFT_BLOCK points, butterfly j pairs sample k*FFT_BLOCK + j (the `a` operand)
with k*FFT_BLOCK + j + FFT_BLOCK/2 (the `b` operand), ordered block by block
with j fastest. On the CPU that ordering buys nothing on its own; it is kept so
that both apps move the same words in the same order and the comparison is
honest. It is also what lets the FFT_BLOCK/2 twiddles be replayed once per block
instead of being streamed FFT_POINTS/2 times.

Fixed point: the twiddles are Q<FFT_FRAC_BITS>, so w*b comes out scaled by
2^FFT_FRAC_BITS while `a` does not. Neither implementation rescales -- STRELA's
datapath has no shifter, and the CPU version deliberately does not add one -- so
the `a` operand is emitted pre-scaled here and the results are in the same Q
format. That is also why a whole FFT is not chained in one run: every stage would
need a shift the fabric cannot perform.

Everything is exact integer arithmetic, so the expected values below are what
the CPU must produce bit for bit.
"""

import argparse
import math
import random
import sys

INT32_MAX = (1 << 31) - 1
INT32_MIN = -(1 << 31)


def twiddles(block, frac_bits):
    """Q<frac_bits> twiddle factors W^j = e^(-2*pi*i*j/block), j < block/2."""
    scale = 1 << frac_bits
    w_re, w_im = [], []
    for j in range(block // 2):
        angle = -2.0 * math.pi * j / block
        w_re.append(int(round(math.cos(angle) * scale)))
        w_im.append(int(round(math.sin(angle) * scale)))
    return w_re, w_im


def butterfly(a_re, a_im, b_re, b_im, w_re, w_im):
    """One radix-2 butterfly, in the same integer order as the DFG."""
    t_re = b_re * w_re - b_im * w_im
    t_im = b_re * w_im + b_im * w_re
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
        description="Generate the CPU FFT butterfly test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_fft's -- one middle stage of a 32-point FFT: 4 blocks
of 8 points, 4 twiddles replayed 4 times, 16 butterflies. --block FFT_POINTS
gives the final stage (one block, no replay); --block 2 gives the first (a
single twiddle, W^0). Changing them here means the CPU baseline no longer
measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=32,
                        help="points in the transform (default: 32)")
    parser.add_argument("--block", type=int, default=8,
                        help="butterfly block size of this stage (default: 8)")
    parser.add_argument("--frac-bits", type=int, default=12,
                        help="twiddle fractional bits, Q<n> (default: 12)")
    parser.add_argument("--amplitude", type=int, default=1000,
                        help="max |sample| of the input signal (default: 1000)")
    parser.add_argument("--seed", type=int, default=None,
                        help="optional RNG seed for reproducibility")
    args = parser.parse_args()

    n, block, frac = args.N, args.block, args.frac_bits
    if n < 4 or n & (n - 1):
        sys.exit(f"error: N={n} must be a power of two and at least 4")
    if block < 2 or block & (block - 1) or block > n:
        sys.exit(f"error: --block {block} must be a power of two in [2, {n}]")

    if args.seed is not None:
        random.seed(args.seed)

    half = block // 2            # twiddles, and butterflies per block
    blocks = n // block          # blocks over which the twiddles repeat
    nb = n // 2                  # butterflies in the stage
    scale = 1 << frac
    amp = args.amplitude

    # Worst case |x| = |a|*2^frac + 2*|b|*2^frac, with |w| <= 2^frac.
    bound = amp * scale + 2 * amp * scale
    if bound > INT32_MAX:
        sys.exit(f"error: |result| can reach {bound}, past int32; lower "
                 f"--amplitude or --frac-bits")

    sig_re = [random.randint(-amp, amp) for _ in range(n)]
    sig_im = [random.randint(-amp, amp) for _ in range(n)]
    w_re, w_im = twiddles(block, frac)

    # Deinterleave into butterfly operands, block by block, j fastest, so that
    # butterfly i takes twiddle i % half.
    a_re, a_im, b_re, b_im = [], [], [], []
    for k in range(blocks):
        for j in range(half):
            top, bot = k * block + j, k * block + j + half
            a_re.append(sig_re[top] * scale)   # pre-scaled, see module docstring
            a_im.append(sig_im[top] * scale)
            b_re.append(sig_re[bot])
            b_im.append(sig_im[bot])

    x_re, x_im, y_re, y_im = [], [], [], []
    for i in range(nb):
        j = i % half
        xr, xi, yr, yi = butterfly(a_re[i], a_im[i], b_re[i], b_im[i],
                                   w_re[j], w_im[j])
        x_re.append(xr)
        x_im.append(xi)
        y_re.append(yr)
        y_im.append(yi)

    span = max(abs(v) for v in x_re + x_im + y_re + y_im)
    if span > INT32_MAX:
        sys.exit(f"internal error: result magnitude {span} does not fit int32")

    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE       int32_t")
    print(f"#define FFT_POINTS      {n}")
    print(f"#define FFT_BLOCK       {block}   /* points per block in this stage */")
    print(f"#define FFT_TWIDDLES    {half}   /* = FFT_BLOCK/2, replayed per block */")
    print(f"#define FFT_BLOCKS      {blocks}   /* replays of the twiddles */")
    print(f"#define FFT_BFLIES      {nb}  /* = FFT_POINTS/2 butterflies */")
    print(f"#define FFT_FRAC_BITS   {frac}  /* twiddles are Q{frac} */")
    print("")
    print(f"/* Signal range [-{amp}, {amp}], results in Q{frac}, "
          f"max |result| = {span}. */")
    print("")

    for name, values in (("a_re", a_re), ("a_im", a_im),
                         ("b_re", b_re), ("b_im", b_im)):
        emit_array("DATA_TYPE", name, values, "FFT_BFLIES", interleaved=True)
        print("")
    for name, values in (("w_re", w_re), ("w_im", w_im)):
        emit_array("DATA_TYPE", name, values, "FFT_TWIDDLES", interleaved=True)
        print("")

    for name in ("x_re", "x_im", "y_re", "y_im"):
        emit_array("DATA_TYPE", name, [0] * nb, "FFT_BFLIES")
        print("")
    for name, values in (("x_re_expected", x_re), ("x_im_expected", x_im),
                         ("y_re_expected", y_re), ("y_im_expected", y_im)):
        emit_array("DATA_TYPE", name, values, "FFT_BFLIES")
        print("")


if __name__ == "__main__":
    main()
