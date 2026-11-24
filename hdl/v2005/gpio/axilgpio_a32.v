////////////////////////////////////////////////////////////////////////////////
// Module: axilgpio_a32
//
// ZipCPU axilgpio (hdl/axilgpio.v) with 32-bit AXI4-Lite addresses, for tools that
// connect bus interfaces only between ports of equal width (Libero SmartDesign bus
// interfaces, interconnects with 32-bit slave ports).
//
// The core decodes only its 8 registers, so only S_AXI_AWADDR[4:0] / S_AXI_ARADDR[4:0]
// are passed on; the upper bits are the interconnect's business (they select this IP)
// and are ignored here. All other ports and parameters are passed through unchanged.
//
//   Offset  Register
//   0x00    LOAD        outputs (a write replaces all of them)
//   0x04    SET         outputs where a 1 is written are set
//   0x08    CLEAR       outputs where a 1 is written are cleared
//   0x0C    TOGGLE      outputs where a 1 is written are inverted
//   0x10    IN_DATA     inputs (2-FF synchronised)            (NIN > 0)
//   0x14    CHANGED     input changed, write 1 to clear        (NIN > 0)
//   0x18    INT_MASK    1 = a change of this input raises o_int (NIN > 0)
//   0x1C    INT_ACTIVE  CHANGED & INT_MASK                      (NIN > 0)
//
// Files: hdl/axilgpio_a32.v, hdl/axilgpio.v, hdl/skidbuffer.v (Verilog-2001).
//
// Libero bus interface: one AXI4-Lite interface, role Slave, on the S_AXI_* ports
// (ACLK and ARESETN stay outside it).
////////////////////////////////////////////////////////////////////////////////

`default_nettype none

module axilgpio_a32 #(
    parameter [0:0] OPT_SKIDBUFFER = 1'b1,
    parameter [0:0] OPT_LOWPOWER = 1'b0,
    parameter NOUT = 30,
    parameter NIN = 5,
    parameter [NOUT-1:0] DEFAULT_OUTPUT = 0
) (
    input  wire                              S_AXI_ACLK,
    input  wire                              S_AXI_ARESETN,
    // write address
    input  wire                              S_AXI_AWVALID,
    output wire                              S_AXI_AWREADY,
    input  wire [31:0]                       S_AXI_AWADDR,
    input  wire [2:0]                        S_AXI_AWPROT,
    // write data
    input  wire                              S_AXI_WVALID,
    output wire                              S_AXI_WREADY,
    input  wire [31:0]                       S_AXI_WDATA,
    input  wire [3:0]                        S_AXI_WSTRB,
    // write response
    output wire                              S_AXI_BVALID,
    input  wire                              S_AXI_BREADY,
    output wire [1:0]                        S_AXI_BRESP,
    // read address
    input  wire                              S_AXI_ARVALID,
    output wire                              S_AXI_ARREADY,
    input  wire [31:0]                       S_AXI_ARADDR,
    input  wire [2:0]                        S_AXI_ARPROT,
    // read data
    output wire                              S_AXI_RVALID,
    input  wire                              S_AXI_RREADY,
    output wire [31:0]                       S_AXI_RDATA,
    output wire [1:0]                        S_AXI_RRESP,
    // GPIO
    output wire [NOUT-1:0]                   o_gpio,
    input  wire [((NIN>0) ? (NIN-1):0):0]    i_gpio,
    output wire                              o_int
);

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
        .S_AXI_AWVALID(S_AXI_AWVALID),
        .S_AXI_AWREADY(S_AXI_AWREADY),
        .S_AXI_AWADDR(S_AXI_AWADDR[4:0]),
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

    // the upper address bits select this IP in the interconnect; not used here
    wire unused_addr;
    assign unused_addr = &{1'b0, S_AXI_AWADDR[31:5], S_AXI_ARADDR[31:5]};

endmodule

`default_nettype wire
