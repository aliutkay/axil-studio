////////////////////////////////////////////////////////////////////////////////
// Module      : cmd_seq
// Description : Programmable command sequencer. AXI4-Lite slave towards the
//               upper interconnect, AXI4-Lite master towards the lower one.
//
// Address (32 bit, the same everywhere in the system):
//   [31:24] block select   (upper interconnect, not looked at here)
//   [23:16] IP select      SELF_ID = this block, anything else = pass-through
//   [15:0]  register offset
//
// Requests to the own window are answered here (CSR, R0-R15, D-mem). All other
// requests are forwarded unchanged (address, data, strobes, prot) and the
// downstream response is returned unchanged.
//
// Own window (offset [15:0]):
//   0x0000 ID         RO  0x5EC0_0200
//   0x0004 CAPS       RO  [7:0] IMEM_AW [15:8] DMEM_AW [23:16] 16 [31:24] SELF_ID
//   0x0008 CTRL       b0 RUN* b1 STOP* b2 RESET* b3 STEP* (*pulses, read 0)
//                     b4 SNAP_EN b5 PT_WR_RUN b6 ERR_HALT b7 IRQ_EN
//   0x000C STATUS     RO  b0 BUSY b1 DONE b2 ERR b3 PAUSED b4 IMEM_PHASE
//                         b5 WR_DRAIN b6 RD_DRAIN (abandoned transfer still in
//                         flight) [8] Z [9] N [10] ERR flag [11] V [12] C [31:16] PC
//   0x0010 ENTRY
//   0x0014 ERR_CODE   RO
//   0x0018 ERR_PC     RO
//   0x001C ERR_ADDR   RO
//   0x0020 IMEM_PTR   write: pointer, phase, CNT and SUM reset
//   0x0024 IMEM_DATA  WO  1st write [31:0], 2nd write [63:32] -> stored, PTR++
//   0x0028 IMEM_CNT   RO
//   0x002C IMEM_SUM   RO  32-bit sum of the words written to IMEM_DATA
//   0x0030 DMEM_PTR
//   0x0034 DMEM_DATA  WO  D[PTR] <= data, PTR++
//   0x0038 SNAP_CNT   RO
//   0x003C TIMER      RO
//   0x0040 IRQ_STAT   W1C b0 DONE b1 ERR b2 IRQ instruction
//   0x0100-0x013C     R0-R15 (RO)
//   0x0400-           D-mem window (published bank when SNAP_EN=1)
// Writes to the own window need WSTRB = 1111 (else SLVERR). Read-only register:
// SLVERR. Unmapped offset: DECERR.
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module cmd_seq #(
    parameter [7:0] SELF_ID     = 8'h00,
    parameter       SEL_LSB     = 16,
    parameter       IMEM_AW     = 9,       // 512 instructions
    parameter       DMEM_AW     = 8,       // 256 words per bank, two banks
    parameter       PT_TIMEOUT  = 1024,    // cycles for a pass-through response
    parameter       BUS_TIMEOUT = 1024     // cycles for a sequencer transfer, 0 = none
) (
    input  wire        S_AXI_ACLK,
    input  wire        S_AXI_ARESETN,
    // AXI4-Lite slave (upper interconnect)
    input  wire        S_AXI_AWVALID,
    output wire        S_AXI_AWREADY,
    input  wire [31:0] S_AXI_AWADDR,
    input  wire [2:0]  S_AXI_AWPROT,
    input  wire        S_AXI_WVALID,
    output wire        S_AXI_WREADY,
    input  wire [31:0] S_AXI_WDATA,
    input  wire [3:0]  S_AXI_WSTRB,
    output wire        S_AXI_BVALID,
    input  wire        S_AXI_BREADY,
    output wire [1:0]  S_AXI_BRESP,
    input  wire        S_AXI_ARVALID,
    output wire        S_AXI_ARREADY,
    input  wire [31:0] S_AXI_ARADDR,
    input  wire [2:0]  S_AXI_ARPROT,
    output wire        S_AXI_RVALID,
    input  wire        S_AXI_RREADY,
    output wire [31:0] S_AXI_RDATA,
    output wire [1:0]  S_AXI_RRESP,
    // AXI4-Lite master (lower interconnect)
    output wire        M_AXI_AWVALID,
    input  wire        M_AXI_AWREADY,
    output wire [31:0] M_AXI_AWADDR,
    output wire [2:0]  M_AXI_AWPROT,
    output wire        M_AXI_WVALID,
    input  wire        M_AXI_WREADY,
    output wire [31:0] M_AXI_WDATA,
    output wire [3:0]  M_AXI_WSTRB,
    input  wire        M_AXI_BVALID,
    output wire        M_AXI_BREADY,
    input  wire [1:0]  M_AXI_BRESP,
    output wire        M_AXI_ARVALID,
    input  wire        M_AXI_ARREADY,
    output wire [31:0] M_AXI_ARADDR,
    output wire [2:0]  M_AXI_ARPROT,
    input  wire        M_AXI_RVALID,
    output wire        M_AXI_RREADY,
    input  wire [31:0] M_AXI_RDATA,
    input  wire [1:0]  M_AXI_RRESP,
    // side band
    input  wire        i_sys_halt,     // level: pause at the next instruction boundary
    input  wire        i_sys_run,      // pulse: start at ENTRY (all blocks together)
    input  wire [7:0]  i_ev,
    output wire [7:0]  o_ev,
    output wire        o_irq
);
    localparam [1:0] OKAY = 2'b00, SLVERR = 2'b10, DECERR = 2'b11;

    localparam [15:0] A_ID = 16'h0000, A_CAPS = 16'h0004, A_CTRL = 16'h0008,
                      A_STATUS = 16'h000C, A_ENTRY = 16'h0010, A_ERR_CODE = 16'h0014,
                      A_ERR_PC = 16'h0018, A_ERR_ADDR = 16'h001C, A_IMEM_PTR = 16'h0020,
                      A_IMEM_DATA = 16'h0024, A_IMEM_CNT = 16'h0028, A_IMEM_SUM = 16'h002C,
                      A_DMEM_PTR = 16'h0030, A_DMEM_DATA = 16'h0034, A_SNAP_CNT = 16'h0038,
                      A_TIMER = 16'h003C, A_IRQ_STAT = 16'h0040, A_REG_BASE = 16'h0100,
                      A_DMEM_BASE = 16'h0400;
    localparam        DMEM_BYTES = 4 * (1 << DMEM_AW);
    localparam [31:0] C_ID = 32'h5EC0_0200;

    wire clk = S_AXI_ACLK;
    wire rst = !S_AXI_ARESETN;

    // ------------------------------------------------------------------ memories
    reg [63:0] imem [0:(1 << IMEM_AW) - 1];
    reg [31:0] dmem [0:(1 << (DMEM_AW + 1)) - 1];     // two banks
    integer k;
    initial begin
        for (k = 0; k < (1 << IMEM_AW); k = k + 1) imem[k] = 64'd0;
        for (k = 0; k < (1 << (DMEM_AW + 1)); k = k + 1) dmem[k] = 32'd0;
    end

    // ------------------------------------------------------------------ state
    reg               snap_en, pt_wr_run, err_halt, irq_en;
    reg [IMEM_AW-1:0] entry;
    reg               cmd_run, cmd_stop, cmd_reset, cmd_step;
    reg [2:0]         irq_stat;
    reg [31:0]        timer, snap_cnt;
    reg               pub_bank;
    reg [IMEM_AW:0]   imem_ptr;               // MSB set: I-mem full
    reg               imem_phase;
    reg [31:0]        imem_lo, imem_cnt, imem_sum;
    reg [DMEM_AW:0]   dmem_ptr;

    // core
    wire [IMEM_AW-1:0] core_imem_addr;
    reg  [63:0]        core_imem_data;
    wire [DMEM_AW-1:0] core_d_addr;
    wire               core_d_we;
    wire [31:0]        core_d_wdata;
    reg  [31:0]        core_d_rdata;
    wire               core_wr_req, core_rd_req;
    wire [31:0]        core_addr, core_wr_data;
    wire               core_wr_done, core_rd_done;
    wire               core_busy, core_done, core_err, core_paused;
    wire [IMEM_AW-1:0] core_pc, core_err_pc;
    wire [4:0]         core_flags;
    wire [7:0]         core_err_code;
    wire [31:0]        core_err_addr;
    wire               core_snap, core_irq, core_done_p, core_err_p;
    wire [16*32-1:0]   core_regs;

    // ------------------------------------------------------------------ timer
    initial timer = 0;
    always @(posedge clk)
        if (rst) timer <= 32'd0;
        else     timer <= timer + 1'b1;

    // ------------------------------------------------------------------ I-mem read port
    initial core_imem_data = 64'd0;
    always @(posedge clk)
        core_imem_data <= imem[core_imem_addr];

    // ------------------------------------------------------------------ D-mem port A (core)
    wire [DMEM_AW:0] a_addr = {snap_en ? !pub_bank : 1'b0, core_d_addr};
    initial core_d_rdata = 32'd0;
    always @(posedge clk) begin
        if (core_d_we)
            dmem[a_addr] <= core_d_wdata;
        core_d_rdata <= dmem[a_addr];
    end

    // ------------------------------------------------------------------ D-mem port B (AXI)
    reg              b_we;
    reg [DMEM_AW:0]  b_waddr, b_raddr;
    reg [31:0]       b_wdata, b_rdata;
    initial begin b_we = 0; b_waddr = 0; b_raddr = 0; b_wdata = 0; b_rdata = 0; end
    always @(posedge clk) begin
        if (b_we)
            dmem[b_waddr] <= b_wdata;
        else
            b_rdata <= dmem[b_raddr];
    end

    // ------------------------------------------------------------------ master engines
    // A requester holds req until done. Dropping req abandons the request: if the
    // engine has not taken it yet nothing is sent; if it has, the transfer is
    // finished on the bus and its response is dropped (orphan).
    localparam [1:0] M_IDLE = 2'd0, M_ADDR = 2'd1, M_RESP = 2'd2;

    // pass-through requests (from the slave FSMs)
    reg         pt_wr_req, pt_rd_req;
    reg         pt_wr_last, pt_rd_last;   // the requester times out this cycle
    reg  [31:0] sw_addr, sw_data, sr_addr;
    reg  [3:0]  sw_strb;
    reg  [2:0]  sw_prot, sr_prot;
    wire        pt_wr_block = core_busy && !pt_wr_run;

    // write engine
    reg  [1:0]  mw_state;
    reg         mw_own_core, mw_orphan, mw_done_p, aw_done, w_done;
    reg  [31:0] mw_addr, mw_data;
    reg  [3:0]  mw_strb;
    reg  [2:0]  mw_prot;
    reg  [1:0]  mw_resp;
    wire        mw_own_req = mw_own_core ? core_wr_req : pt_wr_req;
    wire        mw_take_core = core_wr_req;
    wire        mw_take_pt   = pt_wr_req && !pt_wr_last && !pt_wr_block;

    initial begin
        mw_state = M_IDLE; mw_own_core = 0; mw_orphan = 0; mw_done_p = 0;
        aw_done = 0; w_done = 0; mw_addr = 0; mw_data = 0; mw_strb = 0; mw_prot = 0;
        mw_resp = 0;
    end

    assign M_AXI_AWVALID = (mw_state == M_ADDR) && !aw_done;
    assign M_AXI_WVALID  = (mw_state == M_ADDR) && !w_done;
    assign M_AXI_AWADDR  = mw_addr;
    assign M_AXI_AWPROT  = mw_prot;
    assign M_AXI_WDATA   = mw_data;
    assign M_AXI_WSTRB   = mw_strb;
    assign M_AXI_BREADY  = (mw_state == M_RESP);

    assign core_wr_done  = mw_done_p &&  mw_own_core && core_wr_req;
    wire   pt_wr_done    = mw_done_p && !mw_own_core && pt_wr_req;
    wire   pt_wr_taken   = (mw_state != M_IDLE) && !mw_own_core && !mw_orphan;

    always @(posedge clk) begin
        mw_done_p <= 1'b0;
        if (rst) begin
            mw_state <= M_IDLE; aw_done <= 1'b0; w_done <= 1'b0; mw_orphan <= 1'b0;
        end else case (mw_state)
        M_IDLE: begin
            aw_done <= 1'b0; w_done <= 1'b0; mw_orphan <= 1'b0;
            if (!mw_done_p && mw_take_core) begin
                mw_own_core <= 1'b1; mw_addr <= core_addr; mw_data <= core_wr_data;
                mw_strb <= 4'hF; mw_prot <= 3'b000; mw_state <= M_ADDR;
            end else if (!mw_done_p && mw_take_pt) begin
                mw_own_core <= 1'b0; mw_addr <= sw_addr; mw_data <= sw_data;
                mw_strb <= sw_strb; mw_prot <= sw_prot; mw_state <= M_ADDR;
            end
        end
        M_ADDR: begin
            if (!mw_own_req) mw_orphan <= 1'b1;
            if (M_AXI_AWREADY) aw_done <= 1'b1;
            if (M_AXI_WREADY)  w_done  <= 1'b1;
            if ((aw_done || M_AXI_AWREADY) && (w_done || M_AXI_WREADY))
                mw_state <= M_RESP;
        end
        default: begin  // M_RESP
            if (!mw_own_req) mw_orphan <= 1'b1;
            if (M_AXI_BVALID) begin
                mw_resp   <= M_AXI_BRESP;
                mw_done_p <= !mw_orphan && mw_own_req;
                mw_state  <= M_IDLE;
            end
        end
        endcase
    end

    // read engine
    reg  [1:0]  mr_state;
    reg         mr_own_core, mr_orphan, mr_done_p;
    reg  [31:0] mr_addr, mr_data;
    reg  [2:0]  mr_prot;
    reg  [1:0]  mr_resp;
    wire        mr_own_req = mr_own_core ? core_rd_req : pt_rd_req;

    initial begin
        mr_state = M_IDLE; mr_own_core = 0; mr_orphan = 0; mr_done_p = 0;
        mr_addr = 0; mr_data = 0; mr_prot = 0; mr_resp = 0;
    end

    assign M_AXI_ARVALID = (mr_state == M_ADDR);
    assign M_AXI_ARADDR  = mr_addr;
    assign M_AXI_ARPROT  = mr_prot;
    assign M_AXI_RREADY  = (mr_state == M_RESP);

    assign core_rd_done  = mr_done_p &&  mr_own_core && core_rd_req;
    wire   pt_rd_done    = mr_done_p && !mr_own_core && pt_rd_req;

    always @(posedge clk) begin
        mr_done_p <= 1'b0;
        if (rst) begin
            mr_state <= M_IDLE; mr_orphan <= 1'b0;
        end else case (mr_state)
        M_IDLE: begin
            mr_orphan <= 1'b0;
            if (!mr_done_p && core_rd_req) begin
                mr_own_core <= 1'b1; mr_addr <= core_addr; mr_prot <= 3'b000;
                mr_state <= M_ADDR;
            end else if (!mr_done_p && pt_rd_req && !pt_rd_last) begin
                mr_own_core <= 1'b0; mr_addr <= sr_addr; mr_prot <= sr_prot;
                mr_state <= M_ADDR;
            end
        end
        M_ADDR: begin
            if (!mr_own_req) mr_orphan <= 1'b1;
            if (M_AXI_ARREADY) mr_state <= M_RESP;
        end
        default: begin  // M_RESP
            if (!mr_own_req) mr_orphan <= 1'b1;
            if (M_AXI_RVALID) begin
                mr_data   <= M_AXI_RDATA;
                mr_resp   <= M_AXI_RRESP;
                mr_done_p <= !mr_orphan && mr_own_req;
                mr_state  <= M_IDLE;
            end
        end
        endcase
    end

    // ------------------------------------------------------------------ slave: write path
    localparam [1:0] SW_IDLE = 2'd0, SW_LOCAL = 2'd1, SW_PT = 2'd2, SW_RESP = 2'd3;
    reg [1:0]  sw_state;
    reg [1:0]  s_bresp;
    reg        s_bvalid;
    reg [31:0] sw_tmo;

    initial begin
        sw_state = SW_IDLE; s_bresp = OKAY; s_bvalid = 0; sw_tmo = 0;
        pt_wr_req = 0; pt_wr_last = 0; sw_addr = 0; sw_data = 0; sw_strb = 0; sw_prot = 0;
    end

    assign S_AXI_AWREADY = (sw_state == SW_IDLE) && S_AXI_AWVALID && S_AXI_WVALID;
    assign S_AXI_WREADY  = S_AXI_AWREADY;
    assign S_AXI_BVALID  = s_bvalid;
    assign S_AXI_BRESP   = s_bresp;

    wire [15:0] sw_ofs  = {sw_addr[15:2], 2'b00};
    wire        sw_dwin = (sw_ofs >= A_DMEM_BASE) && (sw_ofs < A_DMEM_BASE + DMEM_BYTES);
    wire [DMEM_AW-1:0] sw_didx = sw_ofs[DMEM_AW+1:2] - A_DMEM_BASE[DMEM_AW+1:2];
    wire        sw_ro   = sw_ofs == A_ID || sw_ofs == A_CAPS || sw_ofs == A_STATUS ||
                          sw_ofs == A_ERR_CODE || sw_ofs == A_ERR_PC || sw_ofs == A_ERR_ADDR ||
                          sw_ofs == A_IMEM_CNT || sw_ofs == A_IMEM_SUM || sw_ofs == A_SNAP_CNT ||
                          sw_ofs == A_TIMER ||
                          (sw_ofs >= A_REG_BASE && sw_ofs < A_REG_BASE + 16'd64);

    // one-cycle set requests from the core, applied together with W1C
    wire [2:0] irq_set = {core_irq, core_err_p, core_done_p};
    reg  [2:0] irq_clr;

    always @(posedge clk) begin
        cmd_run <= 1'b0; cmd_stop <= 1'b0; cmd_reset <= 1'b0; cmd_step <= 1'b0;
        b_we <= 1'b0;
        irq_clr = 3'd0;

        if (rst) begin
            sw_state <= SW_IDLE; s_bvalid <= 1'b0; pt_wr_req <= 1'b0; pt_wr_last <= 1'b0;
            snap_en <= 1'b0; pt_wr_run <= 1'b0; err_halt <= 1'b1; irq_en <= 1'b0;
            entry <= 0; irq_stat <= 3'd0;
            imem_ptr <= 0; imem_phase <= 1'b0; imem_cnt <= 32'd0; imem_sum <= 32'd0;
            dmem_ptr <= 0; pub_bank <= 1'b0; snap_cnt <= 32'd0;
        end else begin
            if (core_snap) begin
                snap_cnt <= snap_cnt + 1'b1;
                if (snap_en)
                    pub_bank <= !pub_bank;
            end

            case (sw_state)
            SW_IDLE:
                if (S_AXI_AWVALID && S_AXI_WVALID) begin
                    sw_addr <= S_AXI_AWADDR; sw_data <= S_AXI_WDATA;
                    sw_strb <= S_AXI_WSTRB;  sw_prot <= S_AXI_AWPROT;
                    if (S_AXI_AWADDR[SEL_LSB +: 8] == SELF_ID)
                        sw_state <= SW_LOCAL;
                    else if (pt_wr_block) begin         // writes are closed while running
                        s_bresp <= SLVERR; s_bvalid <= 1'b1; sw_state <= SW_RESP;
                    end else begin
                        pt_wr_req <= 1'b1; pt_wr_last <= (PT_TIMEOUT <= 1);
                        sw_tmo <= 32'd0; sw_state <= SW_PT;
                    end
                end

            SW_LOCAL: begin
                s_bresp <= OKAY;
                if (sw_strb != 4'hF)
                    s_bresp <= SLVERR;
                else if (sw_ofs == A_CTRL) begin
                    cmd_run   <= sw_data[0];
                    cmd_stop  <= sw_data[1];
                    cmd_reset <= sw_data[2];
                    cmd_step  <= sw_data[3];
                    snap_en   <= sw_data[4];
                    pt_wr_run <= sw_data[5];
                    err_halt  <= sw_data[6];
                    irq_en    <= sw_data[7];
                end else if (sw_ofs == A_ENTRY)
                    entry <= sw_data[IMEM_AW-1:0];
                else if (sw_ofs == A_IMEM_PTR) begin
                    imem_ptr   <= {1'b0, sw_data[IMEM_AW-1:0]};
                    imem_phase <= 1'b0;
                    imem_cnt   <= 32'd0;
                    imem_sum   <= 32'd0;
                end else if (sw_ofs == A_IMEM_DATA) begin
                    if (core_busy || core_paused || imem_ptr[IMEM_AW])
                        s_bresp <= SLVERR;              // running, paused or I-mem full
                    else begin
                        imem_sum <= imem_sum + sw_data;
                        if (!imem_phase) begin
                            imem_lo <= sw_data; imem_phase <= 1'b1;
                        end else begin
                            imem[imem_ptr[IMEM_AW-1:0]] <= {sw_data, imem_lo};
                            imem_ptr   <= imem_ptr + 1'b1;
                            imem_cnt   <= imem_cnt + 1'b1;
                            imem_phase <= 1'b0;
                        end
                    end
                end else if (sw_ofs == A_DMEM_PTR)
                    dmem_ptr <= {1'b0, sw_data[DMEM_AW-1:0]};
                else if (sw_ofs == A_DMEM_DATA) begin
                    if (dmem_ptr[DMEM_AW])
                        s_bresp <= SLVERR;              // past the end of D-mem
                    else begin
                        b_waddr <= {snap_en ? pub_bank : 1'b0, dmem_ptr[DMEM_AW-1:0]};
                        b_wdata <= sw_data; b_we <= 1'b1;
                        dmem_ptr <= dmem_ptr + 1'b1;
                    end
                end else if (sw_ofs == A_IRQ_STAT)
                    irq_clr = sw_data[2:0];
                else if (sw_dwin) begin
                    b_waddr <= {snap_en ? pub_bank : 1'b0, sw_didx};
                    b_wdata <= sw_data; b_we <= 1'b1;
                end else if (sw_ro)
                    s_bresp <= SLVERR;
                else
                    s_bresp <= DECERR;
                s_bvalid <= 1'b1;
                sw_state <= SW_RESP;
            end

            SW_PT:
                if (pt_wr_done) begin
                    pt_wr_req <= 1'b0; pt_wr_last <= 1'b0;
                    s_bresp <= mw_resp; s_bvalid <= 1'b1; sw_state <= SW_RESP;
                end else if (!pt_wr_taken && pt_wr_block) begin    // not sent: withdraw
                    pt_wr_req <= 1'b0; pt_wr_last <= 1'b0;
                    s_bresp <= SLVERR; s_bvalid <= 1'b1; sw_state <= SW_RESP;
                end else if (pt_wr_last) begin                    // timeout
                    pt_wr_req <= 1'b0; pt_wr_last <= 1'b0;
                    s_bresp <= SLVERR; s_bvalid <= 1'b1; sw_state <= SW_RESP;
                end else begin
                    sw_tmo <= sw_tmo + 1'b1;
                    pt_wr_last <= (sw_tmo + 2 >= PT_TIMEOUT);
                end

            default:    // SW_RESP
                if (S_AXI_BREADY) begin
                    s_bvalid <= 1'b0; sw_state <= SW_IDLE;
                end
            endcase

            irq_stat <= (irq_stat & ~irq_clr) | irq_set;    // a new event wins over W1C
        end
    end

    // ------------------------------------------------------------------ slave: read path
    localparam [2:0] SR_IDLE = 3'd0, SR_LOCAL = 3'd1, SR_DMEM = 3'd2, SR_DMEM2 = 3'd3,
                     SR_PT = 3'd4, SR_RESP = 3'd5;
    reg [2:0]  sr_state;
    reg [31:0] s_rdata;
    reg [1:0]  s_rresp;
    reg        s_rvalid;
    reg [31:0] sr_tmo;

    initial begin
        sr_state = SR_IDLE; s_rdata = 0; s_rresp = OKAY; s_rvalid = 0; sr_tmo = 0;
        pt_rd_req = 0; pt_rd_last = 0; sr_addr = 0; sr_prot = 0;
    end

    assign S_AXI_ARREADY = (sr_state == SR_IDLE);
    assign S_AXI_RVALID  = s_rvalid;
    assign S_AXI_RDATA   = s_rdata;
    assign S_AXI_RRESP   = s_rresp;

    wire [15:0] sr_ofs  = {sr_addr[15:2], 2'b00};
    wire        sr_dwin = (sr_ofs >= A_DMEM_BASE) && (sr_ofs < A_DMEM_BASE + DMEM_BYTES);
    wire [DMEM_AW-1:0] sr_didx = sr_ofs[DMEM_AW+1:2] - A_DMEM_BASE[DMEM_AW+1:2];
    wire [3:0]  sr_ridx = sr_ofs[5:2];

    reg [31:0] csr_rdata;
    reg        csr_ok;
    always @(*) begin
        csr_rdata = 32'd0;
        csr_ok    = 1'b1;
        case (sr_ofs)
        A_ID:       csr_rdata = C_ID;
        A_CAPS:     csr_rdata = {SELF_ID, 8'd16, DMEM_AW[7:0], IMEM_AW[7:0]};
        A_CTRL:     csr_rdata = {24'd0, irq_en, err_halt, pt_wr_run, snap_en, 4'd0};
        A_STATUS:   begin
                        csr_rdata[0] = core_busy;
                        csr_rdata[1] = core_done;
                        csr_rdata[2] = core_err;
                        csr_rdata[3] = core_paused;
                        csr_rdata[4] = imem_phase;
                        csr_rdata[5] = (mw_state != M_IDLE) && mw_orphan;
                        csr_rdata[6] = (mr_state != M_IDLE) && mr_orphan;
                        csr_rdata[12:8] = {core_flags[4], core_flags[3], core_flags[2],
                                           core_flags[1], core_flags[0]};
                        csr_rdata[16 +: IMEM_AW] = core_pc;
                    end
        A_ENTRY:    csr_rdata[IMEM_AW-1:0] = entry;
        A_ERR_CODE: csr_rdata[7:0] = core_err_code;
        A_ERR_PC:   csr_rdata[IMEM_AW-1:0] = core_err_pc;
        A_ERR_ADDR: csr_rdata = core_err_addr;
        A_IMEM_PTR: csr_rdata[IMEM_AW:0] = imem_ptr;
        A_IMEM_CNT: csr_rdata = imem_cnt;
        A_IMEM_SUM: csr_rdata = imem_sum;
        A_DMEM_PTR: csr_rdata[DMEM_AW:0] = dmem_ptr;
        A_SNAP_CNT: csr_rdata = snap_cnt;
        A_TIMER:    csr_rdata = timer;
        A_IRQ_STAT: csr_rdata[2:0] = irq_stat;
        default:
            if (sr_ofs >= A_REG_BASE && sr_ofs < A_REG_BASE + 16'd64)
                csr_rdata = core_regs[32*sr_ridx +: 32];
            else
                csr_ok = 1'b0;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            sr_state <= SR_IDLE; s_rvalid <= 1'b0; pt_rd_req <= 1'b0; pt_rd_last <= 1'b0;
        end else case (sr_state)
        SR_IDLE:
            if (S_AXI_ARVALID) begin
                sr_addr <= S_AXI_ARADDR; sr_prot <= S_AXI_ARPROT;
                if (S_AXI_ARADDR[SEL_LSB +: 8] == SELF_ID)
                    sr_state <= SR_LOCAL;
                else begin
                    pt_rd_req <= 1'b1; pt_rd_last <= (PT_TIMEOUT <= 1);
                    sr_tmo <= 32'd0; sr_state <= SR_PT;
                end
            end

        SR_LOCAL:
            if (sr_dwin) begin
                b_raddr <= {snap_en ? pub_bank : 1'b0, sr_didx};
                sr_state <= SR_DMEM;
            end else begin
                s_rdata  <= csr_ok ? csr_rdata : 32'd0;
                s_rresp  <= csr_ok ? OKAY : DECERR;
                s_rvalid <= 1'b1;
                sr_state <= SR_RESP;
            end

        SR_DMEM:                        // port B reads when it is not writing
            if (!b_we)
                sr_state <= SR_DMEM2;

        SR_DMEM2: begin
            s_rdata <= b_rdata; s_rresp <= OKAY; s_rvalid <= 1'b1; sr_state <= SR_RESP;
        end

        SR_PT:
            if (pt_rd_done) begin
                pt_rd_req <= 1'b0; pt_rd_last <= 1'b0;
                s_rdata <= mr_data; s_rresp <= mr_resp; s_rvalid <= 1'b1; sr_state <= SR_RESP;
            end else if (pt_rd_last) begin                    // timeout
                pt_rd_req <= 1'b0; pt_rd_last <= 1'b0;
                s_rdata <= 32'd0; s_rresp <= SLVERR; s_rvalid <= 1'b1; sr_state <= SR_RESP;
            end else begin
                sr_tmo <= sr_tmo + 1'b1;
                pt_rd_last <= (sr_tmo + 2 >= PT_TIMEOUT);
            end

        default:    // SR_RESP
            if (S_AXI_RREADY) begin
                s_rvalid <= 1'b0; sr_state <= SR_IDLE;
            end
        endcase
    end

    assign o_irq = irq_en && (irq_stat != 3'd0);

    // ------------------------------------------------------------------ core
`ifdef F_ABSTRACT_CORE
    // formal: the core is replaced by its proven request behaviour (formal/README)
    cmd_seq_core_abs #(
`else
    cmd_seq_core #(
`endif
        .SELF_ID(SELF_ID), .SEL_LSB(SEL_LSB), .IMEM_AW(IMEM_AW), .DMEM_AW(DMEM_AW),
        .BUS_TIMEOUT(BUS_TIMEOUT)
    ) u_core (
        .clk(clk), .rst(rst),
        .run(cmd_run), .stop(cmd_stop), .reset(cmd_reset), .step(cmd_step),
        .sys_halt(i_sys_halt), .sys_run(i_sys_run), .err_halt(err_halt),
        .entry(entry), .timer(timer),
        .imem_addr(core_imem_addr), .imem_data(core_imem_data),
        .d_addr(core_d_addr), .d_we(core_d_we), .d_wdata(core_d_wdata), .d_rdata(core_d_rdata),
        .wr_req(core_wr_req), .rd_req(core_rd_req), .bus_addr(core_addr),
        .wr_data(core_wr_data), .wr_done(core_wr_done), .wr_resp(mw_resp),
        .rd_done(core_rd_done), .rd_data(mr_data), .rd_resp(mr_resp),
        .ev_in(i_ev), .ev_out(o_ev),
        .busy(core_busy), .done(core_done), .err(core_err), .paused(core_paused),
        .pc_o(core_pc), .flags_o(core_flags),
        .err_code(core_err_code), .err_pc(core_err_pc), .err_addr(core_err_addr),
        .snap_p(core_snap), .irq_p(core_irq), .done_p(core_done_p), .err_p(core_err_p),
        .regs_o(core_regs)
    );

`ifdef FORMAL
`include "cmd_seq_formal.vh"
`endif
endmodule
`default_nettype wire
