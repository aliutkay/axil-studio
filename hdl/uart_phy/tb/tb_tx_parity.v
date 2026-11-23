`timescale 1ns/1ps
`default_nettype none
module tb;
    reg clk = 0, rstn = 0; always #5 clk = ~clk;
    reg awvalid=0, wvalid=0, bready=0, arvalid=0, rready=0;
    reg [4:0] awaddr=0, araddr=0; reg [31:0] wdata=0;
    wire awready, wready, bvalid, arready, rvalid; wire [1:0] bresp, rresp; wire [31:0] rdata;
    wire txd, de, re_n;
    axiluart_phy #(.INITIAL_SETUP(31'd16), .HAS_RE(1'b0), .DEF_TIMING(32'd0)) dut (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rstn),
        .S_AXI_AWVALID(awvalid), .S_AXI_AWREADY(awready), .S_AXI_AWADDR(awaddr), .S_AXI_AWPROT(3'd0),
        .S_AXI_WVALID(wvalid), .S_AXI_WREADY(wready), .S_AXI_WDATA(wdata), .S_AXI_WSTRB(4'hF),
        .S_AXI_BVALID(bvalid), .S_AXI_BREADY(bready), .S_AXI_BRESP(bresp),
        .S_AXI_ARVALID(arvalid), .S_AXI_ARREADY(arready), .S_AXI_ARADDR(araddr), .S_AXI_ARPROT(3'd0),
        .S_AXI_RVALID(rvalid), .S_AXI_RREADY(rready), .S_AXI_RDATA(rdata), .S_AXI_RRESP(rresp),
        .o_txd(txd), .i_rxd(1'b1), .o_de(de), .o_re_n(re_n), .o_rts_n(), .i_cts_n(1'b0),
        .o_uart_rx_int(), .o_uart_tx_int(), .o_uart_rxfifo_int(), .o_uart_txfifo_int());
    task axi_write(input [4:0] a, input [31:0] d);
        begin @(posedge clk); awaddr<=a; wdata<=d; awvalid<=1; wvalid<=1; bready<=1;
        fork begin @(posedge clk); while(!awready) @(posedge clk); awvalid<=0; end
             begin @(posedge clk); while(!wready) @(posedge clk); wvalid<=0; end join
        while(!bvalid) @(posedge clk); @(posedge clk); bready<=0; end
    endtask
    // decode one frame from txd: start, n data, parity
    task decode(input integer nbits, output [7:0] d, output p);
        integer k; begin
        d = 0;
        @(negedge txd); repeat(8) @(posedge clk);          // middle of start bit
        for (k=0;k<nbits;k=k+1) begin repeat(16) @(posedge clk); d[k] = txd; end
        repeat(16) @(posedge clk); p = txd;
        repeat(16) @(posedge clk);
        end endtask
    reg [7:0] d; reg p; integer f, i, bad;
    reg [31:0] setups [0:3]; integer nb [0:3]; reg [0:0] even [0:3];
    initial begin
        setups[0]=32'h0500_0010; nb[0]=8; even[0]=1;   // 8E1
        setups[1]=32'h0400_0010; nb[1]=8; even[1]=0;   // 8O1
        setups[2]=32'h1500_0010; nb[2]=7; even[2]=1;   // 7E1
        setups[3]=32'h3400_0010; nb[3]=5; even[3]=0;   // 5O1
        repeat(5) @(posedge clk); rstn<=1; repeat(50) @(posedge clk);
        for (f=0;f<4;f=f+1) begin
            axi_write(5'h00, setups[f]); repeat(100) @(posedge clk);
            bad=0;
            for (i=0;i<6;i=i+1) begin
                fork
                    axi_write(5'h0C, 8'h41 + i);
                    decode(nb[f], d, p);
                join
                // standard: even -> parity bit = XOR(data); odd -> ~XOR(data)
                if (p !== ((^d) ^ !even[f])) bad=bad+1;
                repeat(64) @(posedge clk);
            end
            $display("setup %h (%0d bits, %s): data last %h parity %b, nonstandard parity bits %0d/6",
                     setups[f], nb[f], even[f] ? "even" : "odd", d, p, bad);
        end
        $finish;
    end
endmodule
