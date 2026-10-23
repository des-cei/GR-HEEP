// Directed test of STRELA v1's stream engines: 8-, 16- and 32-bit streams,
// strides, unaligned bases, short and long counts, the 16-bit span limit,
// sign extension on the ISE side and byte enables on the OSE side.
//
// Two committed regress/4x4 kernels drive it. relu (one ISE, one OSE, both on
// channel 1) is the single-stream workhorse: max(x, 0) tells a sign-extended
// narrow input from a zero-extended one, since -5 must come out as 0 and not
// as 251. gemm_2 (four ISEs, two OSEs, alpha*tmp + beta*c per lane) gives every
// engine a different width at once, which is what checks that each engine
// reads its own ISE_SEW / OSE_SEW field.
//
// Every output region is pre-filled with a canary byte, and every byte that
// is not part of an output element (before, between at a stride, and after)
// must still hold it after the run.

#include <stdio.h>
#include <stdint.h>
#include "x-heep.h"
#include "mmio.h"
#include "gr_heep.h"
#include "strela.h"
#include "strela_v1_regs.h"
#include "relu_kernel.h"
#include "gemm_2_kernel.h"

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

// The input buffer covers a whole 16-bit span; the output one does not need to.
#define IN_BYTES (64 * 1024)
#define OUT_BYTES (16 * 1024)
#define OUT_GUARD 8
#define MAX_COUNT 4096
#define CANARY 0xA5

#define SEW_ALL_8 0x6DBu

#define GEMM_2_COUNT 50
#define GEMM_2_ALPHA 3
#define GEMM_2_BETA (-2)

static uint8_t in_buf[IN_BYTES] __attribute__((section(".xheep_data_interleaved"), aligned(4)));
static uint8_t out_buf[OUT_BYTES] __attribute__((section(".xheep_data_interleaved"), aligned(4)));
static int32_t expected[MAX_COUNT];

mmio_region_t strela_v1;

static uint32_t rng_state = 0x1234567u;

static uint32_t rng(void) {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 17;
    rng_state ^= rng_state << 5;
    return rng_state;
}

static inline void strela_v1_write(uint32_t offset, uint32_t value) {
    mmio_region_write32(strela_v1, (ptrdiff_t) offset, value);
}

static inline uint32_t strela_v1_read(uint32_t offset) {
    return mmio_region_read32(strela_v1, (ptrdiff_t) offset);
}

// Start one execution with the parameters already programmed, poll until every
// stream engine is done and return the execution cycles.
static uint32_t strela_v1_run(void) {
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_PERF_CTR_BIT);
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_START_BIT);
    while (!(strela_v1_read(STRELA_V1_STATUS_REG_OFFSET) & (1 << STRELA_V1_STATUS_EXEC_DONE_BIT)))
        ;
    return strela_v1_read(STRELA_V1_PERF_CTR_EXEC_CYCLES_REG_OFFSET);
}

// Little-endian element access with the engines' conventions: an ISE
// sign-extends, an OSE keeps the low bytes.
static int32_t load_elem(const uint8_t *p, uint32_t bytes) {
    if (bytes == 1)
        return (int8_t) p[0];
    if (bytes == 2)
        return (int16_t) (p[0] | p[1] << 8);
    return (int32_t) (p[0] | p[1] << 8 | p[2] << 16 | (uint32_t) p[3] << 24);
}

static void store_elem(uint8_t *p, uint32_t bytes, int32_t value) {
    for (uint32_t b = 0; b < bytes; b++)
        p[b] = (uint8_t) ((uint32_t) value >> (8 * b));
}

// Write `count` random elements of an input stream.
static void fill_stream(uint32_t base, uint32_t stride, uint32_t bytes, uint32_t count) {
    for (uint32_t k = 0; k < count; k++)
        store_elem(&in_buf[base + k * stride], bytes, (int32_t) rng());
}

static void canary_region(uint32_t base, uint32_t len) {
    for (uint32_t j = 0; j < len; j++)
        out_buf[base + j] = CANARY;
}

// Length of the canary region an output stream at `off` needs.
static uint32_t out_region(uint32_t off, uint32_t stride, uint32_t count) {
    return off + count * stride + OUT_GUARD;
}

// Check an output stream against expected[] (truncated to the element width)
// and every other byte of its region against the canary.
static int check_stream(uint32_t base, uint32_t off, uint32_t stride, uint32_t bytes, uint32_t count) {
    const uint32_t len = out_region(off, stride, count);
    int errors = 0;
    uint32_t j = 0;

    int reported = 0;

    for (uint32_t k = 0; k < count; k++) {
        const uint32_t start = off + k * stride;
        for (; j < start; j++)
            errors += out_buf[base + j] != CANARY;
        const uint32_t want = (uint32_t) expected[k];
        const uint32_t got = (uint32_t) load_elem(&out_buf[base + j], bytes);
        int elem_errors = 0;
        for (uint32_t b = 0; b < bytes; b++, j++)
            elem_errors += out_buf[base + j] != (uint8_t) (want >> (8 * b));
        if (elem_errors && reported++ < 3)
            PRINTF("  [%u] expected 0x%08x, got 0x%08x\n", k, want, got);
        errors += elem_errors;
    }
    for (; j < len; j++)
        errors += out_buf[base + j] != CANARY;
    return errors;
}

/*************** relu cases ***************/

typedef struct {
    uint8_t in_sew, out_sew;
    uint16_t in_stride, out_stride;
    uint8_t in_off, out_off;
    uint16_t count;
} relu_case_t;

#define S8 STRELA_V1_SEW_8
#define S16 STRELA_V1_SEW_16
#define S32 STRELA_V1_SEW_32

// The first case is 8-bit on purpose: its START also loads the bitstream, which
// must reach the fabric as 32-bit words whatever width ISE_SEW already holds
// (sign-extending the configuration was a bug of the first ISE version).
static const relu_case_t relu_cases[] = {
#ifdef STREAM_TEST_QUICK
    {S32, S32, 4, 4, 0, 0, 8},    {S8, S8, 1, 1, 0, 0, 8},
#else
    // Every input/output width pair, contiguous, a count that is not a
    // multiple of four so the last word is partial
    {S8, S8, 1, 1, 0, 0, 37},     {S8, S16, 1, 2, 0, 0, 37},     {S8, S32, 1, 4, 0, 0, 37},
    {S16, S8, 2, 1, 0, 0, 37},    {S16, S16, 2, 2, 0, 0, 37},    {S16, S32, 2, 4, 0, 0, 37},
    {S32, S8, 4, 1, 0, 0, 37},    {S32, S16, 4, 2, 0, 0, 37},    {S32, S32, 4, 4, 0, 0, 37},
    // Unaligned bases and short counts
    {S8, S8, 1, 1, 1, 3, 1},      {S8, S8, 1, 1, 2, 2, 2},       {S8, S8, 1, 1, 3, 1, 3},
    {S8, S8, 1, 1, 1, 2, 5},      {S16, S16, 2, 2, 2, 2, 1},     {S16, S16, 2, 2, 2, 2, 3},
    {S8, S16, 1, 2, 3, 2, 7},     {S16, S8, 2, 1, 2, 3, 6},
    // Strides: obione has dedicated byte-enable paths for byte strides 1-3
    {S8, S8, 2, 3, 0, 0, 33},     {S8, S8, 3, 2, 1, 1, 33},      {S8, S16, 5, 6, 0, 2, 21},
    {S16, S8, 6, 5, 2, 3, 21},    {S32, S32, 12, 8, 0, 0, 19},   {S16, S32, 4, 12, 2, 0, 19},
    {S8, S8, 1, 4, 1, 2, 30},     {S8, S8, 4, 1, 3, 0, 30},
    // Long streams
    {S8, S8, 1, 1, 0, 0, 4096},   {S32, S32, 4, 4, 0, 0, 1024},  {S16, S16, 2, 2, 0, 0, 2048},
    // The 16-bit span limit: 13107 * 5 = 65535 and 16380 * 4 = 65520
    {S8, S32, 13107, 4, 0, 0, 5}, {S32, S8, 16380, 4095, 0, 1, 4},
    // A zero count finishes immediately and writes nothing
    {S8, S8, 1, 1, 0, 0, 0},
#endif
};

static int run_relu_case(const relu_case_t *c) {
    const uint32_t in_bytes = strela_v1_sew_bytes(c->in_sew);
    const uint32_t out_bytes = strela_v1_sew_bytes(c->out_sew);
    const uint32_t ise = RELU_KERNEL_IN_INPUT0_CH;
    const uint32_t ose = RELU_KERNEL_OUT_OUTPUT0_CH;

    fill_stream(c->in_off, c->in_stride, in_bytes, c->count);
    for (uint32_t k = 0; k < c->count; k++) {
        int32_t x = load_elem(&in_buf[c->in_off + k * c->in_stride], in_bytes);
        expected[k] = x > 0 ? x : 0;
    }
    canary_region(0, out_region(c->out_off, c->out_stride, c->count));

    strela_v1_write(STRELA_V1_ISE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, ise, c->in_sew));
    strela_v1_write(STRELA_V1_OSE_SEW_REG_OFFSET, strela_v1_sew(STRELA_V1_SEW_ALL_32, ose, c->out_sew));
    strela_v1_write(STRELA_V1_ISE_ADDR_REG_OFFSET(ise), (uint32_t) &in_buf[c->in_off]);
    strela_v1_write(STRELA_V1_ISE_PARAM_REG_OFFSET(ise), strela_v1_se_param(c->count, c->in_stride));
    strela_v1_write(STRELA_V1_OSE_ADDR_REG_OFFSET(ose), (uint32_t) &out_buf[c->out_off]);
    strela_v1_write(STRELA_V1_OSE_PARAM_REG_OFFSET(ose), strela_v1_se_param(c->count, c->out_stride));

    uint32_t cycles = strela_v1_run();
    int errors = check_stream(0, c->out_off, c->out_stride, out_bytes, c->count);

    PRINTF("relu %2u->%2u in %u+%u*%u out %u+%u*%u: %u cycles, %s (%d)\n",
           8 * in_bytes, 8 * out_bytes, c->in_off, c->in_stride, c->count,
           c->out_off, c->out_stride, c->count, cycles, errors ? "FAIL" : "ok", errors);
    return errors != 0;
}

/*************** gemm_2 case ***************/

// One stream of the gemm_2 case: an engine, its width and its layout.
typedef struct {
    uint32_t se, sew, stride, off, base;
} stream_t;

static void program_ise(const stream_t *s, uint32_t count) {
    strela_v1_write(STRELA_V1_ISE_ADDR_REG_OFFSET(s->se), (uint32_t) &in_buf[s->base + s->off]);
    strela_v1_write(STRELA_V1_ISE_PARAM_REG_OFFSET(s->se), strela_v1_se_param(count, s->stride));
}

static void program_ose(const stream_t *s, uint32_t count) {
    strela_v1_write(STRELA_V1_OSE_ADDR_REG_OFFSET(s->se), (uint32_t) &out_buf[s->base + s->off]);
    strela_v1_write(STRELA_V1_OSE_PARAM_REG_OFFSET(s->se), strela_v1_se_param(count, s->stride));
}

static int32_t elem_at(const stream_t *s, uint32_t k) {
    return load_elem(&in_buf[s->base + s->off + k * s->stride], strela_v1_sew_bytes(s->sew));
}

// d = alpha * tmp + beta * c on both lanes, with a different width on every
// engine; the two OSEs gemm_2 leaves unused get widths too, which they must
// ignore since they never start.
static int run_gemm_2_case(void) {
    const stream_t tmp_a = {GEMM_2_KERNEL_IN_INPUT0_CH, S32, 4, 0, 0};
    const stream_t c_a = {GEMM_2_KERNEL_IN_INPUT1_CH, S8, 3, 1, 1024};
    const stream_t tmp_b = {GEMM_2_KERNEL_IN_INPUT2_CH, S16, 4, 2, 2048};
    const stream_t c_b = {GEMM_2_KERNEL_IN_INPUT3_CH, S8, 1, 0, 3072};
    const stream_t d_a = {GEMM_2_KERNEL_OUT_OUTPUT0_CH, S8, 2, 1, 0};
    const stream_t d_b = {GEMM_2_KERNEL_OUT_OUTPUT1_CH, S16, 2, 0, 1024};
    const stream_t *ins[] = {&tmp_a, &c_a, &tmp_b, &c_b};
    const uint32_t n = GEMM_2_COUNT;
    uint32_t ise_sew = 0, ose_sew = SEW_ALL_8;
    int errors = 0;

    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT0_CH, (uint32_t) GEMM_2_ALPHA);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT2_CH, (uint32_t) GEMM_2_ALPHA);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT1_CH, (uint32_t) GEMM_2_BETA);
    strela_v1_set_pe_const(gemm_2_kernel, GEMM_2_KERNEL_IN_INPUT3_CH, (uint32_t) GEMM_2_BETA);

    // CLR_CONF makes the next START load gemm_2; CLR_PARAM clears CONF_ADDR,
    // so it is written after.
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_CONF_BIT | 1 << STRELA_V1_CTRL_CLR_PARAM_BIT);
    strela_v1_write(STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) gemm_2_kernel);

    for (int i = 0; i < 4; i++) {
        fill_stream(ins[i]->base + ins[i]->off, ins[i]->stride, strela_v1_sew_bytes(ins[i]->sew), n);
        program_ise(ins[i], n);
        ise_sew = strela_v1_sew(ise_sew, ins[i]->se, ins[i]->sew);
    }
    ose_sew = strela_v1_sew(ose_sew, d_a.se, d_a.sew);
    ose_sew = strela_v1_sew(ose_sew, d_b.se, d_b.sew);
    strela_v1_write(STRELA_V1_ISE_SEW_REG_OFFSET, ise_sew);
    strela_v1_write(STRELA_V1_OSE_SEW_REG_OFFSET, ose_sew);
    program_ose(&d_a, n);
    program_ose(&d_b, n);
    canary_region(d_a.base, out_region(d_a.off, d_a.stride, n));
    canary_region(d_b.base, out_region(d_b.off, d_b.stride, n));

    uint32_t cycles = strela_v1_run();

    for (uint32_t k = 0; k < n; k++)
        expected[k] = GEMM_2_ALPHA * elem_at(&tmp_a, k) + GEMM_2_BETA * elem_at(&c_a, k);
    errors += check_stream(d_a.base, d_a.off, d_a.stride, strela_v1_sew_bytes(d_a.sew), n);
    for (uint32_t k = 0; k < n; k++)
        expected[k] = GEMM_2_ALPHA * elem_at(&tmp_b, k) + GEMM_2_BETA * elem_at(&c_b, k);
    errors += check_stream(d_b.base, d_b.off, d_b.stride, strela_v1_sew_bytes(d_b.sew), n);

    PRINTF("gemm_2 ISE_SEW 0x%03x OSE_SEW 0x%03x: %u cycles, %s (%d)\n",
           ise_sew, ose_sew, cycles, errors ? "FAIL" : "ok", errors);
    return errors != 0;
}

/*************** Register checks ***************/

static int check_reg(const char *name, uint32_t offset, uint32_t expected_value) {
    uint32_t value = strela_v1_read(offset);
    if (value != expected_value) {
        PRINTF("%s = 0x%08x, expected 0x%08x: FAIL\n", name, value, expected_value);
        return 1;
    }
    return 0;
}

// CLR_PARAM returns both SEW registers to all-32-bit and clears the stream
// parameters and CONF_ADDR.
static int check_clr_param(void) {
    int failures = 0;

    strela_v1_write(STRELA_V1_ISE_SEW_REG_OFFSET, SEW_ALL_8);
    strela_v1_write(STRELA_V1_OSE_SEW_REG_OFFSET, SEW_ALL_8);
    strela_v1_write(STRELA_V1_ISE_PARAM_REG_OFFSET(2), 0x00040010);
    strela_v1_write(STRELA_V1_OSE_PARAM_REG_OFFSET(3), 0x00020008);
    failures += check_reg("ISE_SEW (written)", STRELA_V1_ISE_SEW_REG_OFFSET, SEW_ALL_8);
    failures += check_reg("OSE_SEW (written)", STRELA_V1_OSE_SEW_REG_OFFSET, SEW_ALL_8);

    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_PARAM_BIT);
    failures += check_reg("ISE_SEW", STRELA_V1_ISE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    failures += check_reg("OSE_SEW", STRELA_V1_OSE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    failures += check_reg("ISE_2_PARAM", STRELA_V1_ISE_PARAM_REG_OFFSET(2), 0);
    failures += check_reg("OSE_3_PARAM", STRELA_V1_OSE_PARAM_REG_OFFSET(3), 0);
    failures += check_reg("CONF_ADDR", STRELA_V1_CONF_ADDR_REG_OFFSET, 0);

    PRINTF("CLR_PARAM: %s\n", failures ? "FAIL" : "ok");
    return failures != 0;
}

int main(void) {
    int failures = 0;
    int cases = 0;

    PRINTF("\nSTRELA v1 stream engine test starts\n");

    strela_v1 = mmio_region_from_addr(STRELA_V1_PERIPH_START_ADDRESS);

    // Reset values, then the same after CLR
    failures += check_reg("ISE_SEW (reset)", STRELA_V1_ISE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    failures += check_reg("OSE_SEW (reset)", STRELA_V1_OSE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_BIT);
    failures += check_reg("ISE_SEW (CLR)", STRELA_V1_ISE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    failures += check_reg("OSE_SEW (CLR)", STRELA_V1_OSE_SEW_REG_OFFSET, STRELA_V1_SEW_ALL_32);
    cases += 4;
    strela_v1_write(STRELA_V1_MODE_REG_OFFSET, 1 << STRELA_V1_MODE_PERF_CTR_EN_BIT);

    // relu, configured once by the first START and reused by every case: the
    // engines' widths and parameters change between runs, the bitstream does not
    strela_v1_write(STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) relu_kernel);
    for (uint32_t i = 0; i < sizeof(relu_cases) / sizeof(relu_cases[0]); i++, cases++)
        failures += run_relu_case(&relu_cases[i]);

    // CLR_PARAM leaves the configuration loaded, so relu runs again with the
    // default widths and freshly written parameters
    failures += check_clr_param();
    cases++;
    static const relu_case_t relu_default = {S32, S32, 4, 4, 0, 0, 64};
    failures += run_relu_case(&relu_default);
    cases++;

    // Kernel switch to gemm_2, every engine at its own width
    failures += run_gemm_2_case();
    cases++;

    // And back to relu, reloading its configuration
    strela_v1_write(STRELA_V1_CTRL_REG_OFFSET, 1 << STRELA_V1_CTRL_CLR_CONF_BIT | 1 << STRELA_V1_CTRL_CLR_PARAM_BIT);
    strela_v1_write(STRELA_V1_CONF_ADDR_REG_OFFSET, (uint32_t) relu_kernel);
    static const relu_case_t relu_reload = {S8, S16, 1, 2, 1, 2, 45};
    failures += run_relu_case(&relu_reload);
    cases++;

    strela_v1_write(STRELA_V1_MODE_REG_OFFSET, 0);

    if (failures) {
        PRINTF("FAIL!! %d of %d checks failed\n", failures, cases);
    }
    else {
        PRINTF("SUCCESS! %d checks\n", cases);
    }

    PRINTF("STRELA v1 stream engine test ends\n\n");
    return failures;
}
