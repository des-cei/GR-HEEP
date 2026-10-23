#include <stdio.h>
#include <stdbool.h>
#include "x-heep.h"
#include "hart.h"
#include "csr.h"
#include "csr_registers.h"
#include "fast_intr_ctrl.h"
#include "fast_intr_ctrl_regs.h"
#include "mmio.h"
#include "gr_heep.h"
#include "strela.h"
#include "strela_v1_regs.h"
#include "fir_kernel.h"
#include "dataset.h"

#if !defined(STRELA_V1_0_PERIPH_START_ADDRESS) || !defined(STRELA_V1_1_PERIPH_START_ADDRESS)
#error "strela_v1_dual_fir needs two STRELA v1, strela_v1_0 and strela_v1_1, in config/mcu-gen-config.py"
#endif

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

#define INSTANCES 2

// Global definitions
mmio_region_t strela_v1[INSTANCES];
volatile int32_t *const xs[INSTANCES] = {x0, x1};
volatile int32_t *const ys[INSTANCES] = {y0, y1};
volatile int32_t *const ys_expected[INSTANCES] = {y0_expected, y1_expected};

// Instance 1's signal and output as int16, for the mixed-width phase. Its
// golden is still y1_expected: the samples and results fit int16.
volatile int16_t *const xs_16[INSTANCES] = {0, x1_16};
volatile int16_t *const ys_16[INSTANCES] = {0, y1_16};

static int check(const char *phase, int inst, uint32_t sew) {
    int errors = 0;
    for (int i = 0; i < FIR_SAMPLES; i++) {
        int32_t got = sew == STRELA_V1_SEW_16 ? ys_16[inst][i] : ys[inst][i];
        if (got != ys_expected[inst][i])
            errors++;
    }
    if (errors)
        PRINTF("%s, y%d: %d/%d wrong\n", phase, inst, errors, FIR_SAMPLES);
    return errors;
}

// Everything but MODE and START, with both streams of width `sew`
// (STRELA_V1_SEW_32, or STRELA_V1_SEW_16 on instance 1). CLR also clears every
// engine parameter, every width back to 32 bits, the loaded configuration and
// the performance counters, so each phase starts from the same state,
// configuration load included.
static void fir_program(int inst, uint32_t sew) {
    mmio_region_t s = strela_v1[inst];
    uint32_t x, y;
    if (sew == STRELA_V1_SEW_16) {
        for (int i = 0; i < FIR_SAMPLES; i++)
            ys_16[inst][i] = 0;
        x = (uint32_t) xs_16[inst];
        y = (uint32_t) ys_16[inst];
    }
    else {
        for (int i = 0; i < FIR_SAMPLES; i++)
            ys[inst][i] = 0;
        x = (uint32_t) xs[inst];
        y = (uint32_t) ys[inst];
    }
    uint32_t bytes = strela_v1_sew_bytes(sew);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_BIT);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_ISE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, FIR_KERNEL_IN_INPUT0_CH, sew));
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_OSE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, FIR_KERNEL_OUT_OUTPUT0_CH, sew));
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) fir_kernel);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_ISE_ADDR_REG_OFFSET(FIR_KERNEL_IN_INPUT0_CH), x);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_ISE_PARAM_REG_OFFSET(FIR_KERNEL_IN_INPUT0_CH), strela_v1_se_param(FIR_SAMPLES, bytes));
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_OSE_ADDR_REG_OFFSET(FIR_KERNEL_OUT_OUTPUT0_CH), y);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_OSE_PARAM_REG_OFFSET(FIR_KERNEL_OUT_OUTPUT0_CH), strela_v1_se_param(FIR_SAMPLES, bytes));
}

// TOT counts from the MODE write, so writing it right before START keeps each
// instance's counters to its own run.
static void fir_start(int inst) {
    mmio_region_t s = strela_v1[inst];
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_MODE_REG_OFFSET, 1 << STRELA_V1_MODE_INTR_EN_BIT | 1 << STRELA_V1_MODE_PERF_CTR_EN_BIT);
    mmio_region_write32(s, (ptrdiff_t) STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_START_BIT);
}

// STATUS.EXEC_DONE is sticky until the next START or CLR.
static bool fir_done(int inst) {
    return mmio_region_read32(strela_v1[inst], (ptrdiff_t) STRELA_V1_STATUS_REG_OFFSET) & (1 << STRELA_V1_STATUS_EXEC_DONE_BIT);
}

// Stop the counters of every instance in `mask` that has finished, so its
// TOT ends with its run rather than with the other instance's, and return
// which ones those were.
static uint32_t stop_finished(uint32_t mask) {
    uint32_t finished = 0;
    for (int inst = 0; inst < INSTANCES; inst++) {
        if ((mask & (1u << inst)) && fir_done(inst)) {
            mmio_region_write32(strela_v1[inst], (ptrdiff_t) STRELA_V1_MODE_REG_OFFSET, 0);
            finished |= 1u << inst;
        }
    }
    return finished;
}

// Both instances raise the same fast external-peripheral interrupt, so an
// interrupt only says that one of them may have finished: STATUS says which.
// Interrupts are masked around the check so that one arriving between the
// check and the WFI still wakes the core (MIP is set either way), instead of
// being taken by the handler first and leaving the WFI waiting forever.
static void wait_done(uint32_t mask) {
    while (mask) {
        CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
        mask &= ~stop_finished(mask);
        if (mask)
            wait_for_interrupt();
        CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    }
}

static uint32_t report(int inst) {
    mmio_region_t s = strela_v1[inst];

    uint32_t total_cycles = mmio_region_read32(s, (ptrdiff_t) STRELA_V1_PERF_CTR_TOTAL_CYCLES_REG_OFFSET);
    uint32_t conf_cycles = mmio_region_read32(s, (ptrdiff_t) STRELA_V1_PERF_CTR_CONF_CYCLES_REG_OFFSET);
    uint32_t exec_cycles = mmio_region_read32(s, (ptrdiff_t) STRELA_V1_PERF_CTR_EXEC_CYCLES_REG_OFFSET);
    uint32_t stall_cycles = mmio_region_read32(s, (ptrdiff_t) STRELA_V1_PERF_CTR_STALL_CYCLES_REG_OFFSET);

    PRINTF("  STRELA v1 #%d TOT: %u CFG: %u EXE: %u STL: %u\n", inst, total_cycles, conf_cycles, exec_cycles, stall_cycles);
    return total_cycles;
}

int main(void) {
    PRINTF("\nSTRELA v1 dual 4-tap FIR starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // strela_v1_fir's kernel on both instances, each over its own signal.
    // Every delay_value is 0 and the taps are PE constants, so both read the
    // one kernel array untouched.
    strela_v1[0] = mmio_region_from_addr(STRELA_V1_0_PERIPH_START_ADDRESS);
    strela_v1[1] = mmio_region_from_addr(STRELA_V1_1_PERIPH_START_ADDRESS);

    // The core sleeps in WFI with its clock gated, so mcycle cannot time the
    // runs; the phases are timed with the STRELA counters instead, each one
    // stopped by wait_done() as soon as its instance finishes.
    int errors = 0;

    // Phase 1: one instance after the other, the baseline. Its run time is
    // the sum of the two TOTs.
    for (int inst = 0; inst < INSTANCES; inst++)
        fir_program(inst, STRELA_V1_SEW_32);
    for (int inst = 0; inst < INSTANCES; inst++) {
        fir_start(inst);
        wait_done(1u << inst);
    }

    PRINTF("Sequential:\n");
    uint32_t seq_cycles = 0;
    for (int inst = 0; inst < INSTANCES; inst++) {
        seq_cycles += report(inst);
        errors += check("Sequential", inst, STRELA_V1_SEW_32);
    }

    // Phase 2: both at once, sharing the bus and the memory banks. Its run
    // time is the longer TOT, short by the few cycles between the two STARTs.
    for (int inst = 0; inst < INSTANCES; inst++)
        fir_program(inst, STRELA_V1_SEW_32);
    for (int inst = 0; inst < INSTANCES; inst++)
        fir_start(inst);
    wait_done((1u << INSTANCES) - 1);

    PRINTF("Concurrent:\n");
    uint32_t conc_cycles = 0;
    for (int inst = 0; inst < INSTANCES; inst++) {
        uint32_t tot = report(inst);
        if (tot > conc_cycles)
            conc_cycles = tot;
        errors += check("Concurrent", inst, STRELA_V1_SEW_32);
    }

    // Phase 3: both at once again, instance 1 now with 16-bit streams on the
    // int16 copy of its signal. The element widths are per instance, so
    // instance 0 keeps streaming 32-bit words, and both must still match
    // their goldens.
    fir_program(0, STRELA_V1_SEW_32);
    fir_program(1, STRELA_V1_SEW_16);
    for (int inst = 0; inst < INSTANCES; inst++)
        fir_start(inst);
    wait_done((1u << INSTANCES) - 1);

    PRINTF("Concurrent, instance 1 at 16 bits:\n");
    for (int inst = 0; inst < INSTANCES; inst++) {
        report(inst);
        errors += check("Mixed widths", inst, inst ? STRELA_V1_SEW_16 : STRELA_V1_SEW_32);
    }

    PRINTF("Sequential %u cycles, concurrent %u cycles, speedup %u.%02ux\n", seq_cycles, conc_cycles,
           seq_cycles / conc_cycles, (seq_cycles % conc_cycles) * 100 / conc_cycles);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v1 dual 4-tap FIR ends\n\n");
    return errors;
}
