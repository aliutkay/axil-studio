// System testbench of the UART loopback example, the same design as examples/cmd_seq_rs485 (the
// blocks AXIL Studio generates for rs485_terminal.axproj.json and the packed IPs of hdl/v2005);
// only the programs differ.
//
//   TB master -> seq_main -> xbar SEQ_MAIN -+-> seq_gpio -> GPIO (LED 0 = OK, LED 1 = FAIL)
//                                           +-> seq_uart -> xbar SEQ_UART -+-> UART1 --+
//                                                                          +-> UART2 --+  crossed
// The cable is modelled here: UART1 TX -> UART2 RX (path A), UART2 TX -> UART1 RX (path B), and
// the TB can cut a wire or flip a bit on it. It prints "PHASE n", "R addr data" register reads and
// "LEDS ok fail"; run_loopback.py checks the output.
`timescale 1ns/1ps
`default_nettype none
module tb_loopback;
    parameter PMAIN = "seq_main.hex", PGPIO = "seq_gpio.hex", PUART = "seq_uart.hex";
    parameter NMAIN = 1, NGPIO = 1, NUART = 1;
    parameter BIT = 434;                     // clocks per bit: INITIAL_SETUP of axiluart_phy_pk

    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;

    // ---- upstream master (the PC), packed
    reg  [31:0] s_awaddr = 0, s_wdata = 0, s_araddr = 0;
    reg         s_awvalid = 0, s_wvalid = 0, s_bready = 0, s_arvalid = 0, s_rready = 0;
    wire [110:0] up_req = {s_awaddr, 3'b000, s_awvalid, s_wdata, 4'hF, s_wvalid, s_bready,
                           s_araddr, 3'b000, s_arvalid, s_rready};
    wire [40:0]  up_rsp;
    wire        s_awready = up_rsp[40], s_wready = up_rsp[39], s_bvalid = up_rsp[36];
    wire [1:0]  s_bresp = up_rsp[38:37], s_rresp = up_rsp[2:1];
    wire        s_arready = up_rsp[35], s_rvalid = up_rsp[0];
    wire [31:0] s_rdata = up_rsp[34:3];

    wire [110:0] main_m_req, x0_s0_req, x0_s1_req, gpio_m_req, uart_m_req, x1_s0_req, x1_s1_req;
    wire [40:0]  main_m_rsp, x0_s0_rsp, x0_s1_rsp, gpio_m_rsp, uart_m_rsp, x1_s0_rsp, x1_s1_rsp;
    wire [7:0]   ev0, ev1, ev2;
    wire         irq0, irq1, irq2;

    seq_main_cmd_seq_pk u_main (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .i_sys_halt(1'b0),
        .i_sys_run(1'b0), .i_ev(8'h00), .o_ev(ev0), .o_irq(irq0),
        .SLAVE_AXI_REQ(up_req), .SLAVE_AXI_RSP(up_rsp), .MASTER_AXI_REQ(main_m_req), .MASTER_AXI_RSP(main_m_rsp));
    axil_xbar_SEQ_MAIN_pk u_x0 (.ACLK(clk), .ARESETN(rst_n),
        .MASTER0_AXI_REQ(main_m_req), .MASTER0_AXI_RSP(main_m_rsp),
        .SLAVE0_AXI_REQ(x0_s0_req), .SLAVE0_AXI_RSP(x0_s0_rsp),
        .SLAVE1_AXI_REQ(x0_s1_req), .SLAVE1_AXI_RSP(x0_s1_rsp));
    seq_gpio_cmd_seq_pk u_sgpio (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .i_sys_halt(1'b0),
        .i_sys_run(1'b0), .i_ev(8'h00), .o_ev(ev1), .o_irq(irq1),
        .SLAVE_AXI_REQ(x0_s0_req), .SLAVE_AXI_RSP(x0_s0_rsp), .MASTER_AXI_REQ(gpio_m_req), .MASTER_AXI_RSP(gpio_m_rsp));
    seq_uart_cmd_seq_pk u_suart (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .i_sys_halt(1'b0),
        .i_sys_run(1'b0), .i_ev(8'h00), .o_ev(ev2), .o_irq(irq2),
        .SLAVE_AXI_REQ(x0_s1_req), .SLAVE_AXI_RSP(x0_s1_rsp), .MASTER_AXI_REQ(uart_m_req), .MASTER_AXI_RSP(uart_m_rsp));

    wire [29:0] gpio; wire gpio_int;
    axilgpio_pk u_gpio (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .o_gpio(gpio), .i_gpio(5'd0),
        .o_int(gpio_int), .SLAVE_AXI_REQ(gpio_m_req), .SLAVE_AXI_RSP(gpio_m_rsp));

    axil_xbar_SEQ_UART_pk u_x1 (.ACLK(clk), .ARESETN(rst_n),
        .MASTER0_AXI_REQ(uart_m_req), .MASTER0_AXI_RSP(uart_m_rsp),
        .SLAVE0_AXI_REQ(x1_s0_req), .SLAVE0_AXI_RSP(x1_s0_rsp),
        .SLAVE1_AXI_REQ(x1_s1_req), .SLAVE1_AXI_RSP(x1_s1_rsp));

    wire rxd1, rxd2;
    wire txd1, txd2, de1, de2, ren1, ren2, rts1, rts2, i10, i11, i12, i13, i20, i21, i22, i23;
    axiluart_phy_pk u_uart1 (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .o_txd(txd1), .i_rxd(rxd1),
        .o_de(de1), .o_re_n(ren1), .o_rts_n(rts1), .i_cts_n(1'b0), .o_uart_rx_int(i10),
        .o_uart_tx_int(i11), .o_uart_rxfifo_int(i12), .o_uart_txfifo_int(i13),
        .SLAVE_AXI_REQ(x1_s0_req), .SLAVE_AXI_RSP(x1_s0_rsp));
    axiluart_phy_pk u_uart2 (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n), .o_txd(txd2), .i_rxd(rxd2),
        .o_de(de2), .o_re_n(ren2), .o_rts_n(rts2), .i_cts_n(1'b0), .o_uart_rx_int(i20),
        .o_uart_tx_int(i21), .o_uart_rxfifo_int(i22), .o_uart_txfifo_int(i23),
        .SLAVE_AXI_REQ(x1_s1_req), .SLAVE_AXI_RSP(x1_s1_rsp));

    // ---- upstream tasks (AW and W together; signals change at the falling edge)
    integer bus_errors = 0;
    task wr(input [31:0] addr, input [31:0] data);
        begin
            @(negedge clk);
            s_awaddr = addr; s_wdata = data; s_awvalid = 1; s_wvalid = 1;
            #1;
            while (!(s_awready && s_wready)) @(negedge clk);
            @(posedge clk); #1;
            s_awvalid = 0; s_wvalid = 0;
            @(negedge clk);
            s_bready = 1;
            while (!s_bvalid) @(negedge clk);
            if (s_bresp != 0) begin bus_errors = bus_errors + 1; $display("BUSERR W %h", addr); end
            @(posedge clk); #1;
            s_bready = 0;
        end
    endtask
    task rd(input [31:0] addr, output [31:0] data);
        begin
            @(negedge clk);
            s_araddr = addr; s_arvalid = 1;
            #1;
            while (!s_arready) @(negedge clk);
            @(posedge clk); #1;
            s_arvalid = 0;
            @(negedge clk);
            s_rready = 1;
            while (!s_rvalid) @(negedge clk);
            data = s_rdata;
            if (s_rresp != 0) begin bus_errors = bus_errors + 1; $display("BUSERR R %h", addr); end
            @(posedge clk); #1;
            s_rready = 0;
        end
    endtask
    task show(input [31:0] addr);
        reg [31:0] v;
        begin rd(addr, v); $display("R %h %h", addr, v); end
    endtask

    reg [63:0] pm [0:511];
    integer i;
    reg [31:0] v;
    task load(input [31:0] base, input integer n);
        begin
            wr(base + 32'h08, 32'h2);                    // STOP
            wr(base + 32'h20, 0);                        // IMEM_PTR
            for (i = 0; i < n; i = i + 1) begin
                wr(base + 32'h24, pm[i][31:0]);
                wr(base + 32'h24, pm[i][63:32]);
            end
            rd(base + 32'h28, v);
            $display("LOADED %h %0d", base, v);
        end
    endtask

    // ---- the loopback cable, with faults
    reg cut_a = 0, cut_b = 0, flip_a = 0, flip_b = 0;
    assign rxd2 = cut_a ? 1'b1 : txd1 ^ flip_a;       // path A: UART1 -> UART2
    assign rxd1 = cut_b ? 1'b1 : txd2 ^ flip_b;       // path B: UART2 -> UART1
    task flip_bit_b;                                   // one wrong bit in a byte UART2 sends
        begin
            @(negedge txd2);                           // a start bit
            repeat (BIT * 4 + BIT / 4) @(posedge clk); // into data bit 2
            flip_b = 1; repeat (BIT / 2) @(posedge clk); flip_b = 0;
        end
    endtask
    task leds;
        begin $display("LEDS %0d %0d", gpio[0], gpio[1]); end
    endtask
    task rounds_done(input integer n);                 // wait until SEQ_MAIN ran n more rounds
        reg [31:0] r0, r1;
        begin
            rd(32'h0000_0400, r0);
            r1 = r0;
            while (r1 < r0 + n) begin repeat (2000) @(posedge clk); rd(32'h0000_0400, r1); end
            repeat (3000) @(posedge clk);             // SEQ_GPIO took the result (BLINK 300 dark)
        end
    endtask
    task stats;
        begin
            show(32'h0000_0400); show(32'h0000_0404); show(32'h0000_0408);    // SEQ_MAIN ROUNDS LAST FAILS
            show(32'h0004_0438); show(32'h0004_043C); show(32'h0004_0440); show(32'h0004_0444);  // GOOD BAD LOST LINE
            show(32'h0004_044C); show(32'h0004_0450); show(32'h0004_0454); show(32'h0004_0458);  // F_*
            show(32'h0002_0414);                                               // SEQ_GPIO HELD
        end
    endtask

    integer LEN = 32;
    initial begin
        repeat (20) @(posedge clk); rst_n = 1; repeat (20) @(posedge clk);
        $readmemh(PGPIO, pm, 0, NGPIO - 1); load(32'h0002_0000, NGPIO);
        $readmemh(PUART, pm, 0, NUART - 1); load(32'h0004_0000, NUART);
        $readmemh(PMAIN, pm, 0, NMAIN - 1); load(32'h0000_0000, NMAIN);
        wr(32'h0002_0418, 32'd300);                   // SEQ_GPIO BLINK: short
        wr(32'h0004_0418, 40 * BIT + 1000);           // SEQ_UART TMO: about 4 byte times
        wr(32'h0000_0410, 64 * BIT + 4000);           // SEQ_MAIN GAP (> 16 bit times idle line at the start)
        wr(32'h0000_0420, 400 * BIT * 2 * LEN / 10 + 100000);   // WDOG
        wr(32'h0000_0008, 32'h61);                    // SEQ_MAIN: RUN + PT_WR_RUN + ERR_HALT
        repeat (1000) @(posedge clk);
        show(32'h0002_000C); show(32'h0004_000C);     // children started (STATUS BUSY)
        $display("LAMP %0d %0d", gpio[0], gpio[1]);   // lamp test: both on
        rounds_done(3);
        $display("PHASE 1"); leds; stats;             // cable fine: OK LED
        cut_a = 1;                                    // UART1 -> UART2 wire cut
        rounds_done(2);
        $display("PHASE 2"); leds; stats;             // lost bytes on path A: FAIL LED
        cut_a = 0;
        rounds_done(2);
        $display("PHASE 3"); leds; stats;             // fine again: OK LED
        wr(32'h0002_0410, 1);                         // HOLD on
        rounds_done(1);
        wr(32'h0004_0438, 0);                         // GOOD := 0 (to see what the next rounds do)
        flip_bit_b;                                   // one wrong bit on path B
        rounds_done(2);
        $display("PHASE 4"); leds; stats;             // FAIL LED, held
        rounds_done(1);
        $display("PHASE 5"); leds; show(32'h0000_0404); show(32'h0002_0414);  // passed, still held
        wr(32'h0002_0414, 0);                         // let go
        rounds_done(1);
        $display("PHASE 6"); leds;
        show(32'h0000_000C); show(32'h0002_000C); show(32'h0004_000C);   // STATUS of the three
        $display("BUSERR %0d", bus_errors);
        $finish;
    end
endmodule
