#include <stdio.h>
#include <stdlib.h>
#include "x-heep.h"
#include "csr.h"
#include "csr_registers.h"
#include "dataset.h"

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

/* Elementwise max(x, 0). The compare is strictly `> 0`, like the DFG's, so an
   input of exactly zero takes the false branch -- the same answer by the other
   path, and the stimulus plants a zero in every lane to exercise it. One flat
   loop: the four lanes exist in strela_relu because the fabric unrolls by four,
   and on the CPU they are just contiguous slices of the same array. */
static void relu_cpu(int n, volatile DATA_TYPE *x, volatile DATA_TYPE *y)
{
    for (int i = 0; i < n; i++)
        y[i] = x[i] > 0 ? x[i] : 0;
}

int main(void) {
    PRINTF("\nStarting CPU ReLU application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    relu_cpu(RELU_SAMPLES, x, y);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d samples (%d lanes x %d)\n",
           RELU_SAMPLES, RELU_LANES, RELU_PER_LANE);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < RELU_SAMPLES; i++) {
        if(y[i] == y_expected[i]) check++;
    }
    if(check != RELU_SAMPLES) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU ReLU application...\r\n");
    return (check != RELU_SAMPLES);
}
