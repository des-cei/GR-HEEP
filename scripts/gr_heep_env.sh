#!/usr/bin/env bash
# Copyright 2026 EPFL, Politecnico di Torino, and Universidad Politecnica de Madrid.
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Author: Daniel Vázquez
# Description: Source the GR-HEEP toolchain environment, then exec the command.
#
# `source /tools/env_x-heep.sh` ends in `conda activate core-v-mini-mcu`, which
# FAILS in a non-interactive shell ("Run 'conda init' before 'conda activate'")
# and makes the whole source return 1. Chaining it as
# `source /tools/env_x-heep.sh && make ...` therefore silently never runs make:
# the build looks like it failed instantly with an empty log. Loading conda's
# shell hook first is what fixes it, and folding all of it into this wrapper
# also keeps each step a single allowlistable command.
#
# Usage: scripts/gr_heep_env.sh <command> [args...]
#   e.g. scripts/gr_heep_env.sh make app PROJECT=strela_fft

set -e

# conda's shell hook must load before /tools/env_x-heep.sh runs `conda activate`.
for candidate in "$HOME/miniconda3" "$HOME/miniforge3" "$HOME/anaconda3"; do
  if [ -f "$candidate/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    source "$candidate/etc/profile.d/conda.sh"
    break
  fi
done
if ! command -v conda > /dev/null 2>&1 && [ -n "$CONDA_EXE" ]; then
  # shellcheck disable=SC1091
  source "$(dirname "$(dirname "$CONDA_EXE")")/etc/profile.d/conda.sh"
fi

# RISC-V toolchain, Verilator, Verible, OpenOCD, Vivado, and the conda env.
# It still exits non-zero on some hosts (Vivado settings64.sh is chatty), so it
# is deliberately not guarded by `set -e` semantics here.
# shellcheck disable=SC1091
source /tools/env_x-heep.sh || true

# Gurobi, for the elastic-cgra mapper. Absent on hosts where gurobipy is
# installed in the conda env instead, which is enough for `make map`.
if [ -f /tools/gurobi_env.sh ]; then
  # shellcheck disable=SC1091
  source /tools/gurobi_env.sh || true
fi

if [ "$CONDA_DEFAULT_ENV" != "core-v-mini-mcu" ]; then
  conda activate core-v-mini-mcu
fi

exec "$@"
