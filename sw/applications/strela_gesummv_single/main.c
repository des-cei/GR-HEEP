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
#include "strela_regs.h"
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
 * PE indices of the nodes this app parameterises. They belong to the solve that
 * gesummv_hv_kernel.h came from, so re-derive them after any re-solve; the
 * bitstream itself is the source of truth. Word 2 of a PE holds delay_value in
 * bits 31:16 and word 4 holds its constant, both at
 * PE_OFFSET[pe] + PE_POSITION[pe]*5 (sw/strela.h), so:
 *
 *   python3 -c "import re;w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+', \
 *     re.sub(r'//.*','',open('gesummv_hv_kernel.h').read().split('{',1)[1]))]; \
 *     P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12];O=[0,1,2,3]*4; \
 *     print([(p, w[O[p]+P[p]*5+2]>>16, w[O[p]+P[p]*5+4]) for p in range(16)])"
 *
 * prints (pe, delay, const) for all 16 PEs: the four with a non-zero delay are
 * add0..add3, and the two with a non-zero constant are mul8 (alpha) and mul9
 * (beta). For this bitstream that is delay on PEs 0/2/9/14 and constants 2 on
 * PE 5 and 3 on PE 7.
 */
#define PE_ALPHA        5
#define PE_BETA         7
static const uint8_t acc_pes[4] = {0, 2, 9, 14};

// Global definitions
mmio_region_t strela;

static int check(const char *name, volatile int32_t *got, volatile int32_t *expected) {
    int errors = 0;
    for (int i = 0; i < GESUMMV_M; i++) {
        if (got[i] != expected[i])
            errors++;
    }
    if (errors)
        PRINTF("%s: %d/%d wrong\n", name, errors, GESUMMV_M);
    return errors;
}

int main(void) {
    PRINTF("\nSTRELA v2 gesummv starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Parameterise the bitstream in RAM before TR_CONF loads it: the two
    // scalars, and the reduction length on each accumulator PE. The bitstream
    // was solved for a 3-column problem; GESUMMV_N is what actually runs.
    set_pe_const(gesummv_hv_kernel, PE_ALPHA, (uint32_t) GESUMMV_ALPHA);
    set_pe_const(gesummv_hv_kernel, PE_BETA,  (uint32_t) GESUMMV_BETA);
    for (int i = 0; i < 4; i++)
        set_pe_delay_value(gesummv_hv_kernel, acc_pes[i], (uint32_t) GESUMMV_N);

    // STRELA
    strela = mmio_region_from_addr(STRELA_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela, (ptrdiff_t) STRELA_CTRL_REG_OFFSET, 1 << STRELA_CTRL_CLR_BIT);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_MODE_REG_OFFSET, 1 << STRELA_MODE_INTR_EN_BIT | 1 << STRELA_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors
    mmio_region_write32(strela, (ptrdiff_t) STRELA_ISE_0_TAB_ADDR_REG_OFFSET, (uint32_t) ise_0_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_ISE_1_TAB_ADDR_REG_OFFSET, (uint32_t) ise_1_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_ISE_2_TAB_ADDR_REG_OFFSET, (uint32_t) ise_2_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_ISE_3_TAB_ADDR_REG_OFFSET, (uint32_t) ise_3_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_OSE_0_TAB_ADDR_REG_OFFSET, (uint32_t) ose_0_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_OSE_1_TAB_ADDR_REG_OFFSET, (uint32_t) ose_1_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_OSE_2_TAB_ADDR_REG_OFFSET, (uint32_t) ose_2_table);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_OSE_3_TAB_ADDR_REG_OFFSET, (uint32_t) ose_3_table);

    // Start STRELA execution
    mmio_region_write32(strela, (ptrdiff_t) STRELA_CTRL_REG_OFFSET, 1 << STRELA_CTRL_START_BIT);

    // Wait until STRELA finishes
    wait_for_interrupt();

    // Disable performance counters
    mmio_region_write32(strela, (ptrdiff_t) STRELA_MODE_REG_OFFSET, 0);

    // Read performance counters
    uint32_t total_cycles = mmio_region_read32(strela, (ptrdiff_t) STRELA_PERF_CTR_TOTAL_CYCLES_REG_OFFSET);
    uint32_t conf_cycles = mmio_region_read32(strela, (ptrdiff_t) STRELA_PERF_CTR_CONF_CYCLES_REG_OFFSET);
    uint32_t tab_cycles = mmio_region_read32(strela, (ptrdiff_t) STRELA_PERF_CTR_TAB_CYCLES_REG_OFFSET);
    uint32_t stall_cycles = mmio_region_read32(strela, (ptrdiff_t) STRELA_PERF_CTR_STALL_CYCLES_REG_OFFSET);

    PRINTF("TOT: %u\n", total_cycles);
    PRINTF("CFG: %u\n", conf_cycles);
    PRINTF("TAB: %u\n", tab_cycles);
    PRINTF("STL: %u\n", stall_cycles);

    int errors = check("y", vec_y, vec_y_expected);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 gesummv ends\n\n");
    return errors;
}
