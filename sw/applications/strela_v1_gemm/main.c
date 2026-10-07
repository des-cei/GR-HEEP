#include <stdio.h>
#include "x-heep.h"
#include "mmio.h"
#include "gr_heep.h"
#include "strela.h"
#include "strela_v1_regs.h"
#include "mm_kernel.h"
#include "gemm_2_kernel.h"
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

// mm's three A rows, in the DFG's order: input<r> -> mul<r> -> add<r> ->
// output<r>, so row r of a block goes in on MM_IN_ROW[r] and its dot product
// comes out on MM_OUT_ROW[r].
static const uint32_t MM_IN_ROW[3] = {
    MM_KERNEL_IN_INPUT0_CH, MM_KERNEL_IN_INPUT1_CH, MM_KERNEL_IN_INPUT2_CH
};
static const uint32_t MM_OUT_ROW[3] = {
    MM_KERNEL_OUT_OUTPUT0_CH, MM_KERNEL_OUT_OUTPUT1_CH, MM_KERNEL_OUT_OUTPUT2_CH
};

static inline void strela_v1_write(uint32_t offset, uint32_t value) {
    mmio_region_write32(strela_v1, (ptrdiff_t) offset, value);
}

// Start one execution with the parameters already programmed, and poll until
// every memory node is done. EXEC_DONE is cleared by START itself, so there is
// no stale bit to clear first.
static void strela_v1_run(void) {
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_START_BIT);
    while (!(mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_STATUS_REG_OFFSET) & (1 << STRELA_V1_STATUS_EXEC_DONE_BIT)))
        ;
}

int main(void) {
    PRINTF("\nSTRELA v1 GEMM starts\n");

    // Kernel config. mm's three accumulators sit right under their
    // multipliers (PEs 5, 6, 7 in this solve) and reduce over NK.
    strela_v1_set_pe_delay_value(mm_kernel, 5, NK);
    strela_v1_set_pe_delay_value(mm_kernel, 6, NK);
    strela_v1_set_pe_delay_value(mm_kernel, 7, NK);

    // gemm_2's four scaling multipliers each sit directly under the north
    // input they scale, so PE number == channel here: alpha on the two matAB
    // streams (input0, input2), beta on the two matC streams (input1, input3).
    // The DFG ships them as placeholders 15 and 3.
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT0_CH, (uint32_t) alpha);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT2_CH, (uint32_t) alpha);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT1_CH, (uint32_t) beta);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT3_CH, (uint32_t) beta);

    // STRELA v1
    strela_v1 = mmio_region_from_addr(STRELA_V1_PERIPH_START_ADDRESS);

    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_BIT);
    strela_v1_write(STRELA_V1_MODE_REG_OFFSET, 1 << STRELA_V1_MODE_PERF_CTR_EN_BIT);

    // Phase 1: matAB = matA * matB, one run per 3-row x 1-column block. The
    // node sizes and strides are the same for every block and survive across
    // runs, as does the configuration (the fabric is configured by the first
    // START only), so a block costs its addresses and a START.
    strela_v1_write(STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) mm_kernel);
    strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(MM_KERNEL_IN_INPUT3_CH), strela_v1_imn_param(NK, NJ * sizeof(int32_t)));
    for (int r = 0; r < 3; r++) {
        strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(MM_IN_ROW[r]), strela_v1_imn_param(NK, sizeof(int32_t)));
        strela_v1_write(STRELA_V1_OMN_SIZE_REG_OFFSET(MM_OUT_ROW[r]), strela_v1_omn_size(1));
    }

    for (int i = 0; i < NI; i += 3) {
        for (int r = 0; r < 3; r++)
            strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(MM_IN_ROW[r]), (uint32_t) &matA[(i + r) * NK]);
        for (int j = 0; j < NJ; j++) {
            strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(MM_KERNEL_IN_INPUT3_CH), (uint32_t) &matB[j]);
            for (int r = 0; r < 3; r++)
                strela_v1_write(STRELA_V1_OMN_ADDR_REG_OFFSET(MM_OUT_ROW[r]), (uint32_t) &matAB[(i + r) * NJ + j]);
            strela_v1_run();
        }
    }

    // Phase 2: matD = alpha * matAB + beta * matC, two lanes over the two
    // halves of the flat arrays, in one run. CLR_CONF makes the next START
    // load gemm_2, and CLR_PARAM drops phase 1's node parameters: mm's output
    // nodes 1 and 2 are unused here and would otherwise wait for data forever.
    // It clears CONF_ADDR too, so that is written after.
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_CONF_BIT | 1 << STRELA_V1_CTRL_CLR_PARAM_BIT);
    strela_v1_write(STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) gemm_2_kernel);

    const uint32_t half_a = (NI * NJ + 1) / 2;
    const uint32_t half_b = NI * NJ - half_a;

    strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT0_CH), (uint32_t) &matAB[0]);
    strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT0_CH), strela_v1_imn_param(half_a, sizeof(int32_t)));
    strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT1_CH), (uint32_t) &matC[0]);
    strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT1_CH), strela_v1_imn_param(half_a, sizeof(int32_t)));
    strela_v1_write(STRELA_V1_OMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_OUT_OUTPUT0_CH), (uint32_t) &matD[0]);
    strela_v1_write(STRELA_V1_OMN_SIZE_REG_OFFSET(GEMM_2_KERNEL_OUT_OUTPUT0_CH), strela_v1_omn_size(half_a));

    strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT2_CH), (uint32_t) &matAB[half_a]);
    strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT2_CH), strela_v1_imn_param(half_b, sizeof(int32_t)));
    strela_v1_write(STRELA_V1_IMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT3_CH), (uint32_t) &matC[half_a]);
    strela_v1_write(STRELA_V1_IMN_PARAM_REG_OFFSET(GEMM_2_KERNEL_IN_INPUT3_CH), strela_v1_imn_param(half_b, sizeof(int32_t)));
    strela_v1_write(STRELA_V1_OMN_ADDR_REG_OFFSET(GEMM_2_KERNEL_OUT_OUTPUT1_CH), (uint32_t) &matD[half_a]);
    strela_v1_write(STRELA_V1_OMN_SIZE_REG_OFFSET(GEMM_2_KERNEL_OUT_OUTPUT1_CH), strela_v1_omn_size(half_b));

    strela_v1_run();

    // Disable performance counters
    strela_v1_write(STRELA_V1_MODE_REG_OFFSET, 0);

    // Read performance counters. TOT runs from the MODE write on, so it
    // includes the CPU's register programming between runs.
    uint32_t total_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_TOTAL_CYCLES_REG_OFFSET);
    uint32_t conf_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_CONF_CYCLES_REG_OFFSET);
    uint32_t exec_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_EXEC_CYCLES_REG_OFFSET);
    uint32_t stall_cycles = mmio_region_read32(strela_v1, (ptrdiff_t) STRELA_V1_PERF_CTR_STALL_CYCLES_REG_OFFSET);

    PRINTF("TOT: %u\n", total_cycles);
    PRINTF("CFG: %u\n", conf_cycles);
    PRINTF("EXE: %u\n", exec_cycles);
    PRINTF("STL: %u\n", stall_cycles);

    int errors = 0;
    int errors_ab = 0;

    for (int x = 0; x < NI * NJ; x++) {
        if (matAB_expected[x] != matAB[x])
            errors_ab++;
        if (matD_expected[x] != matD[x])
            errors++;
    }

    if (errors_ab)
        PRINTF("matAB: %d/%d wrong\n", errors_ab, NI * NJ);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v1 GEMM ends\n\n");
    return errors;
}
