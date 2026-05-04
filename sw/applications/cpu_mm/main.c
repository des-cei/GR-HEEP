#include <stdio.h>
#include <stdint.h>
#include "x-heep.h"
#include "csr.h"
#include "dataset.h"
#include "gpio.h"

#define GPIO_TOGGLE 6
#define GPIO_NEW_VCD 7

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

int main()
{
    int i, j, k;
    uint32_t sw_time;

    CSR_WRITE(CSR_REG_MCOUNTINHIBIT, 0);

    // GPIOs
    gpio_result_t gpio_res;

    gpio_cfg_t pin_cfg = {
        .pin = GPIO_TOGGLE,
        .mode = GpioModeOutPushPull
    };

    gpio_res = gpio_config(pin_cfg);

    if (gpio_res != GpioOk)
        PRINTF("Gpio initialization failed!\n");

    gpio_cfg_t pin_cfg_vcd = {
        .pin = GPIO_NEW_VCD,
        .mode = GpioModeOutPushPull
    };

    gpio_res = gpio_config(pin_cfg_vcd);
    
    if (gpio_res != GpioOk)
        PRINTF("Gpio initialization failed!\n");
    
    PRINTF("\nStarting MM CPU application...\n");
 
    CSR_WRITE(CSR_REG_MCYCLE, 0);
    gpio_write(GPIO_NEW_VCD, true);
    gpio_write(GPIO_NEW_VCD, false);
    gpio_write(GPIO_TOGGLE, true);
    for (i = 0; i < N; i++) {
        for (k = 0; k < K; k++) {
            for (j = 0; j < M; j++)
                matC[i*M+j] += matA[i*K+k] * matB[k*M+j];
        }
    }
    gpio_write(GPIO_TOGGLE, false);
    CSR_READ(CSR_REG_MCYCLE, &sw_time);
    
    PRINTF("Data size: %d, %d, %d\n", N, M, K);
    PRINTF("Total cycles: %lu\n", sw_time);

    // Check results
    int32_t check = 0;
    for(i = 0; i < M*N; i++)
        if(matC[i] == matC_expected[i])
            check++;

    if(check != N*M) PRINTF("FAIL!!!\n");
    else PRINTF("SUCCESS!\n");

    PRINTF("Finishing MM CPU application...\n\n");
    return (check != N*M);
}