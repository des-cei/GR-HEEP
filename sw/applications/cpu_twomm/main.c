#include <stdio.h>
#include <stdlib.h>
#include "x-heep.h"
#include "csr.h"
#include "csr_registers.h"
#include "polybench_cpu.h"
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

// Scratch buffer for alpha*A*B, not part of the generated dataset
static DATA_TYPE tmp[NI*NJ];

int main(void) {
    PRINTF("\nStarting CPU 2MM application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    twomm_cpu(NI, NJ, NK, NL, &alpha, &beta, tmp, A, B, C, D);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d, %d, %d, %d\n", NI, NJ, NK, NL);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < NI*NL; i++) {
        if(D[i] == expected_D[i]) check++;
    }
    if(check != NI*NL) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU 2MM application...\r\n");
    return (check != NI*NL);
}
