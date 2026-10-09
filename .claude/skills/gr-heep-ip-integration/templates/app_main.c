// Copyright {{YEAR}} CEIMM-UPM
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// {{AUTHOR}}
//
// TODO <what is computed> on the {{ip}} accelerator, checked against the golden
// of gen_data.py, with the same computation on the CPU as the baseline.
//
//   1. CPU reference, timed, checked against the golden.
//   2. Accelerator, polling: timed, checked.
//   3. Accelerator again (restart path), completion seen through the interrupt
//      line (fast interrupt 15, latched by the FIC; no handler, the pending bit
//      is polled), checked.
//
// Returns the number of failed checks.

#include <stdio.h>
#include <stdint.h>

#include "x-heep.h"
#include "csr.h"
#include "csr_registers.h"
#include "core_v_mini_mcu.h"
#include "fast_intr_ctrl_regs.h"
#include "{{ip}}.h"
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

// All external-peripheral interrupts are OR'd onto fast interrupt 15.
#define FIC_EXT_PERIPH_BIT 15
#define FIC_REG(offset) (*(volatile uint32_t *)(FAST_INTR_CTRL_START_ADDRESS + (offset)))

// Upper bound for any wait loop, in iterations: a missing DONE or IRQ must fail
// the test, not hang the simulation. TODO: ~3x the expected run length.
#define MAX_SPINS 10000

static DATA_T result[DATA_N];  // TODO types/sizes from dataset.h

static inline uint32_t cycles(void) {
    uint32_t c;
    CSR_READ(CSR_REG_MCYCLE, &c);
    return c;
}

// TODO: the CPU reference of the computation (same arithmetic as the hardware).
static void ref_compute(const DATA_T *in, DATA_T *out) {
    for (int i = 0; i < DATA_N; i++) out[i] = in[i];
}

static int check(const char *what, const DATA_T *r) {
    int errors = 0;
    for (int i = 0; i < DATA_N; i++) {
        if (r[i] != golden[i]) {
            if (errors < 8)
                PRINTF("  %s[%d] = %d, expected %d\n", what, i, (int)r[i], (int)golden[i]);
            errors++;
        }
    }
    PRINTF("%s: %s (%d mismatches)\n", what, errors ? "FAIL" : "OK", errors);
    return errors != 0;
}

static int wait_done_bounded(void) {
    int spins = 0;
    while (!{{ip}}_done() && ++spins < MAX_SPINS)
        ;
    return spins < MAX_SPINS;
}

int main(void) {
    int fails = 0;
    uint32_t t0, t1, t2, t3;

    PRINTF("\nStarting {{ip}} application...\n");
    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    /* 1. CPU reference */
    t0 = cycles();
    ref_compute(data_in, result);
    t1 = cycles();
    uint32_t cpu_cycles = t1 - t0;
    PRINTF("CPU: %u cycles\n", cpu_cycles);
    fails += check("cpu", result);

    /* 2. Accelerator, polling */
    if ({{ip}}_busy() || {{ip}}_done()) {
        PRINTF("status not idle after reset\n");
        fails++;
    }
    t0 = cycles();
    // TODO: load inputs / configure
    t1 = cycles();
    {{ip}}_start();
    if (!wait_done_bounded()) {
        PRINTF("DONE never set\n");
        return fails + 1;
    }
    t2 = cycles();
    // TODO: read results into result[]
    t3 = cycles();
    PRINTF("ACC: LOAD %u EXE %u READ %u TOT %u cycles\n", t1 - t0, t2 - t1, t3 - t2, t3 - t0);
    fails += check("acc", result);
    PRINTF("Speedup: %u.%02u x\n", cpu_cycles / (t3 - t0), (100 * cpu_cycles / (t3 - t0)) % 100);

    /* 3. Accelerator again, completion through the interrupt line */
    {{ip}}_clear_done();
    // The FIC latches only enabled lines; mie stays clear, so no trap is taken.
    FIC_REG(FAST_INTR_CTRL_FAST_INTR_ENABLE_REG_OFFSET) |= 1u << FIC_EXT_PERIPH_BIT;
    FIC_REG(FAST_INTR_CTRL_FAST_INTR_CLEAR_REG_OFFSET) = 1u << FIC_EXT_PERIPH_BIT;
    {{ip}}_intr_enable(1);
    // TODO: load inputs again
    {{ip}}_start();
    int spins = 0;
    while (!(FIC_REG(FAST_INTR_CTRL_FAST_INTR_PENDING_REG_OFFSET) & (1u << FIC_EXT_PERIPH_BIT))
           && ++spins < MAX_SPINS)
        ;
    if (spins >= MAX_SPINS) {
        PRINTF("IRQ never raised\n");
        fails++;
    }
    {{ip}}_clear_done();
    FIC_REG(FAST_INTR_CTRL_FAST_INTR_CLEAR_REG_OFFSET) = 1u << FIC_EXT_PERIPH_BIT;
    if (FIC_REG(FAST_INTR_CTRL_FAST_INTR_PENDING_REG_OFFSET) & (1u << FIC_EXT_PERIPH_BIT)) {
        PRINTF("IRQ still pending after clearing DONE\n");
        fails++;
    }
    {{ip}}_intr_enable(0);
    // TODO: read results into result[]
    fails += check("acc_irq", result);

    PRINTF(fails ? "FAIL!!!\n" : "SUCCESS!\n");
    PRINTF("Finishing {{ip}} application...\n");
    return fails;
}
