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
 * PolyBench floyd-warshall as one STRELA execution: a single bitstream
 * (fw_kernel, from the committed fw_hv regression) swept over the matrix once
 * per pivot.
 *
 *   for k: for i: for j:
 *       path[i][j] = min(path[i][j], path[i][k] + path[k][j])
 *
 * The four lanes each relax one line, so a pivot costs N_PAD/4 fenced passes and
 * the run is N*N_PAD/4 of them -- every one behind a FENCE_SE, because k must
 * complete before k+1 and because a scratchpad replay must drain before the next
 * pass's TR_MEM_* reloads it. There is only ever one configuration: the pivot is
 * an *address*, not a bitstream field, so unlike strela_atax or strela_gemver
 * this app never reconfigures. See gen_descriptors.py.
 *
 * Nothing is patched at runtime. Every PE of fw_hv reads back delay_value 0 and
 * constant 0 -- there is no accumulator whose delay is a reduction length and no
 * constant-multiply PE -- so this app has no set_pe_* calls at all. Word 2 of a
 * PE holds delay_value in bits 31:16 and word 4 its constant, both at
 * PE_OFFSET[pe] + PE_POSITION[pe]*5 (sw/strela.h), so:
 *
 *   python3 -c "import re,sys;w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+', \
 *     re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]; \
 *     P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12];O=[0,1,2,3]*4; \
 *     print([(p, w[O[p]+P[p]*5+2]>>16, w[O[p]+P[p]*5+4]) for p in range(16)])" \
 *     fw_kernel.h
 *
 * prints (pe, delay, const) for all 16 PEs and is the check to re-run after a
 * re-solve.
 *
 * The two buffers ping-pong, one pivot each, so the answer is in path_a for even
 * N and path_b for odd N -- N is a build-time constant, so this is decided here
 * rather than tracked at runtime.
 */

// Global definitions
mmio_region_t strela;

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
    PRINTF("\nSTRELA v2 floyd-warshall starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // STRELA
    strela = mmio_region_from_addr(STRELA_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela, (ptrdiff_t) STRELA_CTRL_REG_OFFSET, 1 << STRELA_CTRL_CLR_BIT);
    mmio_region_write32(strela, (ptrdiff_t) STRELA_MODE_REG_OFFSET, 1 << STRELA_MODE_INTR_EN_BIT | 1 << STRELA_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors. Every pivot is in these same eight tables,
    // separated by FENCE_SEs, so the whole sweep is a single execution.
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
    // are done, i.e. after the last pivot; a fence parks an engine in S_WAIT,
    // which is not done, so it cannot fire in between.
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

    // Pivot k reads one buffer and writes the other, so after N of them the
    // answer is in path_a when N is even and path_b when it is odd.
    volatile int32_t *result = (FW_N % 2) ? path_b : path_a;

    int errors = check("path", result, path_expected, FW_N_PAD * FW_N);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 floyd-warshall ends\n\n");
    return errors;
}
