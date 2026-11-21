////////////////////////////////////////////////////////////////////////////////
// Module: axil_xbar_SBB_1
//
// AXI4-Lite crossbar, 1 master port(s) x 1 slave port(s): ZipCPU axilxbar,
// configured only through its parameters. This wrapper adds no logic; it gives
// every port its own named AXI4-Lite signals:
//   MASTER<i>_AXI_*  connect to master i (its M_AXI_*)
//   SLAVE<j>_AXI_*   connect to slave j  (its S_AXI_*)
// Slave j is selected when (address & SLAVE<j>_MASK) == SLAVE<j>_ADDR; any other
// address is answered with DECERR by axilxbar.
//
// Address map (SLAVE<j>_ADDR / SLAVE<j>_MASK defaults):
//   Port     Base        Mask        Window    Name
//   MASTER0                                    SBB_1
//   SLAVE0   0x00006000  0xFFFFF000     0x1000  ip_3
//
// Requires axilxbar.v, addrdecode.v, skidbuffer.v (wb2axip, Apache 2.0,
// Gisselquist Technology LLC).
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module axil_xbar_SBB_1 #(
    parameter integer C_AXI_ADDR_WIDTH = 32,
    parameter integer C_AXI_DATA_WIDTH = 32,
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE0_ADDR = 32'h0000_6000,   // ip_3
    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE0_MASK = 32'hFFFF_F000,
    parameter [0:0]   OPT_LOWPOWER = 1'b1,
    parameter integer OPT_LINGER   = 4,
    parameter integer LGMAXBURST   = 5
) (
    input  wire ACLK,
    input  wire ARESETN,
    // Master port 0: SBB_1
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
    // Slave port 0: ip_3
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
    output wire                           SLAVE0_AXI_RREADY
);

    localparam integer AW = C_AXI_ADDR_WIDTH;
    localparam integer DW = C_AXI_DATA_WIDTH;
    localparam integer SW = C_AXI_DATA_WIDTH/8;

    // axilxbar's flattened ports: port k in bits [k*width +: width]
    wire [1*AW-1:0] s_awaddr;
    wire [1*3-1:0] s_awprot;
    wire [1*1-1:0] s_awvalid;
    wire [1*1-1:0] s_awready;
    wire [1*DW-1:0] s_wdata;
    wire [1*SW-1:0] s_wstrb;
    wire [1*1-1:0] s_wvalid;
    wire [1*1-1:0] s_wready;
    wire [1*2-1:0] s_bresp;
    wire [1*1-1:0] s_bvalid;
    wire [1*1-1:0] s_bready;
    wire [1*AW-1:0] s_araddr;
    wire [1*3-1:0] s_arprot;
    wire [1*1-1:0] s_arvalid;
    wire [1*1-1:0] s_arready;
    wire [1*DW-1:0] s_rdata;
    wire [1*2-1:0] s_rresp;
    wire [1*1-1:0] s_rvalid;
    wire [1*1-1:0] s_rready;
    wire [1*AW-1:0] m_awaddr;
    wire [1*3-1:0] m_awprot;
    wire [1*1-1:0] m_awvalid;
    wire [1*1-1:0] m_awready;
    wire [1*DW-1:0] m_wdata;
    wire [1*SW-1:0] m_wstrb;
    wire [1*1-1:0] m_wvalid;
    wire [1*1-1:0] m_wready;
    wire [1*2-1:0] m_bresp;
    wire [1*1-1:0] m_bvalid;
    wire [1*1-1:0] m_bready;
    wire [1*AW-1:0] m_araddr;
    wire [1*3-1:0] m_arprot;
    wire [1*1-1:0] m_arvalid;
    wire [1*1-1:0] m_arready;
    wire [1*DW-1:0] m_rdata;
    wire [1*2-1:0] m_rresp;
    wire [1*1-1:0] m_rvalid;
    wire [1*1-1:0] m_rready;

    // MASTER0: SBB_1
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

    // SLAVE0: ip_3
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

    axilxbar #(
        .C_AXI_DATA_WIDTH (C_AXI_DATA_WIDTH),
        .C_AXI_ADDR_WIDTH (C_AXI_ADDR_WIDTH),
        .NM               (1),
        .NS               (1),
        // { slave NS-1, ..., slave 0 }
        .SLAVE_ADDR       ({ SLAVE0_ADDR }),
        .SLAVE_MASK       ({ SLAVE0_MASK }),
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
