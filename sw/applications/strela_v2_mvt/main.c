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
#include "strela_v2_regs.h"
#include "descriptors.h"
#include "soc_ctrl.h"

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

/*
 * PE indices of the nodes this app parameterises. They belong to the solve the
 * matvec header came from (regress/4x4-HV/mvt_1_hv), so re-derive them after
 * any re-solve; the bitstream itself is the source of truth. Word 2 of a PE
 * holds delay_value in bits 31:16 and word 4 its constant, both at
 * PE_OFFSET[pe] + PE_POSITION[pe]*5 (sw/strela_v2.h), so:
 *
 *   python3 -c "import re,sys;w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+', \
 *     re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]; \
 *     P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12];O=[0,1,2,3]*4; \
 *     print([(p, w[O[p]+P[p]*5+2]>>16, w[O[p]+P[p]*5+4]) for p in range(16)])" \
 *     matvec_kernel.h
 *
 * prints (pe, delay, const) for all 16 PEs; here PEs 4, 5, 6 and 7 are the lane
 * accumulators and no PE carries a constant. That last part is the difference
 * from gesummv_1_hv, whose vector reaches the lanes through a constant-multiply
 * PE that has to be patched to 1: this solve puts input4 straight on router 0's
 * horizontal bus, so y is broadcast unscaled and delay_value is the only thing
 * to set. The add kernel (mvt_2_hv) has neither accumulators nor constants, so
 * nothing in it is patched at all.
 */
static const uint8_t acc_pes[4] = {4, 5, 6, 7};

// Global definitions
mmio_region_t strela_v2;

static int check(const char *name, volatile int32_t *got,
                 volatile int32_t *expected) {
    int errors = 0;
    for (int i = 0; i < MVT_N_PAD; i++) {
        if (got[i] != expected[i])
            errors++;
    }
    if (errors)
        PRINTF("%s: %d/%d wrong\n", name, errors, MVT_N_PAD);
    return errors;
}

int main(void) {
    PRINTF("\nSTRELA v2 mvt starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Kernel config. A single copy of the matrix-vector bitstream serves both
    // products: A is square, so A@y_1 and A^T@y_2 reduce over the same N and
    // want the same delay_value -- the transposed one is passes of this same
    // configuration, differing only in the stride its descriptors carry.
    // (Contrast strela_v2_atax and strela_v2_bicg, which run the same pair of
    // products over a rectangular matrix and therefore need one patched copy
    // per reduction length.) The add kernel has nothing to patch.
    for (int i = 0; i < 4; i++)
        set_pe_delay_value(matvec_kernel, acc_pes[i], (uint32_t) MVT_N_PAD);

    // STRELA
    strela_v2 = mmio_region_from_addr(STRELA_V2_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_CLR_BIT);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 1 << STRELA_V2_MODE_INTR_EN_BIT | 1 << STRELA_V2_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. Both kernels live in these same eight
    // tables, separated by FENCE_SEs, so this is a single execution.
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_ISE_0_TAB_ADDR_REG_OFFSET, (uint32_t) ise_0_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_ISE_1_TAB_ADDR_REG_OFFSET, (uint32_t) ise_1_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_ISE_2_TAB_ADDR_REG_OFFSET, (uint32_t) ise_2_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_ISE_3_TAB_ADDR_REG_OFFSET, (uint32_t) ise_3_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_OSE_0_TAB_ADDR_REG_OFFSET, (uint32_t) ose_0_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_OSE_1_TAB_ADDR_REG_OFFSET, (uint32_t) ose_1_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_OSE_2_TAB_ADDR_REG_OFFSET, (uint32_t) ose_2_table);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_OSE_3_TAB_ADDR_REG_OFFSET, (uint32_t) ose_3_table);

    // Start STRELA execution
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_START_BIT);

    // Wait until STRELA finishes. The interrupt fires when all eight engines
    // are done, i.e. after the add phase; a fence parks an engine in S_WAIT,
    // which is not done, so it cannot fire in between.
    wait_for_interrupt();

    // Disable performance counters
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 0);

    // Read performance counters
    uint32_t total_cycles = mmio_region_read32(strela_v2, (ptrdiff_t) STRELA_V2_PERF_CTR_TOTAL_CYCLES_REG_OFFSET);
    uint32_t conf_cycles = mmio_region_read32(strela_v2, (ptrdiff_t) STRELA_V2_PERF_CTR_CONF_CYCLES_REG_OFFSET);
    uint32_t tab_cycles = mmio_region_read32(strela_v2, (ptrdiff_t) STRELA_V2_PERF_CTR_TAB_CYCLES_REG_OFFSET);
    uint32_t stall_cycles = mmio_region_read32(strela_v2, (ptrdiff_t) STRELA_V2_PERF_CTR_STALL_CYCLES_REG_OFFSET);

    PRINTF("TOT: %u\n", total_cycles);
    PRINTF("CFG: %u\n", conf_cycles);
    PRINTF("TAB: %u\n", tab_cycles);
    PRINTF("STL: %u\n", stall_cycles);

    // Check both products as well as both results: in a chained run a wrong
    // final value is much easier to place when the intermediates are checked
    // too -- and here it also tells the two directions apart.
    int errors = check("A@y_1",   vec_t1, vec_t1_expected)
               + check("A^T@y_2", vec_t2, vec_t2_expected)
               + check("x1",      vec_x1, vec_x1_expected)
               + check("x2",      vec_x2, vec_x2_expected);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 mvt ends\n\n");
    return errors;
}
