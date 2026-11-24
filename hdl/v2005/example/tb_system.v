// Testbench of system_top: the PC side sends hexbus commands over the debug UART; the replies
// are printed line by line, the byte the UART PHY sends on o_txd is printed as "TXD xx".
// Run: see hdl/v2005/README.md (tests/test_v2005.py checks the output).
`timescale 1ns/1ps
`default_nettype none
module tb_system;
    localparam CPB  = 10;          // debug UART: CLK_FREQ_HZ / BAUD_RATE below
    localparam UCPB = 16;          // UART PHY: UART_SETUP below
    reg clk = 0, rst_n = 0, dbg_rx = 1;
    always #5 clk = !clk;
    wire dbg_tx, txd, de, re_n;
    wire [7:0] gpio;

    system_top #(.CLK_FREQ_HZ(1_000_000), .BAUD_RATE(100_000), .UART_SETUP(31'd16)) dut (
        .clk(clk), .rst_n(rst_n), .i_dbg_rx(dbg_rx), .o_dbg_tx(dbg_tx),
        .o_gpio(gpio), .i_gpio(4'h0), .o_txd(txd), .i_rxd(1'b1), .o_de(de), .o_re_n(re_n));

    task send(input [7:0] c);
        integer i;
        begin
            dbg_rx = 0; repeat (CPB) @(posedge clk);
            for (i = 0; i < 8; i = i + 1) begin dbg_rx = c[i]; repeat (CPB) @(posedge clk); end
            dbg_rx = 1; repeat (2 * CPB) @(posedge clk);
        end
    endtask

    task sends(input [8*32-1:0] s);    // a command, NUL-padded on the left
        integer k;
        begin
            for (k = 31; k >= 0; k = k - 1)
                if (s[8*k +: 8] != 0) send(s[8*k +: 8]);
            repeat (3000) @(posedge clk);
        end
    endtask

    // replies of the bridge ('Z' idle characters are not printed)
    integer b; reg [7:0] ch;
    always begin
        @(negedge dbg_tx); repeat (CPB + CPB / 2) @(posedge clk);
        for (b = 0; b < 8; b = b + 1) begin ch[b] = dbg_tx; repeat (CPB) @(posedge clk); end
        if (ch == 8'h0a) $write("\n"); else if (ch != 8'h0d && ch != "Z") $write("%c", ch);
    end

    // the byte sent by the UART PHY (8N1)
    integer u; reg [7:0] uc;
    always begin
        @(negedge txd); repeat (UCPB + UCPB / 2) @(posedge clk);
        for (u = 0; u < 8; u = u + 1) begin uc[u] = txd; repeat (UCPB) @(posedge clk); end
        $display("TXD %h", uc);
    end

    initial begin
        repeat (20) @(posedge clk); rst_n = 1; repeat (20) @(posedge clk);
        sends("A3010001W5a\n");           // GPIO LOAD = 0x5a (data in lower-case hex)
        $display("GPIO %h", gpio);
        sends("R\n");                     // read it back
        sends("A3020015R\n");             // UART PHY_TIMING (reset 0x00fa0032)
        sends("A302000dW41\n");           // UART TXREG = 'A'
        sends("A5000001R\n");             // nothing there: DECERR -> E
        $display("DONE");
        $finish;
    end
endmodule
