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
    PRINTF("\nSTRELA v2 2mm starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Kernel config -- the three bitstreams are patched before the run: STRELA
    // loads each one with its own TR_CONF, out of these arrays in RAM.
    //
    // The two matmul phases are the same bitstream (2mm_1_hv, byte-identical
    // to mm_hv) held twice, because their accumulator delay -- the reduction
    // length -- differs: NK for matAB = matA*matB, NJ for matABC = matAB*matC.
    // The eight accumulator PEs are a property of this solve; re-derive them
    // after a re-solve by decoding the kernel header (word 2 bits 31:16 at
    // PE_OFFSET[pe] + PE_POSITION[pe]*5 holds delay_value).
    set_pe_delay_value(mm_ab_kernel, 4, NK);
    set_pe_delay_value(mm_ab_kernel, 5, NK);
    set_pe_delay_value(mm_ab_kernel, 6, NK);
    set_pe_delay_value(mm_ab_kernel, 7, NK);

    set_pe_delay_value(mm_ab_kernel, 12, NK);
    set_pe_delay_value(mm_ab_kernel, 13, NK);
    set_pe_delay_value(mm_ab_kernel, 14, NK);
    set_pe_delay_value(mm_ab_kernel, 15, NK);

    set_pe_delay_value(mm_abc_kernel, 4, NJ);
    set_pe_delay_value(mm_abc_kernel, 5, NJ);
    set_pe_delay_value(mm_abc_kernel, 6, NJ);
    set_pe_delay_value(mm_abc_kernel, 7, NJ);

    set_pe_delay_value(mm_abc_kernel, 12, NJ);
    set_pe_delay_value(mm_abc_kernel, 13, NJ);
    set_pe_delay_value(mm_abc_kernel, 14, NJ);
    set_pe_delay_value(mm_abc_kernel, 15, NJ);

    // 2mm_2_hv: the four scaling constants, which the DFG ships as a
    // placeholder 3. Which PE scales which stream is a property of this solve,
    // read out of the bitstream and the io_map:
    //
    //   input0 -> PE 3 (mul) -+-> PE 7  (add) -> output0 -> OSE3 -> matD[0..]
    //   input1 -> PE 5 (mul) -'
    //   input2 -> PE 0 (mul) -+-> PE 1  (add) -> output1 -> OSE1 -> matD[N/2..]
    //   input3 -> PE 2 (mul) -'
    //
    // and gen_descriptors.py streams matABC on input0/input2 and matDin on
    // input1/input3. Re-derive after a re-solve: decode the kernel header with
    // elastic-cgra's bitstream/CGRA.py (PE.to_dict() gives alu_op and the
    // input muxes, so the mul PEs and what feeds them are explicit) and read
    // the entry point of each input off 2mm_2_hv_io_map.json.
    set_pe_const(scale_add_kernel, 3, (uint32_t) alpha);   // matABC, lane 0
    set_pe_const(scale_add_kernel, 0, (uint32_t) alpha);   // matABC, lane 1
    set_pe_const(scale_add_kernel, 5, (uint32_t) beta);    // matDin, lane 0
    set_pe_const(scale_add_kernel, 2, (uint32_t) beta);    // matDin, lane 1

    // STRELA
    strela_v2 = mmio_region_from_addr(STRELA_V2_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_CLR_BIT);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 1 << STRELA_V2_MODE_INTR_EN_BIT | 1 << STRELA_V2_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. All three kernels live in these same eight
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
    // are done, i.e. after the third kernel; a fence parks an engine in
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

    // Check every stage: in a chained run a wrong final result is much easier
    // to place when the intermediates are checked too.
    int errors_ab = 0, errors_abc = 0, errors_d = 0;

    for(int x = 0; x < NI * NJ; x++) {
        if(matAB_expected[x] != matAB[x])
            errors_ab++;
    }

    for(int x = 0; x < NI * NL; x++) {
        if(matABC_expected[x] != matABC[x])
            errors_abc++;
        if(matD_expected[x] != matD[x])
            errors_d++;
    }

    int errors = errors_ab + errors_abc + errors_d;

    if (errors) {
        PRINTF("FAIL!! With %d errors (matAB %d, matABC %d, matD %d)\n",
               errors, errors_ab, errors_abc, errors_d);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 2mm ends\n\n");
    return errors;
}
