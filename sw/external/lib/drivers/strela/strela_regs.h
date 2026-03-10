// Generated register defines for strela

// Copyright information found in source file:
// Copyright 2025 CEI-UPM

// Licensing information found in source file:
// 
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1

#ifndef _STRELA_REG_DEFS_
#define _STRELA_REG_DEFS_

#ifdef __cplusplus
extern "C" {
#endif
// Register width
#define STRELA_PARAM_REG_WIDTH 32

// STRELA control register
#define STRELA_CTRL_REG_OFFSET 0x0
#define STRELA_CTRL_START_BIT 0
#define STRELA_CTRL_CLR_BIT 1
#define STRELA_CTRL_CLR_PERF_CTR_BIT 2

// STRELA mode register
#define STRELA_MODE_REG_OFFSET 0x4
#define STRELA_MODE_INTR_EN_BIT 0
#define STRELA_MODE_PERF_CTR_EN_BIT 1

// STRELA status register
#define STRELA_STATUS_REG_OFFSET 0x8
#define STRELA_STATUS_DONE_BIT 0

// Clock-gates the STRELA memories
#define STRELA_MEMS_CLK_GATE_REG_OFFSET 0xc
#define STRELA_MEMS_CLK_GATE_MEMS_CLK_GATE_MASK 0xff
#define STRELA_MEMS_CLK_GATE_MEMS_CLK_GATE_OFFSET 0
#define STRELA_MEMS_CLK_GATE_MEMS_CLK_GATE_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_CLK_GATE_MEMS_CLK_GATE_MASK, .index = STRELA_MEMS_CLK_GATE_MEMS_CLK_GATE_OFFSET })

// Used by the memory switches to ack STRELA
#define STRELA_MEMS_POWER_GATE_ACK_REG_OFFSET 0x10
#define STRELA_MEMS_POWER_GATE_ACK_MEMS_POWER_GATE_ACK_MASK 0xff
#define STRELA_MEMS_POWER_GATE_ACK_MEMS_POWER_GATE_ACK_OFFSET 0
#define STRELA_MEMS_POWER_GATE_ACK_MEMS_POWER_GATE_ACK_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_POWER_GATE_ACK_MEMS_POWER_GATE_ACK_MASK, .index = STRELA_MEMS_POWER_GATE_ACK_MEMS_POWER_GATE_ACK_OFFSET })

// Switch off the memories domain
#define STRELA_MEMS_POWER_GATE_SWITCH_REG_OFFSET 0x14
#define STRELA_MEMS_POWER_GATE_SWITCH_MEMS_POWER_GATE_SWITCH_MASK 0xff
#define STRELA_MEMS_POWER_GATE_SWITCH_MEMS_POWER_GATE_SWITCH_OFFSET 0
#define STRELA_MEMS_POWER_GATE_SWITCH_MEMS_POWER_GATE_SWITCH_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_POWER_GATE_SWITCH_MEMS_POWER_GATE_SWITCH_MASK, .index = STRELA_MEMS_POWER_GATE_SWITCH_MEMS_POWER_GATE_SWITCH_OFFSET })

// Wait for the memories domain switch ack
#define STRELA_MEMS_WAIT_ACK_SWITCH_ON_REG_OFFSET 0x18
#define STRELA_MEMS_WAIT_ACK_SWITCH_ON_MEMS_WAIT_ACK_SWITCH_ON_MASK 0xff
#define STRELA_MEMS_WAIT_ACK_SWITCH_ON_MEMS_WAIT_ACK_SWITCH_ON_OFFSET 0
#define STRELA_MEMS_WAIT_ACK_SWITCH_ON_MEMS_WAIT_ACK_SWITCH_ON_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_WAIT_ACK_SWITCH_ON_MEMS_WAIT_ACK_SWITCH_ON_MASK, .index = STRELA_MEMS_WAIT_ACK_SWITCH_ON_MEMS_WAIT_ACK_SWITCH_ON_OFFSET })

// Set on the isolation of the memories domain
#define STRELA_MEMS_ISO_REG_OFFSET 0x1c
#define STRELA_MEMS_ISO_MEMS_ISO_MASK 0xff
#define STRELA_MEMS_ISO_MEMS_ISO_OFFSET 0
#define STRELA_MEMS_ISO_MEMS_ISO_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_ISO_MEMS_ISO_MASK, .index = STRELA_MEMS_ISO_MEMS_ISO_OFFSET })

// Set on retentive mode for the memories domain
#define STRELA_MEMS_RETENTIVE_REG_OFFSET 0x20
#define STRELA_MEMS_RETENTIVE_MEMS_RETENTIVE_MASK 0xff
#define STRELA_MEMS_RETENTIVE_MEMS_RETENTIVE_OFFSET 0
#define STRELA_MEMS_RETENTIVE_MEMS_RETENTIVE_FIELD \
  ((bitfield_field32_t) { .mask = STRELA_MEMS_RETENTIVE_MEMS_RETENTIVE_MASK, .index = STRELA_MEMS_RETENTIVE_MEMS_RETENTIVE_OFFSET })

// STRELA performance counter: total cycles
#define STRELA_PERF_CTR_TOTAL_CYCLES_REG_OFFSET 0x24

// STRELA performance counter: descriptor fetching cycles
#define STRELA_PERF_CTR_TAB_CYCLES_REG_OFFSET 0x28

// STRELA performance counter: configuration cycles
#define STRELA_PERF_CTR_CONF_CYCLES_REG_OFFSET 0x2c

// STRELA performance counter: stall cycles
#define STRELA_PERF_CTR_STALL_CYCLES_REG_OFFSET 0x30

// STRELA Input Stream Engine 0 table address register
#define STRELA_ISE_0_TAB_ADDR_REG_OFFSET 0x34

// STRELA Input Stream Engine 1 table address register
#define STRELA_ISE_1_TAB_ADDR_REG_OFFSET 0x38

// STRELA Input Stream Engine 2 table address register
#define STRELA_ISE_2_TAB_ADDR_REG_OFFSET 0x3c

// STRELA Input Stream Engine 3 table address register
#define STRELA_ISE_3_TAB_ADDR_REG_OFFSET 0x40

// STRELA Output Stream Engine 0 table address register
#define STRELA_OSE_0_TAB_ADDR_REG_OFFSET 0x44

// STRELA Output Stream Engine 1 table address register
#define STRELA_OSE_1_TAB_ADDR_REG_OFFSET 0x48

// STRELA Output Stream Engine 2 table address register
#define STRELA_OSE_2_TAB_ADDR_REG_OFFSET 0x4c

// STRELA Output Stream Engine 3 table address register
#define STRELA_OSE_3_TAB_ADDR_REG_OFFSET 0x50

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _STRELA_REG_DEFS_
// End generated register defines for strela