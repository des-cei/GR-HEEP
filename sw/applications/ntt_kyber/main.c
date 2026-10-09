// Copyright 2026 CEI-UPM
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Daniel Vazquez (daniel.vazquez@upm.es)
//
// CRYSTALS-Kyber forward NTT on the ntt_kyber accelerator, checked bit for bit
// against the reference ntt() (pq-crystals, i.e. poly_ntt() without its final
// Barrett reduction), which also runs here on the CPU as the baseline.
//
//   1. CPU reference, timed, checked against the golden of gen_data.py.
//   2. Accelerator, polling, 32-bit window accesses: LOAD / EXE / READ cycles.
//   3. Accelerator again (restart path), halfword window accesses, completion
//      seen through the interrupt line (fast interrupt 15, latched by the FIC;
//      no handler is installed, the pending bit is polled).
//
// Returns the number of failed checks.
//
// Under Verilator the CPU reference dominates the run time (~21k cycles against
// ~4k for the accelerator, including the transfers).

#include <stdio.h>
#include <stdint.h>

#include "x-heep.h"
#include "csr.h"
#include "csr_registers.h"
#include "core_v_mini_mcu.h"
#include "fast_intr_ctrl_regs.h"
#include "ntt_kyber.h"
#include "dataset.h"

/* By default, printfs are activated for FPGA and disabled for simulation. */
#define PRINTF_IN_FPGA  1
#define PRINTF_IN_SIM   1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif TARGET_IS_FPGA && PRINTF_IN_FPGA
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#define FIC_EXT_PERIPH_BIT 15
#define FIC_REG(offset) (*(volatile uint32_t *)(FAST_INTR_CTRL_START_ADDRESS + (offset)))

static int16_t poly[NTT_N];

/* ---- Reference ntt() (pq-crystals ref/ntt.c) ---- */

static int16_t montgomery_reduce(int32_t a) {
    int16_t t = (int16_t)a * NTT_QINV;
    return (a - (int32_t)t * NTT_Q) >> 16;
}

static int16_t fqmul(int16_t a, int16_t b) {
    return montgomery_reduce((int32_t)a * b);
}

static void ntt_ref(int16_t r[NTT_N]) {
    unsigned int len, start, j, k = 1;
    for (len = 128; len >= 2; len >>= 1) {
        for (start = 0; start < NTT_N; start = j + len) {
            int16_t zeta = zetas[k++];
            for (j = start; j < start + len; j++) {
                int16_t t = fqmul(zeta, r[j + len]);
                r[j + len] = r[j] - t;
                r[j] = r[j] + t;
            }
        }
    }
}

static inline uint32_t cycles(void) {
    uint32_t c;
    CSR_READ(CSR_REG_MCYCLE, &c);
    return c;
}

static int check(const char *what, const int16_t *r) {
    int errors = 0;
    for (int i = 0; i < NTT_N; i++) {
        if (r[i] != ntt_golden[i]) {
            if (errors < 8)
                PRINTF("  %s[%d] = %d, expected %d\n", what, i, r[i], ntt_golden[i]);
            errors++;
        }
    }
    PRINTF("%s: %s (%d mismatches)\n", what, errors ? "FAIL" : "OK", errors);
    return errors != 0;
}

int main(void) {
    int fails = 0;
    uint32_t t0, t1, t2, t3;

    PRINTF("\nStarting NTT Kyber application...\n");
    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    /* 1. CPU reference */
    for (int i = 0; i < NTT_N; i++) poly[i] = ntt_in[i];
    t0 = cycles();
    ntt_ref(poly);
    t1 = cycles();
    PRINTF("CPU: %u cycles\n", t1 - t0);
    fails += check("cpu", poly);
    uint32_t cpu_cycles = t1 - t0;

    /* 2. Accelerator, polling, 32-bit accesses */
    if (ntt_kyber_busy() || ntt_kyber_done()) {
        PRINTF("status not idle after reset\n");
        fails++;
    }
    t0 = cycles();
    ntt_kyber_load(ntt_in);
    t1 = cycles();
    ntt_kyber_start();
    if (!ntt_kyber_busy()) {
        PRINTF("BUSY not set after START\n");
        fails++;
    }
    ntt_kyber_wait();
    t2 = cycles();
    if (ntt_kyber_busy()) {
        PRINTF("BUSY still set with DONE\n");
        fails++;
    }
    ntt_kyber_store(poly);
    t3 = cycles();
    PRINTF("NTT: LOAD %u EXE %u READ %u TOT %u cycles\n", t1 - t0, t2 - t1, t3 - t2, t3 - t0);
    fails += check("ntt", poly);
    PRINTF("Speedup: %u.%02u x (EXE only: %u.%02u x)\n",
           cpu_cycles / (t3 - t0), (100 * cpu_cycles / (t3 - t0)) % 100,
           cpu_cycles / (t2 - t1), (100 * cpu_cycles / (t2 - t1)) % 100);

    /* 3. Accelerator again: halfword accesses, completion via the interrupt line */
    ntt_kyber_clear_done();
    if (ntt_kyber_done()) {
        PRINTF("DONE not cleared by write-1-to-clear\n");
        fails++;
    }
    // The FIC only latches lines enabled here; the core still takes no trap,
    // since mie is left clear.
    FIC_REG(FAST_INTR_CTRL_FAST_INTR_ENABLE_REG_OFFSET) |= 1u << FIC_EXT_PERIPH_BIT;
    ntt_kyber_intr_enable(1);
    if (FIC_REG(FAST_INTR_CTRL_FAST_INTR_PENDING_REG_OFFSET) & (1u << FIC_EXT_PERIPH_BIT)) {
        PRINTF("IRQ pending before the run\n");
        fails++;
        FIC_REG(FAST_INTR_CTRL_FAST_INTR_CLEAR_REG_OFFSET) = 1u << FIC_EXT_PERIPH_BIT;
    }
    for (int i = 0; i < NTT_N; i++) NTT_KYBER_COEFFS[i] = ntt_in[i];
    ntt_kyber_start();
    if (ntt_kyber_done()) {
        PRINTF("DONE not cleared by START\n");
        fails++;
    }
    // Bounded: the NTT takes ~700 cycles, so a missing IRQ fails instead of hanging.
    int spins = 0;
    while (!(FIC_REG(FAST_INTR_CTRL_FAST_INTR_PENDING_REG_OFFSET) & (1u << FIC_EXT_PERIPH_BIT))
           && ++spins < 2000)
        ;
    if (spins >= 2000) {
        PRINTF("IRQ never raised\n");
        fails++;
    }
    if (!ntt_kyber_done()) {
        PRINTF("IRQ without DONE\n");
        fails++;
    }
    ntt_kyber_clear_done();
    FIC_REG(FAST_INTR_CTRL_FAST_INTR_CLEAR_REG_OFFSET) = 1u << FIC_EXT_PERIPH_BIT;
    if (FIC_REG(FAST_INTR_CTRL_FAST_INTR_PENDING_REG_OFFSET) & (1u << FIC_EXT_PERIPH_BIT)) {
        PRINTF("IRQ still pending after clearing DONE\n");
        fails++;
    }
    ntt_kyber_intr_enable(0);
    for (int i = 0; i < NTT_N; i++) poly[i] = NTT_KYBER_COEFFS[i];
    fails += check("ntt_irq", poly);

    PRINTF(fails ? "FAIL!!!\n" : "SUCCESS!\n");
    PRINTF("Finishing NTT Kyber application...\n");
    return fails;
}
