// AXIL Studio (hdl/v2005): `localparam` written as `parameter` (same values) for Verilog-2001 /
// 2005 tools. Do not override the former localparams.
////////////////////////////////////////////////////////////////////////////////
// Module      : hexbus_axil_top
// Description : UART to AXI4-Lite master debug bridge based on the ZipCPU
//               hexbus (dbgbus/hexbus/rtl/hbaxil.v). The internal reset is
//               driven by both the hexbus 'T' reset command and S_AXI_ARESETN.
// Dependencies: rxuartlite.v, txuartlite.v (ZipCPU/wbuart32)
//               hbdechex.v, hbpack.v, hbexecaxi.v, hbints.v, hbidle.v,
//               hbdeword.v, hbgenhex.v, hbnewline.v (ZipCPU/dbgbus)
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module hexbus_axil_top #(
    parameter integer CLK_FREQ_HZ      = 50_000_000, // S_AXI_ACLK frequency
    parameter integer BAUD_RATE        = 115_200,
    parameter integer C_AXI_ADDR_WIDTH = 32          // Byte address width
) (
    input  wire                        S_AXI_ACLK,
    input  wire                        S_AXI_ARESETN,

    // UART interface
    input  wire                        i_uart_rx,
    output wire                        o_uart_tx,

    // Interrupt notification to host (tie to 0 if unused)
    input  wire                        i_interrupt,

    // AXI4-Lite master interface (+ constant AXI4 sideband signals, see below)
    output wire                        M_AXI_AWVALID,
    input  wire                        M_AXI_AWREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0] M_AXI_AWADDR,
    output wire [2:0]                  M_AXI_AWPROT,
    output wire                        M_AXI_WVALID,
    input  wire                        M_AXI_WREADY,
    output wire [31:0]                 M_AXI_WDATA,
    output wire [3:0]                  M_AXI_WSTRB,
    input  wire                        M_AXI_BVALID,
    output wire                        M_AXI_BREADY,
    input  wire [1:0]                  M_AXI_BRESP,
    output wire                        M_AXI_ARVALID,
    input  wire                        M_AXI_ARREADY,
    output wire [C_AXI_ADDR_WIDTH-1:0] M_AXI_ARADDR,
    output wire [2:0]                  M_AXI_ARPROT,
    input  wire                        M_AXI_RVALID,
    output wire                        M_AXI_RREADY,
    input  wire [31:0]                 M_AXI_RDATA,
    input  wire [1:0]                  M_AXI_RRESP,

    // AXI4 sideband signals for AXI4 bus interfaces. Every hexbus access is a
    // single 32-bit beat; leaving these unmapped lets the interconnect
    // assume 1-byte transfers (AxSIZE = 0) and mask WSTRB down to byte 0.
    output wire [7:0]                  M_AXI_AWLEN,
    output wire [2:0]                  M_AXI_AWSIZE,
    output wire [1:0]                  M_AXI_AWBURST,
    output wire                        M_AXI_WLAST,
    output wire [7:0]                  M_AXI_ARLEN,
    output wire [2:0]                  M_AXI_ARSIZE,
    output wire [1:0]                  M_AXI_ARBURST
);

    assign M_AXI_AWLEN   = 8'd0;      // 1 beat
    assign M_AXI_AWSIZE  = 3'b010;    // 4 bytes
    assign M_AXI_AWBURST = 2'b01;     // INCR
    assign M_AXI_WLAST   = 1'b1;
    assign M_AXI_ARLEN   = 8'd0;
    assign M_AXI_ARSIZE  = 3'b010;
    assign M_AXI_ARBURST = 2'b01;

    parameter integer AW  = C_AXI_ADDR_WIDTH - 2;     // Word address width
    parameter integer CPB = CLK_FREQ_HZ / BAUD_RATE;  // Clocks per baud

    wire i_clk     = S_AXI_ACLK;
    wire ext_reset = !S_AXI_ARESETN;

    // UART receiver
    wire       rx_stb;
    wire [7:0] rx_byte;

    rxuartlite #(
        .TIMER_BITS      (24),
        .CLOCKS_PER_BAUD (CPB[23:0])
    ) u_rx (
        .i_clk     (i_clk),
        .i_reset   (ext_reset),
        .i_uart_rx (i_uart_rx),
        .o_wr      (rx_stb),
        .o_data    (rx_byte)
    );

    // Command path: hex characters -> 34-bit command words
    wire       dec_stb, dec_reset;
    wire [4:0] dec_bits;
    wire       w_reset = dec_reset || ext_reset;

    hbdechex u_dechex (
        .i_clk     (i_clk),
        .i_stb     (rx_stb),
        .i_byte    (rx_byte),
        .o_dh_stb  (dec_stb),
        .o_reset   (dec_reset),
        .o_dh_bits (dec_bits)
    );

    wire        iw_stb;
    wire [33:0] iw_word;

    hbpack u_pack (
        .i_clk      (i_clk),
        .i_reset    (w_reset),
        .i_stb      (dec_stb),
        .i_bits     (dec_bits),
        .o_pck_stb  (iw_stb),
        .o_pck_word (iw_word)
    );

    // AXI4-Lite command executor
    wire        exec_busy;
    wire        ow_stb;
    wire [33:0] ow_word;

    hbexecaxi #(AW) u_exec (
        .i_clk         (i_clk),
        .i_reset       (w_reset),
        .i_cmd_stb     (iw_stb),
        .i_cmd_word    (iw_word),
        .o_cmd_busy    (exec_busy),
        .o_rsp_stb     (ow_stb),
        .o_rsp_word    (ow_word),
        .M_AXI_AWVALID (M_AXI_AWVALID),
        .M_AXI_AWREADY (M_AXI_AWREADY),
        .M_AXI_AWADDR  (M_AXI_AWADDR),
        .M_AXI_AWPROT  (M_AXI_AWPROT),
        .M_AXI_WVALID  (M_AXI_WVALID),
        .M_AXI_WREADY  (M_AXI_WREADY),
        .M_AXI_WDATA   (M_AXI_WDATA),
        .M_AXI_WSTRB   (M_AXI_WSTRB),
        .M_AXI_BVALID  (M_AXI_BVALID),
        .M_AXI_BREADY  (M_AXI_BREADY),
        .M_AXI_BRESP   (M_AXI_BRESP),
        .M_AXI_ARVALID (M_AXI_ARVALID),
        .M_AXI_ARREADY (M_AXI_ARREADY),
        .M_AXI_ARADDR  (M_AXI_ARADDR),
        .M_AXI_ARPROT  (M_AXI_ARPROT),
        .M_AXI_RVALID  (M_AXI_RVALID),
        .M_AXI_RREADY  (M_AXI_RREADY),
        .M_AXI_RDATA   (M_AXI_RDATA),
        .M_AXI_RRESP   (M_AXI_RRESP)
    );

    // Response path: 34-bit response words -> hex characters
    wire        int_busy, int_stb, idl_busy, idl_stb;
    wire [33:0] int_word, idl_word;
    wire        hb_busy, hb_stb, hx_busy, hx_stb, nl_busy;
    wire        tx_stb, tx_busy;
    wire [4:0]  hb_bits;
    wire [6:0]  hx_byte, tx_byte;

    hbints u_ints (
        .i_clk       (i_clk),
        .i_reset     (w_reset),
        .i_interrupt (i_interrupt),
        .i_stb       (ow_stb),
        .i_word      (ow_word),
        .o_int_busy  (int_busy),
        .o_int_stb   (int_stb),
        .o_int_word  (int_word),
        .i_busy      (idl_busy)
    );

    hbidle u_idle (
        .i_clk      (i_clk),
        .i_reset    (w_reset),
        .i_cmd_stb  (int_stb),
        .i_cmd_word (int_word),
        .o_idl_busy (idl_busy),
        .o_idl_stb  (idl_stb),
        .o_idl_word (idl_word),
        .i_busy     (hb_busy)
    );

    hbdeword u_deword (
        .i_clk     (i_clk),
        .i_reset   (w_reset),
        .i_stb     (idl_stb),
        .i_word    (idl_word),
        .o_dw_busy (hb_busy),
        .o_dw_stb  (hb_stb),
        .o_dw_bits (hb_bits),
        .i_tx_busy (hx_busy)
    );

    hbgenhex u_genhex (
        .i_clk     (i_clk),
        .i_reset   (w_reset),
        .i_stb     (hb_stb),
        .i_bits    (hb_bits),
        .o_gx_busy (hx_busy),
        .o_gx_stb  (hx_stb),
        .o_gx_char (hx_byte),
        .i_busy    (nl_busy)
    );

    hbnewline u_newline (
        .i_clk     (i_clk),
        .i_reset   (w_reset),
        .i_stb     (hx_stb),
        .i_byte    (hx_byte),
        .o_nl_busy (nl_busy),
        .o_nl_stb  (tx_stb),
        .o_nl_byte (tx_byte),
        .i_busy    (tx_busy)
    );

    // UART transmitter
    txuartlite #(
        .TIMING_BITS     (5'd24),
        .CLOCKS_PER_BAUD (CPB[23:0])
    ) u_tx (
        .i_clk     (i_clk),
        .i_reset   (ext_reset),
        .i_wr      (tx_stb),
        .i_data    ({1'b0, tx_byte}),
        .o_uart_tx (o_uart_tx),
        .o_busy    (tx_busy)
    );

endmodule

`default_nettype wire
