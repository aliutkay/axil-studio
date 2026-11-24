// System testbench of the RS-485 terminal example, built like the HDL Designer design: the packed
// blocks AXIL Studio generates for this project (seq_*_cmd_seq_pk, axil_xbar_SEQ_*_pk) and the
// packed IPs of hdl/v2005 (axilgpio_pk, axiluart_phy_pk; run_sim.py can make the UART faster).
//
//   TB master -> seq_main -> xbar SEQ_MAIN -+-> seq_gpio -> GPIO
//                                           +-> seq_uart -> xbar SEQ_UART -+-> UART1
//                                                                          +-> UART2
// The TB loads the three programs through the upstream port, starts SEQ_MAIN (CTRL 0x61), sends
// bytes on the RS-485 lines and prints what comes back:
//   "TX1 xx" / "TX2 xx"  a byte sent by UART1 / UART2      "LED n"  rising edge n of o_gpio[0]
//   "DE1 n" / "DE2 n"    driver enable pulses              "R addr data"  register reads
// run_sim.py checks the output.
`timescale 1ns/1ps
`default_nettype none
module tb_rs485;
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

    reg  rxd1 = 1, rxd2 = 1;
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

    // ---- RS-485 lines: send a byte (8N1), decode what the UARTs send, count LED and DE pulses
    task send(input integer line, input [7:0] c);
        integer b;
        begin
            if (line == 1) rxd1 = 0; else rxd2 = 0;
            repeat (BIT) @(posedge clk);
            for (b = 0; b < 8; b = b + 1) begin
                if (line == 1) rxd1 = c[b]; else rxd2 = c[b];
                repeat (BIT) @(posedge clk);
            end
            rxd1 = 1; rxd2 = 1;
            repeat (2 * BIT) @(posedge clk);
        end
    endtask
    integer k1, k2; reg [7:0] c1, c2;
    always begin
        @(negedge txd1); repeat (BIT + BIT / 2) @(posedge clk);
        for (k1 = 0; k1 < 8; k1 = k1 + 1) begin c1[k1] = txd1; repeat (BIT) @(posedge clk); end
        $display("TX1 %h", c1);
    end
    always begin
        @(negedge txd2); repeat (BIT + BIT / 2) @(posedge clk);
        for (k2 = 0; k2 < 8; k2 = k2 + 1) begin c2[k2] = txd2; repeat (BIT) @(posedge clk); end
        $display("TX2 %h", c2);
    end
    integer leds = 0, dp1 = 0, dp2 = 0;
    always @(posedge gpio[0]) if (rst_n) begin leds = leds + 1; $display("LED %0d", leds); end
    always @(posedge de1) if (rst_n) dp1 = dp1 + 1;
    always @(posedge de2) if (rst_n) dp2 = dp2 + 1;

`ifdef DEBUG_UART
    // SEQ_UART's accesses to the UARTs (packed request: AWADDR [110:79], AWVALID [75], WDATA [74:43])
    reg [31:0] dbg_ar;
    always @(posedge clk) begin
        if (uart_m_req[75] && uart_m_rsp[40]) $display("%0t UW %h %h", $time, uart_m_req[110:79], uart_m_req[74:43]);
        if (uart_m_req[1] && uart_m_rsp[35]) dbg_ar <= uart_m_req[36:5];
        if (uart_m_rsp[0] && uart_m_req[0] && dbg_ar[7:0] == 8'h08 && !uart_m_rsp[11])
            $display("%0t UR %h %h", $time, dbg_ar, uart_m_rsp[34:3]);
    end
`endif

    task wait_quiet;                                  // reply sent and blinks over
        begin repeat (120 * BIT + 8000) @(posedge clk); end
    endtask

    initial begin
        repeat (20) @(posedge clk); rst_n = 1; repeat (20) @(posedge clk);
        $readmemh(PGPIO, pm, 0, NGPIO - 1); load(32'h0002_0000, NGPIO);
        $readmemh(PUART, pm, 0, NUART - 1); load(32'h0004_0000, NUART);
        $readmemh(PMAIN, pm, 0, NMAIN - 1); load(32'h0000_0000, NMAIN);
        wr(32'h0002_040C, 32'd300);                   // SEQ_GPIO ON / OFF: short blinks
        wr(32'h0002_0410, 32'd300);
        wr(32'h0000_0008, 32'h61);                    // SEQ_MAIN: RUN + PT_WR_RUN + ERR_HALT
        repeat (40 * BIT + 2000) @(posedge clk);      // rxuart ignores the line for 16 bit times after reset
        show(32'h0002_000C); show(32'h0004_000C);     // children started (STATUS BUSY)
        $display("PHASE 1");                          // UART1 receives, UART2 answers
        send(1, "3"); wait_quiet;
        send(1, "A"); wait_quiet;
        send(1, 8'h0D); wait_quiet;                   // CR: no answer, no blink
        $display("DE %0d %0d", dp1, dp2);
        $display("PHASE 2");                          // swap from the PC, through running SEQ_MAIN
        wr(32'h0004_0400, 32'h0005_1000);
        wr(32'h0004_0404, 32'h0005_0000);
        repeat (2000) @(posedge clk);
        send(2, "2"); wait_quiet;
        $display("DE %0d %0d", dp1, dp2);
        show(32'h0004_0410); show(32'h0004_041C); show(32'h0004_0418);   // SEQ_UART COUNT, EVENTS, ERRORS
        show(32'h0000_0400); show(32'h0002_0414);                         // SEQ_MAIN FWD, SEQ_GPIO DONE
        show(32'h0000_000C); show(32'h0002_000C); show(32'h0004_000C);   // STATUS of the three
        $display("BUSERR %0d", bus_errors);
        $finish;
    end
endmodule
