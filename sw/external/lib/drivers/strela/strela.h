#ifndef STRELA_H
#define STRELA_H

#include <stdint.h>

/*************** Definitions ***************/

#define NPE 16
#define NROUTERS 4
#define CONFIG_SIZE (NPE * 5 + NROUTERS)
#define CONFIG_BYTES (CONFIG_SIZE * 4)

// OPCODES
#define IDLE_SE 0
#define FENCE_ISE 1
#define FENCE_OSE 1
#define FENCE_SE 2
#define TR_CONF_ISE 3
#define CFG_MEM_W_OSE 4
#define CFG_MEM_E_OSE 5
// RESERVED
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

static const uint8_t PE_POSITION[16] = {
    3, 7, 11, 15,
    2, 6, 10, 14,
    1, 5,  9, 13,
    0, 4,  8, 12
};

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
        .opcode  = (iters << 25u) | (mode << 24u) | (size << 14u) | (addr << 4u) | opcode,
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

/*************** Functions   ***************/

static inline uint32_t get_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number) {
	return conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+3];
}

static inline void set_pe_initial_value(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+3] = value;
}

static inline uint32_t get_pe_const(uint32_t *conf_addr, uint8_t pe_number) {
	return conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+4];
}

static inline void set_pe_const(uint32_t *conf_addr, uint8_t pe_number, uint32_t value) {
	conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+4] = value;
}

static inline uint32_t get_pe_delay_value(uint32_t *conf_addr, uint8_t pe_number) {
	return conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+2] >> 16;
}

static inline void set_pe_delay_value(uint32_t * conf_addr, uint8_t pe_number, uint32_t value) {
	conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+2] = 
		(conf_addr[PE_OFFSET[pe_number]+PE_POSITION[pe_number]*5+2] & 0x0000FFFF) | value << 16;
}

#endif
