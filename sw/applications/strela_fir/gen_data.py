#!/usr/bin/env python3
"""Generate the STRELA 4-tap FIR test data header.

The fabric runs the whole filter in one streaming pass over FIR_SAMPLES
samples (mapper/applications/fir/main.dot):

    y[n] = (h0*x[n] + h1*x[n-1] + h2*x[n-2] + h3*x[n-3]) >> 8

with the Q8 low-pass h = [32, 96, 96, 32] / 256 -- the binomial (1+z^-1)^3 / 8,
linear phase, three zeros at Nyquist. The taps sum to exactly 256, so the >>8
rescale leaves unity DC gain. The coefficients are *baked into the bitstream* as
per-PE constants, so they are documented here but not emitted: changing them
means re-solving the DFG, not editing this file.

Boundary condition: the DFG is the transposed direct form and its three z^-1
delays are the initial_valid preload of mul3, add2 and add1, i.e. zero. The
fabric therefore emits exactly one output per input, starting from zero state
(x[m] = 0 for m < 0), and the trailing K-1 samples of the full convolution are
simply not produced. The reference below models that exactly.

Shift semantics: `shr` maps to the SHFA opcode, which is an *arithmetic* right
shift (rtl/alu/ash.sv with DATA_TC set), so it rounds toward -infinity. Python's
>> on ints does the same thing, so the golden values are bit-exact.

Stimulus: a deterministic broadband pseudo-random signal. This matters -- the
obvious alternative, the Nyquist tone x[n] = a*n*(-1)^n that elastic-cgra's own
regression drives, is *annihilated* by this filter (its three zeros sit exactly
at Nyquist), and the golden collapses to three non-zero samples in the whole
record, which no longer checks the taps. Broadband noise exercises all four taps with
independent values, so a single wrong coefficient shows up immediately.
"""

import argparse
import random
import sys

INT32_MAX = (1 << 31) - 1

TAPS = [32, 96, 96, 32]     # Q8, baked into the bitstream as PE constants
SHIFT = 8                   # shr0's constant is -8 (negative = shift right)


def fir(x, taps, shift):
    """Transposed-form FIR with zero initial state, one output per input.

    Same integer semantics as the DFG: exact 32-bit-range accumulation, then a
    single arithmetic right shift at the end (never a per-tap rescale).
    """
    y = []
    for n in range(len(x)):
        acc = 0
        for k, h in enumerate(taps):
            if n - k >= 0:
                acc += h * x[n - k]
        y.append(acc >> shift)
    return y


def emit_array(ctype, name, values, size_expr, per_line=10, interleaved=False):
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
        description="Generate the STRELA 4-tap FIR test data header.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
The defaults must match gen_descriptors.py's: `make gen-app-data
PROJECT=strela_fir` runs both with no arguments, so change N in both or the C
array sizes and the descriptor byte counts silently diverge.
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

    # The accumulator is a plain int32 in the fabric and wraps silently, so
    # bound it here instead of letting a wrap look like a CGRA bug.
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
    print(f"#define FIR_SAMPLES     {n}")
    print(f"#define FIR_TAPS        {len(TAPS)}")
    print("")
    print(f"/* Q{SHIFT} low-pass h = {TAPS} / {1 << SHIFT}, baked into the")
    print(f" * bitstream as PE constants. Signal range [-{amp}, {amp}],")
    print(f" * max |y| = {span}, seed {args.seed}. */")
    print("")

    # Read by the CGRA: interleaved banks, like the other STRELA apps.
    emit_array("int32_t", "x", x, "FIR_SAMPLES", interleaved=True)
    print("")

    # Written by the CGRA, plus the golden values to compare against.
    emit_array("int32_t", "y", [0] * n, "FIR_SAMPLES")
    print("")
    emit_array("int32_t", "y_expected", y, "FIR_SAMPLES")
    print("")


if __name__ == "__main__":
    main()
