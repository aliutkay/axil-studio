////////////////////////////////////////////////////////////////////////////////
// Testbench : tb_adapter_defaults
// Checks that PHY_CTRL after reset is limited to the hardware (HAS_* generics)
// and that writing the value read back is accepted (no CFG_ERR), for several
// generic combinations.
//   iverilog -g2012 -o sim tb/tb_adapter_defaults.v ../v2005/uart_phy/uart_phy_adapter.v && vvp sim
////////////////////////////////////////////////////////////////////////////////
`timescale 1ns/1ps
`default_nettype none

module adapter_case #(
    parameter [0:0]  HAS_TX = 1'b1, HAS_RX = 1'b1, HAS_DE = 1'b1,
    parameter [31:0] DEF_CTRL = 32'h0000_00C2,
    parameter [31:0] EXPECT   = 32'h0000_00C2
) (input wire clk, input wire rst, output reg fail);
    reg         ctrl_wr = 1'b0;
    reg  [31:0] wdata   = 32'd0;
    wire [31:0] ctrl, timing, status;
    wire        txd, de, re_n, tx_go, rxd_core;

    uart_phy_adapter #(.HAS_TX(HAS_TX), .HAS_RX(HAS_RX), .HAS_DE(HAS_DE), .DEF_CTRL(DEF_CTRL)) dut (
        .i_clk(clk), .i_reset(rst),
        .i_ctrl_wr(ctrl_wr), .i_timing_wr(1'b0), .i_status_wr(1'b0), .i_wdata(wdata),
        .o_ctrl(ctrl), .o_timing(timing), .o_status(status),
        .i_uart_txd(1'b1), .i_uart_tx_active(1'b0), .o_uart_tx_go(tx_go), .o_uart_rxd(rxd_core),
        .o_txd(txd), .i_rxd(1'b1), .o_de(de), .o_re_n(re_n));

    initial begin
        fail = 1'b0;
        @(negedge rst);
        @(posedge clk); #1;
        if (ctrl !== EXPECT) begin
            $display("FAIL HAS_TX=%0d HAS_RX=%0d HAS_DE=%0d DEF_CTRL=%h: PHY_CTRL after reset %h, expected %h",
                     HAS_TX, HAS_RX, HAS_DE, DEF_CTRL, ctrl, EXPECT);
            fail = 1'b1;
        end
        wdata = ctrl;                     // write back what was read
        ctrl_wr = 1'b1;
        @(posedge clk); #1;
        ctrl_wr = 1'b0;
        @(posedge clk); #1;
        if (status[12]) begin
            $display("FAIL HAS_TX=%0d HAS_RX=%0d HAS_DE=%0d DEF_CTRL=%h: writing back %h set CFG_ERR",
                     HAS_TX, HAS_RX, HAS_DE, DEF_CTRL, wdata);
            fail = 1'b1;
        end
    end
endmodule

module tb_adapter_defaults;
    reg clk = 1'b0, rst = 1'b1;
    always #5 clk = ~clk;
    wire [5:0] f;

    // full hardware: the default stays
    adapter_case #(.DEF_CTRL(32'h0000_00C2), .EXPECT(32'h0000_00C2)) c0 (clk, rst, f[0]);
    // no DE pin: DE_MODE 2 becomes DE low
    adapter_case #(.HAS_DE(1'b0), .DEF_CTRL(32'h0000_00C2), .EXPECT(32'h0000_00C0)) c1 (clk, rst, f[1]);
    // receive-only IP: no TX_EN
    adapter_case #(.HAS_TX(1'b0), .HAS_DE(1'b0), .DEF_CTRL(32'h0000_00C2), .EXPECT(32'h0000_0080)) c2 (clk, rst, f[2]);
    // transmit-only IP: no RX_EN
    adapter_case #(.HAS_RX(1'b0), .DEF_CTRL(32'h0000_00C6), .EXPECT(32'h0000_0046)) c3 (clk, rst, f[3]);
    // ECHO without HALF and the invalid DE_MODE 3 are dropped; polarity bits stay
    adapter_case #(.DEF_CTRL(32'h0000_00FB), .EXPECT(32'h0000_00F0)) c4 (clk, rst, f[4]);
    // only the defined bits [7:0] are kept
    adapter_case #(.DEF_CTRL(32'h8000_01CE), .EXPECT(32'h0000_00CE)) c5 (clk, rst, f[5]);

    initial begin
        repeat (3) @(posedge clk);
        rst = 1'b0;
        repeat (10) @(posedge clk);
        if (|f) begin
            $display("tb_adapter_defaults: FAILED");
            $fatal(1);
        end
        $display("tb_adapter_defaults: 6 cases, 0 errors: PASSED");
        $finish;
    end
endmodule
