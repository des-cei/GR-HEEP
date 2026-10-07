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

/* The two smallest values and their indices.

   This is deliberately *not* the usual `if (v < min1) {...} else if (v < min2)
   {...}` scan. strela_v2_find2min's fabric runs three coupled trackers, each
   reading its own previous output, so the min2 tracker sees min1 as it was
   before this element updated it and only accepts the demoted candidate when
   `min2 > cand` strictly. On duplicates the two formulations disagree on idx2
   (x = [5, 5, 3] gives 1 here, 0 for the sequential scan), so the baseline
   follows the accelerator's dataflow to stay comparable with it. The trackers
   are seeded with FIND2MIN_SENTINEL, which must exceed every element -- the
   seed is a real token in the fabric and would otherwise survive into the
   answer. */
static void find2min_cpu(int n, volatile DATA_TYPE *x, volatile DATA_TYPE *result)
{
    DATA_TYPE min1 = FIND2MIN_SENTINEL, idx1 = 0;
    DATA_TYPE min2 = FIND2MIN_SENTINEL, idx2 = 0;

    for (int i = 0; i < n; i++) {
        DATA_TYPE v = x[i];
        int cond0 = min1 > v;
        /* min1 demotes into the min2 candidate exactly when it is displaced. */
        DATA_TYPE cand     = cond0 ? min1 : v;
        DATA_TYPE cand_idx = cond0 ? idx1 : i;
        DATA_TYPE new_min1 = cond0 ? v : min1;
        DATA_TYPE new_idx1 = cond0 ? i : idx1;
        int cond1 = min2 > cand;
        min2 = cond1 ? cand : min2;
        idx2 = cond1 ? cand_idx : idx2;
        min1 = new_min1;
        idx1 = new_idx1;
    }

    result[0] = min1;
    result[1] = min2;
    result[2] = idx1;
    result[3] = idx2;
}

int main(void) {
    PRINTF("\nStarting CPU FIND2MIN application...\r\n");

    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    CSR_WRITE(CSR_REG_MCYCLE, 0);
    find2min_cpu(FIND2MIN_SAMPLES, x, result);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);

    PRINTF("Data size: %d samples\n", FIND2MIN_SAMPLES);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(int i = 0; i < FIND2MIN_RESULTS; i++) {
        if(result[i] == result_expected[i]) check++;
    }
    if(check != FIND2MIN_RESULTS) PRINTF("FAIL!!!\r\n");
    else PRINTF("SUCCESS!\r\n");

    PRINTF("Finishing CPU FIND2MIN application...\r\n");
    return (check != FIND2MIN_RESULTS);
}
