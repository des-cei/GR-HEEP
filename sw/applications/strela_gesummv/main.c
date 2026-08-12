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
 * PE indices of the nodes this app parameterises. They belong to the two solves
 * the kernel headers came from, so re-derive them after any re-solve; the
 * bitstreams themselves are the source of truth. Word 2 of a PE holds
 * delay_value in bits 31:16 and word 4 its constant, both at
 * PE_OFFSET[pe] + PE_POSITION[pe]*5 (sw/strela.h), so:
 *
 *   python3 -c "import re,sys;w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+', \
 *     re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]; \
 *     P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12];O=[0,1,2,3]*4; \
 *     print([(p, w[O[p]+P[p]*5+2]>>16, w[O[p]+P[p]*5+4]) for p in range(16)])" \
 *     matvec_kernel.h
 *
 * prints (pe, delay, const) for all 16 PEs.
 *
 * matvec (gesummv_1_hv): PE 0 is the multiply that scales x and broadcasts it
 * to the four lanes over the row-0 horizontal bus -- the DFG ships a
 * placeholder 15 and this app wants x itself, so the constant is 1. PEs 5, 6, 7
 * and 8 are the lane accumulators; their delay_value is the reduction length.
 *
 * scale_add (gesummv_2_hv, byte-identical to 2mm_2_hv and gemm_2_hv): four
 * `mul` PEs, one per input, and which one is which follows from the io_map
 * entry points and the bitstream's muxes:
 *
 *   input0 -> PE 3 (mul) -+-> PE 7 (add) -> output0 -> OSE3 -> vec_y[0..]
 *   input1 -> PE 5 (mul) -'
 *   input2 -> PE 0 (mul) -+-> PE 1 (add) -> output1 -> OSE1 -> vec_y[HALF..]
 *   input3 -> PE 2 (mul) -'
 *
 * and gen_descriptors.py streams vec_ax on input0/input2 and vec_bx on
 * input1/input3, so alpha lands on PEs 3 and 0 and beta on PEs 5 and 2.
 */
#define PE_X_SCALE      0
static const uint8_t acc_pes[4]   = {5, 6, 7, 8};
static const uint8_t alpha_pes[2] = {3, 0};   /* the vec_ax multiplies */
static const uint8_t beta_pes[2]  = {5, 2};   /* the vec_bx multiplies */

// Global definitions
mmio_region_t strela;

static int check(const char *name, volatile int32_t *got, volatile int32_t *expected) {
    int errors = 0;
    for (int i = 0; i < GESUMMV_M_PAD; i++) {
        if (got[i] != expected[i])
            errors++;
    }
    if (errors)
        PRINTF("%s: %d/%d wrong\n", name, errors, GESUMMV_M_PAD);
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

    // Kernel config -- both bitstreams are patched in RAM before the run, and
    // STRELA loads each one with its own TR_CONF out of these arrays.
    //
    // One copy of the matrix-vector bitstream serves both products: they reduce
    // over the same N and want the same constant, so A@x and B@x are passes of
    // one configuration rather than two phases (unlike strela_2mm, whose two
    // matmuls reduce over different lengths and therefore need one copy each).
    set_pe_const(matvec_kernel, PE_X_SCALE, 1);
    for (int i = 0; i < 4; i++)
        set_pe_delay_value(matvec_kernel, acc_pes[i], (uint32_t) GESUMMV_N);

    // The scale-and-add kernel applies the two scalars, which is why the
    // products above run unscaled.
    for (int i = 0; i < 2; i++) {
        set_pe_const(scale_add_kernel, alpha_pes[i], (uint32_t) GESUMMV_ALPHA);
        set_pe_const(scale_add_kernel, beta_pes[i],  (uint32_t) GESUMMV_BETA);
    }

    // STRELA
    strela = mmio_region_from_addr(STRELA_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela, (ptrdiff_t) STRELA_CTRL_REG_OFFSET, 1 << STRELA_CTRL_CLR_BIT);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_MODE_REG_OFFSET, 1 << STRELA_MODE_INTR_EN_BIT | 1 << STRELA_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. Both kernels live in these same eight
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
    // are done, i.e. after the second kernel; a fence parks an engine in
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

    // Check both products as well as the result: in a chained run a wrong final
    // value is much easier to place when the intermediates are checked too.
    int errors = check("A@x", vec_ax, vec_ax_expected)
               + check("B@x", vec_bx, vec_bx_expected)
               + check("y",   vec_y,  vec_y_expected);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 gesummv ends\n\n");
    return errors;
}
