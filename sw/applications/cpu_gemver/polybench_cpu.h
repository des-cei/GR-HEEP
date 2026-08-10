#ifndef _POLYBENCH_CPU_H
#define _POLYBENCH_CPU_H

#include <stdint.h>

#define DATA_TYPE int32_t

void gemm_cpu(
    int GEMM_NI,
    int GEMM_NJ,
    int GEMM_NK,
    DATA_TYPE *alpha,
    DATA_TYPE *beta,
    DATA_TYPE *A,
    DATA_TYPE *B,
    DATA_TYPE *C);

void gemver_cpu(
    int GEMVER_N,
    DATA_TYPE *alpha,
    DATA_TYPE *beta,
    DATA_TYPE *A,
    DATA_TYPE *u1,
    DATA_TYPE *v1,
    DATA_TYPE *u2,
    DATA_TYPE *v2,
    DATA_TYPE *w,
    DATA_TYPE *x,
    DATA_TYPE *y,
    DATA_TYPE *z);

void gesummv_cpu(
    int GESUMMV_N,
    DATA_TYPE *alpha,
    DATA_TYPE *beta,
    DATA_TYPE *A,
    DATA_TYPE *B, 
    DATA_TYPE *tmp,
    DATA_TYPE *x,
    DATA_TYPE *y);

void symm_cpu(
    int SYMM_N,
    int SYMM_M,
    DATA_TYPE *alpha, 
    DATA_TYPE *beta,
    DATA_TYPE *A,
    DATA_TYPE *B,
    DATA_TYPE *C);

void twomm_cpu(
    int TWOMM_NI,
    int TWOMM_NJ,
    int TWOMM_NK,
    int TWOMM_NL,
    DATA_TYPE *alpha,
    DATA_TYPE *beta,
    DATA_TYPE *tmp,
    DATA_TYPE *A,
    DATA_TYPE *B,
    DATA_TYPE *C,
    DATA_TYPE *D);

void threemm_cpu(
    int THREEMM_NI,
    int THREEMM_NJ,
    int THREEMM_NK,
    int THREEMM_NL,
    int THREEMM_NM,
    DATA_TYPE *A,
    DATA_TYPE *B,
    DATA_TYPE *C,
    DATA_TYPE *D,
    DATA_TYPE *E,
    DATA_TYPE *F,
    DATA_TYPE *G);

void atax_cpu(
    int ATAX_N,
    int ATAX_M,
    DATA_TYPE *A,
    DATA_TYPE *x,
    DATA_TYPE *y,
    DATA_TYPE *tmp);

void bicg_cpu(
    int BICG_M,
    int BICG_N,
    DATA_TYPE *A,
    DATA_TYPE *s,
    DATA_TYPE *q,
    DATA_TYPE *p,
    DATA_TYPE *r);

void doitgen_cpu(
    int DOITGEN_NP,
    int DOITGEN_NQ,
    int DOITGEN_NR,
    DATA_TYPE *A,
    DATA_TYPE *C4,
    DATA_TYPE *sum);

void mvt_cpu(
    int MVT_N,
    DATA_TYPE *x1,
    DATA_TYPE *x2,
    DATA_TYPE *y_1,
    DATA_TYPE *y_2,
    DATA_TYPE *A);

void jacobi1d_cpu(
    int TSTEPS,
    int N,
    DATA_TYPE *A,
    DATA_TYPE *B);

#endif
