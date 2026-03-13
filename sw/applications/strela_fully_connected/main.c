#include <stdio.h>
#include <stdint.h>
#include "x-heep.h"
#include "hart.h"
#include "csr.h"
#include "csr_registers.h"
#include "fast_intr_ctrl.h"
#include "fast_intr_ctrl_regs.h"
#include "strela_fully_connected.h"

/* By default, printfs are activated for FPGA and disabled for simulation. */
#define PRINTF_IN_FPGA  1
#define PRINTF_IN_SIM   1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif TARGET_IS_FPGA && PRINTF_IN_FPGA
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#define MAX_N 16
#define MAX_M 16

static int32_t input_data [MAX_M];
static int32_t filter_data[MAX_N * MAX_M];
static int32_t bias_data  [MAX_N];
static int32_t output_data[MAX_N];
static int32_t expected   [MAX_N];

/* Reference fully-connected: out[i] = sum_j((in[j]+in_off)*(w[i][j]+w_off)) + bias[i] */
static void fc_ref(int N, int M,
                   int32_t in_off, int32_t w_off,
                   const int32_t *bias,
                   const int32_t *in, const int32_t *w,
                   const int32_t output_multiplier,
                   const int32_t output_shift,
                   const int32_t output_activation_min,
                   const int32_t output_activation_max,
                   int32_t *out) {
    for (int i = 0; i < N; i++) {
        int32_t acc = 0;
        for (int j = 0; j < M; j++)
            acc += (in[j] + in_off) * (w[i * M + j] + w_off);
        acc += (bias ? bias[i] : 0);
        acc = MultiplyByQuantizedMultiplier(acc, output_multiplier, output_shift);
        acc = (acc > output_activation_min) ? acc : output_activation_min;
        out[i] = (acc < output_activation_max) ? acc : output_activation_max;
    }
}

/* Fill input, filter, and bias with non-trivial patterns for given N, M */
static void fill_data(int N, int M) {
    for (int j = 0; j < M; j++)
        input_data[j] = (j % 5) - 2;              /* -2,-1,0,1,2,-2,-1,... */

    for (int i = 0; i < N; i++)
        for (int j = 0; j < M; j++)
            filter_data[i * M + j] = ((i + j * 3) % 7) - 3;  /* -3..3 */

    for (int i = 0; i < N; i++)
        bias_data[i] = (i % 6) - 2;               /* -2,-1,0,1,2,3,-2,... */
}

/* Compare STRELA output against reference, print result */
static int check(const char *tag, int N) {
    for (int i = 0; i < N; i++) {
        if (output_data[i] != expected[i]) {
            PRINTF("  %s FAIL [%d]: got %d, expected %d\n",
                   tag, i, (int)output_data[i], (int)expected[i]);
            return 0;
        }
    }
    PRINTF("  %s PASS\n", tag);
    return 1;
}

/* Run one test: STRELA vs reference, with and without bias */
static int run_test(int N, int M, int32_t in_off, int32_t w_off, int32_t o_mul, int32_t o_shf, int32_t o_min, int32_t o_max) {
    PRINTF("Test N=%d M=%d in_off=%d w_off=%d\n", N, M, (int)in_off, (int)w_off);
    fill_data(N, M);

    int ok = 1;

    /* Without bias */
    fc_ref(N, M, in_off, w_off, NULL, input_data, filter_data, o_mul, o_shf, o_min, o_max, expected);
    strela_fully_connected(N, M, in_off, w_off, 0, NULL,
                           input_data, filter_data, o_mul, o_shf, o_min, o_max, output_data);
    ok &= check("no-bias", N);

    /* With bias */
    fc_ref(N, M, in_off, w_off, bias_data, input_data, filter_data, o_mul, o_shf, o_min, o_max, expected);
    strela_fully_connected(N, M, in_off, w_off, 0, bias_data,
                           input_data, filter_data, o_mul, o_shf, o_min, o_max, output_data);
    ok &= check("bias   ", N);

    return ok;
}

int main(void) {
    PRINTF("\nStarting STRELA v2 fully connected tests...\n\n");

    enable_all_fast_interrupts(true);
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    const uint32_t mask = 1 << 31;
    CSR_SET_BITS(CSR_REG_MIE, mask);

    int all_pass = 1;

    /* N multiple of 4 */
    all_pass &= run_test(16, 16,  0,  0, 20, 10, 15, 200);
    all_pass &= run_test( 8, 16,  0,  0, 20, 10, 15, 200);
    all_pass &= run_test( 4,  8,  1, -1, 20, 10, 15, 200);

    /* N not multiple of 4 */
    all_pass &= run_test( 7, 16,  0,  0, 20, 10, 15, 200);
    all_pass &= run_test( 5,  8,  2,  0, 20, 10, 15, 200);
    all_pass &= run_test( 6, 16, -1,  1, 20, 10, 15, 200);

    /* Only remainder (N < 4) */
    all_pass &= run_test( 3, 16,  0,  0, 20, 10, 15, 200);
    all_pass &= run_test( 2,  8,  0,  0, 20, 10, 15, 200);
    all_pass &= run_test( 1,  4,  0,  0, 20, 10, 15, 200);

    PRINTF("\n%s\n", all_pass ? "ALL TESTS PASSED" : "SOME TESTS FAILED");
    return all_pass ? 0 : 1;
}
