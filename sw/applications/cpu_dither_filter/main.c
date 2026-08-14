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

/* 1-D error diffusion: threshold each pixel and carry its quantisation error
   into the next one. The `& 0xFF` is part of the kernel, not bookkeeping -- one
   pixel lives in the low byte of each word and the rest is a tag, so a dropped
   mask feeds ~10^9-sized values into the threshold and every output flips.

   `err` is a loop-carried dependence, which is the whole point of comparing
   this app against strela_dither_filter: the fabric closes the same feedback
   inside itself and is bound by that ring rather than by its streams. */
static void dither_cpu(int n, volatile DATA_TYPE *in, volatile DATA_TYPE *out)
{
    DATA_TYPE err = 0;

    for (int i = 0; i < n; i++) {
        DATA_TYPE acc = (in[i] & 0xFF) + err;
        DATA_TYPE pixel = (acc > DITHER_THRESHOLD) ? DITHER_LEVEL : 0;
        err = acc - pixel;
        out[i] = pixel;
    }
}

int main(void) {
    PRINTF("\nStarting CPU DITHER application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    dither_cpu(DITHER_PIXELS, input, output);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d pixels\n", DITHER_PIXELS);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < DITHER_PIXELS; i++) {
        if(output[i] == output_expected[i]) check++;
    }
    if(check != DITHER_PIXELS) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU DITHER application...\r\n");
    return (check != DITHER_PIXELS);
}
