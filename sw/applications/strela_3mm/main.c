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

// Global definitions
mmio_region_t strela;

int main(void) {
    PRINTF("\nSTRELA v2 3mm starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Kernel config -- the three phases run the same bitstream (3mm_hv,
    // byte-identical to mm_hv) held three times in RAM, because the eight
    // accumulator PEs' delay is the reduction length and the three products
    // reduce over different dimensions. STRELA loads each copy with its own
    // TR_CONF, out of these arrays.
    //
    // The accumulator PEs are a property of this solve; re-derive them after a
    // re-solve by decoding the kernel header (word 2 bits 31:16 at
    // PE_OFFSET[pe] + PE_POSITION[pe]*5 holds delay_value).
    set_pe_delay_value(mm_e_kernel, 4, NK);       // matE = matA * matB
    set_pe_delay_value(mm_e_kernel, 5, NK);
    set_pe_delay_value(mm_e_kernel, 6, NK);
    set_pe_delay_value(mm_e_kernel, 7, NK);

    set_pe_delay_value(mm_e_kernel, 12, NK);
    set_pe_delay_value(mm_e_kernel, 13, NK);
    set_pe_delay_value(mm_e_kernel, 14, NK);
    set_pe_delay_value(mm_e_kernel, 15, NK);

    set_pe_delay_value(mm_f_kernel, 4, NM);       // matF = matC * matD
    set_pe_delay_value(mm_f_kernel, 5, NM);
    set_pe_delay_value(mm_f_kernel, 6, NM);
    set_pe_delay_value(mm_f_kernel, 7, NM);

    set_pe_delay_value(mm_f_kernel, 12, NM);
    set_pe_delay_value(mm_f_kernel, 13, NM);
    set_pe_delay_value(mm_f_kernel, 14, NM);
    set_pe_delay_value(mm_f_kernel, 15, NM);

    set_pe_delay_value(mm_g_kernel, 4, NJ);       // matG = matE * matF
    set_pe_delay_value(mm_g_kernel, 5, NJ);
    set_pe_delay_value(mm_g_kernel, 6, NJ);
    set_pe_delay_value(mm_g_kernel, 7, NJ);

    set_pe_delay_value(mm_g_kernel, 12, NJ);
    set_pe_delay_value(mm_g_kernel, 13, NJ);
    set_pe_delay_value(mm_g_kernel, 14, NJ);
    set_pe_delay_value(mm_g_kernel, 15, NJ);

    // STRELA
    strela = mmio_region_from_addr(STRELA_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela, (ptrdiff_t) STRELA_CTRL_REG_OFFSET, 1 << STRELA_CTRL_CLR_BIT);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_MODE_REG_OFFSET, 1 << STRELA_MODE_INTR_EN_BIT | 1 << STRELA_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. All three kernels live in these same eight
    // tables, separated by FENCE_SEs, so this is a single execution.
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

    // Wait until STRELA finishes. The interrupt fires when all eight engines
    // are done, i.e. after the third kernel; a fence parks an engine in
    // S_WAIT, which is not done, so it cannot fire in between.
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

    // Check every stage: in a chained run a wrong final result is much easier
    // to place when the intermediates are checked too. matF is checked over
    // all NJ_PAD rows -- the padding rows come from the zeroed rows of matC,
    // so they must be zero, and phase 2 never reads them back.
    int errors_e = 0, errors_f = 0, errors_g = 0;

    for(int x = 0; x < NI * NJ; x++) {
        if(matE_expected[x] != matE[x])
            errors_e++;
    }

    for(int x = 0; x < NJ_PAD * NL; x++) {
        if(matF_expected[x] != matF[x])
            errors_f++;
    }

    for(int x = 0; x < NI * NL; x++) {
        if(matG_expected[x] != matG[x])
            errors_g++;
    }

    int errors = errors_e + errors_f + errors_g;

    if (errors) {
        PRINTF("FAIL!! With %d errors (matE %d, matF %d, matG %d)\n",
               errors, errors_e, errors_f, errors_g);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 3mm ends\n\n");
    return errors;
}
