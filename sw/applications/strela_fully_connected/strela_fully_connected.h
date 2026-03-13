#ifndef STRELA_FC_H_
#define STRELA_FC_H_

#include <stdint.h>

int32_t MultiplyByQuantizedMultiplier(int64_t x, int32_t quantized_multiplier,
                                      int shift);

void strela_fully_connected(int N, int M,
                            const int32_t input_offset,
                            const int32_t filter_offset,
                            const int32_t output_offset,
                            const int32_t *bias_data,
                            const int32_t *input_data,
                            const int32_t *filter_data,
                            const int32_t output_multiplier,
                            const int32_t output_shift,
                            const int32_t output_activation_min,
                            const int32_t output_activation_max,
                            int32_t *output_data);

#endif // STRELA_FC_H_
