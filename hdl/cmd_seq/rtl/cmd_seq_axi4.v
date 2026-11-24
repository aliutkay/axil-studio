////////////////////////////////////////////////////////////////////////////////
// Module: cmd_seq_axi4
//
// cmd_seq with full AXI4 signal sets on both ports, for tools whose bus definition
// is AXI4 and treat the burst / ID signals as required (Libero SmartDesign: bus
// definition AXI4, S_AXI = slave, M_AXI = master).
//
// Behaviour is that of cmd_seq (AXI4-Lite): every transfer is a single beat.
//
// M_AXI (master): single 32-bit beats. AWLEN/ARLEN = 0, AWSIZE/ARSIZE = 3'b010 (4 bytes),
//   AWBURST/ARBURST = INCR, WLAST = 1, ID = 0, LOCK / CACHE / QOS / REGION = 0.
//   BID, RID and RLAST are not needed (one transfer at a time) and are ignored.
//   This is legal AXI4: it can be connected to any AXI4 slave or interconnect port.
//
// S_AXI (slave): the ID of a request is returned with its response (BID / RID), RLAST = 1.
//   The block accepts one write and one read at a time, so one ID register per direction
//   is enough. The master above must send single beats (AWLEN = ARLEN = 0, 4-byte SIZE):
//   bursts are not supported. Behind CoreAXI4Interconnect this holds when the slave slot
//   of this block is set to AXI4-Lite, or when the masters only make single accesses
//   (a CPU doing 32-bit loads and stores to registers).
//   AWSIZE / AWBURST / AWLOCK / AWCACHE / AWQOS / AWREGION and the AR equivalents are
//   accepted and ignored.
//
// Files: cmd_seq_axi4.v, cmd_seq.v, cmd_seq_core.v (Verilog-2001).
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module cmd_seq_axi4 #(
    parameter [7:0] SELF_ID     = 8'h00,
    parameter       IMEM_AW     = 9,
    parameter       DMEM_AW     = 8,
    parameter       PT_TIMEOUT  = 1024,
    parameter       BUS_TIMEOUT = 1024,
    parameter       S_ID_WIDTH  = 1,       // ID width of the slave port (as the interconnect gives it)
    parameter       M_ID_WIDTH  = 1        // ID width of the master port
) (
    input  wire                    S_AXI_ACLK,
    input  wire                    S_AXI_ARESETN,

    // ---- slave port (upper interconnect / PC side)
    input  wire [S_ID_WIDTH-1:0]   S_AXI_AWID,
    input  wire [31:0]             S_AXI_AWADDR,
    input  wire [7:0]              S_AXI_AWLEN,
    input  wire [2:0]              S_AXI_AWSIZE,
    input  wire [1:0]              S_AXI_AWBURST,
    input  wire                    S_AXI_AWLOCK,
    input  wire [3:0]              S_AXI_AWCACHE,
    input  wire [2:0]              S_AXI_AWPROT,
    input  wire [3:0]              S_AXI_AWQOS,
    input  wire [3:0]              S_AXI_AWREGION,
    input  wire                    S_AXI_AWVALID,
    output wire                    S_AXI_AWREADY,
    input  wire [31:0]             S_AXI_WDATA,
    input  wire [3:0]              S_AXI_WSTRB,
    input  wire                    S_AXI_WLAST,
    input  wire                    S_AXI_WVALID,
    output wire                    S_AXI_WREADY,
    output wire [S_ID_WIDTH-1:0]   S_AXI_BID,
    output wire [1:0]              S_AXI_BRESP,
    output wire                    S_AXI_BVALID,
    input  wire                    S_AXI_BREADY,
    input  wire [S_ID_WIDTH-1:0]   S_AXI_ARID,
    input  wire [31:0]             S_AXI_ARADDR,
    input  wire [7:0]              S_AXI_ARLEN,
    input  wire [2:0]              S_AXI_ARSIZE,
    input  wire [1:0]              S_AXI_ARBURST,
    input  wire                    S_AXI_ARLOCK,
    input  wire [3:0]              S_AXI_ARCACHE,
    input  wire [2:0]              S_AXI_ARPROT,
    input  wire [3:0]              S_AXI_ARQOS,
    input  wire [3:0]              S_AXI_ARREGION,
    input  wire                    S_AXI_ARVALID,
    output wire                    S_AXI_ARREADY,
    output wire [S_ID_WIDTH-1:0]   S_AXI_RID,
    output wire [31:0]             S_AXI_RDATA,
    output wire [1:0]              S_AXI_RRESP,
    output wire                    S_AXI_RLAST,
    output wire                    S_AXI_RVALID,
    input  wire                    S_AXI_RREADY,

    // ---- master port (lower interconnect / slaves)
    output wire [M_ID_WIDTH-1:0]   M_AXI_AWID,
    output wire [31:0]             M_AXI_AWADDR,
    output wire [7:0]              M_AXI_AWLEN,
    output wire [2:0]              M_AXI_AWSIZE,
    output wire [1:0]              M_AXI_AWBURST,
    output wire                    M_AXI_AWLOCK,
    output wire [3:0]              M_AXI_AWCACHE,
    output wire [2:0]              M_AXI_AWPROT,
    output wire [3:0]              M_AXI_AWQOS,
    output wire [3:0]              M_AXI_AWREGION,
    output wire                    M_AXI_AWVALID,
    input  wire                    M_AXI_AWREADY,
    output wire [31:0]             M_AXI_WDATA,
    output wire [3:0]              M_AXI_WSTRB,
    output wire                    M_AXI_WLAST,
    output wire                    M_AXI_WVALID,
    input  wire                    M_AXI_WREADY,
    input  wire [M_ID_WIDTH-1:0]   M_AXI_BID,
    input  wire [1:0]              M_AXI_BRESP,
    input  wire                    M_AXI_BVALID,
    output wire                    M_AXI_BREADY,
    output wire [M_ID_WIDTH-1:0]   M_AXI_ARID,
    output wire [31:0]             M_AXI_ARADDR,
    output wire [7:0]              M_AXI_ARLEN,
    output wire [2:0]              M_AXI_ARSIZE,
    output wire [1:0]              M_AXI_ARBURST,
    output wire                    M_AXI_ARLOCK,
    output wire [3:0]              M_AXI_ARCACHE,
    output wire [2:0]              M_AXI_ARPROT,
    output wire [3:0]              M_AXI_ARQOS,
    output wire [3:0]              M_AXI_ARREGION,
    output wire                    M_AXI_ARVALID,
    input  wire                    M_AXI_ARREADY,
    input  wire [M_ID_WIDTH-1:0]   M_AXI_RID,
    input  wire [31:0]             M_AXI_RDATA,
    input  wire [1:0]              M_AXI_RRESP,
    input  wire                    M_AXI_RLAST,
    input  wire                    M_AXI_RVALID,
    output wire                    M_AXI_RREADY,

    // ---- sideband (see cmd_seq.v)
    input  wire                    i_sys_halt,
    input  wire                    i_sys_run,
    input  wire [7:0]              i_ev,
    output wire [7:0]              o_ev,
    output wire                    o_irq
);

    // ---- slave port: IDs of the request in progress (one write and one read at a time)
    reg [S_ID_WIDTH-1:0] bid_q, rid_q;
    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            bid_q <= {S_ID_WIDTH{1'b0}};
            rid_q <= {S_ID_WIDTH{1'b0}};
        end else begin
            if (S_AXI_AWVALID && S_AXI_AWREADY) bid_q <= S_AXI_AWID;
            if (S_AXI_ARVALID && S_AXI_ARREADY) rid_q <= S_AXI_ARID;
        end
    end
    assign S_AXI_BID   = bid_q;
    assign S_AXI_RID   = rid_q;
    assign S_AXI_RLAST = 1'b1;

    // ---- master port: single beats
    assign M_AXI_AWID     = {M_ID_WIDTH{1'b0}};
    assign M_AXI_AWLEN    = 8'd0;
    assign M_AXI_AWSIZE   = 3'b010;
    assign M_AXI_AWBURST  = 2'b01;
    assign M_AXI_AWLOCK   = 1'b0;
    assign M_AXI_AWCACHE  = 4'b0000;
    assign M_AXI_AWQOS    = 4'd0;
    assign M_AXI_AWREGION = 4'd0;
    assign M_AXI_WLAST    = 1'b1;
    assign M_AXI_ARID     = {M_ID_WIDTH{1'b0}};
    assign M_AXI_ARLEN    = 8'd0;
    assign M_AXI_ARSIZE   = 3'b010;
    assign M_AXI_ARBURST  = 2'b01;
    assign M_AXI_ARLOCK   = 1'b0;
    assign M_AXI_ARCACHE  = 4'b0000;
    assign M_AXI_ARQOS    = 4'd0;
    assign M_AXI_ARREGION = 4'd0;

    cmd_seq #(
        .SELF_ID(SELF_ID), .IMEM_AW(IMEM_AW), .DMEM_AW(DMEM_AW),
        .PT_TIMEOUT(PT_TIMEOUT), .BUS_TIMEOUT(BUS_TIMEOUT)
    ) u_cmd_seq (
        .S_AXI_ACLK(S_AXI_ACLK), .S_AXI_ARESETN(S_AXI_ARESETN),
        .S_AXI_AWVALID(S_AXI_AWVALID), .S_AXI_AWREADY(S_AXI_AWREADY),
        .S_AXI_AWADDR(S_AXI_AWADDR), .S_AXI_AWPROT(S_AXI_AWPROT),
        .S_AXI_WVALID(S_AXI_WVALID), .S_AXI_WREADY(S_AXI_WREADY),
        .S_AXI_WDATA(S_AXI_WDATA), .S_AXI_WSTRB(S_AXI_WSTRB),
        .S_AXI_BVALID(S_AXI_BVALID), .S_AXI_BREADY(S_AXI_BREADY), .S_AXI_BRESP(S_AXI_BRESP),
        .S_AXI_ARVALID(S_AXI_ARVALID), .S_AXI_ARREADY(S_AXI_ARREADY),
        .S_AXI_ARADDR(S_AXI_ARADDR), .S_AXI_ARPROT(S_AXI_ARPROT),
        .S_AXI_RVALID(S_AXI_RVALID), .S_AXI_RREADY(S_AXI_RREADY),
        .S_AXI_RDATA(S_AXI_RDATA), .S_AXI_RRESP(S_AXI_RRESP),
        .M_AXI_AWVALID(M_AXI_AWVALID), .M_AXI_AWREADY(M_AXI_AWREADY),
        .M_AXI_AWADDR(M_AXI_AWADDR), .M_AXI_AWPROT(M_AXI_AWPROT),
        .M_AXI_WVALID(M_AXI_WVALID), .M_AXI_WREADY(M_AXI_WREADY),
        .M_AXI_WDATA(M_AXI_WDATA), .M_AXI_WSTRB(M_AXI_WSTRB),
        .M_AXI_BVALID(M_AXI_BVALID), .M_AXI_BREADY(M_AXI_BREADY), .M_AXI_BRESP(M_AXI_BRESP),
        .M_AXI_ARVALID(M_AXI_ARVALID), .M_AXI_ARREADY(M_AXI_ARREADY),
        .M_AXI_ARADDR(M_AXI_ARADDR), .M_AXI_ARPROT(M_AXI_ARPROT),
        .M_AXI_RVALID(M_AXI_RVALID), .M_AXI_RREADY(M_AXI_RREADY),
        .M_AXI_RDATA(M_AXI_RDATA), .M_AXI_RRESP(M_AXI_RRESP),
        .i_sys_halt(i_sys_halt), .i_sys_run(i_sys_run),
        .i_ev(i_ev), .o_ev(o_ev), .o_irq(o_irq)
    );

    // inputs not needed for single-beat transfers
    wire unused;
    assign unused = &{1'b0, S_AXI_AWLEN, S_AXI_AWSIZE, S_AXI_AWBURST, S_AXI_AWLOCK, S_AXI_AWCACHE,
                      S_AXI_AWQOS, S_AXI_AWREGION, S_AXI_WLAST, S_AXI_ARLEN, S_AXI_ARSIZE,
                      S_AXI_ARBURST, S_AXI_ARLOCK, S_AXI_ARCACHE, S_AXI_ARQOS, S_AXI_ARREGION,
                      M_AXI_BID, M_AXI_RID, M_AXI_RLAST};

endmodule

`default_nettype wire
