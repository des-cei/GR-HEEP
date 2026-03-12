#include <stdint.h>
#include "hart.h"
#include "mmio.h"
#include "gr_heep.h"
#include "strela.h"
#include "strela_regs.h"

// Kernels
#include "strela_fully_connected_0_kernel.h"
#include "strela_fully_connected_1_0_kernel.h"

#define ISE_0_MAX 64
#define ISE_1_MAX 64
#define ISE_2_MAX 64
#define ISE_3_MAX 64
#define OSE_0_MAX 64
#define OSE_1_MAX 64
#define OSE_2_MAX 64
#define OSE_3_MAX 64

static memory_node_t ise_0_tab[ISE_0_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[0], 4u << 16 | CONFIG_SIZE},
    {TR_NORTH_ISE, (uintptr_t)0, 0},  // 1 -> change with addr and params
    {FENCE_SE, 0, 0},
    {TR_CONF_ISE, (uintptr_t)&fc_1_0_kernel[0], 4u << 16 | CONFIG_SIZE}
    // completed in runtime with filter rows
}; 

static memory_node_t ise_1_tab[ISE_1_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[21], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0},
    {TR_CONF_ISE, (uintptr_t)&fc_1_0_kernel[21], 4u << 16 | CONFIG_SIZE}
    // completed in runtime with filter rows
};

static memory_node_t ise_2_tab[ISE_2_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[42], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0},
    {TR_CONF_ISE, (uintptr_t)&fc_1_0_kernel[42], 4u << 16 | CONFIG_SIZE},
    {0, 0, 4u << 16} // 3 -> change with west mem params
    // completed in runtime with filter rows
};

static memory_node_t ise_3_tab[ISE_3_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[63], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0},
    {TR_CONF_ISE, (uintptr_t)&fc_1_0_kernel[63], 4u << 16 | CONFIG_SIZE}
    // completed in runtime with filter rows
};

static memory_node_t ose_0_tab[OSE_0_MAX] = {
    {FENCE_SE, 0, 0},
    {TR_SOUTH_OSE, 0, 0}, // 1 -> change with output params
    {IDLE_SE, 0, 0}
};

static memory_node_t ose_1_tab[OSE_1_MAX] = {
    {0, 0, 0}, // 0 -> change with west mem params
    {FENCE_SE, 0, 0},
    {TR_SOUTH_OSE, 0, 0}, // 2 -> change with output params
    {IDLE_SE, 0, 0}
};

static memory_node_t ose_2_tab[OSE_2_MAX] = {
    {FENCE_SE, 0, 0},
    {TR_SOUTH_OSE, 0, 0}, // 1 -> change with output params
    {IDLE_SE, 0, 0}
};

static memory_node_t ose_3_tab[OSE_3_MAX] = {
    {FENCE_SE, 0, 0},
    {TR_SOUTH_OSE, 0, 0}, // 1 -> change with output params
    {IDLE_SE, 0, 0}
};


void strela_fully_connected(int N, int M,
                            const int32_t input_offset,
                            const int32_t filter_offset,
                            const int32_t output_offset,
                            const int32_t *input_data,
                            const int32_t *filter_data,
                            int32_t *output_data) {
    
    
    int rows_4 = N / 4;
    uint32_t sz = (uint32_t)sizeof(int32_t);
    uint32_t ise_param = sz << 16 | (sz * (uint32_t)M);
    uint32_t ose_param = (4 * sz) << 16 | (4 * sz * (uint32_t)rows_4);

    // Set input offset in bitstream 0
    set_pe_const(fc_0_kernel, 0, input_offset);

    // Set filter offset in bitstream 1.0
    set_pe_const(fc_1_0_kernel, 0, filter_offset);
    set_pe_const(fc_1_0_kernel, 1, filter_offset);
    set_pe_const(fc_1_0_kernel, 2, filter_offset);
    set_pe_const(fc_1_0_kernel, 3, filter_offset);
    
    // Set delay value in bitstream 1.0
    set_pe_delay_value(fc_1_0_kernel,  8, (uint32_t) M);
    set_pe_delay_value(fc_1_0_kernel,  9, (uint32_t) M);
    set_pe_delay_value(fc_1_0_kernel, 10, (uint32_t) M);
    set_pe_delay_value(fc_1_0_kernel, 11, (uint32_t) M);

    // Modify ISE and OSE descriptors
    ise_0_tab[1].address = (uintptr_t)input_data;
    ise_0_tab[1].params = ise_param;
    ise_2_tab[3].opcode = (uint32_t)
        ((uint32_t)rows_4 << 25 | 1u << 24 | (uint32_t)M << 14 | TR_MEM_W_ISE);

    for (int i = 0; i < 4; i++) {
        memory_node_t *tab = (i == 0) ? ise_0_tab :
                             (i == 1) ? ise_1_tab :
                             (i == 2) ? ise_2_tab : ise_3_tab;

        int idx =   (i == 0) ? 4 :
                    (i == 1) ? 3 : 
                    (i == 2) ? 4 : 3;

        for (int row = 0; row < rows_4; row++) {
            // Every ISE streams its filter slice
            tab[idx++] = (memory_node_t){TR_NORTH_ISE, (uintptr_t)&filter_data[M * i + 4 * M * row], ise_param};
        }

        tab[idx] = (memory_node_t){IDLE_SE, 0, 0};
    }

    ose_0_tab[1].address = (uintptr_t)&output_data[0];
    ose_0_tab[1].params = ose_param;

    ose_1_tab[0].opcode = (uint32_t)((uint32_t)M << 14 | CFG_MEM_W_OSE);
    ose_1_tab[2].address = (uintptr_t)&output_data[1];
    ose_1_tab[2].params = ose_param;

    ose_2_tab[1].address = (uintptr_t)&output_data[2];
    ose_2_tab[1].params = ose_param;

    ose_3_tab[1].address = (uintptr_t)&output_data[3];
    ose_3_tab[1].params = ose_param;

    /* ----------------------------------------------------------------- */
    /* 4. Configure STRELA and launch execution.                         */
    /* ----------------------------------------------------------------- */
    mmio_region_t strela = mmio_region_from_addr(STRELA_PERIPH_START_ADDRESS);

    mmio_region_write32(strela, (ptrdiff_t)STRELA_CTRL_REG_OFFSET, 1u << STRELA_CTRL_CLR_BIT);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_MODE_REG_OFFSET, 1u << STRELA_MODE_INTR_EN_BIT);

    mmio_region_write32(strela, (ptrdiff_t)STRELA_ISE_0_TAB_ADDR_REG_OFFSET, (uint32_t)ise_0_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_ISE_1_TAB_ADDR_REG_OFFSET, (uint32_t)ise_1_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_ISE_2_TAB_ADDR_REG_OFFSET, (uint32_t)ise_2_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_ISE_3_TAB_ADDR_REG_OFFSET, (uint32_t)ise_3_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_OSE_0_TAB_ADDR_REG_OFFSET, (uint32_t)ose_0_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_OSE_1_TAB_ADDR_REG_OFFSET, (uint32_t)ose_1_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_OSE_2_TAB_ADDR_REG_OFFSET, (uint32_t)ose_2_tab);
    mmio_region_write32(strela, (ptrdiff_t)STRELA_OSE_3_TAB_ADDR_REG_OFFSET, (uint32_t)ose_3_tab);

    mmio_region_write32(strela, (ptrdiff_t)STRELA_CTRL_REG_OFFSET, 1u << STRELA_CTRL_START_BIT);

    wait_for_interrupt();
}