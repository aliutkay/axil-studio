`timescale 1ns/1ps
`default_nettype none
module tb;
    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg         awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    reg  [4:0]  awaddr=0, araddr=0;
    reg  [31:0] wdata=0;
    wire        awready, wready, bvalid, arready, rvalid;
    wire [1:0]  bresp, rresp;
    wire [31:0] rdata;
    wire txd, de, re_n, rts_n;
    reg  [1:0] loop_mode = 0;     // 0: loopback, 1: RS-485 bus model, 2: testbench driver
    reg  tb_line = 1;
    wire bus   = de ? txd : 1'b1; // pulled-up 2-wire bus
    wire ro    = re_n ? 1'b1 : (loop_mode == 2) ? tb_line : (loop_mode == 1) ? bus : txd;

    axiluart_phy #(.INITIAL_SETUP(31'd16), .DEF_TIMING({16'd20, 16'd8})) dut (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rstn),
        .S_AXI_AWVALID(awvalid), .S_AXI_AWREADY(awready), .S_AXI_AWADDR(awaddr), .S_AXI_AWPROT(3'd0),
        .S_AXI_WVALID(wvalid), .S_AXI_WREADY(wready), .S_AXI_WDATA(wdata), .S_AXI_WSTRB(4'hF),
        .S_AXI_BVALID(bvalid), .S_AXI_BREADY(bready), .S_AXI_BRESP(bresp),
        .S_AXI_ARVALID(arvalid), .S_AXI_ARREADY(arready), .S_AXI_ARADDR(araddr), .S_AXI_ARPROT(3'd0),
        .S_AXI_RVALID(rvalid), .S_AXI_RREADY(rready), .S_AXI_RDATA(rdata), .S_AXI_RRESP(rresp),
        .o_txd(txd), .i_rxd(ro), .o_de(de), .o_re_n(re_n), .o_rts_n(rts_n), .i_cts_n(1'b0),
        .o_uart_rx_int(), .o_uart_tx_int(), .o_uart_rxfifo_int(), .o_uart_txfifo_int());

    integer errors = 0;
    task axi_write(input [4:0] a, input [31:0] d);
        begin
            @(posedge clk); awaddr <= a; wdata <= d; awvalid <= 1; wvalid <= 1; bready <= 1;
            fork
                begin @(posedge clk); while (!awready) @(posedge clk); awvalid <= 0; end
                begin @(posedge clk); while (!wready)  @(posedge clk); wvalid  <= 0; end
            join
            while (!bvalid) @(posedge clk);
            @(posedge clk); bready <= 0;
        end
    endtask
    task axi_read(input [4:0] a, output [31:0] d);
        begin
            @(posedge clk); araddr <= a; arvalid <= 1; rready <= 1;
            @(posedge clk); while (!arready) @(posedge clk); arvalid <= 0;
            while (!rvalid) @(posedge clk);
            d = rdata; @(posedge clk); rready <= 0;
        end
    endtask
    task check(input [255:0] name, input [31:0] got, input [31:0] exp);
        if (got !== exp) begin errors = errors + 1; $display("FAIL %0s: got %h exp %h", name, got, exp); end
        else $display("ok   %0s = %h", name, got);
    endtask

    // Timing monitor: DE rise -> start bit, stop bit end -> DE fall
    integer t_de_rise, t_start, t_last_rise, lead_meas, tail_meas;
    always @(posedge de) t_de_rise = $time;
    always @(negedge txd) if (de && t_start == 0) t_start = $time;
    always @(posedge txd) t_last_rise = $time;
    always @(negedge de) tail_meas = ($time - t_last_rise) / 10;

    // Drive one 8-bit frame on tb_line: even parity bit inverted when bad_par
    task send_par_byte(input [7:0] d, input bad_par, input integer stops);
        integer k;
        begin
            tb_line = 0; repeat (16) @(posedge clk);
            for (k = 0; k < 8; k = k + 1) begin tb_line = d[k]; repeat (16) @(posedge clk); end
            tb_line = (^d) ^ bad_par; repeat (16) @(posedge clk);   // even parity
            tb_line = 1; repeat (16 * stops) @(posedge clk);
            tb_line = 1; repeat (64) @(posedge clk);
        end
    endtask

    // Frame with even parity; optional bad parity and/or bad (low) first stop bit
    task send_frame(input [7:0] d, input bad_par, input bad_stop, input integer stops);
        integer k;
        begin
            tb_line = 0; repeat (16) @(posedge clk);
            for (k = 0; k < 8; k = k + 1) begin tb_line = d[k]; repeat (16) @(posedge clk); end
            tb_line = (^d) ^ bad_par; repeat (16) @(posedge clk);
            tb_line = !bad_stop; repeat (16) @(posedge clk);
            tb_line = 1; repeat (16 * stops + 48) @(posedge clk);
        end
    endtask

    reg [31:0] v;
    integer i;
    initial begin
        t_start = 0;
        repeat (5) @(posedge clk); rstn <= 1; repeat (400) @(posedge clk);  // RX idle detect

        axi_read(5'h00, v);  check("SETUP after reset", v, 32'h4000_0010);
        axi_read(5'h10, v);  check("PHY_CTRL default", v, 32'hC2);
        axi_read(5'h14, v);  check("PHY_TIMING default", v, {16'd20, 16'd8});
        axi_read(5'h18, v);  check("PHY_STATUS caps", v & 32'h1F, 32'h0F);

        // 1) Full duplex loopback, DE while transmitting
        axi_write(5'h0C, 32'h55);
        repeat (300) @(posedge clk);
        lead_meas = (t_start - t_de_rise) / 10;
        $display("     lead measured = %0d clk (cfg 8), DE after last edge = %0d clk (stop bit 16 + tail 20)", lead_meas, tail_meas);
        if (lead_meas < 8) begin errors = errors + 1; $display("FAIL lead too short"); end
        check("DE released", de, 0);
        axi_read(5'h08, v);  check("RX loopback byte", v & 32'h1FF, 32'h055);

        // 2) Half duplex, no echo, on RS-485 bus model: own byte must not be received
        loop_mode = 1;
        axi_write(5'h10, 32'hC6);  // TX_EN|RX_EN|HALF|DE_TX
        axi_read(5'h10, v);  check("PHY_CTRL half", v, 32'hC6);
        axi_write(5'h0C, 32'hA3);
        @(posedge de); #1 check("RE_N high while driving", re_n, 1);
        repeat (300) @(posedge clk);
        axi_read(5'h08, v);  check("RX empty (no echo)", v[8], 1);

        // 3) Half duplex with echo
        axi_write(5'h10, 32'hCE);
        axi_write(5'h0C, 32'h3C);
        repeat (300) @(posedge clk);
        axi_read(5'h08, v);  check("RX echo byte", v & 32'h1FF, 32'h03C);

        // 4) Burst of 3 bytes: DE must stay high between bytes
        axi_write(5'h0C, 32'h01); axi_write(5'h0C, 32'h02); axi_write(5'h0C, 32'h03);
        wait (de);
        i = 0; while (de) begin @(posedge clk); i = i + 1; end
        $display("     DE high for %0d clk over 3-byte burst (>= %0d expected)", i, 3*10*16);
        check("DE continuous over burst", i >= 3*10*16, 1);
        repeat (300) @(posedge clk);
        axi_read(5'h08, v); check("burst rx 1", v & 32'hFF, 1);
        axi_read(5'h08, v); check("burst rx 2", v & 32'hFF, 2);
        axi_read(5'h08, v); check("burst rx 3", v & 32'hFF, 3);

        // 5) Invalid configuration: DE_MODE=3 rejected, CFG_ERR set and cleared
        axi_write(5'h10, 32'hC3);
        axi_read(5'h10, v);  check("PHY_CTRL unchanged", v, 32'hCE);
        axi_read(5'h18, v);  check("CFG_ERR set", v[12], 1);
        axi_write(5'h18, 32'h1000);
        axi_read(5'h18, v);  check("CFG_ERR cleared", v[12], 0);

        // 6) TX inversion: idle line low
        loop_mode = 0;
        axi_write(5'h10, 32'hD2);
        repeat (5) @(posedge clk);
        check("TX_INV idle level", txd, 0);
        axi_write(5'h10, 32'hC2);

        // 7) RX only: DE low, TX idle, no transmission
        axi_write(5'h10, 32'h80);
        axi_write(5'h0C, 32'hFF);
        repeat (300) @(posedge clk);
        check("RX-only DE low", de, 0);
        check("RX-only TX idle", txd, 1);
        axi_write(5'h10, 32'hC2);           // re-enable TX: queued byte is sent
        repeat (300) @(posedge clk);
        axi_read(5'h08, v);  check("queued byte after TX enable", v & 32'h1FF, 32'h0FF);

        // 8) Sticky frame error: byte with a 0 stop bit, flag must survive later reads
        loop_mode = 2;
        tb_line = 0; repeat (16) @(posedge clk);                 // start bit
        for (i = 0; i < 8; i = i + 1) begin tb_line = i[0]; repeat (16) @(posedge clk); end
        tb_line = 0; repeat (16) @(posedge clk);                 // bad stop bit
        tb_line = 1; repeat (400) @(posedge clk);
        axi_read(5'h08, v);  $display("     RXREG = %h", v); check("frame error flagged", v[10], 1);
        repeat (200) @(posedge clk);
        axi_read(5'h08, v);  check("frame error sticky", v[10], 1);
        axi_write(5'h08, 32'h400);
        axi_read(5'h08, v);  check("frame error cleared (W1C)", v[10], 0);

        // 10) SETUP write / read-back (parity enabled, then restore)
        axi_write(5'h00, 32'h0400_0010);
        axi_read(5'h00, v);  check("SETUP write 8E1", v, 32'h4400_0010);
        axi_write(5'h00, 32'h0000_0010);
        axi_read(5'h00, v);  check("SETUP restore", v, 32'h4000_0010);
        repeat (400) @(posedge clk);      // SETUP write resets RX/TX: wait for RX idle detect

        // 9) Sticky RX FIFO overflow: 20 bytes into a 16-deep FIFO without reading
        loop_mode = 0;
        for (i = 0; i < 20; i = i + 1) begin
            axi_write(5'h0C, i);
            repeat (170) @(posedge clk);
        end
        repeat (200) @(posedge clk);
        axi_read(5'h08, v);  check("overflow flagged", v[12], 1);
        axi_read(5'h08, v);  check("overflow sticky", v[12], 1);
        axi_write(5'h08, 32'h1000);       // RX reset clears FIFO and flag
        repeat (5) @(posedge clk);
        axi_read(5'h08, v);  check("overflow cleared", v[12], 0);

        // 11) Parity errors with one and two stop bits (8E1 / 8E2)
        loop_mode = 2; tb_line = 1;
        for (i = 1; i <= 2; i = i + 1) begin
            axi_write(5'h00, (i == 2 ? 32'h0D00_0010 : 32'h0500_0010));
            repeat (400) @(posedge clk);
            send_par_byte(8'h41, 1'b0, i);
            axi_read(5'h08, v);
            if (i == 1) check("8E1 good parity: data", v & 32'h3FF, 32'h041);
            else        check("8E2 good parity: data", v & 32'h3FF, 32'h041);
            send_par_byte(8'h42, 1'b1, i);
            axi_read(5'h08, v);
            if (i == 1) check("8E1 bad parity: PERR", v[9], 1);
            else        check("8E2 bad parity: PERR", v[9], 1);
            axi_write(5'h08, 32'h200);
            axi_read(5'h08, v);
            check("PERR cleared", v[9], 0);
        end

        // 12) Per-byte error bits: good, bad parity, good, bad stop, good
        for (i = 1; i <= 2; i = i + 1) begin
            axi_write(5'h00, (i == 2 ? 32'h0D00_0010 : 32'h0500_0010));
            repeat (400) @(posedge clk);
            send_frame(8'h11, 0, 0, i);
            send_frame(8'h22, 1, 0, i);
            send_frame(8'h33, 0, 0, i);
            send_frame(8'h44, 0, 1, i);
            repeat (400) @(posedge clk);   // rxuart resyncs after a frame error: needs an idle frame
            send_frame(8'h55, 0, 0, i);
            axi_read(5'h08, v); check(i == 1 ? "8E1 byte1 ok"        : "8E2 byte1 ok",        {v[14:13], v[7:0]}, {2'b00, 8'h11});
            axi_read(5'h08, v); check(i == 1 ? "8E1 byte2 BYTE_PERR" : "8E2 byte2 BYTE_PERR", {v[14:13], v[7:0]}, {2'b01, 8'h22});
            axi_read(5'h08, v); check(i == 1 ? "8E1 byte3 ok"        : "8E2 byte3 ok",        {v[14:13], v[7:0]}, {2'b00, 8'h33});
            axi_read(5'h08, v); check(i == 1 ? "8E1 byte4 BYTE_FERR" : "8E2 byte4 BYTE_FERR", {v[14:13], v[7:0]}, {2'b10, 8'h44});
            axi_read(5'h08, v); check(i == 1 ? "8E1 byte5 ok"        : "8E2 byte5 ok",        {v[14:13], v[7:0]}, {2'b00, 8'h55});
            axi_read(5'h08, v); check("FIFO empty, byte flags 0", {v[14:13], v[8]}, 3'b001);
            axi_write(5'h08, 32'h600);
        end

        // 13) RX reset right after a bad frame: next good byte must be clean
        send_frame(8'h66, 1, 0, 1);
        axi_write(5'h08, 32'h1000);
        repeat (400) @(posedge clk);
        send_frame(8'h77, 0, 0, 1);
        axi_read(5'h08, v); check("after RX reset: clean byte", {v[14:13], v[8], v[7:0]}, {3'b000, 8'h77});
        axi_write(5'h00, 32'h0000_0010);

        $display("\n%0s: %0d error(s)", errors ? "FAILED" : "PASSED", errors);
        $finish;
    end
    initial begin #2_000_000; $display("TIMEOUT"); $finish; end
endmodule
