#!/usr/bin/env python3
"""Generate the CPU 4-tap FIR test data header.

The software twin of strela_fir: same filter, same stimulus, same defaults, so
the cycle counts of the two apps are directly comparable.

    y[n] = (h0*x[n] + h1*x[n-1] + h2*x[n-2] + h3*x[n-3]) >> 8

with the Q8 low-pass h = [32, 96, 96, 32] / 256 -- the binomial (1+z^-1)^3 / 8,
linear phase, three zeros at Nyquist. The taps sum to exactly 256, so the >>8
rescale leaves unity DC gain. STRELA bakes them into the bitstream as per-PE
constants; here they are emitted as a `static const` array, which is the one
place they are written down, so main.c and this reference cannot drift.

Boundary condition: zero initial state (x[m] = 0 for m < 0) and exactly one
output per input, matching the DFG's transposed direct form -- its three z^-1
delays are the initial_valid preload of mul3, add2 and add1. The trailing K-1
samples of the full convolution are not produced by either implementation.

Shift semantics: the fabric's `shr` is the SHFA opcode, an *arithmetic* right
shift, so it rounds toward -infinity. GCC's >> on a signed int does the same on
RISC-V, and so does Python's, so all three agree bit for bit.

Stimulus: a deterministic broadband pseudo-random signal, seeded. This matters --
the Nyquist tone x[n] = a*n*(-1)^n is *annihilated* by this filter (its three
zeros sit exactly at Nyquist), and the golden would collapse to three non-zero
samples in the whole record, which no longer checks the taps.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

TAPS = [32, 96, 96, 32]     # Q8, the same constants strela_fir bakes into its PEs
SHIFT = 8


def fir(x, taps, shift):
    """Transposed-form FIR with zero initial state, one output per input.

    Exact 32-bit-range accumulation, then a single arithmetic right shift at the
    end -- never a per-tap rescale, which is what the fabric does too.
    """
    y = []
    for n in range(len(x)):
        acc = 0
        for k, h in enumerate(taps):
            if n - k >= 0:
                acc += h * x[n - k]
        y.append(acc >> shift)
    return y


def emit_array(ctype, name, values, size_expr, per_line=10, interleaved=False,
               qualifier="volatile"):
    section = (' __attribute__((section(".xheep_data_interleaved")))'
               if interleaved else "")
    print(f"{qualifier} {ctype} {name}[{size_expr}]{section} =")
    print("{")
    width = max((len(str(v)) for v in values), default=1)
    for start in range(0, len(values), per_line):
        row = values[start:start + per_line]
        print("    " + ", ".join(f"{v:>{width}d}" for v in row)
              + ("," if start + per_line < len(values) else ""))
    print("};")


def main():
    parser = argparse.ArgumentParser(
        description="Generate the CPU 4-tap FIR test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults match strela_fir's, which is the point of this app: change N here
and the CPU baseline no longer measures the same work as the accelerator.
""")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="input samples to filter (default: 4096)")
    parser.add_argument("--amplitude", type=int, default=10000,
                        help="max |sample| of the input signal (default: 10000)")
    parser.add_argument("--seed", type=int, default=1,
                        help="RNG seed, fixed so the header is reproducible")
    args = parser.parse_args()

    n, amp = args.N, args.amplitude
    if n < len(TAPS):
        sys.exit(f"error: N={n} must be at least {len(TAPS)}, the tap count")
    if amp < 1:
        sys.exit(f"error: --amplitude {amp} must be positive")

    # The accumulator is a plain int32 and wraps silently, so bound it here
    # instead of letting a wrap look like a CPU bug.
    gain = sum(abs(h) for h in TAPS)
    bound = gain * amp
    if bound > INT32_MAX:
        sys.exit(f"error: |accumulator| can reach {bound}, past int32; lower "
                 f"--amplitude to at most {INT32_MAX // gain}")

    rng = random.Random(args.seed)
    x = [rng.randint(-amp, amp) for _ in range(n)]
    y = fir(x, TAPS, SHIFT)

    span = max(abs(v) for v in y)
    print("#include <stdint.h>")
    print("")
    print("#define DATA_TYPE       int32_t")
    print(f"#define FIR_SAMPLES     {n}")
    print(f"#define FIR_TAPS        {len(TAPS)}")
    print(f"#define FIR_SHIFT       {SHIFT}")
    print("")
    print(f"/* Q{SHIFT} low-pass h = {TAPS} / {1 << SHIFT}. Signal range")
    print(f" * [-{amp}, {amp}], max |y| = {span}, seed {args.seed}. */")
    print("")

    # Coefficients: plain const, so the compiler may fold them into immediates
    # exactly as the fabric holds them as PE constants.
    emit_array("DATA_TYPE", "h", TAPS, "FIR_TAPS", qualifier="static const")
    print("")

    emit_array("DATA_TYPE", "x", x, "FIR_SAMPLES", interleaved=True)
    print("")
    emit_array("DATA_TYPE", "y", [0] * n, "FIR_SAMPLES")
    print("")
    emit_array("DATA_TYPE", "y_expected", y, "FIR_SAMPLES")
    print("")


if __name__ == "__main__":
    main()
