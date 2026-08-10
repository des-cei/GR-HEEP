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
    PRINTF("\nStarting CPU MVT application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    mvt_cpu(N, x1, x2, y_1, y_2, A);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d\n", N);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < N; i++) {
        if(x1[i] == expected_x1[i]) check++;
    }
    for(int i = 0; i < N; i++) {
        if(x2[i] == expected_x2[i]) check++;
    }
    if(check != 2*N) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU MVT application...\r\n");
    return (check != 2*N);
}
