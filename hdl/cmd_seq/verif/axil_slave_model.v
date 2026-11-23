// Downstream AXI4-Lite slave for the cmd_seq testbenches (not synthesizable).
//
// IP select = addr[23:16] (addr[31:24] ignored), word = addr[7:2]:
//   1..4  RAM, 64 words each, initial value init_word(ip, i) (same as seqmodel.py)
//   0x0C  hung: accepts the address, never responds
//   0x0D  slow: like 0x0E but answers OKAY after SLOW_LAT cycles (writes go to RAM 1)
//   0x0E  SLVERR, read data 0xBAD0_0000 | addr[15:0]
//   other DECERR, read data 0xDEC0_0000 | addr[15:0]
// READY / VALID come after random delays (0..MAXD cycles) when MAXD > 0.
`timescale 1ns/1ps
`default_nettype none
module axil_slave_model #(
    parameter MAXD     = 3,
    parameter SLOW_LAT = 200,
    parameter SEED     = 1
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        AWVALID, output reg AWREADY, input wire [31:0] AWADDR, input wire [2:0] AWPROT,
    input  wire        WVALID,  output reg WREADY,  input wire [31:0] WDATA,  input wire [3:0] WSTRB,
    output reg         BVALID,  input wire BREADY,  output reg [1:0] BRESP,
    input  wire        ARVALID, output reg ARREADY, input wire [31:0] ARADDR, input wire [2:0] ARPROT,
    output reg         RVALID,  input wire RREADY,  output reg [31:0] RDATA,  output reg [1:0] RRESP
);
    reg [31:0] ram [0:4][0:63];
    integer seed = SEED;
    integer log_fd = 0;          // set by the testbench: core transfers are logged here
    integer wr_count = 0, rd_count = 0;
    reg [2:0]  last_awprot, last_arprot;
    reg [3:0]  last_wstrb;
    reg [31:0] last_awaddr, last_wdata, last_araddr;

    function [31:0] init_word(input integer ip, input integer i);
        reg [63:0] t;
        begin
            t = (32'hA5000000 | (ip << 16) | (i << 8) | (i ^ ip)) * 64'h9E3779B1;
            init_word = t[31:0];
        end
    endfunction

    integer a, b;
    initial begin
        for (a = 0; a <= 4; a = a + 1)
            for (b = 0; b < 64; b = b + 1)
                ram[a][b] = init_word(a, b);
        AWREADY = 0; WREADY = 0; BVALID = 0; BRESP = 0;
        ARREADY = 0; RVALID = 0; RDATA = 0; RRESP = 0;
    end

    function integer rnd(input integer max);
        begin
            if (max <= 0) rnd = 0;
            else          rnd = {$random(seed)} % (max + 1);
        end
    endfunction

    // ---------------------------------------------------------------- write
    reg        have_aw, have_w;
    reg [31:0] aw_q, w_q;
    reg [3:0]  s_q;
    integer    wdelay;
    initial begin have_aw = 0; have_w = 0; wdelay = 0; end

    wire [7:0] wip = aw_q[23:16];
    always @(posedge clk) begin
        if (rst) begin
            AWREADY <= 0; WREADY <= 0; BVALID <= 0; have_aw <= 0; have_w <= 0;
        end else begin
            AWREADY <= !have_aw && !AWREADY && AWVALID && rnd(MAXD) == 0;
            WREADY  <= !have_w  && !WREADY  && WVALID  && rnd(MAXD) == 0;
            if (AWVALID && AWREADY) begin
                have_aw <= 1; aw_q <= AWADDR; AWREADY <= 0;
                last_awaddr <= AWADDR; last_awprot <= AWPROT;
            end
            if (WVALID && WREADY) begin
                have_w <= 1; w_q <= WDATA; s_q <= WSTRB; WREADY <= 0;
                last_wdata <= WDATA; last_wstrb <= WSTRB;
            end
            if (have_aw && have_w && !BVALID) begin
                if (wdelay == 0)
                    wdelay = (wip == 8'h0D) ? SLOW_LAT : 1 + rnd(MAXD);
                if (wip == 8'h0C)
                    ;                                       // hung
                else if (wdelay == 1) begin
                    wdelay = 0;
                    BVALID <= 1;
                    if (wip >= 1 && wip <= 4 || wip == 8'h0D) begin
                        BRESP <= 2'b00;
                        for (a = 0; a < 4; a = a + 1)
                            if (s_q[a])
                                ram[(wip == 8'h0D) ? 1 : wip][aw_q[7:2]][8*a +: 8] = w_q[8*a +: 8];
                    end else
                        BRESP <= (wip == 8'h0E) ? 2'b10 : 2'b11;
                    wr_count = wr_count + 1;
                    if (log_fd != 0 && wip != 8'h04)
                        $fdisplay(log_fd, "log W %h %h %0d", aw_q, w_q,
                                  (wip >= 1 && wip <= 4) ? 0 : (wip == 8'h0E) ? 2 : 3);
                end else
                    wdelay = wdelay - 1;
            end
            if (BVALID && BREADY) begin
                BVALID <= 0; have_aw <= 0; have_w <= 0;
            end
        end
    end

    // ---------------------------------------------------------------- read
    reg        have_ar;
    reg [31:0] ar_q;
    integer    rdelay;
    initial begin have_ar = 0; rdelay = 0; end
    wire [7:0] rip = ar_q[23:16];

    always @(posedge clk) begin
        if (rst) begin
            ARREADY <= 0; RVALID <= 0; have_ar <= 0;
        end else begin
            ARREADY <= !have_ar && !ARREADY && ARVALID && rnd(MAXD) == 0;
            if (ARVALID && ARREADY) begin
                have_ar <= 1; ar_q <= ARADDR; ARREADY <= 0;
                last_araddr <= ARADDR; last_arprot <= ARPROT;
            end
            if (have_ar && !RVALID) begin
                if (rdelay == 0)
                    rdelay = (rip == 8'h0D) ? SLOW_LAT : 1 + rnd(MAXD);
                if (rip == 8'h0C)
                    ;
                else if (rdelay == 1) begin
                    rdelay = 0;
                    RVALID <= 1;
                    if (rip >= 1 && rip <= 4 || rip == 8'h0D) begin
                        RDATA <= ram[(rip == 8'h0D) ? 1 : rip][ar_q[7:2]]; RRESP <= 2'b00;
                    end else if (rip == 8'h0E) begin
                        RDATA <= 32'hBAD00000 | ar_q[15:0]; RRESP <= 2'b10;
                    end else begin
                        RDATA <= 32'hDEC00000 | ar_q[15:0]; RRESP <= 2'b11;
                    end
                    rd_count = rd_count + 1;
                    if (log_fd != 0 && rip != 8'h04)
                        $fdisplay(log_fd, "log R %h %h %0d", ar_q,
                                  (rip >= 1 && rip <= 4) ? ram[rip][ar_q[7:2]] :
                                  (rip == 8'h0E) ? (32'hBAD00000 | ar_q[15:0]) : (32'hDEC00000 | ar_q[15:0]),
                                  (rip >= 1 && rip <= 4) ? 0 : (rip == 8'h0E) ? 2 : 3);
                end else
                    rdelay = rdelay - 1;
            end
            if (RVALID && RREADY) begin
                RVALID <= 0; have_ar <= 0;
            end
        end
    end
endmodule
`default_nettype wire
