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

int main(void) {
    PRINTF("\nStarting CPU 3MM application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    threemm_cpu(NI, NJ, NK, NL, NM, A, B, C, D, E, F, G);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d, %d, %d, %d, %d\n", NI, NJ, NK, NL, NM);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < NI*NJ; i++) {
        if(E[i] == expected_E[i]) check++;
    }
    for(int i = 0; i < NJ*NL; i++) {
        if(F[i] == expected_F[i]) check++;
    }
    for(int i = 0; i < NI*NL; i++) {
        if(G[i] == expected_G[i]) check++;
    }
    if(check != NI*NJ + NJ*NL + NI*NL) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU 3MM application...\r\n");
    return (check != NI*NJ + NJ*NL + NI*NL);
}
