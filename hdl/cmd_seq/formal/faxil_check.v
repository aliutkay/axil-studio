////////////////////////////////////////////////////////////////////////////////
// AXI4-Lite property checkers for cmd_seq (one transfer at a time per direction).
//
// faxil_slave_check : the design is the slave. The master side is assumed to
//                     follow the protocol, the slave outputs are asserted.
// faxil_master_check: the design is the master. Its outputs are asserted; the
//                     slave is assumed to follow the protocol but may stall or
//                     never answer (no liveness assumption).
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module faxil_slave_check #(
    parameter F_MAXWAIT = 16      // cycles from request to response (VALID), 0 = no check
) (
    input wire        clk, resetn,
    input wire        AWVALID, AWREADY, input wire [31:0] AWADDR, input wire [2:0] AWPROT,
    input wire        WVALID, WREADY, input wire [31:0] WDATA, input wire [3:0] WSTRB,
    input wire        BVALID, BREADY, input wire [1:0] BRESP,
    input wire        ARVALID, ARREADY, input wire [31:0] ARADDR, input wire [2:0] ARPROT,
    input wire        RVALID, RREADY, input wire [31:0] RDATA, input wire [1:0] RRESP,
    output reg  [1:0] f_wr_out, output reg [1:0] f_rd_out
);
    reg f_past_valid = 1'b0;
    always @(posedge clk) f_past_valid <= 1'b1;

    // ---- master behaviour (assumed) ---------------------------------------------
    always @(posedge clk) if (f_past_valid) begin
        if (!$past(resetn)) begin
            assume(!AWVALID); assume(!WVALID); assume(!ARVALID);
        end else if (resetn) begin
            if ($past(AWVALID && !AWREADY))
                assume(AWVALID && $stable(AWADDR) && $stable(AWPROT));
            if ($past(WVALID && !WREADY))
                assume(WVALID && $stable(WDATA) && $stable(WSTRB));
            if ($past(ARVALID && !ARREADY))
                assume(ARVALID && $stable(ARADDR) && $stable(ARPROT));
        end
    end

    // ---- slave behaviour (asserted) ---------------------------------------------
    initial begin f_wr_out = 0; f_rd_out = 0; end
    always @(posedge clk)
        if (!resetn) begin
            f_wr_out <= 0; f_rd_out <= 0;
        end else begin
            f_wr_out <= f_wr_out + (AWVALID && AWREADY) - (BVALID && BREADY);
            f_rd_out <= f_rd_out + (ARVALID && ARREADY) - (RVALID && RREADY);
        end

    always @(*) if (resetn) begin
        assert(AWREADY == WREADY);              // address and data taken together
        if (AWREADY) assert(AWVALID && WVALID);
        assert(f_wr_out <= 1);
        assert(f_rd_out <= 1);
        if (BVALID)   assert(f_wr_out == 1);    // no response without a request
        if (RVALID)   assert(f_rd_out == 1);
        if (f_wr_out) assert(!AWREADY);
        if (f_rd_out) assert(!ARREADY);
    end

    always @(posedge clk) if (f_past_valid) begin
        if (!$past(resetn)) begin
            assert(!BVALID); assert(!RVALID);
        end else if (resetn) begin
            if ($past(BVALID && !BREADY)) assert(BVALID && $stable(BRESP));
            if ($past(RVALID && !RREADY)) assert(RVALID && $stable(RDATA) && $stable(RRESP));
        end
    end

    // ---- every request is answered within F_MAXWAIT cycles ---------------------------
    reg [15:0] f_wr_wait, f_rd_wait;
    initial begin f_wr_wait = 0; f_rd_wait = 0; end
    always @(posedge clk) begin
        f_wr_wait <= (resetn && f_wr_out != 0 && !BVALID) ? f_wr_wait + 1'b1 : 16'd0;
        f_rd_wait <= (resetn && f_rd_out != 0 && !RVALID) ? f_rd_wait + 1'b1 : 16'd0;
    end
    always @(*) if (F_MAXWAIT != 0) begin
        assert(f_wr_wait <= F_MAXWAIT);
        assert(f_rd_wait <= F_MAXWAIT);
    end
endmodule


module faxil_master_check (
    input wire        clk, resetn,
    input wire        AWVALID, AWREADY, input wire [31:0] AWADDR, input wire [2:0] AWPROT,
    input wire        WVALID, WREADY, input wire [31:0] WDATA, input wire [3:0] WSTRB,
    input wire        BVALID, BREADY, input wire [1:0] BRESP,
    input wire        ARVALID, ARREADY, input wire [31:0] ARADDR, input wire [2:0] ARPROT,
    input wire        RVALID, RREADY, input wire [31:0] RDATA, input wire [1:0] RRESP,
    output reg  [1:0] f_aw_out, output reg [1:0] f_w_out, output reg [1:0] f_ar_out
);
    reg f_past_valid = 1'b0;
    always @(posedge clk) f_past_valid <= 1'b1;

    initial begin f_aw_out = 0; f_w_out = 0; f_ar_out = 0; end
    always @(posedge clk)
        if (!resetn) begin
            f_aw_out <= 0; f_w_out <= 0; f_ar_out <= 0;
        end else begin
            f_aw_out <= f_aw_out + (AWVALID && AWREADY) - (BVALID && BREADY);
            f_w_out  <= f_w_out  + (WVALID && WREADY)   - (BVALID && BREADY);
            f_ar_out <= f_ar_out + (ARVALID && ARREADY) - (RVALID && RREADY);
        end

    // ---- slave behaviour (assumed) ------------------------------------------------
    always @(*) if (resetn) begin
        if (BVALID) assume(f_aw_out != 0 && f_w_out != 0);
        if (RVALID) assume(f_ar_out != 0);
    end
    always @(posedge clk) if (f_past_valid) begin
        if (!$past(resetn)) begin
            assume(!BVALID); assume(!RVALID);
        end else if (resetn) begin
            if ($past(BVALID && !BREADY)) assume(BVALID && $stable(BRESP));
            if ($past(RVALID && !RREADY)) assume(RVALID && $stable(RDATA) && $stable(RRESP));
        end
    end

    // ---- master behaviour (asserted) --------------------------------------------------
    always @(*) if (resetn) begin
        assert(f_aw_out <= 1);                  // one transfer at a time
        assert(f_w_out <= 1);
        assert(f_ar_out <= 1);
        if (f_aw_out != 0 && f_w_out != 0) assert(!AWVALID && !WVALID);
        if (f_aw_out != 0) assert(!AWVALID);
        if (f_w_out != 0)  assert(!WVALID);
        if (f_ar_out != 0) assert(!ARVALID);
    end
    always @(posedge clk) if (f_past_valid) begin
        if (!$past(resetn)) begin
            assert(!AWVALID); assert(!WVALID); assert(!ARVALID);
        end else if (resetn) begin
            if ($past(AWVALID && !AWREADY))
                assert(AWVALID && $stable(AWADDR) && $stable(AWPROT));
            if ($past(WVALID && !WREADY))
                assert(WVALID && $stable(WDATA) && $stable(WSTRB));
            if ($past(ARVALID && !ARREADY))
                assert(ARVALID && $stable(ARADDR) && $stable(ARPROT));
        end
    end
endmodule
`default_nettype wire
