#include <stdio.h>
#include <stdlib.h>
#include "x-heep.h"
#include "csr.h"
#include "csr_registers.h"
#include "polybench_cpu.h"
#include "dataset.h"

/* By default, printfs are activated for FPGA and disabled for simulation. */
#define PRINTF_IN_FPGA 1
#define PRINTF_IN_SIM 0

#if TARGET_SIM && PRINTF_IN_SIM
#define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif TARGET_IS_FPGA && PRINTF_IN_FPGA
#define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
#define PRINTF(...)
#endif

int main(void)
{
    PRINTF("\nStarting CPU GeMM application...\r\n");

    uint32_t sw_time;
    int32_t sum;
    int i, j, k;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);

    gemm_cpu(NI, NJ, NK, &alpha, &beta, A, B, C);

    // for (i = 0; i < NI; i++)
    // {
    //     for (j = 0; j < NJ; j++)
    //     {
    //         sum = 0;
    //         for (k = 0; k < NK; k++)
    //         {
    //             sum += A[i * NK + k] * B[k * NJ + j];
    //         }
    //         C[i * NJ + j] = alpha * sum + beta * C[i * NJ + j];
    //     }
    // }
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d, %d, %d\n", NI, NJ, NK);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for (int i = 0; i < NI * NJ; i++)
    {
        if (C[i] == expected_result[i])
            check++;
    }
    if (check != NI * NJ)
        PRINTF("FAIL!!!\r\n");
    else
        PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU GeMM application...\r\n");
    return (check != NI * NJ);
}
