// Shared testbench body: clock, DUT, downstream slave model and AXI4-Lite
// master tasks for the upstream port. Included inside a testbench module that
// defines the parameters TB_IMEM_AW, TB_DMEM_AW, TB_PT_TIMEOUT, TB_BUS_TIMEOUT,
// TB_MAXD, TB_SLOW_LAT and TB_SEED.

    reg clk = 0, rst_n = 0;
    always #5 clk = !clk;

    reg  [31:0] s_awaddr = 0, s_wdata = 0, s_araddr = 0;
    reg  [2:0]  s_awprot = 0, s_arprot = 0;
    reg  [3:0]  s_wstrb = 4'hF;
    reg         s_awvalid = 0, s_wvalid = 0, s_bready = 0, s_arvalid = 0, s_rready = 0;
    wire        s_awready, s_wready, s_bvalid, s_arready, s_rvalid;
    wire [1:0]  s_bresp, s_rresp;
    wire [31:0] s_rdata;

    wire        m_awvalid, m_awready, m_wvalid, m_wready, m_bvalid, m_bready;
    wire        m_arvalid, m_arready, m_rvalid, m_rready;
    wire [31:0] m_awaddr, m_wdata, m_araddr, m_rdata;
    wire [2:0]  m_awprot, m_arprot;
    wire [3:0]  m_wstrb;
    wire [1:0]  m_bresp, m_rresp;

    reg         sys_halt = 0, sys_run = 0;
    reg  [7:0]  ev_in = 0;
    wire [7:0]  ev_out;
    wire        irq;

    cmd_seq #(
        .IMEM_AW(TB_IMEM_AW), .DMEM_AW(TB_DMEM_AW),
        .PT_TIMEOUT(TB_PT_TIMEOUT), .BUS_TIMEOUT(TB_BUS_TIMEOUT)
    ) dut (
        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rst_n),
        .S_AXI_AWVALID(s_awvalid), .S_AXI_AWREADY(s_awready), .S_AXI_AWADDR(s_awaddr),
        .S_AXI_AWPROT(s_awprot), .S_AXI_WVALID(s_wvalid), .S_AXI_WREADY(s_wready),
        .S_AXI_WDATA(s_wdata), .S_AXI_WSTRB(s_wstrb), .S_AXI_BVALID(s_bvalid),
        .S_AXI_BREADY(s_bready), .S_AXI_BRESP(s_bresp), .S_AXI_ARVALID(s_arvalid),
        .S_AXI_ARREADY(s_arready), .S_AXI_ARADDR(s_araddr), .S_AXI_ARPROT(s_arprot),
        .S_AXI_RVALID(s_rvalid), .S_AXI_RREADY(s_rready), .S_AXI_RDATA(s_rdata),
        .S_AXI_RRESP(s_rresp),
        .M_AXI_AWVALID(m_awvalid), .M_AXI_AWREADY(m_awready), .M_AXI_AWADDR(m_awaddr),
        .M_AXI_AWPROT(m_awprot), .M_AXI_WVALID(m_wvalid), .M_AXI_WREADY(m_wready),
        .M_AXI_WDATA(m_wdata), .M_AXI_WSTRB(m_wstrb), .M_AXI_BVALID(m_bvalid),
        .M_AXI_BREADY(m_bready), .M_AXI_BRESP(m_bresp), .M_AXI_ARVALID(m_arvalid),
        .M_AXI_ARREADY(m_arready), .M_AXI_ARADDR(m_araddr), .M_AXI_ARPROT(m_arprot),
        .M_AXI_RVALID(m_rvalid), .M_AXI_RREADY(m_rready), .M_AXI_RDATA(m_rdata),
        .M_AXI_RRESP(m_rresp),
        .i_sys_halt(sys_halt), .i_sys_run(sys_run), .i_ev(ev_in), .o_ev(ev_out), .o_irq(irq)
    );

    axil_slave_model #(.MAXD(TB_MAXD), .SLOW_LAT(TB_SLOW_LAT), .SEED(TB_SEED)) slave (
        .clk(clk), .rst(!rst_n),
        .AWVALID(m_awvalid), .AWREADY(m_awready), .AWADDR(m_awaddr), .AWPROT(m_awprot),
        .WVALID(m_wvalid), .WREADY(m_wready), .WDATA(m_wdata), .WSTRB(m_wstrb),
        .BVALID(m_bvalid), .BREADY(m_bready), .BRESP(m_bresp),
        .ARVALID(m_arvalid), .ARREADY(m_arready), .ARADDR(m_araddr), .ARPROT(m_arprot),
        .RVALID(m_rvalid), .RREADY(m_rready), .RDATA(m_rdata), .RRESP(m_rresp)
    );

    // ---- simple protocol checks on the upstream port (formal covers all cases) ----
    integer proto_errors = 0;
    reg [31:0] p_rdata; reg [1:0] p_rresp, p_bresp; reg p_rhold = 0, p_bhold = 0;
    always @(posedge clk) begin
        // VALID without READY: VALID and the payload must stay in the next cycle
        if (p_rhold && (!s_rvalid || s_rdata !== p_rdata || s_rresp !== p_rresp))
            proto_errors = proto_errors + 1;
        if (p_bhold && (!s_bvalid || s_bresp !== p_bresp))
            proto_errors = proto_errors + 1;
        p_rhold <= rst_n && s_rvalid && !s_rready; p_rdata <= s_rdata; p_rresp <= s_rresp;
        p_bhold <= rst_n && s_bvalid && !s_bready; p_bresp <= s_bresp;
    end

    // ---- upstream master tasks ----------------------------------------------------
    integer seed_m = TB_SEED * 7 + 3;
    integer cyc = 0;
    always @(posedge clk) cyc <= cyc + 1;

    task tick(input integer n);
        integer t;
        begin
            for (t = 0; t < n; t = t + 1) @(posedge clk);
        end
    endtask

    // Signals change at the falling edge; a handshake seen at the falling edge
    // happens at the next rising edge.
    task axi_write_s(input [31:0] addr, input [31:0] data, input [3:0] strb, input [2:0] prot,
                     output [1:0] resp);
        begin
            @(negedge clk);
            s_awaddr = addr; s_wdata = data; s_wstrb = strb; s_awprot = prot;
            s_awvalid = 1; s_wvalid = 1;
            #1;                                 // let READY follow VALID
            while (!(s_awready && s_wready)) @(negedge clk);
            @(posedge clk); #1;
            s_awvalid = 0; s_wvalid = 0;
            @(negedge clk);
            s_bready = ({$random(seed_m)} % 4) != 0;
            while (!(s_bvalid && s_bready)) begin
                @(negedge clk);
                s_bready = ({$random(seed_m)} % 4) != 0;
            end
            resp = s_bresp;
            @(posedge clk); #1;
            s_bready = 0;
        end
    endtask

    task axi_write(input [31:0] addr, input [31:0] data, output [1:0] resp);
        axi_write_s(addr, data, 4'hF, 3'b000, resp);
    endtask

    task axi_read_p(input [31:0] addr, input [2:0] prot, output [31:0] data, output [1:0] resp);
        begin
            @(negedge clk);
            s_araddr = addr; s_arprot = prot; s_arvalid = 1;
            #1;
            while (!s_arready) @(negedge clk);
            @(posedge clk); #1;
            s_arvalid = 0;
            @(negedge clk);
            s_rready = ({$random(seed_m)} % 4) != 0;
            while (!(s_rvalid && s_rready)) begin
                @(negedge clk);
                s_rready = ({$random(seed_m)} % 4) != 0;
            end
            data = s_rdata; resp = s_rresp;
            @(posedge clk); #1;
            s_rready = 0;
        end
    endtask

    task axi_read(input [31:0] addr, output [31:0] data, output [1:0] resp);
        axi_read_p(addr, 3'b000, data, resp);
    endtask

    // own window of the block (block bits [31:24] are ignored by the DUT)
    localparam [31:0] SELF = 32'h0300_0000;
    reg [1:0]  wr_r;
    reg [31:0] rd_d;
    reg [1:0]  rd_r;

    task csr_w(input [15:0] ofs, input [31:0] data);
        axi_write(SELF | ofs, data, wr_r);
    endtask

    task csr_r(input [15:0] ofs, output [31:0] data);
        axi_read(SELF | ofs, data, rd_r);
    endtask
