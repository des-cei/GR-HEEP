#include <stdio.h>
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

// Global definitions
mmio_region_t strela_v1;

static int check(const char *name, volatile int32_t *got, volatile int32_t *expected) {
    int errors = 0;
    for (int i = 0; i < FIR_SAMPLES; i++) {
        if (got[i] != expected[i])
            errors++;
    }
    if (errors)
        PRINTF("%s: %d/%d wrong\n", name, errors, FIR_SAMPLES);
    return errors;
}

int main(void) {
    PRINTF("\nSTRELA v1 4-tap FIR starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // The same transposed-form FIR as strela_v2_fir, mapped on STRELA v1's 4x4
    // fabric: every delay_value is 0 and the taps are baked in as PE
    // constants, so the kernel array goes to the fabric untouched.

    // STRELA v1
    strela_v1 = mmio_region_from_addr(STRELA_V1_PERIPH_START_ADDRESS);

    // Configure STRELA mode. CLR also clears every node parameter, so the
    // nodes the kernel does not use stay at size 0 and finish immediately.
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_BIT);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_MODE_REG_OFFSET, 1 << STRELA_V1_MODE_INTR_EN_BIT | 1 << STRELA_V1_MODE_PERF_CTR_EN_BIT);

    // Configuration, then one stream in and one stream out. A v1 memory node
    // index is the fabric channel the io_map binds the port to.
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) fir_kernel);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_IMN_ADDR_REG_OFFSET(FIR_KERNEL_IN_INPUT0_CH), (uint32_t) x);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_IMN_PARAM_REG_OFFSET(FIR_KERNEL_IN_INPUT0_CH), strela_v1_imn_param(FIR_SAMPLES, sizeof(int32_t)));
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_OMN_ADDR_REG_OFFSET(FIR_KERNEL_OUT_OUTPUT0_CH), (uint32_t) y);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_OMN_SIZE_REG_OFFSET(FIR_KERNEL_OUT_OUTPUT0_CH), strela_v1_omn_size(FIR_SAMPLES));

    // Start STRELA execution
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_START_BIT);

    // Wait until STRELA finishes
    wait_for_interrupt();

    // Disable performance counters
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_MODE_REG_OFFSET, 0);

    // Read performance counters
    uint32_t total_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_TOTAL_CYCLES_REG_OFFSET);
    uint32_t conf_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_CONF_CYCLES_REG_OFFSET);
    uint32_t exec_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_EXEC_CYCLES_REG_OFFSET);
    uint32_t stall_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_STALL_CYCLES_REG_OFFSET);

    PRINTF("TOT: %u\n", total_cycles);
    PRINTF("CFG: %u\n", conf_cycles);
    PRINTF("EXE: %u\n", exec_cycles);
    PRINTF("STL: %u\n", stall_cycles);

    int errors = 0;
    errors += check("y", y, y_expected);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v1 4-tap FIR ends\n\n");
    return errors;
}
