#include <stdio.h>
#include <stdint.h>
#include "x-heep.h"
#include "strela_fc.h"

/* By default, printfs are activated for FPGA and disabled for simulation. */
#define PRINTF_IN_FPGA  1
#define PRINTF_IN_SIM   0

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

// Filter: all ones → each output[i] = sum(input) = 136
// Layout: [rows_4 groups][4 ISEs][M] = [4][4][16]
static const int32_t filter_data[N * M] = {
    [0 ... N * M - 1] = 1
};

static int32_t output_data[N];

int main(void) {
    PRINTF("\nStarting STRELA v2 fully connected app...\n");

    strela_fully_connected(N, M,
                           /*input_offset=*/  0,
                           /*filter_offset=*/ 0,
                           /*output_offset=*/ 0,
                           input_data,
                           filter_data,
                           output_data);

    // Expected: output_data[i] = 1+2+...+16 = 136 for all i
    int pass = 1;
    for (int i = 0; i < N; i++) {
        PRINTF("output[%d] = %d\n", i, (int)output_data[i]);
        if (output_data[i] != 136) pass = 0;
    }
    PRINTF(pass ? "PASS\n" : "FAIL\n");

    PRINTF("Exiting STRELA v2 fully connected app...\n\n");
    return 0;
}