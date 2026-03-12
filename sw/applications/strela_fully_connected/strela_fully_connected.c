#include <stdint.h>
#include "hart.h"
#include "mmio.h"
#include "gr_heep.h"
#include "strela.h"
#include "strela_regs.h"

// Kernels
#include "strela_fully_connected_0_kernel.h"
#include "strela_fully_connected_1_0_kernel.h"
#include "strela_fully_connected_1_1_kernel.h"

#define ISE_0_MAX 64
#define ISE_1_MAX 64
#define ISE_2_MAX 64
#define ISE_3_MAX 64
#define OSE_0_MAX 64
#define OSE_1_MAX 64
#define OSE_2_MAX 64
#define OSE_3_MAX 64

#define ROUTER1_IDX 41

/* Phase 0 entries (static, never change) */
static memory_node_t ise_0_tab[ISE_0_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[0], 4u << 16 | CONFIG_SIZE},
    {TR_NORTH_ISE, (uintptr_t)0, 0},  // 1 -> change with addr and params
    {FENCE_SE, 0, 0}
    // completed in runtime
};

static memory_node_t ise_1_tab[ISE_1_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[21], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0}
};

static memory_node_t ise_2_tab[ISE_2_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[42], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0}
};

static memory_node_t ise_3_tab[ISE_3_MAX] = {
    {TR_CONF_ISE, (uintptr_t)&fc_0_kernel[63], 4u << 16 | CONFIG_SIZE},
    {FENCE_SE, 0, 0}
};

static memory_node_t ose_0_tab[OSE_0_MAX];
static memory_node_t ose_1_tab[OSE_1_MAX];
static memory_node_t ose_2_tab[OSE_2_MAX];
static memory_node_t ose_3_tab[OSE_3_MAX];


void strela_fully_connected(int N, int M,
                            const int32_t input_offset,
                            const int32_t filter_offset,
                            const int32_t output_offset,
                            const int32_t *bias_data,
                            const int32_t *input_data,
                            const int32_t *filter_data,
                            int32_t *output_data) {

    int rows_4 = N / 4;
    int rest_4 = N % 4;
    uint32_t sz = (uint32_t)sizeof(int32_t);
    uint32_t ise_param = sz << 16 | (sz * (uint32_t)M);

    // Set input offset in bitstream 0
    set_pe_const(fc_0_kernel, 0, input_offset);

    // Select kernel for phase 1 based on bias
    uint32_t *fc_1_kernel = (bias_data != NULL) ? fc_1_1_kernel : fc_1_0_kernel;

    // Set filter offset and delay value in the selected kernel
    set_pe_const(fc_1_kernel, 0, filter_offset);
    set_pe_const(fc_1_kernel, 1, filter_offset);
    set_pe_const(fc_1_kernel, 2, filter_offset);
    set_pe_const(fc_1_kernel, 3, filter_offset);

    set_pe_delay_value(fc_1_kernel,  8, (uint32_t)M);
    set_pe_delay_value(fc_1_kernel,  9, (uint32_t)M);
    set_pe_delay_value(fc_1_kernel, 10, (uint32_t)M);
    set_pe_delay_value(fc_1_kernel, 11, (uint32_t)M);

    // Create remainder kernel copy with modified Router 1
    uint32_t fc_1_rest_kernel[CONFIG_SIZE];
    if (rest_4 > 0) {
        for(int i = 0; i < CONFIG_SIZE; i++) {
            fc_1_rest_kernel[i] = fc_1_kernel[i];
        }
        static const uint32_t router1_vals[4] = {
            0, 0x00005000u, 0x0000D000u, 0x0001D000u
        };
        fc_1_rest_kernel[ROUTER1_IDX] = router1_vals[rest_4];
    }

    // Phase 0: fill input address and params
    ise_0_tab[1].address = (uintptr_t)input_data;
    ise_0_tab[1].params = ise_param;

    /* ----------------------------------------------------------------- */
    /* Build ISE tables                                                  */
    /* ----------------------------------------------------------------- */
    for (int i = 0; i < 4; i++) {
        memory_node_t *tab = (i == 0) ? ise_0_tab :
                             (i == 1) ? ise_1_tab :
                             (i == 2) ? ise_2_tab : ise_3_tab;
        int idx = (i == 0) ? 3 : 2;

        /* --- Phase 1 main --- */
        if (rows_4 > 0) {
            tab[idx++] = (memory_node_t){TR_CONF_ISE,
                (uintptr_t)&fc_1_kernel[i * 21], 4u << 16 | CONFIG_SIZE};

            if (bias_data != NULL) {
                uint32_t bop_w = 1u << 25 | (uint32_t)rows_4 << 14 | TR_MEM_W_ISE;
                uint32_t bop_e = 1u << 25 | (uint32_t)rows_4 << 14 | TR_MEM_E_ISE;
                uint32_t bpar  = (4u * sz) << 16 | ((uint32_t)rows_4 * 4u * sz);

                if (i == 0) {
                    tab[idx++] = (memory_node_t){bop_w, (uintptr_t)&bias_data[1], bpar};
                    tab[idx++] = (memory_node_t){bop_e, (uintptr_t)&bias_data[3], bpar};
                } else if (i == 3) {
                    tab[idx++] = (memory_node_t){bop_w, (uintptr_t)&bias_data[0], bpar};
                    tab[idx++] = (memory_node_t){bop_e, (uintptr_t)&bias_data[2], bpar};
                }
            }

            if (i == 2) {
                tab[idx++] = (memory_node_t){
                    (uint32_t)((uint32_t)rows_4 << 25 | 1u << 24 | (uint32_t)M << 14 | TR_MEM_W_ISE),
                    0, 4u << 16};
            }

            for (int row = 0; row < rows_4; row++) {
                tab[idx++] = (memory_node_t){TR_NORTH_ISE,
                    (uintptr_t)&filter_data[M * i + 4 * M * row], ise_param};
            }
        }

        /* --- FENCE_SE between passes --- */
        if (rest_4 > 0 && rows_4 > 0) {
            tab[idx++] = (memory_node_t){FENCE_SE, 0, 0};
        }

        /* --- Phase 1 remainder --- */
        if (rest_4 > 0) {
            tab[idx++] = (memory_node_t){TR_CONF_ISE,
                (uintptr_t)&fc_1_rest_kernel[i * 21], 4u << 16 | CONFIG_SIZE};

            if (bias_data != NULL) {
                uint32_t rbop_w = 1u << 25 | 1u << 14 | TR_MEM_W_ISE;
                uint32_t rbop_e = 1u << 25 | 1u << 14 | TR_MEM_E_ISE;
                uint32_t rbpar  = sz << 16 | sz;

                if (i == 0) {
                    tab[idx++] = (memory_node_t){rbop_w,
                        (uintptr_t)&bias_data[rows_4 * 4 + 1], rbpar};
                } else if (i == 3) {
                    tab[idx++] = (memory_node_t){rbop_w,
                        (uintptr_t)&bias_data[rows_4 * 4], rbpar};
                    if (rest_4 >= 3) {
                        tab[idx++] = (memory_node_t){rbop_e,
                            (uintptr_t)&bias_data[rows_4 * 4 + 2], rbpar};
                    }
                }
            }

            if (i == 2) {
                tab[idx++] = (memory_node_t){
                    (uint32_t)(1u << 25 | 1u << 24 | (uint32_t)M << 14 | TR_MEM_W_ISE),
                    0, 4u << 16};
            }

            if (i < rest_4) {
                tab[idx++] = (memory_node_t){TR_NORTH_ISE,
                    (uintptr_t)&filter_data[M * i + 4 * M * rows_4], ise_param};
            }
        }

        tab[idx] = (memory_node_t){IDLE_SE, 0, 0};
    }

    /* ----------------------------------------------------------------- */
    /* Build OSE tables                                                  */
    /* ----------------------------------------------------------------- */
    uint32_t ose_param_main = (4u * sz) << 16 | (4u * sz * (uint32_t)rows_4);
    uint32_t ose_param_rest = sz << 16 | sz;

    for (int i = 0; i < 4; i++) {
        memory_node_t *tab = (i == 0) ? ose_0_tab :
                             (i == 1) ? ose_1_tab :
                             (i == 2) ? ose_2_tab : ose_3_tab;
        int idx = 0;

        if (i == 1) {
            tab[idx++] = (memory_node_t){
                (uint32_t)((uint32_t)M << 14 | CFG_MEM_W_OSE), 0, 0};
        }

        tab[idx++] = (memory_node_t){FENCE_SE, 0, 0};

        if (rows_4 > 0) {
            tab[idx++] = (memory_node_t){TR_SOUTH_OSE,
                (uintptr_t)&output_data[i], ose_param_main};
        }

        if (rest_4 > 0 && rows_4 > 0) {
            tab[idx++] = (memory_node_t){FENCE_SE, 0, 0};
        }

        if (rest_4 > 0 && i < rest_4) {
            tab[idx++] = (memory_node_t){TR_SOUTH_OSE,
                (uintptr_t)&output_data[rows_4 * 4 + i], ose_param_rest};
        }

        tab[idx] = (memory_node_t){IDLE_SE, 0, 0};
    }

    /* ----------------------------------------------------------------- */
    /* Configure STRELA and launch execution                             */
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
