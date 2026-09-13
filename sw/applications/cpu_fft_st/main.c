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

/* One radix-2 butterfly stage with a single, stationary twiddle w = (w_re,
   w_im), in place: butterfly i pairs sample i (`a`) with sample i + g (`b`),
   x = a + w*b lands on a's slot and y = a - w*b on b's -- the same words in
   the same order as strela_fft_st, whose fabric holds the twiddle as PE
   constants. The loop is x-trela's cpu_fft_nt one. No rescale anywhere: the
   twiddle is a pair of small integers, not a Q-format table. */
static void fft_st_cpu(int g, DATA_TYPE w_re, DATA_TYPE w_im,
                       volatile DATA_TYPE *re, volatile DATA_TYPE *im)
{
    for (int i = 0; i < g; i++) {
        DATA_TYPE b_re = re[i + g];
        DATA_TYPE b_im = im[i + g];

        DATA_TYPE t_re = w_re * b_re - w_im * b_im;
        DATA_TYPE t_im = w_im * b_re + w_re * b_im;

        DATA_TYPE a_re = re[i];
        DATA_TYPE a_im = im[i];

        re[i] = a_re + t_re;
        im[i] = a_im + t_im;
        re[i + g] = a_re - t_re;
        im[i + g] = a_im - t_im;
    }
}

int main(void) {
    PRINTF("\nStarting CPU stationary-twiddle FFT application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    fft_st_cpu(FFT_BFLIES, FFT_W_RE, FFT_W_IM, real, imag);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d complex points (%d butterflies, w = %d + %di)\n",
           DATA_SIZE, FFT_BFLIES, FFT_W_RE, FFT_W_IM);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < DATA_SIZE; i++) {
        if(real[i] == expected_real[i]) check++;
        if(imag[i] == expected_imag[i]) check++;
    }
    if(check != 2*DATA_SIZE) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU stationary-twiddle FFT application...\r\n");
    return (check != 2*DATA_SIZE);
}
