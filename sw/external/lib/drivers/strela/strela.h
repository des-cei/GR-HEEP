#ifndef STRELA_H
#define STRELA_H

// Driver definitions for both STRELA generations integrated in GR-HEEP:
//
//   STRELA v2 (hw/vendor/strela-v2, STRELA_V2_PERIPH_*, strela_v2_regs.h): stream
//     engines driven by descriptor tables, on the 4x4-HV elastic-cgra fabric.
//   STRELA v1 (hw/vendor/strela-v1, STRELA_V1_PERIPH_*, strela_v1_regs.h):
//     memory nodes programmed through MMIO registers, on the plain 4x4 fabric.
//
// Both fabrics are generated from the same elastic-cgra, so a PE has the same
// 5-word configuration and the same place in its column's configuration chain
// on either; the one difference in the bitstream is the router word v2 appends
// to each column. The PE accessors are therefore shared, and each generation
// only says how many router words a column carries. That is all the two have
// in common, and only it is named plain strela_/STRELA_. Below the fabric
// section, STRELA v1's names carry a STRELA_V1_/strela_v1_ prefix, and STRELA
// v2's section mirrors hw/vendor/strela-v2/sw/strela_v2.h, whose descriptor
// API (memory_node_t, the opcodes, CONFIG_SIZE, set_pe_const...) is unprefixed
// but never named strela.

#include <stdint.h>

/*************** Fabric (both generations) ***************/

#define NPE 16
#define NCOLS 4
#define PE_WORDS 5

// Position of a PE (row-major number, as in the mapper and the io_map) inside
// its column's configuration chain. The chain is shifted in from the north, so
// the first word of a column configures its southernmost PE.
static const uint8_t PE_POSITION[16] = {
    3, 7, 11, 15,
    2, 6, 10, 14,
    1, 5,  9, 13,
    0, 4,  8, 12
};

// Index of `word` (0..PE_WORDS-1) of PE `pe` in a bitstream whose columns each
// end in `router_words` router words (1 on STRELA v2, 0 on STRELA v1).
static inline uint32_t strela_pe_word_index(uint8_t pe, uint8_t word, uint8_t router_words) {
	return router_words * (pe % NCOLS) + PE_POSITION[pe] * PE_WORDS + word;
}

// Word 2 bits 31:16 hold delay_value, word 3 the initial value, word 4 the
// constant.
static inline uint32_t strela_get_pe_initial_value(const uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words) {
	return conf_addr[strela_pe_word_index(pe_number, 3, router_words)];
}

static inline void strela_set_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words, uint32_t value) {
	conf_addr[strela_pe_word_index(pe_number, 3, router_words)] = value;
}

static inline uint32_t strela_get_pe_const(const uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words) {
	return conf_addr[strela_pe_word_index(pe_number, 4, router_words)];
}

static inline void strela_set_pe_const(uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words, uint32_t value) {
	conf_addr[strela_pe_word_index(pe_number, 4, router_words)] = value;
}

static inline uint32_t strela_get_pe_delay_value(const uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words) {
	return conf_addr[strela_pe_word_index(pe_number, 2, router_words)] >> 16;
}

static inline void strela_set_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number, uint8_t router_words, uint32_t value) {
	uint32_t *word = &conf_addr[strela_pe_word_index(pe_number, 2, router_words)];
	*word = (*word & 0x0000FFFF) | value << 16;
}

/*************** STRELA v2 ***************/

#define NROUTERS 4
#define CONFIG_SIZE (NPE * 5 + NROUTERS)
#define CONFIG_BYTES (CONFIG_SIZE * 4)

// The configuration chain is per column: every ISE shifts its own column and
// nothing else. A column is (NPE / NCOLS) PEs x 5 words, daisy-chained north
// to south, followed by that column's router word.
//
// CONFIG_COL_BYTES -- not CONFIG_BYTES -- is what a TR_CONF_ISE descriptor
// must carry, with the array base offset by CONFIG_COL_WORDS * column. Use
// conf_node() below rather than spelling this out.
#define CONFIG_COL_WORDS ((NPE / NCOLS) * 5 + 1)
#define CONFIG_COL_BYTES (CONFIG_COL_WORDS * 4)

// OPCODES
#define IDLE_SE 0
#define FENCE_ISE 1
#define FENCE_OSE 1
#define FENCE_SE 2
#define TR_CONF_ISE 3
#define CFG_MEM_W_OSE 4
#define CFG_MEM_E_OSE 5
// RESERVED (6-7)

// Transfer opcodes. The numeric suffix is the SEW (Selected Element Width) in
// bits: the width of one element as it is stored in system memory.
//
// WARNING: only the 8, 16 and 32 bit variants are implemented. The obione DMA
// rejects any other SEW (assertion a_sew_valid in rtl/obione/rtl/obione.sv),
// so the _1_, _2_ and _4_ opcodes below are placeholders that reserve their
// encoding -- do not use them.
//
// The ISE sign-extends 8- and 16-bit elements to 32 bits before handing them
// to the CGRA; mask inside the kernel if unsigned semantics are needed.
#define TR_NORTH_1_ISE 8
#define TR_NORTH_2_ISE 9
#define TR_NORTH_4_ISE 10
#define TR_NORTH_8_ISE 11
#define TR_NORTH_16_ISE 12
#define TR_NORTH_32_ISE 13
#define TR_SOUTH_1_OSE 8
#define TR_SOUTH_2_OSE 9
#define TR_SOUTH_4_OSE 10
#define TR_SOUTH_8_OSE 11
#define TR_SOUTH_16_OSE 12
#define TR_SOUTH_32_OSE 13
#define TR_VER_1_ISE 14
#define TR_VER_2_ISE 15
#define TR_VER_4_ISE 16
#define TR_VER_8_ISE 17
#define TR_VER_16_ISE 18
#define TR_VER_32_ISE 19
#define TR_VER_1_OSE 14
#define TR_VER_2_OSE 15
#define TR_VER_4_OSE 16
#define TR_VER_8_OSE 17
#define TR_VER_16_OSE 18
#define TR_VER_32_OSE 19
#define TR_MEM_W_1_ISE 20
#define TR_MEM_W_2_ISE 21
#define TR_MEM_W_4_ISE 22
#define TR_MEM_W_8_ISE 23
#define TR_MEM_W_16_ISE 24
#define TR_MEM_W_32_ISE 25
#define TR_MEM_W_1_OSE 20
#define TR_MEM_W_2_OSE 21
#define TR_MEM_W_4_OSE 22
#define TR_MEM_W_8_OSE 23
#define TR_MEM_W_16_OSE 24
#define TR_MEM_W_32_OSE 25
#define TR_MEM_E_1_ISE 26
#define TR_MEM_E_2_ISE 27
#define TR_MEM_E_4_ISE 28
#define TR_MEM_E_8_ISE 29
#define TR_MEM_E_16_ISE 30
#define TR_MEM_E_32_ISE 31
#define TR_MEM_E_1_OSE 26
#define TR_MEM_E_2_OSE 27
#define TR_MEM_E_4_OSE 28
#define TR_MEM_E_8_OSE 29
#define TR_MEM_E_16_OSE 30
#define TR_MEM_E_32_OSE 31

// Memory parameters. Offsets
#define MEM_PARAM_ADDR_OFFSET 5u
#define MEM_PARAM_SIZE_OFFSET 14u
#define MEM_PARAM_MODE_OFFSET 23u
#define MEM_PARAM_ITER_OFFSET 24u

// Router words that precede PE `pe` in the bitstream, one per column to its
// west: PE_OFFSET[pe] + PE_POSITION[pe]*5 is the PE's first word.
static const uint8_t PE_OFFSET[16] = {
    0, 1, 2, 3,
    0, 1, 2, 3,
    0, 1, 2, 3,
    0, 1, 2, 3
};

typedef struct {
	uint32_t opcode;
	uintptr_t address;
	uint32_t params;
} memory_node_t;

/*************** Descriptor helpers ***************/

// Build a memory_node_t for internal-memory operations (TR_MEM_W_ISE, TR_MEM_E_ISE,
// CFG_MEM_W_OSE, CFG_MEM_E_OSE, TR_MEM_W_OSE, TR_MEM_E_OSE).
//
// opcode : one of TR_MEM_W_ISE, TR_MEM_E_ISE, ...
// iters  : number of times to replay the size-element burst (usually 1)
// mode   : 0 = PE-side port, 1 = horizontal port
// size   : number of elements to store in the internal SRAM
// addr   : starting word address inside the internal SRAM (usually 0)
// data   : pointer to source/destination in system memory
// stride : byte step between consecutive system-memory accesses
// total  : total bytes to transfer from/to system memory (stride * size * iters)
static inline memory_node_t mem_node_mem(uint8_t opcode,
                                         uint32_t iters, uint32_t mode,
                                         uint32_t size,  uint32_t addr,
                                         const void *data,
                                         uint32_t stride, uint32_t total) {
    return (memory_node_t){
        .opcode  = (iters << MEM_PARAM_ITER_OFFSET) | (mode << MEM_PARAM_MODE_OFFSET) |
            (size << MEM_PARAM_SIZE_OFFSET) | (addr << MEM_PARAM_ADDR_OFFSET) | opcode,
        .address = (uintptr_t)data,
        .params  = (stride << 16u) | total
    };
}

// Build a memory_node_t for simple stream transfers (TR_NORTH_ISE, TR_SOUTH_OSE,
// TR_VER_ISE, TR_CONF_ISE, ...) where the opcode field is just the opcode.
//
// stride : byte step between consecutive system-memory accesses
// total  : total bytes to transfer
static inline memory_node_t mem_node_tr(uint8_t opcode, const void *data,
                                        uint32_t stride, uint32_t total) {
    return (memory_node_t){
        .opcode  = opcode,
        .address = (uintptr_t)data,
        .params  = (stride << 16u) | total
    };
}

// Build the TR_CONF_ISE descriptor for one CGRA column.
//
// kernel : the full CONFIG_SIZE-word bitstream array
// column : which column this ISE configures (0..NCOLS-1, == the ISE index)
//
// All four ISEs must issue their own conf_node(), including ones with no data
// work to do: the CGRA input handshakes stay gated until every ISE has
// finished its TR_CONF.
static inline memory_node_t conf_node(const uint32_t *kernel, uint32_t column) {
    return mem_node_tr(TR_CONF_ISE, &kernel[column * CONFIG_COL_WORDS],
                       4u, CONFIG_COL_BYTES);
}

// PE accessors on a STRELA v2 bitstream (one router word per column).
static inline uint32_t get_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_initial_value(conf_addr, pe_number, 1);
}

static inline void set_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_initial_value(conf_addr, pe_number, 1, value);
}

static inline uint32_t get_pe_const(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_const(conf_addr, pe_number, 1);
}

static inline void set_pe_const(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_const(conf_addr, pe_number, 1, value);
}

static inline uint32_t get_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_delay_value(conf_addr, pe_number, 1);
}

static inline void set_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_delay_value(conf_addr, pe_number, 1, value);
}

/*************** STRELA v1 ***************/

// No router words: the bitstream is the four columns back to back, each
// (NPE / NCOLS) PEs x 5 words. Input memory node i loads column i's slice
// (STRELA_V1_CONFIG_BYTES / NCOLS bytes from CONF_ADDR + i * that) on its own,
// so CONF_ADDR takes the whole array.
#define STRELA_V1_CONFIG_SIZE (NPE * PE_WORDS)
#define STRELA_V1_CONFIG_BYTES (STRELA_V1_CONFIG_SIZE * 4)

// Input memory node i streams into the north border of column i, i.e. fabric
// input channel i; output memory node i drains the south border of column i,
// fabric output channel i. A kernel header's <ARRAY>_IN_<PORT>_CH /
// <ARRAY>_OUT_<PORT>_CH defines are therefore node indices directly.
#define STRELA_V1_INPUT_NODES 4
#define STRELA_V1_OUTPUT_NODES 4

// The per-node registers are evenly spaced in strela_v1_regs.h (include it
// to use these): an ADDR register, then IMN_i_PARAM / OMN_i_SIZE.
#define STRELA_V1_IMN_ADDR_REG_OFFSET(i) (STRELA_V1_IMN_0_ADDR_REG_OFFSET + 8u * (i))
#define STRELA_V1_IMN_PARAM_REG_OFFSET(i) (STRELA_V1_IMN_0_PARAM_REG_OFFSET + 8u * (i))
#define STRELA_V1_OMN_ADDR_REG_OFFSET(i) (STRELA_V1_OMN_0_ADDR_REG_OFFSET + 8u * (i))
#define STRELA_V1_OMN_SIZE_REG_OFFSET(i) (STRELA_V1_OMN_0_SIZE_REG_OFFSET + 8u * (i))

// IMN_i_PARAM for `count` 32-bit elements read `stride` bytes apart. The node
// stops once its byte offset reaches the size field, so that field is the
// span stride * count rather than a byte count, and both it and the stride are
// 16 bits: one stream can reach at most 64 KiB past its base address. The
// stride must be non-zero. A node given count 0 finishes as soon as execution
// starts, so unused nodes need no programming beyond CLR_PARAM.
static inline uint32_t strela_v1_imn_param(uint32_t count, uint32_t stride) {
	return stride << 16 | (stride * count);
}

// OMN_i_SIZE for `count` 32-bit elements, written contiguously.
static inline uint32_t strela_v1_omn_size(uint32_t count) {
	return count * 4u;
}

// PE accessors on a STRELA v1 bitstream (no router words).
static inline uint32_t strela_v1_get_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_initial_value(conf_addr, pe_number, 0);
}

static inline void strela_v1_set_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_initial_value(conf_addr, pe_number, 0, value);
}

static inline uint32_t strela_v1_get_pe_const(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_const(conf_addr, pe_number, 0);
}

static inline void strela_v1_set_pe_const(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_const(conf_addr, pe_number, 0, value);
}

static inline uint32_t strela_v1_get_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number) {
	return strela_get_pe_delay_value(conf_addr, pe_number, 0);
}

static inline void strela_v1_set_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	strela_set_pe_delay_value(conf_addr, pe_number, 0, value);
}

#endif
