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

/* Transposed-form FIR with zero initial state, one output per input -- the same
   boundary condition the DFG's initial_valid preloads give strela_fir. The
   accumulation is exact int32 and the single arithmetic right shift happens at
   the end, never per tap. */
static void fir_cpu(int n, int taps, int shift,
                    const DATA_TYPE *h, volatile DATA_TYPE *x,
                    volatile DATA_TYPE *y)
{
    for (int i = 0; i < n; i++) {
        DATA_TYPE acc = 0;
        for (int k = 0; k < taps; k++) {
            if (i - k >= 0)
                acc += h[k] * x[i - k];
        }
        y[i] = acc >> shift;
    }
}

int main(void) {
    PRINTF("\nStarting CPU FIR application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    fir_cpu(FIR_SAMPLES, FIR_TAPS, FIR_SHIFT, h, x, y);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d samples, %d taps\n", FIR_SAMPLES, FIR_TAPS);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < FIR_SAMPLES; i++) {
        if(y[i] == y_expected[i]) check++;
    }
    if(check != FIR_SAMPLES) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU FIR application...\r\n");
    return (check != FIR_SAMPLES);
}
