`timescale 1ns/1ps
`default_nettype none
module tb;
    reg clk = 0, rstn = 0; always #5 clk = ~clk;
    reg awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    reg [4:0] awaddr=0, araddr=0; reg [31:0] wdata=0;
    wire awready, wready, bvalid, arready, rvalid; wire [1:0] bresp, rresp; wire [31:0] rdata;
    wire txd, de, re_n;
    axiluart_phy #(.INITIAL_SETUP(31'd16), .LGFLEN(4'd8), .HAS_RE(1'b0), .DEF_TIMING(32'd0)) dut (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rstn),
        .S_AXI_AWVALID(awvalid), .S_AXI_AWREADY(awready), .S_AXI_AWADDR(awaddr), .S_AXI_AWPROT(3'd0),
        .S_AXI_WVALID(wvalid), .S_AXI_WREADY(wready), .S_AXI_WDATA(wdata), .S_AXI_WSTRB(4'hF),
        .S_AXI_BVALID(bvalid), .S_AXI_BREADY(bready), .S_AXI_BRESP(bresp),
        .S_AXI_ARVALID(arvalid), .S_AXI_ARREADY(arready), .S_AXI_ARADDR(araddr), .S_AXI_ARPROT(3'd0),
        .S_AXI_RVALID(rvalid), .S_AXI_RREADY(rready), .S_AXI_RDATA(rdata), .S_AXI_RRESP(rresp),
        .o_txd(txd), .i_rxd(txd), .o_de(de), .o_re_n(re_n), .o_rts_n(), .i_cts_n(1'b0),
        .o_uart_rx_int(), .o_uart_tx_int(), .o_uart_rxfifo_int(), .o_uart_txfifo_int());
    task axi_write(input [4:0] a, input [31:0] d);
        begin @(posedge clk); awaddr<=a; wdata<=d; awvalid<=1; wvalid<=1; bready<=1;
        fork begin @(posedge clk); while(!awready) @(posedge clk); awvalid<=0; end
             begin @(posedge clk); while(!wready) @(posedge clk); wvalid<=0; end join
        while(!bvalid) @(posedge clk); @(posedge clk); bready<=0; end
    endtask
    task axi_read(input [4:0] a, output [31:0] d);
        begin @(posedge clk); araddr<=a; arvalid<=1; rready<=1;
        @(posedge clk); while(!arready) @(posedge clk); arvalid<=0;
        while(!rvalid) @(posedge clk); d=rdata; @(posedge clk); rready<=0; end
    endtask
    reg [31:0] v; integer f, i, got, errs, gaps, last_end, total_err;
    reg [31:0] setups [0:5]; reg [7:0] masks [0:5];
    // monitor gaps: count idle-high runs longer than 3 baud periods between frames during burst
    initial begin
        // [29:28]=8-data, [27]=2 stop, [26:24]=parity(100 odd,101 even)
        setups[0]=32'h3500_0010; masks[0]=8'h1F;   // 5E1
        setups[1]=32'h2C00_0010; masks[1]=8'h3F;   // 6O2
        setups[2]=32'h1000_0010; masks[2]=8'h7F;   // 7N1
        setups[3]=32'h0800_0010; masks[3]=8'hFF;   // 8N2
        setups[4]=32'h0500_0010; masks[4]=8'hFF;   // 8E1
        setups[5]=32'h1400_0010; masks[5]=8'h7F;   // 7O1
        total_err=0;
        repeat(5) @(posedge clk); rstn<=1; repeat(50) @(posedge clk);
        for (f=0; f<6; f=f+1) begin
            axi_write(5'h00, setups[f]); repeat(400) @(posedge clk);
            axi_write(5'h10, 32'h8000_0082);                 // TX_EN=0, apply now
            for (i=0;i<100;i=i+1) axi_write(5'h0C, (i*53+7) & masks[f]);
            axi_write(5'h10, 32'h8000_00C2);                 // release: gapless burst
            repeat(100*13*16+500) @(posedge clk);
            got=0; errs=0;
            for (i=0;i<101;i=i+1) begin
                axi_read(5'h08, v);
                if (!v[8]) begin if (v[7:0] !== ((got*53+7) & masks[f]) || v[14:13]) errs=errs+1; got=got+1; end
            end
            $display("setup %h: received %0d/100, errors %0d, sticky %b", setups[f], got, errs, v[12:9]);
            if (got != 100 || errs || v[12:9]) total_err = total_err + 1;
        end
        $display("%s", total_err ? "FAILED" : "PASSED");
        $finish;
    end
endmodule
