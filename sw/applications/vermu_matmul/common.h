// common.h

// #include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static inline int safe_vmax(int bits, int o) {
    int64_t lim = ((int64_t)1 << (bits - 1)) - 1;
    int v = 0;
    while ((int64_t)(v + 1) * (v + 1) * o <= lim)
        ++v;
    return v > 0 ? v : 1;   // o too long even for +-1: the generators' budget takes over
}

// Random value in [-vmax, vmax], or 0 once the row's budget of |values| is used
// up. With budget = lim / vmax per row, a dot product against any row with
// |values| <= vmax stays within lim: |sum a*b| <= sum|a| * vmax <= lim.
static inline int rand_elem(int vmax, int *budget) {
    int x = rand() % (2 * vmax + 1) - vmax;
    if (abs(x) > *budget)
        x = 0;
    *budget -= abs(x);
    return x;
}

void gen_rand_2d_int32(int32_t **ar, int n, int m, int vmax) {
    for (int i = 0; i < n; ++i) {
        int budget = INT32_MAX / vmax;
        for (int j = 0; j < m; ++j)
            ar[i][j] = (int32_t)rand_elem(vmax, &budget);
    }
}

void gen_rand_2d_int16(int16_t **ar, int n, int m, int vmax) {
    for (int i = 0; i < n; ++i) {
        int budget = INT16_MAX / vmax;
        for (int j = 0; j < m; ++j)
            ar[i][j] = (int16_t)rand_elem(vmax, &budget);
    }
}

void gen_rand_2d_int8(int8_t **ar, int n, int m, int vmax) {
    for (int i = 0; i < n; ++i) {
        int budget = INT8_MAX / vmax;
        for (int j = 0; j < m; ++j)
            ar[i][j] = (int8_t)rand_elem(vmax, &budget);
    }
}


bool compare_2d_int32(int32_t **golden, int32_t **actual, int n, int m) {
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < m; ++j)
            if (golden[i][j] != actual[i][j])
                return false;
    return true;
}

bool compare_2d_int16(int16_t **golden, int16_t **actual, int n, int m) {
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < m; ++j)
            if (golden[i][j] != actual[i][j])
                return false;
    return true;
}

bool compare_2d_int8(int8_t **golden, int8_t **actual, int n, int m) {
    for (int i = 0; i < n; ++i)
        for (int j = 0; j < m; ++j)
            if (golden[i][j] != actual[i][j])
                return false;
    return true;
}

// int32_t **alloc_array_2d_int32(int n, int m) {
//     int32_t **ret = (int32_t **)malloc(sizeof(int32_t *) * n);
//     for (int i = 0; i < n; ++i) {
//         ret[i] = (int32_t *)malloc(sizeof(int32_t) * m);
//     }
//     return ret;
// }

// int16_t **alloc_array_2d_int16(int n, int m) {
//     int16_t **ret = (int16_t **)malloc(sizeof(int16_t *) * n);
//     for (int i = 0; i < n; ++i) {
//         ret[i] = (int16_t *)malloc(sizeof(int16_t) * m);
//     }
//     return ret;
// }

// int8_t **alloc_array_2d_int8(int n, int m) {
//     int8_t **ret = (int8_t **)malloc(sizeof(int8_t *) * n);
//     for (int i = 0; i < n; ++i) {
//         ret[i] = (int8_t *)malloc(sizeof(int8_t) * m);
//     }
//     return ret;
// }
