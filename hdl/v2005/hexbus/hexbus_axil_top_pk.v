////////////////////////////////////////////////////////////////////////////////
// Module: hexbus_axil_top_pk
//
// hexbus_axil_top with packed AXI4-Lite ports (one request and one response vector).
// All other ports and parameters are passed through unchanged.
//
//   MASTER_AXI_REQ / MASTER_AXI_RSP : master interface (M_AXI_*), 32-bit
//
// Packed layout:
//   REQ [110:0] = AWADDR[110:79] AWPROT[78:76] AWVALID[75] WDATA[74:43] WSTRB[42:39] WVALID[38]
//                 BREADY[37] ARADDR[36:5] ARPROT[4:2] ARVALID[1] RREADY[0]
//   RSP [40:0] = AWREADY[40] WREADY[39] BRESP[38:37] BVALID[36] ARREADY[35] RDATA[34:3]
//                RRESP[2:1] RVALID[0]
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module hexbus_axil_top_pk #(
    parameter integer CLK_FREQ_HZ = 50_000_000,
    parameter integer BAUD_RATE = 115_200,
    parameter integer C_AXI_ADDR_WIDTH = 32
) (
    input  wire S_AXI_ACLK,
    input  wire S_AXI_ARESETN,
    input  wire i_uart_rx,
    output wire o_uart_tx,
    input  wire i_interrupt,
    output wire [7:0] M_AXI_AWLEN,
    output wire [2:0] M_AXI_AWSIZE,
    output wire [1:0] M_AXI_AWBURST,
    output wire M_AXI_WLAST,
    output wire [7:0] M_AXI_ARLEN,
    output wire [2:0] M_AXI_ARSIZE,
    output wire [1:0] M_AXI_ARBURST,
    output wire [110:0] MASTER_AXI_REQ,
    input  wire [40:0] MASTER_AXI_RSP
);

    // MASTER_AXI: packed <-> core M_AXI_*
    wire [31:0] m_awaddr;
    wire [2:0] m_awprot;
    wire m_awvalid;
    wire [31:0] m_wdata;
    wire [3:0] m_wstrb;
    wire m_wvalid;
    wire m_bready;
    wire [31:0] m_araddr;
    wire [2:0] m_arprot;
    wire m_arvalid;
    wire m_rready;
    wire m_awready;
    wire m_wready;
    wire [1:0] m_bresp;
    wire m_bvalid;
    wire m_arready;
    wire [31:0] m_rdata;
    wire [1:0] m_rresp;
    wire m_rvalid;
    assign MASTER_AXI_REQ = {m_awaddr, m_awprot, m_awvalid, m_wdata, m_wstrb, m_wvalid, m_bready, m_araddr, m_arprot, m_arvalid, m_rready};
    assign {m_awready, m_wready, m_bresp, m_bvalid, m_arready, m_rdata, m_rresp, m_rvalid} = MASTER_AXI_RSP;
    wire [C_AXI_ADDR_WIDTH-1:0] M_AXI_AWADDR;
    assign m_awaddr = M_AXI_AWADDR;
    wire [2:0] M_AXI_AWPROT;
    assign m_awprot = M_AXI_AWPROT;
    wire M_AXI_AWVALID;
    assign m_awvalid = M_AXI_AWVALID;
    wire [31:0] M_AXI_WDATA;
    assign m_wdata = M_AXI_WDATA;
    wire [3:0] M_AXI_WSTRB;
    assign m_wstrb = M_AXI_WSTRB;
    wire M_AXI_WVALID;
    assign m_wvalid = M_AXI_WVALID;
    wire M_AXI_BREADY;
    assign m_bready = M_AXI_BREADY;
    wire [C_AXI_ADDR_WIDTH-1:0] M_AXI_ARADDR;
    assign m_araddr = M_AXI_ARADDR;
    wire [2:0] M_AXI_ARPROT;
    assign m_arprot = M_AXI_ARPROT;
    wire M_AXI_ARVALID;
    assign m_arvalid = M_AXI_ARVALID;
    wire M_AXI_RREADY;
    assign m_rready = M_AXI_RREADY;
    wire M_AXI_AWREADY;
    assign M_AXI_AWREADY = m_awready;
    wire M_AXI_WREADY;
    assign M_AXI_WREADY = m_wready;
    wire [1:0] M_AXI_BRESP;
    assign M_AXI_BRESP = m_bresp;
    wire M_AXI_BVALID;
    assign M_AXI_BVALID = m_bvalid;
    wire M_AXI_ARREADY;
    assign M_AXI_ARREADY = m_arready;
    wire [31:0] M_AXI_RDATA;
    assign M_AXI_RDATA = m_rdata;
    wire [1:0] M_AXI_RRESP;
    assign M_AXI_RRESP = m_rresp;
    wire M_AXI_RVALID;
    assign M_AXI_RVALID = m_rvalid;

    hexbus_axil_top #(
        .CLK_FREQ_HZ(CLK_FREQ_HZ),
        .BAUD_RATE(BAUD_RATE),
        .C_AXI_ADDR_WIDTH(C_AXI_ADDR_WIDTH)
    ) u_core (
        .S_AXI_ACLK(S_AXI_ACLK),
        .S_AXI_ARESETN(S_AXI_ARESETN),
        .i_uart_rx(i_uart_rx),
        .o_uart_tx(o_uart_tx),
        .i_interrupt(i_interrupt),
        .M_AXI_AWVALID(M_AXI_AWVALID),
        .M_AXI_AWREADY(M_AXI_AWREADY),
        .M_AXI_AWADDR(M_AXI_AWADDR),
        .M_AXI_AWPROT(M_AXI_AWPROT),
        .M_AXI_WVALID(M_AXI_WVALID),
        .M_AXI_WREADY(M_AXI_WREADY),
        .M_AXI_WDATA(M_AXI_WDATA),
        .M_AXI_WSTRB(M_AXI_WSTRB),
        .M_AXI_BVALID(M_AXI_BVALID),
        .M_AXI_BREADY(M_AXI_BREADY),
        .M_AXI_BRESP(M_AXI_BRESP),
        .M_AXI_ARVALID(M_AXI_ARVALID),
        .M_AXI_ARREADY(M_AXI_ARREADY),
        .M_AXI_ARADDR(M_AXI_ARADDR),
        .M_AXI_ARPROT(M_AXI_ARPROT),
        .M_AXI_RVALID(M_AXI_RVALID),
        .M_AXI_RREADY(M_AXI_RREADY),
        .M_AXI_RDATA(M_AXI_RDATA),
        .M_AXI_RRESP(M_AXI_RRESP),
        .M_AXI_AWLEN(M_AXI_AWLEN),
        .M_AXI_AWSIZE(M_AXI_AWSIZE),
        .M_AXI_AWBURST(M_AXI_AWBURST),
        .M_AXI_WLAST(M_AXI_WLAST),
        .M_AXI_ARLEN(M_AXI_ARLEN),
        .M_AXI_ARSIZE(M_AXI_ARSIZE),
        .M_AXI_ARBURST(M_AXI_ARBURST)
    );

endmodule

`default_nettype wire
