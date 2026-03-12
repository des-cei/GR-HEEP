#include <stdio.h>
#include <stdint.h>
#include "x-heep.h"
#include "hart.h"
#include "csr.h"
#include "csr_registers.h"
#include "fast_intr_ctrl.h"
#include "fast_intr_ctrl_regs.h"
#include "strela_fully_connected.h"

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

#define N 16
#define M 16

// Input: values 1..N
static const int32_t input_data[N] = {
     1,  2,  3,  4,  5,  6,  7,  8,
     9, 10, 11, 12, 13, 14, 15, 16
};

// Filter: all ones
static const int32_t filter_data[N * M] = {
    [0 ... N * M - 1] = 1
};

// Bias: all twos (expected output with bias = 136 + 2 = 138)
static const int32_t bias_data[N] = {
    [0 ... N - 1] = 2
};

static int32_t output_data[N];

int main(void) {
    PRINTF("\nStarting STRELA v2 fully connected app...\n");

    // Core configurations ------------
    enable_all_fast_interrupts(true);

    // Enable interrupt on processor side
    // Enable global interrupt for machine-level interrupts
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);

    // Set mie.MEIE bit to one to enable machine-level fast interrupts
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    // Fully connected without bias
    strela_fully_connected(N, M,
                           /*input_offset=*/  0,
                           /*filter_offset=*/ 0,
                           /*output_offset=*/ 0,
                           /*bias_data=*/     NULL,
                           input_data,
                           filter_data,
                           output_data);

    int pass = 1;
    for (int i = 0; i < N; i++) {
        if (output_data[i] != 136) pass = 0;
    }
    PRINTF(pass ? "No-bias: SUCCESS\n" : "No-bias: FAIL\n");

    // Fully connected with bias
    strela_fully_connected(N, M,
                           /*input_offset=*/  0,
                           /*filter_offset=*/ 0,
                           /*output_offset=*/ 0,
                           /*bias_data=*/     bias_data,
                           input_data,
                           filter_data,
                           output_data);

    pass = 1;
    for (int i = 0; i < N; i++) {
        if (output_data[i] != 138) pass = 0;
    }
    PRINTF(pass ? "With-bias: SUCCESS\n" : "With-bias: FAIL\n");

    PRINTF("Exiting STRELA v2 fully connected app...\n\n");
    return 0;
}