////////////////////////////////////////////////////////////////////////////////
// Abstract cmd_seq_core for the formal proof of cmd_seq (top level).
//
// It has the ports of cmd_seq_core but no program: every output is free, except
// the bus request handshake, which follows exactly what core.sby proves about
// the real core (core_props in cmd_seq_core.v):
//   - one request at a time (write or read), held with a stable address / data
//     until done, or dropped (abandoned: bus timeout or RESET) at any time
//   - after a request ends, req stays low for at least one cycle
//   - the address is never in the own window
// The top-level proof therefore covers every program the real core can run.
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module cmd_seq_core_abs #(
    parameter [7:0] SELF_ID     = 8'h00,
    parameter       IMEM_AW     = 9,
    parameter       DMEM_AW     = 8,
    parameter       BUS_TIMEOUT = 1024
) (
    input  wire                 clk, rst,
    input  wire                 run, stop, reset, step, sys_halt, sys_run, err_halt,
    input  wire [IMEM_AW-1:0]   entry,
    input  wire [31:0]          timer,
    output wire [IMEM_AW-1:0]   imem_addr,
    input  wire [63:0]          imem_data,
    output wire [DMEM_AW-1:0]   d_addr,
    output wire                 d_we,
    output wire [31:0]          d_wdata,
    input  wire [31:0]          d_rdata,
    output reg                  wr_req,
    output reg                  rd_req,
    output reg  [31:0]          bus_addr,
    output reg  [31:0]          wr_data,
    input  wire                 wr_done,
    input  wire [1:0]           wr_resp,
    input  wire                 rd_done,
    input  wire [31:0]          rd_data,
    input  wire [1:0]           rd_resp,
    input  wire [7:0]           ev_in,
    output wire [7:0]           ev_out,
    output wire                 busy, done, err, paused,
    output wire [IMEM_AW-1:0]   pc_o,
    output wire [4:0]           flags_o,
    output wire [7:0]           err_code,
    output wire [IMEM_AW-1:0]   err_pc,
    output wire [31:0]          err_addr,
    output wire                 snap_p, irq_p, done_p, err_p,
    output wire [16*32-1:0]     regs_o
);
    localparam SEL_LSB = 16;
    (* anyseq *) wire [IMEM_AW-1:0] a_imem_addr;
    (* anyseq *) wire [DMEM_AW-1:0] a_d_addr;
    (* anyseq *) wire               a_d_we;
    (* anyseq *) wire [31:0]        a_d_wdata;
    (* anyseq *) wire [7:0]         a_ev_out;
    (* anyseq *) wire               a_busy, a_done, a_err, a_paused;
    (* anyseq *) wire [IMEM_AW-1:0] a_pc, a_err_pc;
    (* anyseq *) wire [4:0]         a_flags;
    (* anyseq *) wire [7:0]         a_err_code;
    (* anyseq *) wire [31:0]        a_err_addr;
    (* anyseq *) wire               a_snap, a_irq, a_done_p, a_err_p;
    (* anyseq *) wire [16*32-1:0]   a_regs;
    (* anyseq *) wire               a_start, a_write, a_drop;
    (* anyseq *) wire [31:0]        a_addr, a_data;

    assign imem_addr = a_imem_addr;
    assign d_addr = a_d_addr;
    assign d_we = a_d_we;
    assign d_wdata = a_d_wdata;
    assign ev_out = a_ev_out;
    assign busy = a_busy;
    assign done = a_done;
    assign err = a_err;
    assign paused = a_paused;
    assign pc_o = a_pc;
    assign flags_o = a_flags;
    assign err_code = a_err_code;
    assign err_pc = a_err_pc;
    assign err_addr = a_err_addr;
    assign snap_p = a_snap;
    assign irq_p = a_irq;
    assign done_p = a_done_p;
    assign err_p = a_err_p;
    assign regs_o = a_regs;

    initial begin wr_req = 0; rd_req = 0; bus_addr = 0; wr_data = 0; end

    // a new request can start in the cycle after the previous one ended (req is low
    // for at least one cycle in between); the real core waits at least two cycles
    always @(posedge clk) begin
        if (rst) begin
            wr_req <= 1'b0; rd_req <= 1'b0;
        end else if (wr_req) begin
            if (wr_done || a_drop) wr_req <= 1'b0;
        end else if (rd_req) begin
            if (rd_done || a_drop) rd_req <= 1'b0;
        end else if (a_start && a_addr[SEL_LSB +: 8] != SELF_ID) begin
            bus_addr <= a_addr;
            wr_data  <= a_data;
            if (a_write) wr_req <= 1'b1;
            else         rd_req <= 1'b1;
        end
    end
endmodule
`default_nettype wire
