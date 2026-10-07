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

// The four tracker PEs decimate their output with an FU delay counter, and the
// two that track values are seeded with a sentinel. Neither the DFG nor the
// io_map says which PE each landed on -- these indices belong to THIS solve and
// must be re-derived after a re-solve, by decoding the kernel array:
//
//   python3 -c "import re,sys
//   w=[int(x,0) for x in re.findall(r'0x[0-9A-Fa-f]+',
//       re.sub(r'//.*','',open(sys.argv[1]).read().split('{',1)[1]))]
//   P=[3,7,11,15,2,6,10,14,1,5,9,13,0,4,8,12]; O=[0,1,2,3]*4
//   for pe in range(16):
//       b=O[pe]+P[pe]*5
//       print(pe,'delay',w[b+2]>>16,'init',w[b+3],'const',w[b+4])" find2min_kernel.h
//
// which reports delay=101 on PEs 5, 7, 10 and 12, of which 10 and 12 carry the
// 255 value seed (5 and 7 are the index trackers, correctly seeded to 0).
static const uint8_t delay_pes[4] = {5, 7, 10, 12};
static const uint8_t min_pes[2] = {10, 12};

// The seed is itself a firing that the FU delay counter counts, so reducing
// FIND2MIN_SAMPLES elements needs a delay of one more than that -- see the
// comment in mapper/applications/find2min/main.dot.
#define FIND2MIN_DELAY  (FIND2MIN_SAMPLES + 1)

// Global definitions
mmio_region_t strela_v2;

static const char *const result_names[4] = {"min1", "min2", "idx1", "idx2"};

static int check(volatile int32_t *got, volatile int32_t *expected) {
    int errors = 0;
    for (int i = 0; i < 4; i++) {
        if (got[i] != expected[i]) {
            PRINTF("%s: got %d, expected %d\n",
                   result_names[i], (int) got[i], (int) expected[i]);
            errors++;
        }
    }
    return errors;
}

int main(void) {
    PRINTF("\nSTRELA v2 find-two-minima starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Parameterise the bitstream in RAM before TR_CONF loads it. The bitstream
    // was solved for a 100-element group seeded with 255; FIND2MIN_SAMPLES is
    // what actually runs, and the sentinel is raised so no input value can be
    // mistaken for the seed and survive into the result.
    for (int i = 0; i < 4; i++)
        set_pe_delay_value(find2min_kernel, delay_pes[i], (uint32_t) FIND2MIN_DELAY);
    for (int i = 0; i < 2; i++)
        set_pe_initial_value(find2min_kernel, min_pes[i], (uint32_t) FIND2MIN_SENTINEL);

    // STRELA
    strela_v2 = mmio_region_from_addr(STRELA_V2_PERIPH_START_ADDRESS);

    // Configure STRELA mode
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_CTRL_REG_OFFSET, 1 << STRELA_V2_CTRL_CLR_BIT);
    mmio_region_write32(strela_v2, (ptrdiff_t) STRELA_V2_MODE_REG_OFFSET, 1 << STRELA_V2_MODE_INTR_EN_BIT | 1 << STRELA_V2_MODE_PERF_CTR_EN_BIT);

    // Configure STRELA descriptors
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

    // Wait until STRELA finishes
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

    int errors = check(result, result_expected);

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("min1 = %d at %d, min2 = %d at %d\n",
               (int) result[0], (int) result[2], (int) result[1], (int) result[3]);
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 find-two-minima ends\n\n");
    return errors;
}
