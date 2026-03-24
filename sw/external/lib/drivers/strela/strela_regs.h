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

// STRELA performance counter: total cycles
#define STRELA_PERF_CTR_TOTAL_CYCLES_REG_OFFSET 0xc

// STRELA performance counter: descriptor fetching cycles
#define STRELA_PERF_CTR_TAB_CYCLES_REG_OFFSET 0x10

// STRELA performance counter: configuration cycles
#define STRELA_PERF_CTR_CONF_CYCLES_REG_OFFSET 0x14

// STRELA performance counter: stall cycles
#define STRELA_PERF_CTR_STALL_CYCLES_REG_OFFSET 0x18

// STRELA Input Stream Engine 0 table address register
#define STRELA_ISE_0_TAB_ADDR_REG_OFFSET 0x1c

// STRELA Input Stream Engine 1 table address register
#define STRELA_ISE_1_TAB_ADDR_REG_OFFSET 0x20

// STRELA Input Stream Engine 2 table address register
#define STRELA_ISE_2_TAB_ADDR_REG_OFFSET 0x24

// STRELA Input Stream Engine 3 table address register
#define STRELA_ISE_3_TAB_ADDR_REG_OFFSET 0x28

// STRELA Output Stream Engine 0 table address register
#define STRELA_OSE_0_TAB_ADDR_REG_OFFSET 0x2c

// STRELA Output Stream Engine 1 table address register
#define STRELA_OSE_1_TAB_ADDR_REG_OFFSET 0x30

// STRELA Output Stream Engine 2 table address register
#define STRELA_OSE_2_TAB_ADDR_REG_OFFSET 0x34

// STRELA Output Stream Engine 3 table address register
#define STRELA_OSE_3_TAB_ADDR_REG_OFFSET 0x38

#ifdef __cplusplus
}  // extern "C"
#endif
#endif  // _STRELA_REG_DEFS_
// End generated register defines for strela