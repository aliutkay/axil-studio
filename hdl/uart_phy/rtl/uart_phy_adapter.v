////////////////////////////////////////////////////////////////////////////////
// Module      : uart_phy_adapter
// Description : Line-interface adapter between a UART core and an external
//               transceiver (TTL, RS-232, RS-422, RS-485 2/4-wire, LIN or
//               tri-state-buffer single-wire). Handles driver/receiver enable,
//               duplex mode, echo suppression, line polarity and TX/RX enables.
//
//               Generics define the hardware that exists on the board;
//               PHY_CTRL / PHY_TIMING select the behaviour at run time.
//               New settings are applied while the transmitter is idle.
//
// PHY_CTRL    : [1:0]  DE_MODE   0: DE low  1: DE high  2: DE while transmitting
//               [2]    HALF      Half duplex: receiver disabled while driving
//               [3]    ECHO      Keep receiver enabled while driving (HALF only)
//               [4]    TX_INV    Invert TX line
//               [5]    RX_INV    Invert RX line
//               [6]    TX_EN     Transmitter enable
//               [7]    RX_EN     Receiver enable
//               [31]   APPLY_NOW Apply immediately, even if the transmitter is busy
// PHY_TIMING  : [15:0] LEAD      DE-to-start-bit delay  (clock cycles)
//               [31:16]TAIL      Stop-bit-to-DE-release (clock cycles)
// PHY_STATUS  : [0] HAS_TX  [1] HAS_RX  [2] HAS_DE  [3] HAS_RE  [4] HAS_FLOW
//               [8] TX_ACTIVE  [9] DE  [10] RE_N  [11] PENDING  [12] CFG_ERR (W1C)
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module uart_phy_adapter #(
    parameter [0:0]  HAS_TX     = 1'b1,
    parameter [0:0]  HAS_RX     = 1'b1,
    parameter [0:0]  HAS_DE     = 1'b1,
    parameter [0:0]  HAS_RE     = 1'b1,
    parameter [0:0]  HAS_FLOW   = 1'b0,
    parameter [31:0] DEF_CTRL   = 32'h0000_00C2,  // TX_EN | RX_EN | DE_MODE=2
    parameter [31:0] DEF_TIMING = 32'h00FA_0032   // TAIL=250, LEAD=50
) (
    input  wire        i_clk,
    input  wire        i_reset,

    // Register interface
    input  wire        i_ctrl_wr,
    input  wire        i_timing_wr,
    input  wire        i_status_wr,
    input  wire [31:0] i_wdata,
    output wire [31:0] o_ctrl,
    output wire [31:0] o_timing,
    output wire [31:0] o_status,

    // UART core side
    input  wire        i_uart_txd,
    input  wire        i_uart_tx_active,
    output wire        o_uart_tx_go,
    output wire        o_uart_rxd,

    // Transceiver side
    output wire        o_txd,
    input  wire        i_rxd,
    output wire        o_de,
    output wire        o_re_n
);

    localparam [1:0] DE_LOW = 2'd0, DE_HIGH = 2'd1, DE_TX = 2'd2;
    localparam [31:0] CTRL_MASK = 32'h0000_00FF;
    localparam [1:0] S_IDLE = 2'd0, S_LEAD = 2'd1, S_ACTIVE = 2'd2, S_TAIL = 2'd3;

    // -------------------------------------------------------------------------
    // Configuration: shadow registers, validation and idle-time application
    // -------------------------------------------------------------------------
    reg  [31:0] act_ctrl, act_timing, pend_ctrl, pend_timing;
    reg         pending, cfg_err;
    reg  [1:0]  state;

    wire [31:0] new_ctrl  = i_wdata & CTRL_MASK;
    wire [1:0]  new_de    = new_ctrl[1:0];
    wire        new_valid = (new_de != 2'd3)
                         && (HAS_DE || new_de == DE_LOW)
                         && (HAS_TX || !new_ctrl[6])
                         && (HAS_RX || !new_ctrl[7])
                         && (!new_ctrl[3] || new_ctrl[2]);

    wire        tx_idle   = (state == S_IDLE) && (!i_uart_tx_active || !act_ctrl[6]);
    wire        apply_now = i_ctrl_wr && i_wdata[31] && new_valid;

    initial begin
        act_ctrl    = DEF_CTRL & CTRL_MASK;
        act_timing  = DEF_TIMING;
        pend_ctrl   = DEF_CTRL & CTRL_MASK;
        pend_timing = DEF_TIMING;
        pending     = 1'b0;
        cfg_err     = 1'b0;
    end

    always @(posedge i_clk)
        if (i_reset) begin
            act_ctrl    <= DEF_CTRL & CTRL_MASK;
            act_timing  <= DEF_TIMING;
            pend_ctrl   <= DEF_CTRL & CTRL_MASK;
            pend_timing <= DEF_TIMING;
            pending     <= 1'b0;
            cfg_err     <= 1'b0;
        end else begin
            if (i_ctrl_wr) begin
                if (new_valid) begin
                    pend_ctrl <= new_ctrl;
                    pending   <= 1'b1;
                end else
                    cfg_err   <= 1'b1;
            end
            if (i_timing_wr) begin
                pend_timing <= i_wdata;
                pending     <= 1'b1;
            end
            if (i_status_wr && i_wdata[12])
                cfg_err <= 1'b0;

            if (apply_now) begin
                act_ctrl   <= new_ctrl;
                act_timing <= i_timing_wr ? i_wdata : pend_timing;
                pending    <= 1'b0;
            end else if (pending && tx_idle && !i_ctrl_wr && !i_timing_wr) begin
                act_ctrl   <= pend_ctrl;
                act_timing <= pend_timing;
                pending    <= 1'b0;
            end
        end

    wire [1:0]  de_mode = HAS_DE ? act_ctrl[1:0] : DE_LOW;
    wire        half    = act_ctrl[2];
    wire        echo    = act_ctrl[3];
    wire        tx_inv  = act_ctrl[4];
    wire        rx_inv  = act_ctrl[5];
    wire        tx_en   = HAS_TX && act_ctrl[6];
    wire        rx_en   = HAS_RX && act_ctrl[7];
    wire [15:0] lead    = act_timing[15:0];
    wire [15:0] tail    = act_timing[31:16];

    // -------------------------------------------------------------------------
    // Driver enable sequencing: IDLE -> LEAD -> ACTIVE -> TAIL -> IDLE
    // -------------------------------------------------------------------------
    reg [15:0] cnt;
    wire       want_tx = tx_en && i_uart_tx_active;

    initial state = S_IDLE;
    initial cnt   = 16'd0;

    always @(posedge i_clk)
        if (i_reset || !tx_en) begin
            state <= S_IDLE;
            cnt   <= 16'd0;
        end else case (state)
            S_IDLE:
                if (want_tx) begin
                    state <= (lead == 16'd0) ? S_ACTIVE : S_LEAD;
                    cnt   <= lead;
                end
            S_LEAD:
                if (cnt <= 16'd1)
                    state <= S_ACTIVE;
                else
                    cnt <= cnt - 1'b1;
            S_ACTIVE:
                if (!i_uart_tx_active) begin
                    state <= (tail == 16'd0) ? S_IDLE : S_TAIL;
                    cnt   <= tail;
                end
            S_TAIL:
                if (want_tx)
                    state <= S_ACTIVE;
                else if (cnt <= 16'd1)
                    state <= S_IDLE;
                else
                    cnt <= cnt - 1'b1;
            default:
                state <= S_IDLE;
        endcase

    wire driving = (state != S_IDLE);

    // -------------------------------------------------------------------------
    // TX path
    // -------------------------------------------------------------------------
    assign o_uart_tx_go = tx_en && (state == S_ACTIVE);
    assign o_txd        = HAS_TX ? ((tx_en ? i_uart_txd : 1'b1) ^ tx_inv) : 1'b1;
    assign o_de         = (de_mode == DE_HIGH) || (de_mode == DE_TX && driving);

    // -------------------------------------------------------------------------
    // RX path: synchronize, then mask while driving in half duplex
    // -------------------------------------------------------------------------
    reg [1:0] rx_sync;
    initial rx_sync = 2'b11;
    always @(posedge i_clk)
        rx_sync <= {rx_sync[0], i_rxd ^ rx_inv};

    wire rx_blocked = !rx_en || (half && !echo && driving);

    assign o_uart_rxd = HAS_RX ? (rx_blocked ? 1'b1 : rx_sync[1]) : 1'b1;
    assign o_re_n     = HAS_RE ? rx_blocked : 1'b0;

    // -------------------------------------------------------------------------
    // Register read-back
    // -------------------------------------------------------------------------
    assign o_ctrl   = act_ctrl;
    assign o_timing = act_timing;
    assign o_status = {19'd0, cfg_err, pending, o_re_n, o_de, driving,
                       3'd0, HAS_FLOW, HAS_RE, HAS_DE, HAS_RX, HAS_TX};

endmodule

`default_nettype wire
