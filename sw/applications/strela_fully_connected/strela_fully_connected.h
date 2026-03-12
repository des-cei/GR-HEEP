#ifndef STRELA_FC_H_
#define STRELA_FC_H_

#include <stdint.h>

void strela_fully_connected(int N, int M,
                            const int32_t input_offset,
                            const int32_t filter_offset,
                            const int32_t output_offset,
                            const int32_t *input_data,
                            const int32_t *filter_data,
                            int32_t *output_data);

#endif // STRELA_FC_H_
