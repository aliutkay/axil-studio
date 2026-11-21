////////////////////////////////////////////////////////////////////////////////
// Module: xb_3x4
//
// AXI4-Lite crossbar, 3 master port(s) x 4 slave port(s): ZipCPU axilxbar,
// configured only through its parameters. This wrapper adds no logic; it gives
// every port its own named AXI4-Lite signals:
//   MASTER<i>_AXI_*  connect to master i (its M_AXI_*)
//   SLAVE<j>_AXI_*   connect to slave j  (its S_AXI_*)
// Slave j is selected when (address & SLAVE<j>_MASK) == SLAVE<j>_ADDR; any other
// address is answered with DECERR by axilxbar.
//
// 3 master port(s), 4 slave port(s). Address map
// (SLAVE<j>_ADDR / SLAVE<j>_MASK defaults):
//   Port     Base        Mask        Window
//   SLAVE0   0x40000000  0xFFFFF000     0x1000
//   SLAVE1   0x40001000  0xFFFFF000     0x1000
//   SLAVE2   0x80000000  0xFFFF0000    0x10000
//   SLAVE3   0x40100000  0xFFF00000   0x100000
//
// Requires axilxbar.v, addrdecode.v, skidbuffer.v (wb2axip, Apache 2.0,
// Gisselquist Technology LLC).
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module xb_3x4 #(
    parameter integer C_AXI_ADDR_WIDTH = 32,
    parameter integer C_AXI_DATA_WIDTH = 32,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE0_ADDR = 32'h4000_0000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE0_MASK = 32'hFFFF_F000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE1_ADDR = 32'h4000_1000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE1_MASK = 32'hFFFF_F000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE2_ADDR = 32'h8000_0000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE2_MASK = 32'hFFFF_0000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE3_ADDR = 32'h4010_0000,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE3_MASK = 32'hFFF0_0000,
    parameter [0:0]   OPT_LOWPOWER = 1'b1,
    parameter integer OPT_LINGER   = 4,
    parameter integer LGMAXBURST   = 5
) (
    input  wire ACLK,
    input  wire ARESETN,
    // Master port 0
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER0_AXI_AWADDR,
    input  wire [2:0]                     MASTER0_AXI_AWPROT,
    input  wire                           MASTER0_AXI_AWVALID,
    output wire                           MASTER0_AXI_AWREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    MASTER0_AXI_WDATA,
    input  wire [C_AXI_DATA_WIDTH/8-1:0]  MASTER0_AXI_WSTRB,
    input  wire                           MASTER0_AXI_WVALID,
    output wire                           MASTER0_AXI_WREADY,
    output wire [1:0]                     MASTER0_AXI_BRESP,
    output wire                           MASTER0_AXI_BVALID,
    input  wire                           MASTER0_AXI_BREADY,
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER0_AXI_ARADDR,
    input  wire [2:0]                     MASTER0_AXI_ARPROT,
    input  wire                           MASTER0_AXI_ARVALID,
    output wire                           MASTER0_AXI_ARREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    MASTER0_AXI_RDATA,
    output wire [1:0]                     MASTER0_AXI_RRESP,
    output wire                           MASTER0_AXI_RVALID,
    input  wire                           MASTER0_AXI_RREADY,
    // Master port 1
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER1_AXI_AWADDR,
    input  wire [2:0]                     MASTER1_AXI_AWPROT,
    input  wire                           MASTER1_AXI_AWVALID,
    output wire                           MASTER1_AXI_AWREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    MASTER1_AXI_WDATA,
    input  wire [C_AXI_DATA_WIDTH/8-1:0]  MASTER1_AXI_WSTRB,
    input  wire                           MASTER1_AXI_WVALID,
    output wire                           MASTER1_AXI_WREADY,
    output wire [1:0]                     MASTER1_AXI_BRESP,
    output wire                           MASTER1_AXI_BVALID,
    input  wire                           MASTER1_AXI_BREADY,
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER1_AXI_ARADDR,
    input  wire [2:0]                     MASTER1_AXI_ARPROT,
    input  wire                           MASTER1_AXI_ARVALID,
    output wire                           MASTER1_AXI_ARREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    MASTER1_AXI_RDATA,
    output wire [1:0]                     MASTER1_AXI_RRESP,
    output wire                           MASTER1_AXI_RVALID,
    input  wire                           MASTER1_AXI_RREADY,
    // Master port 2
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER2_AXI_AWADDR,
    input  wire [2:0]                     MASTER2_AXI_AWPROT,
    input  wire                           MASTER2_AXI_AWVALID,
    output wire                           MASTER2_AXI_AWREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    MASTER2_AXI_WDATA,
    input  wire [C_AXI_DATA_WIDTH/8-1:0]  MASTER2_AXI_WSTRB,
    input  wire                           MASTER2_AXI_WVALID,
    output wire                           MASTER2_AXI_WREADY,
    output wire [1:0]                     MASTER2_AXI_BRESP,
    output wire                           MASTER2_AXI_BVALID,
    input  wire                           MASTER2_AXI_BREADY,
    input  wire [C_AXI_ADDR_WIDTH-1:0]    MASTER2_AXI_ARADDR,
    input  wire [2:0]                     MASTER2_AXI_ARPROT,
    input  wire                           MASTER2_AXI_ARVALID,
    output wire                           MASTER2_AXI_ARREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    MASTER2_AXI_RDATA,
    output wire [1:0]                     MASTER2_AXI_RRESP,
    output wire                           MASTER2_AXI_RVALID,
    input  wire                           MASTER2_AXI_RREADY,
    // Slave port 0
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE0_AXI_AWADDR,
    output wire [2:0]                     SLAVE0_AXI_AWPROT,
    output wire                           SLAVE0_AXI_AWVALID,
    input  wire                           SLAVE0_AXI_AWREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    SLAVE0_AXI_WDATA,
    output wire [C_AXI_DATA_WIDTH/8-1:0]  SLAVE0_AXI_WSTRB,
    output wire                           SLAVE0_AXI_WVALID,
    input  wire                           SLAVE0_AXI_WREADY,
    input  wire [1:0]                     SLAVE0_AXI_BRESP,
    input  wire                           SLAVE0_AXI_BVALID,
    output wire                           SLAVE0_AXI_BREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE0_AXI_ARADDR,
    output wire [2:0]                     SLAVE0_AXI_ARPROT,
    output wire                           SLAVE0_AXI_ARVALID,
    input  wire                           SLAVE0_AXI_ARREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    SLAVE0_AXI_RDATA,
    input  wire [1:0]                     SLAVE0_AXI_RRESP,
    input  wire                           SLAVE0_AXI_RVALID,
    output wire                           SLAVE0_AXI_RREADY,
    // Slave port 1
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE1_AXI_AWADDR,
    output wire [2:0]                     SLAVE1_AXI_AWPROT,
    output wire                           SLAVE1_AXI_AWVALID,
    input  wire                           SLAVE1_AXI_AWREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    SLAVE1_AXI_WDATA,
    output wire [C_AXI_DATA_WIDTH/8-1:0]  SLAVE1_AXI_WSTRB,
    output wire                           SLAVE1_AXI_WVALID,
    input  wire                           SLAVE1_AXI_WREADY,
    input  wire [1:0]                     SLAVE1_AXI_BRESP,
    input  wire                           SLAVE1_AXI_BVALID,
    output wire                           SLAVE1_AXI_BREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE1_AXI_ARADDR,
    output wire [2:0]                     SLAVE1_AXI_ARPROT,
    output wire                           SLAVE1_AXI_ARVALID,
    input  wire                           SLAVE1_AXI_ARREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    SLAVE1_AXI_RDATA,
    input  wire [1:0]                     SLAVE1_AXI_RRESP,
    input  wire                           SLAVE1_AXI_RVALID,
    output wire                           SLAVE1_AXI_RREADY,
    // Slave port 2
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE2_AXI_AWADDR,
    output wire [2:0]                     SLAVE2_AXI_AWPROT,
    output wire                           SLAVE2_AXI_AWVALID,
    input  wire                           SLAVE2_AXI_AWREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    SLAVE2_AXI_WDATA,
    output wire [C_AXI_DATA_WIDTH/8-1:0]  SLAVE2_AXI_WSTRB,
    output wire                           SLAVE2_AXI_WVALID,
    input  wire                           SLAVE2_AXI_WREADY,
    input  wire [1:0]                     SLAVE2_AXI_BRESP,
    input  wire                           SLAVE2_AXI_BVALID,
    output wire                           SLAVE2_AXI_BREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE2_AXI_ARADDR,
    output wire [2:0]                     SLAVE2_AXI_ARPROT,
    output wire                           SLAVE2_AXI_ARVALID,
    input  wire                           SLAVE2_AXI_ARREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    SLAVE2_AXI_RDATA,
    input  wire [1:0]                     SLAVE2_AXI_RRESP,
    input  wire                           SLAVE2_AXI_RVALID,
    output wire                           SLAVE2_AXI_RREADY,
    // Slave port 3
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE3_AXI_AWADDR,
    output wire [2:0]                     SLAVE3_AXI_AWPROT,
    output wire                           SLAVE3_AXI_AWVALID,
    input  wire                           SLAVE3_AXI_AWREADY,
    output wire [C_AXI_DATA_WIDTH-1:0]    SLAVE3_AXI_WDATA,
    output wire [C_AXI_DATA_WIDTH/8-1:0]  SLAVE3_AXI_WSTRB,
    output wire                           SLAVE3_AXI_WVALID,
    input  wire                           SLAVE3_AXI_WREADY,
    input  wire [1:0]                     SLAVE3_AXI_BRESP,
    input  wire                           SLAVE3_AXI_BVALID,
    output wire                           SLAVE3_AXI_BREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0]    SLAVE3_AXI_ARADDR,
    output wire [2:0]                     SLAVE3_AXI_ARPROT,
    output wire                           SLAVE3_AXI_ARVALID,
    input  wire                           SLAVE3_AXI_ARREADY,
    input  wire [C_AXI_DATA_WIDTH-1:0]    SLAVE3_AXI_RDATA,
    input  wire [1:0]                     SLAVE3_AXI_RRESP,
    input  wire                           SLAVE3_AXI_RVALID,
    output wire                           SLAVE3_AXI_RREADY
);

    localparam integer AW = C_AXI_ADDR_WIDTH;
    localparam integer DW = C_AXI_DATA_WIDTH;
    localparam integer SW = C_AXI_DATA_WIDTH/8;

    // axilxbar's flattened ports: port k in bits [k*width +: width]
    wire [3*AW-1:0] s_awaddr;
    wire [3*3-1:0] s_awprot;
    wire [3*1-1:0] s_awvalid;
    wire [3*1-1:0] s_awready;
    wire [3*DW-1:0] s_wdata;
    wire [3*SW-1:0] s_wstrb;
    wire [3*1-1:0] s_wvalid;
    wire [3*1-1:0] s_wready;
    wire [3*2-1:0] s_bresp;
    wire [3*1-1:0] s_bvalid;
    wire [3*1-1:0] s_bready;
    wire [3*AW-1:0] s_araddr;
    wire [3*3-1:0] s_arprot;
    wire [3*1-1:0] s_arvalid;
    wire [3*1-1:0] s_arready;
    wire [3*DW-1:0] s_rdata;
    wire [3*2-1:0] s_rresp;
    wire [3*1-1:0] s_rvalid;
    wire [3*1-1:0] s_rready;
    wire [4*AW-1:0] m_awaddr;
    wire [4*3-1:0] m_awprot;
    wire [4*1-1:0] m_awvalid;
    wire [4*1-1:0] m_awready;
    wire [4*DW-1:0] m_wdata;
    wire [4*SW-1:0] m_wstrb;
    wire [4*1-1:0] m_wvalid;
    wire [4*1-1:0] m_wready;
    wire [4*2-1:0] m_bresp;
    wire [4*1-1:0] m_bvalid;
    wire [4*1-1:0] m_bready;
    wire [4*AW-1:0] m_araddr;
    wire [4*3-1:0] m_arprot;
    wire [4*1-1:0] m_arvalid;
    wire [4*1-1:0] m_arready;
    wire [4*DW-1:0] m_rdata;
    wire [4*2-1:0] m_rresp;
    wire [4*1-1:0] m_rvalid;
    wire [4*1-1:0] m_rready;

    // MASTER0
    assign s_awaddr[0*AW +: AW] = MASTER0_AXI_AWADDR;
    assign s_awprot[0*3 +: 3] = MASTER0_AXI_AWPROT;
    assign s_awvalid[0] = MASTER0_AXI_AWVALID;
    assign MASTER0_AXI_AWREADY = s_awready[0];
    assign s_wdata[0*DW +: DW] = MASTER0_AXI_WDATA;
    assign s_wstrb[0*SW +: SW] = MASTER0_AXI_WSTRB;
    assign s_wvalid[0] = MASTER0_AXI_WVALID;
    assign MASTER0_AXI_WREADY = s_wready[0];
    assign MASTER0_AXI_BRESP = s_bresp[0*2 +: 2];
    assign MASTER0_AXI_BVALID = s_bvalid[0];
    assign s_bready[0] = MASTER0_AXI_BREADY;
    assign s_araddr[0*AW +: AW] = MASTER0_AXI_ARADDR;
    assign s_arprot[0*3 +: 3] = MASTER0_AXI_ARPROT;
    assign s_arvalid[0] = MASTER0_AXI_ARVALID;
    assign MASTER0_AXI_ARREADY = s_arready[0];
    assign MASTER0_AXI_RDATA = s_rdata[0*DW +: DW];
    assign MASTER0_AXI_RRESP = s_rresp[0*2 +: 2];
    assign MASTER0_AXI_RVALID = s_rvalid[0];
    assign s_rready[0] = MASTER0_AXI_RREADY;

    // MASTER1
    assign s_awaddr[1*AW +: AW] = MASTER1_AXI_AWADDR;
    assign s_awprot[1*3 +: 3] = MASTER1_AXI_AWPROT;
    assign s_awvalid[1] = MASTER1_AXI_AWVALID;
    assign MASTER1_AXI_AWREADY = s_awready[1];
    assign s_wdata[1*DW +: DW] = MASTER1_AXI_WDATA;
    assign s_wstrb[1*SW +: SW] = MASTER1_AXI_WSTRB;
    assign s_wvalid[1] = MASTER1_AXI_WVALID;
    assign MASTER1_AXI_WREADY = s_wready[1];
    assign MASTER1_AXI_BRESP = s_bresp[1*2 +: 2];
    assign MASTER1_AXI_BVALID = s_bvalid[1];
    assign s_bready[1] = MASTER1_AXI_BREADY;
    assign s_araddr[1*AW +: AW] = MASTER1_AXI_ARADDR;
    assign s_arprot[1*3 +: 3] = MASTER1_AXI_ARPROT;
    assign s_arvalid[1] = MASTER1_AXI_ARVALID;
    assign MASTER1_AXI_ARREADY = s_arready[1];
    assign MASTER1_AXI_RDATA = s_rdata[1*DW +: DW];
    assign MASTER1_AXI_RRESP = s_rresp[1*2 +: 2];
    assign MASTER1_AXI_RVALID = s_rvalid[1];
    assign s_rready[1] = MASTER1_AXI_RREADY;

    // MASTER2
    assign s_awaddr[2*AW +: AW] = MASTER2_AXI_AWADDR;
    assign s_awprot[2*3 +: 3] = MASTER2_AXI_AWPROT;
    assign s_awvalid[2] = MASTER2_AXI_AWVALID;
    assign MASTER2_AXI_AWREADY = s_awready[2];
    assign s_wdata[2*DW +: DW] = MASTER2_AXI_WDATA;
    assign s_wstrb[2*SW +: SW] = MASTER2_AXI_WSTRB;
    assign s_wvalid[2] = MASTER2_AXI_WVALID;
    assign MASTER2_AXI_WREADY = s_wready[2];
    assign MASTER2_AXI_BRESP = s_bresp[2*2 +: 2];
    assign MASTER2_AXI_BVALID = s_bvalid[2];
    assign s_bready[2] = MASTER2_AXI_BREADY;
    assign s_araddr[2*AW +: AW] = MASTER2_AXI_ARADDR;
    assign s_arprot[2*3 +: 3] = MASTER2_AXI_ARPROT;
    assign s_arvalid[2] = MASTER2_AXI_ARVALID;
    assign MASTER2_AXI_ARREADY = s_arready[2];
    assign MASTER2_AXI_RDATA = s_rdata[2*DW +: DW];
    assign MASTER2_AXI_RRESP = s_rresp[2*2 +: 2];
    assign MASTER2_AXI_RVALID = s_rvalid[2];
    assign s_rready[2] = MASTER2_AXI_RREADY;

    // SLAVE0
    assign SLAVE0_AXI_AWADDR = m_awaddr[0*AW +: AW];
    assign SLAVE0_AXI_AWPROT = m_awprot[0*3 +: 3];
    assign SLAVE0_AXI_AWVALID = m_awvalid[0];
    assign m_awready[0] = SLAVE0_AXI_AWREADY;
    assign SLAVE0_AXI_WDATA = m_wdata[0*DW +: DW];
    assign SLAVE0_AXI_WSTRB = m_wstrb[0*SW +: SW];
    assign SLAVE0_AXI_WVALID = m_wvalid[0];
    assign m_wready[0] = SLAVE0_AXI_WREADY;
    assign m_bresp[0*2 +: 2] = SLAVE0_AXI_BRESP;
    assign m_bvalid[0] = SLAVE0_AXI_BVALID;
    assign SLAVE0_AXI_BREADY = m_bready[0];
    assign SLAVE0_AXI_ARADDR = m_araddr[0*AW +: AW];
    assign SLAVE0_AXI_ARPROT = m_arprot[0*3 +: 3];
    assign SLAVE0_AXI_ARVALID = m_arvalid[0];
    assign m_arready[0] = SLAVE0_AXI_ARREADY;
    assign m_rdata[0*DW +: DW] = SLAVE0_AXI_RDATA;
    assign m_rresp[0*2 +: 2] = SLAVE0_AXI_RRESP;
    assign m_rvalid[0] = SLAVE0_AXI_RVALID;
    assign SLAVE0_AXI_RREADY = m_rready[0];

    // SLAVE1
    assign SLAVE1_AXI_AWADDR = m_awaddr[1*AW +: AW];
    assign SLAVE1_AXI_AWPROT = m_awprot[1*3 +: 3];
    assign SLAVE1_AXI_AWVALID = m_awvalid[1];
    assign m_awready[1] = SLAVE1_AXI_AWREADY;
    assign SLAVE1_AXI_WDATA = m_wdata[1*DW +: DW];
    assign SLAVE1_AXI_WSTRB = m_wstrb[1*SW +: SW];
    assign SLAVE1_AXI_WVALID = m_wvalid[1];
    assign m_wready[1] = SLAVE1_AXI_WREADY;
    assign m_bresp[1*2 +: 2] = SLAVE1_AXI_BRESP;
    assign m_bvalid[1] = SLAVE1_AXI_BVALID;
    assign SLAVE1_AXI_BREADY = m_bready[1];
    assign SLAVE1_AXI_ARADDR = m_araddr[1*AW +: AW];
    assign SLAVE1_AXI_ARPROT = m_arprot[1*3 +: 3];
    assign SLAVE1_AXI_ARVALID = m_arvalid[1];
    assign m_arready[1] = SLAVE1_AXI_ARREADY;
    assign m_rdata[1*DW +: DW] = SLAVE1_AXI_RDATA;
    assign m_rresp[1*2 +: 2] = SLAVE1_AXI_RRESP;
    assign m_rvalid[1] = SLAVE1_AXI_RVALID;
    assign SLAVE1_AXI_RREADY = m_rready[1];

    // SLAVE2
    assign SLAVE2_AXI_AWADDR = m_awaddr[2*AW +: AW];
    assign SLAVE2_AXI_AWPROT = m_awprot[2*3 +: 3];
    assign SLAVE2_AXI_AWVALID = m_awvalid[2];
    assign m_awready[2] = SLAVE2_AXI_AWREADY;
    assign SLAVE2_AXI_WDATA = m_wdata[2*DW +: DW];
    assign SLAVE2_AXI_WSTRB = m_wstrb[2*SW +: SW];
    assign SLAVE2_AXI_WVALID = m_wvalid[2];
    assign m_wready[2] = SLAVE2_AXI_WREADY;
    assign m_bresp[2*2 +: 2] = SLAVE2_AXI_BRESP;
    assign m_bvalid[2] = SLAVE2_AXI_BVALID;
    assign SLAVE2_AXI_BREADY = m_bready[2];
    assign SLAVE2_AXI_ARADDR = m_araddr[2*AW +: AW];
    assign SLAVE2_AXI_ARPROT = m_arprot[2*3 +: 3];
    assign SLAVE2_AXI_ARVALID = m_arvalid[2];
    assign m_arready[2] = SLAVE2_AXI_ARREADY;
    assign m_rdata[2*DW +: DW] = SLAVE2_AXI_RDATA;
    assign m_rresp[2*2 +: 2] = SLAVE2_AXI_RRESP;
    assign m_rvalid[2] = SLAVE2_AXI_RVALID;
    assign SLAVE2_AXI_RREADY = m_rready[2];

    // SLAVE3
    assign SLAVE3_AXI_AWADDR = m_awaddr[3*AW +: AW];
    assign SLAVE3_AXI_AWPROT = m_awprot[3*3 +: 3];
    assign SLAVE3_AXI_AWVALID = m_awvalid[3];
    assign m_awready[3] = SLAVE3_AXI_AWREADY;
    assign SLAVE3_AXI_WDATA = m_wdata[3*DW +: DW];
    assign SLAVE3_AXI_WSTRB = m_wstrb[3*SW +: SW];
    assign SLAVE3_AXI_WVALID = m_wvalid[3];
    assign m_wready[3] = SLAVE3_AXI_WREADY;
    assign m_bresp[3*2 +: 2] = SLAVE3_AXI_BRESP;
    assign m_bvalid[3] = SLAVE3_AXI_BVALID;
    assign SLAVE3_AXI_BREADY = m_bready[3];
    assign SLAVE3_AXI_ARADDR = m_araddr[3*AW +: AW];
    assign SLAVE3_AXI_ARPROT = m_arprot[3*3 +: 3];
    assign SLAVE3_AXI_ARVALID = m_arvalid[3];
    assign m_arready[3] = SLAVE3_AXI_ARREADY;
    assign m_rdata[3*DW +: DW] = SLAVE3_AXI_RDATA;
    assign m_rresp[3*2 +: 2] = SLAVE3_AXI_RRESP;
    assign m_rvalid[3] = SLAVE3_AXI_RVALID;
    assign SLAVE3_AXI_RREADY = m_rready[3];

    axilxbar #(
        .C_AXI_DATA_WIDTH (C_AXI_DATA_WIDTH),
        .C_AXI_ADDR_WIDTH (C_AXI_ADDR_WIDTH),
        .NM               (3),
        .NS               (4),
        // { slave NS-1, ..., slave 0 }
        .SLAVE_ADDR       ({ SLAVE3_ADDR, SLAVE2_ADDR, SLAVE1_ADDR, SLAVE0_ADDR }),
        .SLAVE_MASK       ({ SLAVE3_MASK, SLAVE2_MASK, SLAVE1_MASK, SLAVE0_MASK }),
        .OPT_LOWPOWER     (OPT_LOWPOWER),
        .OPT_LINGER       (OPT_LINGER),
        .LGMAXBURST       (LGMAXBURST)
    ) u_axilxbar (
        .S_AXI_ACLK    (ACLK),
        .S_AXI_ARESETN (ARESETN),
        .S_AXI_AWADDR  (s_awaddr),
        .S_AXI_AWPROT  (s_awprot),
        .S_AXI_AWVALID (s_awvalid),
        .S_AXI_AWREADY (s_awready),
        .S_AXI_WDATA   (s_wdata),
        .S_AXI_WSTRB   (s_wstrb),
        .S_AXI_WVALID  (s_wvalid),
        .S_AXI_WREADY  (s_wready),
        .S_AXI_BRESP   (s_bresp),
        .S_AXI_BVALID  (s_bvalid),
        .S_AXI_BREADY  (s_bready),
        .S_AXI_ARADDR  (s_araddr),
        .S_AXI_ARPROT  (s_arprot),
        .S_AXI_ARVALID (s_arvalid),
        .S_AXI_ARREADY (s_arready),
        .S_AXI_RDATA   (s_rdata),
        .S_AXI_RRESP   (s_rresp),
        .S_AXI_RVALID  (s_rvalid),
        .S_AXI_RREADY  (s_rready),
        .M_AXI_AWADDR  (m_awaddr),
        .M_AXI_AWPROT  (m_awprot),
        .M_AXI_AWVALID (m_awvalid),
        .M_AXI_AWREADY (m_awready),
        .M_AXI_WDATA   (m_wdata),
        .M_AXI_WSTRB   (m_wstrb),
        .M_AXI_WVALID  (m_wvalid),
        .M_AXI_WREADY  (m_wready),
        .M_AXI_BRESP   (m_bresp),
        .M_AXI_BVALID  (m_bvalid),
        .M_AXI_BREADY  (m_bready),
        .M_AXI_ARADDR  (m_araddr),
        .M_AXI_ARPROT  (m_arprot),
        .M_AXI_ARVALID (m_arvalid),
        .M_AXI_ARREADY (m_arready),
        .M_AXI_RDATA   (m_rdata),
        .M_AXI_RRESP   (m_rresp),
        .M_AXI_RVALID  (m_rvalid),
        .M_AXI_RREADY  (m_rready)
    );

endmodule

`default_nettype wire
