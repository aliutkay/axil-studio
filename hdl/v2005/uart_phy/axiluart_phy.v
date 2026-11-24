////////////////////////////////////////////////////////////////////////////////
// Module      : axiluart_phy
// Description : AXI4-Lite UART with configurable line interface.
//               0x00-0x0C : ZipCPU axiluart (SETUP, FIFO, RXREG, TXREG)
//               0x10      : PHY_CTRL    (see uart_phy_adapter.v)
//               0x14      : PHY_TIMING
//               0x18      : PHY_STATUS  (bit 12 write-one-to-clear)
//               0x1C      : reserved, reads 0
// Dependencies: axiluart_v2.v (i_tx_go, o_tx_active, sticky RX flags),
//               uart_phy_adapter.v, rxuart.v, rxuartlite.v, txuart.v,
//               txuartlite.v, ufifo.v, skidbuffer.v (ZipCPU/wbuart32)
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module axiluart_phy #(
    parameter [30:0] INITIAL_SETUP = 31'd434,         // [23:0] = ACLK / baud
    parameter [3:0]  LGFLEN        = 4'd4,            // log2 FIFO depth
    parameter [0:0]  HAS_TX        = 1'b1,
    parameter [0:0]  HAS_RX        = 1'b1,
    parameter [0:0]  HAS_DE        = 1'b1,
    parameter [0:0]  HAS_RE        = 1'b1,
    parameter [0:0]  HAS_FLOW      = 1'b0,            // RTS/CTS pins present
    parameter [31:0] DEF_CTRL      = 32'h0000_00C2,
    parameter [31:0] DEF_TIMING    = 32'h00FA_0032
) (
    input  wire        S_AXI_ACLK,
    input  wire        S_AXI_ARESETN,

    // AXI4-Lite slave (connect address bits [4:0])
    input  wire        S_AXI_AWVALID,
    output wire        S_AXI_AWREADY,
    input  wire [4:0]  S_AXI_AWADDR,
    input  wire [2:0]  S_AXI_AWPROT,
    input  wire        S_AXI_WVALID,
    output wire        S_AXI_WREADY,
    input  wire [31:0] S_AXI_WDATA,
    input  wire [3:0]  S_AXI_WSTRB,
    output wire        S_AXI_BVALID,
    input  wire        S_AXI_BREADY,
    output wire [1:0]  S_AXI_BRESP,
    input  wire        S_AXI_ARVALID,
    output wire        S_AXI_ARREADY,
    input  wire [4:0]  S_AXI_ARADDR,
    input  wire [2:0]  S_AXI_ARPROT,
    output wire        S_AXI_RVALID,
    input  wire        S_AXI_RREADY,
    output wire [31:0] S_AXI_RDATA,
    output wire [1:0]  S_AXI_RRESP,

    // Line interface (to transceiver)
    output wire        o_txd,
    input  wire        i_rxd,
    output wire        o_de,
    output wire        o_re_n,
    output wire        o_rts_n,
    input  wire        i_cts_n,

    // Interrupts
    output wire        o_uart_rx_int,
    output wire        o_uart_tx_int,
    output wire        o_uart_rxfifo_int,
    output wire        o_uart_txfifo_int
);

    wire i_clk   = S_AXI_ACLK;
    wire i_reset = !S_AXI_ARESETN;

    // -------------------------------------------------------------------------
    // AXI4-Lite split: address bit 4 selects UART core (0) or PHY registers (1)
    // One transaction per direction is in flight at a time.
    // -------------------------------------------------------------------------
    wire        u_awready, u_wready, u_bvalid, u_arready, u_rvalid;
    wire [1:0]  u_bresp, u_rresp;
    wire [31:0] u_rdata;

    // Write channel
    reg  w_busy, w_sel_phy, p_bvalid, aw_done, w_done;
    wire w_route_uart = w_busy && !w_sel_phy;
    wire w_route_phy  = w_busy &&  w_sel_phy;
    wire p_wr         = w_route_phy && !p_bvalid && S_AXI_AWVALID && S_AXI_WVALID;

    wire u_awvalid = w_route_uart && !aw_done && S_AXI_AWVALID;
    wire u_wvalid  = w_route_uart && !w_done  && S_AXI_WVALID;

    initial {w_busy, w_sel_phy, p_bvalid, aw_done, w_done} = 5'b00000;
    always @(posedge i_clk)
        if (i_reset) begin
            w_busy   <= 1'b0;
            p_bvalid <= 1'b0;
            aw_done  <= 1'b0;
            w_done   <= 1'b0;
        end else begin
            if (!w_busy && S_AXI_AWVALID) begin
                w_busy    <= 1'b1;
                w_sel_phy <= S_AXI_AWADDR[4];
            end
            if (u_awvalid && u_awready)
                aw_done <= 1'b1;
            if (u_wvalid && u_wready)
                w_done <= 1'b1;
            if (p_wr)
                p_bvalid <= 1'b1;
            if (S_AXI_BVALID && S_AXI_BREADY) begin
                p_bvalid <= 1'b0;
                w_busy   <= 1'b0;
                aw_done  <= 1'b0;
                w_done   <= 1'b0;
            end
        end

    assign S_AXI_AWREADY = w_route_uart ? (u_awready && !aw_done) : p_wr;
    assign S_AXI_WREADY  = w_route_uart ? (u_wready  && !w_done)  : p_wr;
    assign S_AXI_BVALID  = w_route_uart ? u_bvalid  : (w_route_phy && p_bvalid);
    assign S_AXI_BRESP   = w_route_uart ? u_bresp   : 2'b00;

    // Read channel
    reg        r_busy, r_sel_phy, p_rvalid, ar_done;
    reg [31:0] p_rdata;
    wire       r_route_uart = r_busy && !r_sel_phy;
    wire       r_route_phy  = r_busy &&  r_sel_phy;
    wire       p_rd         = r_route_phy && !p_rvalid && S_AXI_ARVALID;

    wire       u_arvalid    = r_route_uart && !ar_done && S_AXI_ARVALID;

    wire [31:0] phy_ctrl, phy_timing, phy_status;

    initial {r_busy, r_sel_phy, p_rvalid, ar_done} = 4'b0000;
    initial p_rdata = 32'd0;
    always @(posedge i_clk)
        if (i_reset) begin
            r_busy   <= 1'b0;
            p_rvalid <= 1'b0;
            ar_done  <= 1'b0;
        end else begin
            if (!r_busy && S_AXI_ARVALID) begin
                r_busy    <= 1'b1;
                r_sel_phy <= S_AXI_ARADDR[4];
            end
            if (u_arvalid && u_arready)
                ar_done <= 1'b1;
            if (p_rd) begin
                p_rvalid <= 1'b1;
                case (S_AXI_ARADDR[3:2])
                    2'd0:    p_rdata <= phy_ctrl;
                    2'd1:    p_rdata <= phy_timing;
                    2'd2:    p_rdata <= phy_status;
                    default: p_rdata <= 32'd0;
                endcase
            end
            if (S_AXI_RVALID && S_AXI_RREADY) begin
                p_rvalid <= 1'b0;
                r_busy   <= 1'b0;
                ar_done  <= 1'b0;
            end
        end

    assign S_AXI_ARREADY = r_route_uart ? (u_arready && !ar_done) : p_rd;
    assign S_AXI_RVALID  = r_route_uart ? u_rvalid  : (r_route_phy && p_rvalid);
    assign S_AXI_RDATA   = r_route_uart ? u_rdata   : p_rdata;
    assign S_AXI_RRESP   = r_route_uart ? u_rresp   : 2'b00;

    // PHY register write strobes (full-word writes)
    wire phy_ctrl_wr   = p_wr && (S_AXI_AWADDR[3:2] == 2'd0);
    wire phy_timing_wr = p_wr && (S_AXI_AWADDR[3:2] == 2'd1);
    wire phy_status_wr = p_wr && (S_AXI_AWADDR[3:2] == 2'd2);

    // -------------------------------------------------------------------------
    // UART core
    // -------------------------------------------------------------------------
    wire uart_txd, uart_rxd, tx_go, tx_active;

    axiluart_v2 #(
        .INITIAL_SETUP                 (INITIAL_SETUP),
        .LGFLEN                        (LGFLEN),
        .HARDWARE_FLOW_CONTROL_PRESENT (HAS_FLOW),
        .C_AXI_ADDR_WIDTH              (4)
    ) u_uart (
        .S_AXI_ACLK        (S_AXI_ACLK),
        .S_AXI_ARESETN     (S_AXI_ARESETN),
        .S_AXI_AWVALID     (u_awvalid),
        .S_AXI_AWREADY     (u_awready),
        .S_AXI_AWADDR      (S_AXI_AWADDR[3:0]),
        .S_AXI_AWPROT      (S_AXI_AWPROT),
        .S_AXI_WVALID      (u_wvalid),
        .S_AXI_WREADY      (u_wready),
        .S_AXI_WDATA       (S_AXI_WDATA),
        .S_AXI_WSTRB       (S_AXI_WSTRB),
        .S_AXI_BVALID      (u_bvalid),
        .S_AXI_BREADY      (w_route_uart && S_AXI_BREADY),
        .S_AXI_BRESP       (u_bresp),
        .S_AXI_ARVALID     (u_arvalid),
        .S_AXI_ARREADY     (u_arready),
        .S_AXI_ARADDR      (S_AXI_ARADDR[3:0]),
        .S_AXI_ARPROT      (S_AXI_ARPROT),
        .S_AXI_RVALID      (u_rvalid),
        .S_AXI_RREADY      (r_route_uart && S_AXI_RREADY),
        .S_AXI_RDATA       (u_rdata),
        .S_AXI_RRESP       (u_rresp),
        .i_uart_rx         (uart_rxd),
        .o_uart_tx         (uart_txd),
        .i_cts_n           (HAS_FLOW ? i_cts_n : 1'b0),
        .o_rts_n           (o_rts_n),
        .o_uart_rx_int     (o_uart_rx_int),
        .o_uart_tx_int     (o_uart_tx_int),
        .o_uart_rxfifo_int (o_uart_rxfifo_int),
        .o_uart_txfifo_int (o_uart_txfifo_int),
        .i_tx_go           (tx_go),
        .o_tx_active       (tx_active)
    );

    // -------------------------------------------------------------------------
    // Line interface adapter
    // -------------------------------------------------------------------------
    uart_phy_adapter #(
        .HAS_TX     (HAS_TX),
        .HAS_RX     (HAS_RX),
        .HAS_DE     (HAS_DE),
        .HAS_RE     (HAS_RE),
        .HAS_FLOW   (HAS_FLOW),
        .DEF_CTRL   (DEF_CTRL),
        .DEF_TIMING (DEF_TIMING)
    ) u_phy (
        .i_clk            (i_clk),
        .i_reset          (i_reset),
        .i_ctrl_wr        (phy_ctrl_wr),
        .i_timing_wr      (phy_timing_wr),
        .i_status_wr      (phy_status_wr),
        .i_wdata          (S_AXI_WDATA),
        .o_ctrl           (phy_ctrl),
        .o_timing         (phy_timing),
        .o_status         (phy_status),
        .i_uart_txd       (uart_txd),
        .i_uart_tx_active (tx_active),
        .o_uart_tx_go     (tx_go),
        .o_uart_rxd       (uart_rxd),
        .o_txd            (o_txd),
        .i_rxd            (i_rxd),
        .o_de             (o_de),
        .o_re_n           (o_re_n)
    );

endmodule

`default_nettype wire
