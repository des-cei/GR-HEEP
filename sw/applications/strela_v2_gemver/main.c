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
 * PolyBench gemver as four chained phases in one STRELA execution:
 *
 *   phase 0  mat_a2  = A + u1*v1^T + u2*v2^T     update_kernel  (gemver_1_hv)
 *   phase 1  vec_tmp = beta*(A2^T @ y)           matvec_beta    (gemver_2_hv)
 *   phase 2  vec_x   = vec_tmp + z               add_kernel     (gemver_3_hv)
 *   phase 3  vec_w   = alpha*(A2 @ x)            matvec_alpha   (gemver_2_hv)
 *
 * Phases 1 and 3 run the same bitstream over the same square matrix, so they
 * reduce over the same N and would be passes of one configuration -- except
 * that the scalar each folds into its replayed vector is a *PE constant*, and a
 * constant lives in the bitstream just like a delay_value does. Hence the two
 * copies patched below: one configuration per constant. See gen_descriptors.py.
 *
 * PE indices of the nodes this app parameterises. They belong to the solves the
 * kernel headers came from, so re-derive them after any re-solve; the bitstreams
 * themselves are the source of truth. Word 2 of a PE holds delay_value in bits
 * 31:16 and word 4 its constant, both at PE_OFFSET[pe] + PE_POSITION[pe]*5
 * (sw/strela_v2.h), so:
 *
 *   python3 -c "import re,sys;w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+', \
 *     re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]; \
 *     P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12];O=[0,1,2,3]*4; \
 *     print([(p, w[O[p]+P[p]*5+2]>>16, w[O[p]+P[p]*5+4]) for p in range(16)])" \
 *     matvec_beta_kernel.h
 *
 * prints (pe, delay, const) for all 16 PEs. For the matrix-vector kernel
 * (gemver_2_hv, byte-identical to gesummv_1_hv) that is PE 0 holding the
 * constant -- the multiply that scales the replayed vector and broadcasts it to
 * the four lanes over the row-0 horizontal bus, shipped with a placeholder 15 --
 * and PEs 5, 6, 7 and 8 as the lane accumulators, whose delay_value is the
 * reduction length.
 *
 * update_kernel (gemver_1_hv) and add_kernel (gemver_3_hv) have neither
 * accumulators nor constants: every PE of both reads back delay 0, const 0, so
 * nothing in those two bitstreams is patched at all.
 */
#define PE_VEC_SCALE    0
static const uint8_t acc_pes[4] = {5, 6, 7, 8};

// Global definitions
mmio_region_t strela_v2;

static int check(const char *name, volatile int32_t *got,
                 volatile int32_t *expected, int count) {
    int errors = 0;
    for (int i = 0; i < count; i++) {
        if (got[i] != expected[i])
            errors++;
    }
    if (errors)
        PRINTF("%s: %d/%d wrong\n", name, errors, count);
    return errors;
}

int main(void) {
    PRINTF("\nSTRELA v2 gemver starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Kernel config -- every bitstream is patched in RAM before the run, and
    // STRELA loads each one with its own TR_CONF out of these arrays.
    //
    // The two matrix-vector copies differ only in the constant: beta scales y
    // for x, alpha scales x for w. Both reduce over N -- A is square -- so the
    // delay_value is the same in both, and it is the constant alone that forces
    // two loaded configurations instead of one.
    set_pe_const(matvec_beta_kernel,  PE_VEC_SCALE, (uint32_t) GEMVER_BETA);
    set_pe_const(matvec_alpha_kernel, PE_VEC_SCALE, (uint32_t) GEMVER_ALPHA);
    for (int i = 0; i < 4; i++) {
        set_pe_delay_value(matvec_beta_kernel,  acc_pes[i], (uint32_t) GEMVER_N_PAD);
        set_pe_delay_value(matvec_alpha_kernel, acc_pes[i], (uint32_t) GEMVER_N_PAD);
    }

    // STRELA
    strela_v2 = mmio_region_from_addr(STRELA_V2_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_CLR_BIT);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 1 << STRELA_V2_MODE_INTR_EN_BIT | 1 << STRELA_V2_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. All four kernels live in these same eight
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
    // are done, i.e. after the last kernel; a fence parks an engine in S_WAIT,
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

    // Check every intermediate, not just w: this is a four-link chain, and a
    // wrong final vector is far easier to place when the three values feeding
    // it have been checked too.
    int errors = check("A2",  mat_a2,  mat_a2_expected, GEMVER_N_PAD * GEMVER_N_PAD)
               + check("tmp", vec_tmp, vec_tmp_expected, GEMVER_N_PAD)
               + check("x",   vec_x,   vec_x_expected,   GEMVER_N_PAD)
               + check("w",   vec_w,   vec_w_expected,   GEMVER_N_PAD);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 gemver ends\n\n");
    return errors;
}
