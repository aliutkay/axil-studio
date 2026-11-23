// Formal properties of cmd_seq, included inside the module when FORMAL is
// defined (see formal/run_formal.py). With F_ANYPROG the I-mem read data is
// free: the core is checked for every instruction sequence.

    reg f_past_valid = 1'b0;
    always @(posedge clk) f_past_valid <= 1'b1;
    always @(*) if (!f_past_valid) assume(!S_AXI_ARESETN);

    localparam F_MAXWAIT = PT_TIMEOUT + 6;

    // ---- protocol on both ports (and bounded upstream response time) --------------
    wire [1:0] f_up_wr_out, f_up_rd_out, f_dn_aw_out, f_dn_w_out, f_dn_ar_out;

    faxil_slave_check #(.F_MAXWAIT(F_MAXWAIT)) f_up (
        .clk(clk), .resetn(S_AXI_ARESETN),
        .AWVALID(S_AXI_AWVALID), .AWREADY(S_AXI_AWREADY), .AWADDR(S_AXI_AWADDR), .AWPROT(S_AXI_AWPROT),
        .WVALID(S_AXI_WVALID), .WREADY(S_AXI_WREADY), .WDATA(S_AXI_WDATA), .WSTRB(S_AXI_WSTRB),
        .BVALID(S_AXI_BVALID), .BREADY(S_AXI_BREADY), .BRESP(S_AXI_BRESP),
        .ARVALID(S_AXI_ARVALID), .ARREADY(S_AXI_ARREADY), .ARADDR(S_AXI_ARADDR), .ARPROT(S_AXI_ARPROT),
        .RVALID(S_AXI_RVALID), .RREADY(S_AXI_RREADY), .RDATA(S_AXI_RDATA), .RRESP(S_AXI_RRESP),
        .f_wr_out(f_up_wr_out), .f_rd_out(f_up_rd_out));

    faxil_master_check f_dn (
        .clk(clk), .resetn(S_AXI_ARESETN),
        .AWVALID(M_AXI_AWVALID), .AWREADY(M_AXI_AWREADY), .AWADDR(M_AXI_AWADDR), .AWPROT(M_AXI_AWPROT),
        .WVALID(M_AXI_WVALID), .WREADY(M_AXI_WREADY), .WDATA(M_AXI_WDATA), .WSTRB(M_AXI_WSTRB),
        .BVALID(M_AXI_BVALID), .BREADY(M_AXI_BREADY), .BRESP(M_AXI_BRESP),
        .ARVALID(M_AXI_ARVALID), .ARREADY(M_AXI_ARREADY), .ARADDR(M_AXI_ARADDR), .ARPROT(M_AXI_ARPROT),
        .RVALID(M_AXI_RVALID), .RREADY(M_AXI_RREADY), .RDATA(M_AXI_RDATA), .RRESP(M_AXI_RRESP),
        .f_aw_out(f_dn_aw_out), .f_w_out(f_dn_w_out), .f_ar_out(f_dn_ar_out));

    // ---- 1. the own window never reaches the lower bus -----------------------------
    always @(*) if (S_AXI_ARESETN) begin
        if (M_AXI_AWVALID) assert(M_AXI_AWADDR[SEL_LSB +: 8] != SELF_ID);
        if (M_AXI_ARVALID) assert(M_AXI_ARADDR[SEL_LSB +: 8] != SELF_ID);
    end

    // ---- 2. pass-through payload is the accepted upstream request, unchanged ---------
    reg [31:0] f_aw_addr, f_w_data, f_ar_addr;
    reg [3:0]  f_w_strb;
    reg [2:0]  f_aw_prot, f_ar_prot;
    always @(posedge clk) begin
        if (S_AXI_AWVALID && S_AXI_AWREADY) begin
            f_aw_addr <= S_AXI_AWADDR; f_aw_prot <= S_AXI_AWPROT;
            f_w_data  <= S_AXI_WDATA;  f_w_strb  <= S_AXI_WSTRB;
        end
        if (S_AXI_ARVALID && S_AXI_ARREADY) begin
            f_ar_addr <= S_AXI_ARADDR; f_ar_prot <= S_AXI_ARPROT;
        end
    end
    always @(*) if (S_AXI_ARESETN) begin
        if (mw_state != M_IDLE && !mw_own_core && !mw_orphan)
            assert(mw_addr == f_aw_addr && mw_prot == f_aw_prot && mw_data == f_w_data &&
                   mw_strb == f_w_strb);
        if (mr_state != M_IDLE && !mr_own_core && !mr_orphan)
            assert(mr_addr == f_ar_addr && mr_prot == f_ar_prot);
        if (mw_state != M_IDLE && mw_own_core)
            assert(mw_strb == 4'hF && mw_prot == 3'b000);
    end

    // ---- 3. every response goes to the request that caused it -----------------------
    // Each request gets a tag when its req rises; the engine keeps the tag of the
    // request it carries; done may only reach a requester with the same tag.
    reg [3:0] f_cw_tag, f_cr_tag, f_pw_tag, f_pr_tag, f_mw_tag, f_mr_tag;
    reg       f_cw_q, f_cr_q, f_pw_q, f_pr_q;
    initial begin
        f_cw_tag = 0; f_cr_tag = 0; f_pw_tag = 0; f_pr_tag = 0; f_mw_tag = 0; f_mr_tag = 0;
        f_cw_q = 0; f_cr_q = 0; f_pw_q = 0; f_pr_q = 0;
    end
    wire [3:0] f_cw_now = f_cw_tag + (core_wr_req && !f_cw_q);
    wire [3:0] f_cr_now = f_cr_tag + (core_rd_req && !f_cr_q);
    wire [3:0] f_pw_now = f_pw_tag + (pt_wr_req && !f_pw_q);
    wire [3:0] f_pr_now = f_pr_tag + (pt_rd_req && !f_pr_q);
    always @(posedge clk) begin
        f_cw_q <= core_wr_req; f_cr_q <= core_rd_req; f_pw_q <= pt_wr_req; f_pr_q <= pt_rd_req;
        f_cw_tag <= f_cw_now; f_cr_tag <= f_cr_now; f_pw_tag <= f_pw_now; f_pr_tag <= f_pr_now;
        if (mw_state == M_IDLE && !mw_done_p && mw_take_core)     f_mw_tag <= f_cw_now;
        else if (mw_state == M_IDLE && !mw_done_p && mw_take_pt)  f_mw_tag <= f_pw_now;
        if (mr_state == M_IDLE && !mr_done_p && core_rd_req)       f_mr_tag <= f_cr_now;
        else if (mr_state == M_IDLE && !mr_done_p && pt_rd_req && !pt_rd_last) f_mr_tag <= f_pr_now;
    end
    always @(*) if (S_AXI_ARESETN && f_past_valid) begin
        if (core_wr_done) assert(f_mw_tag == f_cw_now);
        if (core_rd_done) assert(f_mr_tag == f_cr_now);
        if (pt_wr_done)   assert(f_mw_tag == f_pw_now);
        if (pt_rd_done)   assert(f_mr_tag == f_pr_now);
        // a requester never sees done while it is not waiting
        if (core_wr_done) assert(core_wr_req);
        if (core_rd_done) assert(core_rd_req);
        if (pt_wr_done)   assert(sw_state == SW_PT);
        if (pt_rd_done)   assert(sr_state == SR_PT);
    end

    // ---- 4. a pass-through write answered SLVERR before the engine took it is never sent
    // f_dead: the slave port gave up on a write the engine had not taken. Until the
    // next upstream write arrives, the engine must not be carrying it.
    reg f_dead = 1'b0;
    always @(posedge clk) begin
        if (pt_wr_req && !f_pw_q)
            f_dead <= 1'b0;
        if (sw_state == SW_PT && !pt_wr_done && !pt_wr_taken && (pt_wr_last || pt_wr_block))
            f_dead <= 1'b1;
        if (!S_AXI_ARESETN)
            f_dead <= 1'b0;
    end
    always @(*) if (S_AXI_ARESETN && f_dead)
        assert(!(mw_state != M_IDLE && !mw_own_core && !mw_orphan));

    // writes are closed while the program runs (PT_WR_RUN = 0)
    always @(posedge clk) if (f_past_valid && S_AXI_ARESETN && $past(S_AXI_ARESETN))
        if ($past(mw_state == M_IDLE && !mw_done_p && !mw_take_core && mw_take_pt))
            assert(!$past(pt_wr_block));

    // ---- 5. an interrupt event is never lost to a simultaneous W1C ------------------
    always @(posedge clk) if (f_past_valid && S_AXI_ARESETN && $past(S_AXI_ARESETN))
        assert((irq_stat & $past(irq_set)) == $past(irq_set));

    // ---- helper invariants (state relations, needed for induction) ---------------------
    always @(*) if (S_AXI_ARESETN) begin
        assert(pt_wr_req == (sw_state == SW_PT));
        assert(pt_rd_req == (sr_state == SR_PT));
        assert(s_bvalid == (sw_state == SW_RESP));
        assert(s_rvalid == (sr_state == SR_RESP));
        assert(sw_state != SW_RESP || f_up_wr_out == 1);
        assert(sr_state != SR_RESP || f_up_rd_out == 1);
        assert((sw_state == SW_IDLE) == (f_up_wr_out == 0));
        assert((sr_state == SR_IDLE) == (f_up_rd_out == 0));
        assert(sr_state <= SR_RESP);
        if (sw_state == SW_PT) assert(sw_tmo < PT_TIMEOUT && pt_wr_last == (sw_tmo + 1 >= PT_TIMEOUT));
        if (sr_state == SR_PT) assert(sr_tmo < PT_TIMEOUT && pt_rd_last == (sr_tmo + 1 >= PT_TIMEOUT));
        if (sw_state != SW_PT) assert(!pt_wr_last);
        if (sr_state != SR_PT) assert(!pt_rd_last);
        assert(mw_state != 2'd3);
        assert(mr_state != 2'd3);
        // downstream outstanding counts follow the engines
        assert(f_dn_aw_out == ((mw_state == M_RESP) || (mw_state == M_ADDR && aw_done)));
        assert(f_dn_w_out  == ((mw_state == M_RESP) || (mw_state == M_ADDR && w_done)));
        assert(f_dn_ar_out == (mr_state == M_RESP));
        if (mw_done_p) assert(mw_state == M_IDLE);
        if (mr_done_p) assert(mr_state == M_IDLE);
        // a request still held by its owner carries the owner's tag
        if ((mw_state != M_IDLE && !mw_orphan || mw_done_p) && mw_own_req)
            assert(f_mw_tag == (mw_own_core ? f_cw_tag : f_pw_tag));
        if ((mr_state != M_IDLE && !mr_orphan || mr_done_p) && mr_own_req)
            assert(f_mr_tag == (mr_own_core ? f_cr_tag : f_pr_tag));
        if (f_dead) assert(sw_state != SW_PT || !f_pw_q);
        // requests in flight never address the own window
        if (mw_state != M_IDLE) assert(mw_addr[SEL_LSB +: 8] != SELF_ID);
        if (mr_state != M_IDLE) assert(mr_addr[SEL_LSB +: 8] != SELF_ID);
        if (sw_state == SW_PT) assert(sw_addr[SEL_LSB +: 8] != SELF_ID);
        if (sr_state == SR_PT) assert(sr_addr[SEL_LSB +: 8] != SELF_ID);
        if (core_wr_req || core_rd_req) assert(core_addr[SEL_LSB +: 8] != SELF_ID);
        // the slave FSMs hold the accepted request
        if (sw_state != SW_IDLE)
            assert(sw_addr == f_aw_addr && sw_prot == f_aw_prot && sw_data == f_w_data &&
                   sw_strb == f_w_strb);
        if (sr_state != SR_IDLE) assert(sr_addr == f_ar_addr && sr_prot == f_ar_prot);
        if (mw_state != M_IDLE && mw_own_core && !mw_orphan && core_wr_req)
            assert(mw_addr == core_addr && mw_data == core_wr_data);
        if (mr_state != M_IDLE && mr_own_core && !mr_orphan && core_rd_req)
            assert(mr_addr == core_addr);
        assert(!(core_wr_req && core_rd_req));
    end

    // ---- reachability (cover) ------------------------------------------------------------
    always @(posedge clk) if (S_AXI_ARESETN) begin
        cover(pt_wr_done && mw_resp == 2'b00);          // pass-through write completed
        cover(pt_rd_done);                              // pass-through read completed
        cover(core_wr_done);                            // sequencer write completed
        cover(mw_state == M_RESP && mw_orphan);         // abandoned write in flight
        cover(sw_state == SW_PT && pt_wr_last && !pt_wr_taken);   // PT write timeout, not sent
        cover(S_AXI_BVALID && S_AXI_BRESP == 2'b10 && f_dead);
    end
