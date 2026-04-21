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
#include "soc_ctrl.h"
#include "bypass.h"

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

#define DATA_SIZE 100
#define OFFSET_8 3
#define OFFSET_16 1

// Global definitions
mmio_region_t strela;

int8_t input_8[DATA_SIZE+4] __attribute__((section(".xheep_data_interleaved")));
int16_t input_16[DATA_SIZE+2] __attribute__((section(".xheep_data_interleaved")));
int32_t input_32[DATA_SIZE] __attribute__((section(".xheep_data_interleaved")));
int8_t output_8[DATA_SIZE+4] __attribute__((section(".xheep_data_interleaved")));
int16_t output_16[DATA_SIZE+2] __attribute__((section(".xheep_data_interleaved")));
int32_t output_32[DATA_SIZE] __attribute__((section(".xheep_data_interleaved")));

memory_node_t ise_0_table[] = {
    {TR_CONF_ISE, (uintptr_t)&bypass[0], 4 << 16 | CONFIG_SIZE},
    {TR_NORTH_8_ISE, (uintptr_t)&input_8[OFFSET_8], sizeof(int8_t) << 16 | sizeof(int8_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ise_1_table[] = {
    {TR_CONF_ISE, (uintptr_t)&bypass[21], 4 << 16 | CONFIG_SIZE},
    {TR_NORTH_16_ISE, (uintptr_t)&input_16[OFFSET_16], sizeof(int16_t) << 16 | sizeof(int16_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ise_2_table[] = {
    {TR_CONF_ISE, (uintptr_t)&bypass[42], 4 << 16 | CONFIG_SIZE},
    {TR_NORTH_32_ISE, (uintptr_t)&input_32[0], sizeof(int32_t) << 16 | sizeof(int32_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ise_3_table[] = {
    {TR_CONF_ISE, (uintptr_t)&bypass[63], 4 << 16 | CONFIG_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ose_0_table[] = {
    {TR_SOUTH_8_OSE, (uintptr_t)&output_8[OFFSET_8], sizeof(int8_t) << 16 | sizeof(int8_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ose_1_table[] = {
    {TR_SOUTH_16_OSE, (uintptr_t)&output_16[0], sizeof(int16_t) << 16 | sizeof(int16_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ose_2_table[] = {
    {TR_SOUTH_32_OSE, (uintptr_t)&output_32[0], sizeof(int32_t) << 16 | sizeof(int32_t) * DATA_SIZE},
    {IDLE_SE, 0, 0}
};

memory_node_t ose_3_table[] = {
    {IDLE_SE, 0, 0}
};

int main(void) {
    PRINTF("\nSTRELA v2 test starts\n");
    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    for(int32_t i = 0; i < DATA_SIZE; i++) {
        int32_t aux = i % 2 == 0 ? i : -i;
        input_8[i] = (int8_t) aux;
        input_16[i] = (int16_t) aux;
        input_32[i] = aux;
    }

    input_8[DATA_SIZE] = 100;
    input_8[DATA_SIZE+1] = -101;
    input_8[DATA_SIZE+2] = 102;
    input_8[DATA_SIZE+3] = -103;

    input_16[DATA_SIZE] = 100;
    input_16[DATA_SIZE+1] = -101;

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

    int errors = 0;

    for(int x = 0; x < DATA_SIZE; x++) {
        if(output_8[OFFSET_8+x] != input_8[x+OFFSET_8])
            errors++;
        if(output_16[x] != input_16[x+OFFSET_16]) {
            errors++;
            // if (x < 5) PRINTF("in: 0x%08x, out: 0x%08x\n", input_16[x+OFFSET_16], output_16[x]);
        }
            
        if(output_32[x] != input_32[x])
            errors++;
    }

    if (errors) {
        PRINTF("FAIL!! With %d errors\n", errors);
    }
    else {
        PRINTF("SUCCESS!\n");
    }

    PRINTF("STRELA v2 test ends\n\n");
    return errors;
}