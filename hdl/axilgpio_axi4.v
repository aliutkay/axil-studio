////////////////////////////////////////////////////////////////////////////////
// Module: axilgpio_axi4
//
// ZipCPU axilgpio (hdl/axilgpio.v) with a full AXI4 slave port and 32-bit addresses, for
// tools whose bus definition is AXI4 and treat the ID / burst signals as required
// (Libero SmartDesign: bus definition AXI4, role slave). Registers: see axilgpio_a32.v.
//
// Single beats only: the master must send AWLEN = ARLEN = 0 with 4-byte SIZE (cmd_seq_axi4,
// a CPU doing 32-bit register accesses, or CoreAXI4Interconnect). AWSIZE / AWBURST / LOCK /
// CACHE / QOS / REGION are accepted and ignored.
//
// IDs: a new write address is taken only after the previous write response, and a new read
// address only after the previous read data, so the ID of every response is the ID of its
// request (one ID register per direction).
//
// Files: axilgpio_axi4.v, axilgpio.v, skidbuffer.v (Verilog-2001).
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module axilgpio_axi4 #(
    parameter [0:0] OPT_SKIDBUFFER = 1'b1,
    parameter [0:0] OPT_LOWPOWER = 1'b0,
    parameter NOUT = 30,
    parameter NIN = 5,
    parameter [NOUT-1:0] DEFAULT_OUTPUT = 0,
    parameter ID_WIDTH = 1
) (
    input  wire                            S_AXI_ACLK,
    input  wire                            S_AXI_ARESETN,
    input  wire [ID_WIDTH-1:0]             S_AXI_AWID,
    input  wire [31:0]                     S_AXI_AWADDR,
    input  wire [7:0]                      S_AXI_AWLEN,
    input  wire [2:0]                      S_AXI_AWSIZE,
    input  wire [1:0]                      S_AXI_AWBURST,
    input  wire                            S_AXI_AWLOCK,
    input  wire [3:0]                      S_AXI_AWCACHE,
    input  wire [2:0]                      S_AXI_AWPROT,
    input  wire [3:0]                      S_AXI_AWQOS,
    input  wire [3:0]                      S_AXI_AWREGION,
    input  wire                            S_AXI_AWVALID,
    output wire                            S_AXI_AWREADY,
    input  wire [31:0]                     S_AXI_WDATA,
    input  wire [3:0]                      S_AXI_WSTRB,
    input  wire                            S_AXI_WLAST,
    input  wire                            S_AXI_WVALID,
    output wire                            S_AXI_WREADY,
    output wire [ID_WIDTH-1:0]             S_AXI_BID,
    output wire [1:0]                      S_AXI_BRESP,
    output wire                            S_AXI_BVALID,
    input  wire                            S_AXI_BREADY,
    input  wire [ID_WIDTH-1:0]             S_AXI_ARID,
    input  wire [31:0]                     S_AXI_ARADDR,
    input  wire [7:0]                      S_AXI_ARLEN,
    input  wire [2:0]                      S_AXI_ARSIZE,
    input  wire [1:0]                      S_AXI_ARBURST,
    input  wire                            S_AXI_ARLOCK,
    input  wire [3:0]                      S_AXI_ARCACHE,
    input  wire [2:0]                      S_AXI_ARPROT,
    input  wire [3:0]                      S_AXI_ARQOS,
    input  wire [3:0]                      S_AXI_ARREGION,
    input  wire                            S_AXI_ARVALID,
    output wire                            S_AXI_ARREADY,
    output wire [ID_WIDTH-1:0]             S_AXI_RID,
    output wire [31:0]                     S_AXI_RDATA,
    output wire [1:0]                      S_AXI_RRESP,
    output wire                            S_AXI_RLAST,
    output wire                            S_AXI_RVALID,
    input  wire                            S_AXI_RREADY,
    output wire [NOUT-1:0]                 o_gpio,
    input  wire [((NIN>0) ? (NIN-1):0):0]  i_gpio,
    output wire                            o_int
);

    // one write and one read in flight, so the response IDs are those of the requests
    reg                wr_busy, rd_busy;
    reg [ID_WIDTH-1:0] bid_q, rid_q;
    wire core_awready, core_arready;
    wire aw_take = S_AXI_AWVALID && !wr_busy && core_awready;
    wire ar_take = S_AXI_ARVALID && !rd_busy && core_arready;

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            wr_busy <= 1'b0;
            rd_busy <= 1'b0;
            bid_q   <= {ID_WIDTH{1'b0}};
            rid_q   <= {ID_WIDTH{1'b0}};
        end else begin
            if (aw_take) begin
                wr_busy <= 1'b1;
                bid_q   <= S_AXI_AWID;
            end else if (S_AXI_BVALID && S_AXI_BREADY)
                wr_busy <= 1'b0;
            if (ar_take) begin
                rd_busy <= 1'b1;
                rid_q   <= S_AXI_ARID;
            end else if (S_AXI_RVALID && S_AXI_RREADY)
                rd_busy <= 1'b0;
        end
    end

    assign S_AXI_AWREADY = core_awready && !wr_busy;
    assign S_AXI_ARREADY = core_arready && !rd_busy;
    assign S_AXI_BID     = bid_q;
    assign S_AXI_RID     = rid_q;
    assign S_AXI_RLAST   = 1'b1;

    axilgpio #(
        .C_AXI_ADDR_WIDTH(5),
        .OPT_SKIDBUFFER(OPT_SKIDBUFFER),
        .OPT_LOWPOWER(OPT_LOWPOWER),
        .NOUT(NOUT),
        .NIN(NIN),
        .DEFAULT_OUTPUT(DEFAULT_OUTPUT)
    ) u_axilgpio (
        .S_AXI_ACLK(S_AXI_ACLK),
        .S_AXI_ARESETN(S_AXI_ARESETN),
        .S_AXI_AWVALID(S_AXI_AWVALID && !wr_busy),
        .S_AXI_AWREADY(core_awready),
        .S_AXI_AWADDR(S_AXI_AWADDR[4:0]),
        .S_AXI_AWPROT(S_AXI_AWPROT),
        .S_AXI_WVALID(S_AXI_WVALID),
        .S_AXI_WREADY(S_AXI_WREADY),
        .S_AXI_WDATA(S_AXI_WDATA),
        .S_AXI_WSTRB(S_AXI_WSTRB),
        .S_AXI_BVALID(S_AXI_BVALID),
        .S_AXI_BREADY(S_AXI_BREADY),
        .S_AXI_BRESP(S_AXI_BRESP),
        .S_AXI_ARVALID(S_AXI_ARVALID && !rd_busy),
        .S_AXI_ARREADY(core_arready),
        .S_AXI_ARADDR(S_AXI_ARADDR[4:0]),
        .S_AXI_ARPROT(S_AXI_ARPROT),
        .S_AXI_RVALID(S_AXI_RVALID),
        .S_AXI_RREADY(S_AXI_RREADY),
        .S_AXI_RDATA(S_AXI_RDATA),
        .S_AXI_RRESP(S_AXI_RRESP),
        .o_gpio(o_gpio),
        .i_gpio(i_gpio),
        .o_int(o_int)
    );

    wire unused;
    assign unused = &{1'b0, S_AXI_AWADDR[31:5], S_AXI_ARADDR[31:5], S_AXI_AWLEN, S_AXI_AWSIZE,
                      S_AXI_AWBURST, S_AXI_AWLOCK, S_AXI_AWCACHE, S_AXI_AWQOS, S_AXI_AWREGION,
                      S_AXI_WLAST, S_AXI_ARLEN, S_AXI_ARSIZE, S_AXI_ARBURST, S_AXI_ARLOCK,
                      S_AXI_ARCACHE, S_AXI_ARQOS, S_AXI_ARREGION};

endmodule

`default_nettype wire
