#!/usr/bin/env python3
"""Generate the test data header for two STRELA v1 running the 4-tap FIR at once.

strela_v1_dual_fir drives both STRELA v1 instances of the SoC (strela_v1_0 and
strela_v1_1 in config/mcu-gen-config.py) with the same fir bitstream, each over
its own signal. The filter, its boundary condition and its integer semantics
are strela_v1_fir's, so this generator imports that app's fir() instead of
copying it, and only adds the second signal.

The two signals are drawn from consecutive seeds, so they differ: an instance
that wrote the other one's output, or read the other one's input, fails its
check instead of passing by coincidence. x0 uses strela_v1_fir's own seed, so
y0_expected is that app's y_expected.

Instance 1's signal and output are also emitted as int16_t (x1_16, y1_16), for
the phase that runs it with 16-bit streams next to instance 0 at 32 bits. The
taps sum to exactly 1 << SHIFT, so |y| never exceeds the amplitude, and an
amplitude that fits int16 makes the 16-bit run's golden y1_expected unchanged.
"""

import argparse
import importlib.util
import os
import random
import sys

INT32_MAX = (1 << 31) - 1
INT16_MAX = (1 << 15) - 1

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "strela_v1_fir_gen", os.path.join(HERE, "..", "strela_v1_fir", "gen_data.py"))
fir_gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fir_gen)


def main():
    parser = argparse.ArgumentParser(
        description="Generate the two-STRELA-v1 FIR test data header.")
    parser.add_argument("N", type=int, nargs="?", default=4096,
                        help="input samples per instance (default: 4096)")
    parser.add_argument("--amplitude", type=int, default=10000,
                        help="max |sample| of the input signals (default: 10000)")
    parser.add_argument("--seed", type=int, default=1,
                        help="RNG seed of instance 0; instance 1 uses seed + 1")
    args = parser.parse_args()

    n, amp = args.N, args.amplitude
    taps, shift = fir_gen.TAPS, fir_gen.SHIFT
    if n < len(taps):
        sys.exit(f"error: N={n} must be at least {len(taps)}, the tap count")
    # Each instance streams its whole signal through one stream engine, whose
    # 16-bit size field is the byte span of the stream.
    if 4 * n > 0xFFFF:
        sys.exit(f"error: N={n} spans {4 * n} bytes, past the 16-bit stream "
                 f"engine size field; at most {0xFFFF // 4} samples")
    if amp < 1:
        sys.exit(f"error: --amplitude {amp} must be positive")
    if sum(taps) != 1 << shift:
        sys.exit("error: the taps must sum to 1 << SHIFT for |y| <= amplitude")
    # The 16-bit phase sign-extends x1_16 and keeps the low 16 bits of y.
    if amp > INT16_MAX:
        sys.exit(f"error: --amplitude {amp} does not fit the 16-bit phase's "
                 f"int16 streams; at most {INT16_MAX}")
    gain = sum(abs(h) for h in taps)
    if gain * amp > INT32_MAX:
        sys.exit(f"error: |accumulator| can reach {gain * amp}, past int32; "
                 f"lower --amplitude to at most {INT32_MAX // gain}")

    print("#include <stdint.h>")
    print("")
    print(f"#define FIR_SAMPLES     {n}")
    print(f"#define FIR_TAPS        {len(taps)}")
    print("")
    print(f"/* Q{shift} low-pass h = {taps} / {1 << shift}, baked into the")
    print(f" * bitstream as PE constants. Signal range [-{amp}, {amp}],")
    print(f" * seeds {args.seed} (instance 0) and {args.seed + 1} (instance 1). */")
    print("")

    for inst in range(2):
        rng = random.Random(args.seed + inst)
        x = [rng.randint(-amp, amp) for _ in range(n)]
        y = fir_gen.fir(x, taps, shift)

        # Read by the CGRA: interleaved banks, like the other STRELA apps.
        fir_gen.emit_array("int32_t", f"x{inst}", x, "FIR_SAMPLES",
                           interleaved=True)
        print("")
        # Written by the CGRA, also interleaved: strela_v1_fir's y sits in one
        # contiguous bank, and two of them there land in the *same* 64 KiB
        # bank, which then takes one write per cycle for both instances and
        # halves each one's rate. Interleaved, the two output streams spread
        # over all the banks like the inputs do.
        fir_gen.emit_array("int32_t", f"y{inst}", [0] * n, "FIR_SAMPLES",
                           interleaved=True)
        print("")
        # The golden values to compare against.
        fir_gen.emit_array("int32_t", f"y{inst}_expected", y, "FIR_SAMPLES")
        print("")
        if inst == 1:
            # The same signal and an output for the 16-bit streams; the
            # golden is y1_expected.
            fir_gen.emit_array("int16_t", "x1_16", x, "FIR_SAMPLES",
                               interleaved=True)
            print("")
            fir_gen.emit_array("int16_t", "y1_16", [0] * n, "FIR_SAMPLES",
                               interleaved=True)
            print("")


if __name__ == "__main__":
    main()
