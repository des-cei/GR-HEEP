# Copyright 2026 EPFL
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Description: Turn an already implemented GR-HEEP Vivado project into an image
# that can be written to the Genesys2 on-board Quad SPI flash.
#
# This script is Genesys2-only (Micron N25Q256A, 32 MB, SPIx4), so it takes no
# board argument: it looks for the project FuseSoC leaves at
#   build/x-heep_systems_gr-heep_*/genesys2-vivado/*.xpr
# reopens the implemented design, rewrites the bitstream with the SPI
# configuration settings baked in and writes the flash image next to the
# original .bit, as <project>_flash.bit / <project>_flash.bin (+ .prm).
#
# Usage (see the `vivado-flash-bin` target in the top-level Makefile):
#   vivado -mode batch -source scripts/fpga/bitstream2flash.tcl

set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir .. ..]]

# Locate the Genesys2 project built by `make vivado-fpga FPGA_BOARD=genesys2`
set glob_pat [file join $repo_root build x-heep_systems_gr-heep_* genesys2-vivado *.xpr]
set projects [lsort [glob -nocomplain $glob_pat]]
if {[llength $projects] == 0} {
    error "No Genesys2 Vivado project found at $glob_pat.\
           Run 'make vivado-fpga FPGA_BOARD=genesys2' first."
}
set xpr [lindex $projects 0]
if {[llength $projects] > 1} {
    puts "WARNING: [llength $projects] Genesys2 projects found, using $xpr"
}

set out_dir [file dirname $xpr]
set base [file join $out_dir "[file rootname [file tail $xpr]]_flash"]

puts "### Opening $xpr"
open_project $xpr

# The flash image is generated from the implemented design, so implementation
# must have completed. Fail with a readable message instead of an open_run error.
set impl [get_runs impl_1]
if {[get_property PROGRESS $impl] ne "100%"} {
    error "Implementation run impl_1 is not complete\
           (PROGRESS [get_property PROGRESS $impl], STATUS '[get_property STATUS $impl]').\
           Run 'make vivado-fpga FPGA_BOARD=genesys2' first."
}
open_run impl_1

# Genesys2 configuration flash: Micron N25Q256A, 32 MB, quad SPI
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property CONFIG_MODE SPIx4 [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33 [current_design]

puts "### Writing ${base}.bit"
write_bitstream -force ${base}.bit

puts "### Writing ${base}.bin"
write_cfgmem -format bin -interface SPIx4 -size 32 \
    -loadbit "up 0x0 ${base}.bit" -file ${base}.bin -force

close_project

puts "### DONE! Flash image: ${base}.bin"
