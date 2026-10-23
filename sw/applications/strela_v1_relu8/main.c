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
#include "relu_kernel.h"
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

int main(void) {
    PRINTF("\nSTRELA v1 int%d ReLU starts\n", RELU_WIDTH);
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // elastic-cgra's single-lane 4x4 relu (cmp > 0 -> select against the
    // constant 0). The bitstream is width-agnostic: the ISE sign-extends each
    // 8-bit sample to the fabric's 32 bits and the OSE writes back the low byte
    // of each result, so one bus word carries four elements each way.

    // STRELA v1
    strela_v1 = mmio_region_from_addr(STRELA_V1_PERIPH_START_ADDRESS);

    // Configure STRELA mode. CLR also clears every engine parameter, so the
    // engines the kernel does not use stay at size 0 and finish immediately,
    // and sets every element width back to 32 bits.
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_BIT);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_MODE_REG_OFFSET, 1 << STRELA_V1_MODE_INTR_EN_BIT | 1 << STRELA_V1_MODE_PERF_CTR_EN_BIT);

    // Configuration, then one stream in and one stream out at the data's width
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) relu_kernel);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_ISE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, RELU_KERNEL_IN_INPUT0_CH, STRELA_V1_RELU_SEW));
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_OSE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, RELU_KERNEL_OUT_OUTPUT0_CH, STRELA_V1_RELU_SEW));
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_ISE_ADDR_REG_OFFSET(RELU_KERNEL_IN_INPUT0_CH), (uint32_t) x);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_ISE_PARAM_REG_OFFSET(RELU_KERNEL_IN_INPUT0_CH), strela_v1_se_param(RELU_SAMPLES, sizeof(DATA_TYPE)));
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_OSE_ADDR_REG_OFFSET(RELU_KERNEL_OUT_OUTPUT0_CH), (uint32_t) y);
    mmio_region_write32(strela_v1, (ptrdiff_t) STRELA_V1_OSE_PARAM_REG_OFFSET(RELU_KERNEL_OUT_OUTPUT0_CH), strela_v1_se_param(RELU_SAMPLES, sizeof(DATA_TYPE)));

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

    PRINTF("Data size: %d samples, %d-bit\n", RELU_SAMPLES, RELU_WIDTH);
    PRINTF("TOT: %u\n", total_cycles);
    PRINTF("CFG: %u\n", conf_cycles);
    PRINTF("EXE: %u\n", exec_cycles);
    PRINTF("STL: %u\n", stall_cycles);

    int errors = 0;
    for (int i = 0; i < RELU_SAMPLES; i++) {
        if (y[i] != y_expected[i])
            errors++;
    }

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v1 int%d ReLU ends\n\n", RELU_WIDTH);
    return errors;
}
