////////////////////////////////////////////////////////////////////////////////
// Module: only_ro_pk
//
// HDL Designer block: only_ro with packed AXI4-Lite ports (one request and one
// response vector per interface). The plain module is instantiated unchanged inside.
// All other ports are passed through unchanged.
//
// No parameters (no generics for a VHDL top, where 32-bit values would overflow an
// integer). The parameter values of the module are fixed here (regenerate with
// axilgen --pack FILE --set NAME=VALUE, or the AXIL Studio dialog, to change them):
//   C_AXI_ADDR_WIDTH = 32
//   C_AXI_DATA_WIDTH = 32
//   OPT_SKIDBUFFER = 1'h0
//   OPT_LOWPOWER = 1'h0
//
//   SLAVE_AXI_REQ / SLAVE_AXI_RSP : slave interface (S_AXI_*), 32-bit
//
// Packed layout:
//   REQ [110:0] = AWADDR[110:79] AWPROT[78:76] AWVALID[75] WDATA[74:43] WSTRB[42:39] WVALID[38]
//                 BREADY[37] ARADDR[36:5] ARPROT[4:2] ARVALID[1] RREADY[0]
//   RSP [40:0] = AWREADY[40] WREADY[39] BRESP[38:37] BVALID[36] ARREADY[35] RDATA[34:3]
//                RRESP[2:1] RVALID[0]
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module only_ro_pk (
    input  wire S_AXI_ACLK,
    input  wire S_AXI_ARESETN,
    input  wire [31:0] i_stat_v,
    input  wire [110:0] SLAVE_AXI_REQ,
    output wire [40:0] SLAVE_AXI_RSP
);

    // SLAVE_AXI: packed <-> core S_AXI_*
    wire [31:0] s_awaddr;
    wire [2:0] s_awprot;
    wire s_awvalid;
    wire [31:0] s_wdata;
    wire [3:0] s_wstrb;
    wire s_wvalid;
    wire s_bready;
    wire [31:0] s_araddr;
    wire [2:0] s_arprot;
    wire s_arvalid;
    wire s_rready;
    wire s_awready;
    wire s_wready;
    wire [1:0] s_bresp;
    wire s_bvalid;
    wire s_arready;
    wire [31:0] s_rdata;
    wire [1:0] s_rresp;
    wire s_rvalid;
    assign {s_awaddr, s_awprot, s_awvalid, s_wdata, s_wstrb, s_wvalid, s_bready, s_araddr, s_arprot, s_arvalid, s_rready} = SLAVE_AXI_REQ;
    assign SLAVE_AXI_RSP = {s_awready, s_wready, s_bresp, s_bvalid, s_arready, s_rdata, s_rresp, s_rvalid};
    wire [31:0] S_AXI_AWADDR;
    assign S_AXI_AWADDR = s_awaddr;
    wire [2:0] S_AXI_AWPROT;
    assign S_AXI_AWPROT = s_awprot;
    wire S_AXI_AWVALID;
    assign S_AXI_AWVALID = s_awvalid;
    wire [31:0] S_AXI_WDATA;
    assign S_AXI_WDATA = s_wdata;
    wire [3:0] S_AXI_WSTRB;
    assign S_AXI_WSTRB = s_wstrb;
    wire S_AXI_WVALID;
    assign S_AXI_WVALID = s_wvalid;
    wire S_AXI_BREADY;
    assign S_AXI_BREADY = s_bready;
    wire [31:0] S_AXI_ARADDR;
    assign S_AXI_ARADDR = s_araddr;
    wire [2:0] S_AXI_ARPROT;
    assign S_AXI_ARPROT = s_arprot;
    wire S_AXI_ARVALID;
    assign S_AXI_ARVALID = s_arvalid;
    wire S_AXI_RREADY;
    assign S_AXI_RREADY = s_rready;
    wire S_AXI_AWREADY;
    assign s_awready = S_AXI_AWREADY;
    wire S_AXI_WREADY;
    assign s_wready = S_AXI_WREADY;
    wire [1:0] S_AXI_BRESP;
    assign s_bresp = S_AXI_BRESP;
    wire S_AXI_BVALID;
    assign s_bvalid = S_AXI_BVALID;
    wire S_AXI_ARREADY;
    assign s_arready = S_AXI_ARREADY;
    wire [31:0] S_AXI_RDATA;
    assign s_rdata = S_AXI_RDATA;
    wire [1:0] S_AXI_RRESP;
    assign s_rresp = S_AXI_RRESP;
    wire S_AXI_RVALID;
    assign s_rvalid = S_AXI_RVALID;

    only_ro #(
        .C_AXI_ADDR_WIDTH(32),
        .C_AXI_DATA_WIDTH(32),
        .OPT_SKIDBUFFER(1'h0),
        .OPT_LOWPOWER(1'h0)
    ) u_core (
        .S_AXI_ACLK(S_AXI_ACLK),
        .S_AXI_ARESETN(S_AXI_ARESETN),
        .S_AXI_AWVALID(S_AXI_AWVALID),
        .S_AXI_AWREADY(S_AXI_AWREADY),
        .S_AXI_AWADDR(S_AXI_AWADDR),
        .S_AXI_AWPROT(S_AXI_AWPROT),
        .S_AXI_WVALID(S_AXI_WVALID),
        .S_AXI_WREADY(S_AXI_WREADY),
        .S_AXI_WDATA(S_AXI_WDATA),
        .S_AXI_WSTRB(S_AXI_WSTRB),
        .S_AXI_BVALID(S_AXI_BVALID),
        .S_AXI_BREADY(S_AXI_BREADY),
        .S_AXI_BRESP(S_AXI_BRESP),
        .S_AXI_ARVALID(S_AXI_ARVALID),
        .S_AXI_ARREADY(S_AXI_ARREADY),
        .S_AXI_ARADDR(S_AXI_ARADDR),
        .S_AXI_ARPROT(S_AXI_ARPROT),
        .S_AXI_RVALID(S_AXI_RVALID),
        .S_AXI_RREADY(S_AXI_RREADY),
        .S_AXI_RDATA(S_AXI_RDATA),
        .S_AXI_RRESP(S_AXI_RRESP),
        .i_stat_v(i_stat_v)
    );

endmodule

`default_nettype wire
