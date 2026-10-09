// Copyright 2026 CEI-UPM
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Daniel Vazquez (daniel.vazquez@upm.es)
//
// Minimal driver for the CRYSTALS-Kyber NTT accelerator (hw/vendor/ntt_kyber).
//
// The polynomial lives inside the accelerator: COEFFS is a window of 256 int16_t
// coefficients, two per 32-bit word, readable and writable while STATUS.BUSY is
// clear. A run computes the forward NTT in place, in the same order as the
// reference poly_ntt(), without the final reduction: each output coefficient is
// bounded by 8q in absolute value and is left for software to normalise mod q.

#ifndef NTT_KYBER_H
#define NTT_KYBER_H

#include <stdint.h>

#include "gr_heep.h"
#include "ntt_kyber_regs.h"

#define NTT_KYBER_N 256

#define NTT_KYBER_REG(offset) \
  (*(volatile uint32_t *)(NTT_KYBER_PERIPH_START_ADDRESS + (offset)))

// The coefficient window, as an array of NTT_KYBER_N int16_t.
#define NTT_KYBER_COEFFS \
  ((volatile int16_t *)(NTT_KYBER_PERIPH_START_ADDRESS + NTT_KYBER_COEFFS_REG_OFFSET))

// Copy a polynomial into the accelerator (two coefficients per bus write).
static inline void ntt_kyber_load(const int16_t *poly) {
  volatile uint32_t *win =
      (volatile uint32_t *)(NTT_KYBER_PERIPH_START_ADDRESS + NTT_KYBER_COEFFS_REG_OFFSET);
  for (int i = 0; i < NTT_KYBER_N / 2; i++)
    win[i] = (uint16_t)poly[2 * i] | ((uint32_t)(uint16_t)poly[2 * i + 1] << 16);
}

// Copy the accelerator's polynomial out (two coefficients per bus read).
static inline void ntt_kyber_store(int16_t *poly) {
  volatile uint32_t *win =
      (volatile uint32_t *)(NTT_KYBER_PERIPH_START_ADDRESS + NTT_KYBER_COEFFS_REG_OFFSET);
  for (int i = 0; i < NTT_KYBER_N / 2; i++) {
    uint32_t w = win[i];
    poly[2 * i] = (int16_t)(w & 0xFFFF);
    poly[2 * i + 1] = (int16_t)(w >> 16);
  }
}

// Start a forward NTT. Clears STATUS.DONE.
static inline void ntt_kyber_start(void) {
  NTT_KYBER_REG(NTT_KYBER_CTRL_REG_OFFSET) = 1u << NTT_KYBER_CTRL_START_BIT;
}

static inline int ntt_kyber_done(void) {
  return (NTT_KYBER_REG(NTT_KYBER_STATUS_REG_OFFSET) >> NTT_KYBER_STATUS_DONE_BIT) & 1u;
}

static inline int ntt_kyber_busy(void) {
  return (NTT_KYBER_REG(NTT_KYBER_STATUS_REG_OFFSET) >> NTT_KYBER_STATUS_BUSY_BIT) & 1u;
}

// Busy-wait for the end of the run.
static inline void ntt_kyber_wait(void) {
  while (!ntt_kyber_done())
    ;
}

// Clear STATUS.DONE, and with it the interrupt line (write 1 to clear).
static inline void ntt_kyber_clear_done(void) {
  NTT_KYBER_REG(NTT_KYBER_STATUS_REG_OFFSET) = 1u << NTT_KYBER_STATUS_DONE_BIT;
}

// intr_o follows STATUS.DONE while enabled.
static inline void ntt_kyber_intr_enable(int enable) {
  NTT_KYBER_REG(NTT_KYBER_INTR_EN_REG_OFFSET) = enable ? 1u << NTT_KYBER_INTR_EN_EN_BIT : 0u;
}

// Load, run and read back one polynomial, polling for completion.
static inline void ntt_kyber_run(int16_t *poly) {
  ntt_kyber_load(poly);
  ntt_kyber_start();
  ntt_kyber_wait();
  ntt_kyber_store(poly);
}

#endif  // NTT_KYBER_H
