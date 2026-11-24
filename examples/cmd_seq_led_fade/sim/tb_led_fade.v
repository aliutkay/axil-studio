// Testbench of led_fade.s: cmd_seq drives the real ZipCPU axilgpio (hdl/axilgpio.v) through
// its 32-bit address wrapper hdl/axilgpio_a32.v.
// The program is loaded through the upstream port like the Debug Bridge does it (IMEM_PTR,
// IMEM_DATA, then IMEM_CNT / IMEM_SUM are checked), then started with CTRL.RUN. Every change of
// o_gpio[1:0] is printed as "G <cycle> <b> <a>"; run_sim.py checks the waveform.
`timescale 1ns/1ps
`default_nettype none
module tb_led_fade;
    parameter CYCLES  = 20000;              // simulated cycles after the start
    parameter PROG    = "led_fade.hex";
    parameter NPROG   = 38;                 // instructions in PROG
    parameter [31:0] SUM = 32'h0;           // expected IMEM_SUM

    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;

    // upstream master (the PC side)
    reg  [31:0] s_awaddr = 0, s_wdata = 0, s_araddr = 0;
    reg         s_awvalid = 0, s_wvalid = 0, s_bready = 0, s_arvalid = 0, s_rready = 0;
    wire        s_awready, s_wready, s_bvalid, s_arready, s_rvalid;
    wire [1:0]  s_bresp, s_rresp;
    wire [31:0] s_rdata;

    // cmd_seq master -> axilgpio
    wire        m_awvalid, m_awready, m_wvalid, m_wready, m_bvalid, m_bready;
    wire        m_arvalid, m_arready, m_rvalid, m_rready;
    wire [31:0] m_awaddr, m_wdata, m_araddr, m_rdata;
    wire [2:0]  m_awprot, m_arprot;
    wire [3:0]  m_wstrb;
    wire [1:0]  m_bresp, m_rresp;
    wire [7:0]  ev_out;
    wire        irq, gpio_int;
    wire [7:0]  gpio;

    cmd_seq dut (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n),
        .S_AXI_AWVALID(s_awvalid), .S_AXI_AWREADY(s_awready), .S_AXI_AWADDR(s_awaddr),
        .S_AXI_AWPROT(3'b000), .S_AXI_WVALID(s_wvalid), .S_AXI_WREADY(s_wready),
        .S_AXI_WDATA(s_wdata), .S_AXI_WSTRB(4'hF), .S_AXI_BVALID(s_bvalid),
        .S_AXI_BREADY(s_bready), .S_AXI_BRESP(s_bresp), .S_AXI_ARVALID(s_arvalid),
        .S_AXI_ARREADY(s_arready), .S_AXI_ARADDR(s_araddr), .S_AXI_ARPROT(3'b000),
        .S_AXI_RVALID(s_rvalid), .S_AXI_RREADY(s_rready), .S_AXI_RDATA(s_rdata),
        .S_AXI_RRESP(s_rresp),
        .M_AXI_AWVALID(m_awvalid), .M_AXI_AWREADY(m_awready), .M_AXI_AWADDR(m_awaddr),
        .M_AXI_AWPROT(m_awprot), .M_AXI_WVALID(m_wvalid), .M_AXI_WREADY(m_wready),
        .M_AXI_WDATA(m_wdata), .M_AXI_WSTRB(m_wstrb), .M_AXI_BVALID(m_bvalid),
        .M_AXI_BREADY(m_bready), .M_AXI_BRESP(m_bresp), .M_AXI_ARVALID(m_arvalid),
        .M_AXI_ARREADY(m_arready), .M_AXI_ARADDR(m_araddr), .M_AXI_ARPROT(m_arprot),
        .M_AXI_RVALID(m_rvalid), .M_AXI_RREADY(m_rready), .M_AXI_RDATA(m_rdata),
        .M_AXI_RRESP(m_rresp),
        .i_sys_halt(1'b0), .i_sys_run(1'b0), .i_ev(8'd0), .o_ev(ev_out), .o_irq(irq)
    );

    // the GPIO sits at 0x0301_0000; axilgpio_a32 takes the full 32-bit address
    axilgpio_a32 #(.NOUT(8), .NIN(1)) gpio_i (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n),
        .S_AXI_AWVALID(m_awvalid), .S_AXI_AWREADY(m_awready), .S_AXI_AWADDR(m_awaddr),
        .S_AXI_AWPROT(m_awprot), .S_AXI_WVALID(m_wvalid), .S_AXI_WREADY(m_wready),
        .S_AXI_WDATA(m_wdata), .S_AXI_WSTRB(m_wstrb), .S_AXI_BVALID(m_bvalid),
        .S_AXI_BREADY(m_bready), .S_AXI_BRESP(m_bresp), .S_AXI_ARVALID(m_arvalid),
        .S_AXI_ARREADY(m_arready), .S_AXI_ARADDR(m_araddr), .S_AXI_ARPROT(m_arprot),
        .S_AXI_RVALID(m_rvalid), .S_AXI_RREADY(m_rready), .S_AXI_RDATA(m_rdata),
        .S_AXI_RRESP(m_rresp),
        .i_gpio(1'b0), .o_gpio(gpio), .o_int(gpio_int)
    );

    // ---- upstream AXI4-Lite master tasks (AW and W together). Signals change at the falling
    // edge; a handshake seen at the falling edge happens at the next rising edge.
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
            if (s_bresp != 0) bus_errors = bus_errors + 1;
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
            if (s_rresp != 0) bus_errors = bus_errors + 1;
            @(posedge clk); #1;
            s_rready = 0;
        end
    endtask

    localparam [31:0] SEQ = 32'h0300_0000;   // own window of the sequencer (SELF_ID = 0)
    reg [63:0] pm [0:511];
    reg [31:0] v;
    integer i, cycle = 0, started = 0;
    reg [1:0] last = 2'bxx;

    always @(posedge clk) begin
        cycle <= cycle + 1;
        if (started && gpio[1:0] !== last) begin
            $display("G %0d %0d %0d", cycle - started, gpio[1], gpio[0]);
            last <= gpio[1:0];
        end
    end

    initial begin
        $readmemh(PROG, pm, 0, NPROG - 1);
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (5) @(posedge clk);
        wr(SEQ + 16'h0008, 32'h2);                  // CTRL.STOP
        wr(SEQ + 16'h0020, 0);                      // IMEM_PTR = 0
        for (i = 0; i < NPROG; i = i + 1) begin
            wr(SEQ + 16'h0024, pm[i][31:0]);
            wr(SEQ + 16'h0024, pm[i][63:32]);
        end
        rd(SEQ + 16'h0028, v);
        if (v != NPROG) begin $display("FAIL IMEM_CNT %0d", v); $finish; end
        rd(SEQ + 16'h002C, v);
        if (v != SUM) begin $display("FAIL IMEM_SUM %h", v); $finish; end
        wr(SEQ + 16'h0010, 0);                      // ENTRY = 0
        started = cycle;
        wr(SEQ + 16'h0008, 32'h41);                 // ERR_HALT + RUN
        repeat (CYCLES) @(posedge clk);
        rd(SEQ + 16'h000C, v);                      // STATUS: still running, no error
        $display("STATUS %h", v);
        $display("BUSERR %0d", bus_errors);
        $finish;
    end
endmodule
