// Copyright {{YEAR}} CEIMM-UPM
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// {{AUTHOR}}
//
// {{ip}}: X-HEEP register-interface wrapper of TODO <what the IP does>.
//
// Register map (data/{{ip}}_regs.hjson):
//   0x000 CTRL.START   write 1 to start a run (ignored while busy)
//   0x004 STATUS       DONE (rw1c, sticky, cleared by START too), BUSY (ro)
//   0x008 INTR_EN.EN   intr_o = STATUS.DONE & INTR_EN.EN (level)
//   TODO further registers / windows

module {{ip}} (
    // Clock and reset
    input logic clk_i,
    input logic rst_ni,

    // MMIO interface
    input  xheep_reg_pkg::xheep_reg_req_t reg_req_i,
    output xheep_reg_pkg::xheep_reg_rsp_t reg_rsp_o,

    // TODO class C only: master ports
    // output xheep_obi_pkg::xheep_obi_req_t [{{ip}}_pkg::NumMasters-1:0] masters_req_o,
    // input  xheep_obi_pkg::xheep_obi_rsp_t [{{ip}}_pkg::NumMasters-1:0] masters_resp_i,

    // Interrupt
    output logic intr_o
);

  import {{ip}}_reg_pkg::*;

  {{ip}}_reg2hw_t reg2hw;
  {{ip}}_hw2reg_t hw2reg;

  logic start, done, busy;

  // --- Optional memory window (interfaces.md section 1.5) -------------------
  // Uncomment together with the window in data/{{ip}}_regs.hjson and the two
  // window ports of the reg_top below. Assumes a memory of MEM_WORDS 32-bit words
  // with a byte-enabled write port and a registered (1-cycle) read port that the
  // bus may use while the core is idle.
  //
  // localparam int unsigned MEM_WORDS = 256;  // = items in the hjson window
  //
  // xheep_reg_pkg::xheep_reg_req_t win_req;
  // xheep_reg_pkg::xheep_reg_rsp_t win_rsp;
  // logic [$clog2(MEM_WORDS)-1:0] mem_addr;
  // logic [3:0] mem_be;
  // logic [31:0] mem_wdata, mem_rdata;
  // logic rd_pending_q;
  //
  // // Word index from the low bits of the bus address.
  // assign mem_addr  = win_req.addr[$clog2(MEM_WORDS)+1:2];
  // assign mem_wdata = win_req.wdata;
  //
  // always_comb begin
  //   mem_be        = '0;
  //   win_rsp.ready = 1'b0;
  //   win_rsp.error = 1'b0;
  //   win_rsp.rdata = mem_rdata;
  //   if (win_req.valid) begin
  //     if (busy) begin
  //       // The core owns the memory: answer with a bus error, drop the access.
  //       win_rsp.ready = 1'b1;
  //       win_rsp.error = 1'b1;
  //     end else if (win_req.write) begin
  //       // Writes complete in the request cycle; honour the byte strobes.
  //       mem_be        = win_req.wstrb;
  //       win_rsp.ready = 1'b1;
  //     end else begin
  //       // Reads: address in the first cycle, data in the second.
  //       win_rsp.ready = rd_pending_q;
  //     end
  //   end
  // end
  //
  // always_ff @(posedge clk_i or negedge rst_ni) begin
  //   if (!rst_ni) rd_pending_q <= 1'b0;
  //   else rd_pending_q <= win_req.valid & ~win_req.write & ~busy & ~rd_pending_q;
  // end
  //
  // Connect mem_addr / mem_be / mem_wdata / mem_rdata to the memory's bus-side
  // port (or to a mux in front of a single port, selected by busy).
  // ---------------------------------------------------------------------------

  // Register block.
  {{ip}}_reg_top #(
      .reg_req_t(xheep_reg_pkg::xheep_reg_req_t),
      .reg_rsp_t(xheep_reg_pkg::xheep_reg_rsp_t)
  ) {{ip}}_reg_top_i (
      .clk_i,
      .rst_ni,
      .reg_req_i,
      .reg_rsp_o,
      // .reg_req_win_o(win_req),  // only with a window
      // .reg_rsp_win_i(win_rsp),
      .reg2hw,
      .hw2reg,
      .devmode_i(1'b1)
  );

  // Control: a one-cycle pulse per write of 1 to CTRL.START, dropped while busy.
  assign start = reg2hw.ctrl.qe & reg2hw.ctrl.q & ~busy;

  // Status: DONE is set by the core's done pulse and cleared by START (or by
  // software, rw1c); BUSY follows the core.
  assign hw2reg.status.done.de = done | start;
  assign hw2reg.status.done.d = done;
  assign hw2reg.status.busy.de = 1'b1;
  assign hw2reg.status.busy.d = busy;

  // Interrupt (level)
  assign intr_o = reg2hw.status.done.q & reg2hw.intr_en.q;

  // TODO: the original core. Adapt its conventions here (reset polarity, level vs
  // pulse start, done pulse vs level) rather than inside the core. `done` must be a
  // one-cycle pulse at the end of a run; `busy` high while it runs.
  // {{ip}}_core {{ip}}_core_i (
  //     .clk_i,
  //     .rst_ni,
  //     .start_i(start),
  //     .done_o (done),
  //     .busy_o (busy)
  // );

endmodule
