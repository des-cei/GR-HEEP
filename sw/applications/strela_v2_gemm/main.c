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

// Global definitions
mmio_region_t strela_v2;

int main(void) {
    PRINTF("\nSTRELA v2 GEMM starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Kernel config -- both bitstreams are patched before the run: STRELA
    // loads each one with its own TR_CONF, out of these arrays in RAM.
    //
    // gemm_1_hv: the eight accumulator PEs, whose delay is the reduction
    // length. Same solve (and same bitstream) as strela_v2_mm.
    set_pe_delay_value(gemm_1_hv_kernel, 4, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 5, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 6, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 7, NK);

    set_pe_delay_value(gemm_1_hv_kernel, 12, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 13, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 14, NK);
    set_pe_delay_value(gemm_1_hv_kernel, 15, NK);

    // gemm_2_hv: the four scaling constants, which the DFG ships as a
    // placeholder 3. Which PE scales which stream is a property of this solve,
    // read out of the bitstream and the io_map:
    //
    //   input0 -> PE 3 (mul) -+-> PE 7  (add) -> output0 -> OSE3 -> matD[0..]
    //   input1 -> PE 5 (mul) -'
    //   input2 -> PE 0 (mul) -+-> PE 1  (add) -> output1 -> OSE1 -> matD[N/2..]
    //   input3 -> PE 2 (mul) -'
    //
    // and gen_descriptors.py streams matAB on input0/input2 and matC on
    // input1/input3. Re-derive after a re-solve: decode the kernel header with
    // elastic-cgra's bitstream/CGRA.py (PE.to_dict() gives alu_op and the
    // input muxes, so the mul PEs and what feeds them are explicit) and read
    // the entry point of each input off gemm_2_hv_io_map.json.
    set_pe_const(gemm_2_hv_kernel, 3, (uint32_t) alpha);   // matAB, lane 0
    set_pe_const(gemm_2_hv_kernel, 0, (uint32_t) alpha);   // matAB, lane 1
    set_pe_const(gemm_2_hv_kernel, 5, (uint32_t) beta);    // matC,  lane 0
    set_pe_const(gemm_2_hv_kernel, 2, (uint32_t) beta);    // matC,  lane 1

    // STRELA
    strela_v2 = mmio_region_from_addr(STRELA_V2_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_CLR_BIT);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 1 << STRELA_V2_MODE_INTR_EN_BIT | 1 << STRELA_V2_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. Both kernels live in these same eight
    // tables, separated by a FENCE_SE, so this is a single execution.
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
    // are done, i.e. after the second kernel; a fence parks an engine in
    // S_WAIT, which is not done, so it cannot fire in between.
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

    int errors = 0;

    for(int x = 0; x < NI * NJ; x++) {
        if(matD_expected[x] != matD[x])
            errors++;
            // printf("Error: exp %ld, obt: %ld\n", matD_expected[x], matD[x]);
    }

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 GEMM ends\n\n");
    return errors;
}
