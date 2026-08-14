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

/* One radix-2 DIT stage. The operands arrive deinterleaved -- butterfly i of
   block k reads a[i], b[i] and twiddle i % FFT_TWIDDLES -- so the loop nest
   walks blocks then butterflies and the twiddle index comes for free, no
   modulo. No rescale anywhere: the twiddles are QFFT_FRAC_BITS and `a` is
   emitted pre-scaled, matching strela_fft, whose datapath has no shifter. */
static void fft_stage_cpu(int blocks, int half,
                          volatile DATA_TYPE *a_re, volatile DATA_TYPE *a_im,
                          volatile DATA_TYPE *b_re, volatile DATA_TYPE *b_im,
                          volatile DATA_TYPE *w_re, volatile DATA_TYPE *w_im,
                          volatile DATA_TYPE *x_re, volatile DATA_TYPE *x_im,
                          volatile DATA_TYPE *y_re, volatile DATA_TYPE *y_im)
{
    for (int k = 0; k < blocks; k++) {
        for (int j = 0; j < half; j++) {
            int i = k * half + j;
            DATA_TYPE t_re = b_re[i] * w_re[j] - b_im[i] * w_im[j];
            DATA_TYPE t_im = b_re[i] * w_im[j] + b_im[i] * w_re[j];
            x_re[i] = a_re[i] + t_re;
            x_im[i] = a_im[i] + t_im;
            y_re[i] = a_re[i] - t_re;
            y_im[i] = a_im[i] - t_im;
        }
    }
}

int main(void) {
    PRINTF("\nStarting CPU FFT application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    fft_stage_cpu(FFT_BLOCKS, FFT_TWIDDLES,
                  a_re, a_im, b_re, b_im, w_re, w_im,
                  x_re, x_im, y_re, y_im);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d butterflies (%d-point FFT, block %d)\n",
           FFT_BFLIES, FFT_POINTS, FFT_BLOCK);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < FFT_BFLIES; i++) {
        if(x_re[i] == x_re_expected[i]) check++;
        if(x_im[i] == x_im_expected[i]) check++;
        if(y_re[i] == y_re_expected[i]) check++;
        if(y_im[i] == y_im_expected[i]) check++;
    }
    if(check != 4*FFT_BFLIES) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU FFT application...\r\n");
    return (check != 4*FFT_BFLIES);
}
