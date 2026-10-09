// Copyright {{YEAR}} CEIMM-UPM
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// {{AUTHOR}}
//
// Minimal driver for TODO <what the IP does> (hw/vendor/{{ip}}).
// TODO: one paragraph on the programming model (what to write, in which order,
// what the result looks like, and any access restriction while busy).

#ifndef {{IP}}_H
#define {{IP}}_H

#include <stdint.h>

#include "gr_heep.h"
#include "{{ip}}_regs.h"

#define {{IP}}_REG(offset) \
  (*(volatile uint32_t *)({{IP}}_PERIPH_START_ADDRESS + (offset)))

// Start a run. Clears STATUS.DONE.
static inline void {{ip}}_start(void) {
  {{IP}}_REG({{IP}}_CTRL_REG_OFFSET) = 1u << {{IP}}_CTRL_START_BIT;
}

static inline int {{ip}}_done(void) {
  return ({{IP}}_REG({{IP}}_STATUS_REG_OFFSET) >> {{IP}}_STATUS_DONE_BIT) & 1u;
}

static inline int {{ip}}_busy(void) {
  return ({{IP}}_REG({{IP}}_STATUS_REG_OFFSET) >> {{IP}}_STATUS_BUSY_BIT) & 1u;
}

// Busy-wait for the end of the run.
static inline void {{ip}}_wait(void) {
  while (!{{ip}}_done())
    ;
}

// Clear STATUS.DONE, and with it the interrupt line (write 1 to clear).
static inline void {{ip}}_clear_done(void) {
  {{IP}}_REG({{IP}}_STATUS_REG_OFFSET) = 1u << {{IP}}_STATUS_DONE_BIT;
}

// intr_o follows STATUS.DONE while enabled.
static inline void {{ip}}_intr_enable(int enable) {
  {{IP}}_REG({{IP}}_INTR_EN_REG_OFFSET) = enable ? 1u << {{IP}}_INTR_EN_EN_BIT : 0u;
}

// TODO: load/store helpers for windows or configuration registers, and a
// {{ip}}_run() that does load + start + wait + store.

#endif  // {{IP}}_H
