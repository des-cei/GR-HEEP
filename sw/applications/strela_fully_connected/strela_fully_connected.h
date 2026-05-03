#ifndef STRELA_FC_H_
#define STRELA_FC_H_

#include <stdint.h>

/* Data type selector for input/filter activations.
 * TFLM typically uses INT8 for activations and INT32 for bias/accumulator.
 * Bias and output_data remain int32 (accumulator domain). */
#define STRELA_FC_DTYPE_INT8   1
#define STRELA_FC_DTYPE_INT16  2
#define STRELA_FC_DTYPE_INT32  4

#ifndef STRELA_FC_DTYPE
#define STRELA_FC_DTYPE STRELA_FC_DTYPE_INT8
#endif

#if STRELA_FC_DTYPE == STRELA_FC_DTYPE_INT8
typedef int8_t strela_fc_data_t;
#elif STRELA_FC_DTYPE == STRELA_FC_DTYPE_INT16
typedef int16_t strela_fc_data_t;
#elif STRELA_FC_DTYPE == STRELA_FC_DTYPE_INT32
typedef int32_t strela_fc_data_t;
#else
#error "Unsupported STRELA_FC_DTYPE (use INT8, INT16, or INT32)"
#endif

int32_t MultiplyByQuantizedMultiplier(int64_t x, int32_t quantized_multiplier,
                                      int shift);

void strela_fully_connected(int N, int M,
                            const int32_t input_offset,
                            const int32_t filter_offset,
                            const int32_t output_offset,
                            const int32_t *bias_data,
                            const strela_fc_data_t *input_data,
                            const strela_fc_data_t *filter_data,
                            const int32_t output_multiplier,
                            const int32_t output_shift,
                            const int32_t output_activation_min,
                            const int32_t output_activation_max,
                            int32_t *output_data);

#endif // STRELA_FC_H_
