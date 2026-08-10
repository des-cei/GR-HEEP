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

// Scratch buffer, not part of the generated dataset
static DATA_TYPE B[N];

int main(void) {
    PRINTF("\nStarting CPU JACOBI-1D application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    jacobi1d_cpu(TSTEPS, N, A, B);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: TSTEPS=%d, N=%d\n", TSTEPS, N);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < N; i++) {
        if(A[i] == expected_A[i]) check++;
    }
    if(check != N) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU JACOBI-1D application...\r\n");
    return (check != N);
}
