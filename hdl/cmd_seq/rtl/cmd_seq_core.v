////////////////////////////////////////////////////////////////////////////////
// Module      : cmd_seq_core
// Description : Instruction engine of cmd_seq (programmable command sequencer).
//               Fetches 64-bit instructions from I-mem, keeps R0-R15, the flags
//               and a 4-level call stack, and issues one bus request at a time.
//
// Instruction (64 bit):
//   [63:58] OP  [57:54] RA  [53:50] RB  [49:48] MOD  [47:40] D  [39:32] DLY  [31:0] IMM
//   MOD[0] = 1: second operand is IMM, 0: R[RB]
//   MOD[1] = 1: RD result goes to D[D], 0: to R[RA]
//   Bus address = R[RB] + IMM (R0 reads 0: IMM is an absolute address)
//   DLY        = cycles to wait after the instruction completes
//
// Conditions (BR, D field): 0 AL, 1 Z, 2 NZ, 3 LT, 4 GE, 5 ERR, 6 LO, 7 HS, 8 MI, 9 PL
//   LT / GE are signed (N xor V), LO / HS unsigned (C = no borrow), MI / PL the sign
//   of the result (wrap-safe "a - b < 0" used for timer deadlines). Other codes are
//   illegal.
// Error codes: 1 illegal instruction, 2 bus error response, 3 access to the own
//   window, 4 call stack overflow / underflow, 5 bus timeout, 6 PC out of I-mem,
//   0x10-0xFF KILL codes.
////////////////////////////////////////////////////////////////////////////////
`default_nettype none

module cmd_seq_core #(
    parameter [7:0] SELF_ID     = 8'h00,
    parameter       IMEM_AW     = 9,
    parameter       DMEM_AW     = 8,
    parameter       BUS_TIMEOUT = 1024      // cycles per bus transfer, 0 = none
) (
    input  wire                 clk,
    input  wire                 rst,
    // control (run / stop / reset / step are one-cycle pulses)
    input  wire                 run,
    input  wire                 stop,
    input  wire                 reset,
    input  wire                 step,
    input  wire                 sys_halt,   // level
    input  wire                 sys_run,    // pulse
    input  wire                 err_halt,
    input  wire [IMEM_AW-1:0]   entry,
    input  wire [31:0]          timer,
    // I-mem (registered read: data one cycle after the address)
    output wire [IMEM_AW-1:0]   imem_addr,
    input  wire [63:0]          imem_data,
    // D-mem port A (registered read)
    output reg  [DMEM_AW-1:0]   d_addr,
    output reg                  d_we,
    output reg  [31:0]          d_wdata,
    input  wire [31:0]          d_rdata,
    // bus requests: req is held until done (or dropped to abandon the transfer)
    output reg                  wr_req,
    output reg                  rd_req,
    output reg  [31:0]          bus_addr,
    output wire [31:0]          wr_data,
    input  wire                 wr_done,
    input  wire [1:0]           wr_resp,
    input  wire                 rd_done,
    input  wire [31:0]          rd_data,
    input  wire [1:0]           rd_resp,
    // events
    input  wire [7:0]           ev_in,
    output reg  [7:0]           ev_out,
    // status
    output wire                 busy,
    output wire                 done,
    output wire                 err,
    output wire                 paused,
    output wire [IMEM_AW-1:0]   pc_o,
    output wire [4:0]           flags_o,    // {C, V, ERR, N, Z}
    output reg  [7:0]           err_code,
    output reg  [IMEM_AW-1:0]   err_pc,
    output reg  [31:0]          err_addr,
    output reg                  snap_p,
    output reg                  irq_p,
    output reg                  done_p,
    output reg                  err_p,
    output wire [16*32-1:0]     regs_o
);
    localparam [5:0] OP_NOP = 6'h00, OP_END = 6'h01, OP_KILL = 6'h02,
                     OP_WR  = 6'h08, OP_RD  = 6'h09,
                     OP_LDI = 6'h10, OP_LDD = 6'h11, OP_STD = 6'h12, OP_MOV = 6'h13,
                     OP_ADD = 6'h18, OP_SUB = 6'h19, OP_AND = 6'h1A, OP_OR  = 6'h1B,
                     OP_XOR = 6'h1C, OP_SHL = 6'h1D, OP_SHR = 6'h1E, OP_CMP = 6'h1F,
                     OP_JMP = 6'h20, OP_BR  = 6'h21, OP_LOOP = 6'h22, OP_CALL = 6'h23,
                     OP_RET = 6'h24,
                     OP_WAIT = 6'h28, OP_WAITT = 6'h29, OP_TMR = 6'h2A,
                     OP_SNAP = 6'h30, OP_FENCE = 6'h31, OP_SEV = 6'h32, OP_WFE = 6'h33,
                     OP_IRQ = 6'h34;

    localparam [7:0] E_ILLEGAL = 8'h01, E_BUS = 8'h02, E_SELF = 8'h03, E_STACK = 8'h04,
                     E_TIMEOUT = 8'h05, E_PC = 8'h06, E_DIDX = 8'h07;
    localparam SEL_LSB = 16;            // own window: address bits [23:16] == SELF_ID

    localparam [3:0] S_IDLE = 4'd0, S_FETCH = 4'd1, S_DECODE = 4'd2, S_BUS_WR = 4'd3,
                     S_BUS_RD = 4'd4, S_LDD_A = 4'd5, S_LDD = 4'd6, S_WAIT = 4'd7,
                     S_WAITT = 4'd8, S_WFE = 4'd9, S_DLY = 4'd10, S_PAUSE = 4'd11,
                     S_DONE = 4'd12, S_ERROR = 4'd13;

    localparam [IMEM_AW-1:0] PC_LAST = {IMEM_AW{1'b1}};

    reg  [3:0]          state;
    reg  [IMEM_AW-1:0]  pc;
    reg  [63:0]         ir;
    reg  [31:0]         R [0:15];
    reg  [IMEM_AW-1:0]  stk [0:3];
    reg  [2:0]          sp;
    reg                 fz, fn, fc, fv, ferr;
    reg  [31:0]         cnt;
    reg  [7:0]          dly;
    reg  [31:0]         tmo;
    reg                 step_mode, stop_pend;
    reg  [DMEM_AW-1:0]  didx;              // D-mem index of RD D[..] (computed in DECODE)

    // instruction fields of the instruction being executed (after DECODE)
    wire [3:0]  x_ra  = ir[57:54];
    wire [1:0]  x_mod = ir[49:48];
    wire [7:0]  x_d   = ir[47:40];

    assign imem_addr = pc;
    assign wr_data   = R[x_ra];
    assign busy      = !(state == S_IDLE || state == S_DONE || state == S_ERROR || state == S_PAUSE);
    assign done      = (state == S_DONE);
    assign err       = (state == S_ERROR);
    assign paused    = (state == S_PAUSE);
    assign pc_o      = pc;
    assign flags_o   = {fc, fv, ferr, fn, fz};

    genvar gi;
    generate for (gi = 0; gi < 16; gi = gi + 1) begin : g_regs
        assign regs_o[32*gi +: 32] = R[gi];
    end endgenerate

    // fields of the fetched instruction (valid in DECODE)
    wire [5:0]  f_op  = imem_data[63:58];
    wire [3:0]  f_ra  = imem_data[57:54];
    wire [3:0]  f_rb  = imem_data[53:50];
    wire [1:0]  f_mod = imem_data[49:48];
    wire [7:0]  f_d   = imem_data[47:40];
    wire [7:0]  f_dly = imem_data[39:32];
    wire [31:0] f_imm = imem_data[31:0];

    wire [31:0] f_a    = R[f_ra];
    wire [31:0] f_b    = f_mod[0] ? f_imm : R[f_rb];
    wire [31:0] f_addr = R[f_rb] + f_imm;
    wire        f_self = (f_addr[SEL_LSB +: 8] == SELF_ID);
    // D-mem index: D, or D + R[x] with MOD[0] = 1 (LDD / STD: R[RB]; RD D[..]: R[RA])
    wire        f_dindexed = f_mod[0];
    wire [31:0] f_dsum = {24'd0, f_d} + (!f_dindexed ? 32'd0 :
                         (f_op == OP_RD) ? R[f_ra] : R[f_rb]);
    wire        f_d_ok = (f_dsum >> DMEM_AW) == 0;           // D index inside D-mem
    wire [DMEM_AW-1:0] f_didx = f_dsum[DMEM_AW-1:0];
    wire        f_tgt_ok = (f_imm >> IMEM_AW) == 0;            // jump target inside I-mem
    wire [IMEM_AW-1:0] f_tgt = f_imm[IMEM_AW-1:0];

    // ALU
    wire [32:0] f_sum  = {1'b0, f_a} + {1'b0, f_b};
    wire [32:0] f_diff = {1'b0, f_a} - {1'b0, f_b};
    reg  [31:0] alu_res;
    always @(*) begin
        case (f_op)
        OP_ADD:          alu_res = f_sum[31:0];
        OP_SUB, OP_CMP:  alu_res = f_diff[31:0];
        OP_AND:          alu_res = f_a & f_b;
        OP_OR:           alu_res = f_a | f_b;
        OP_XOR:          alu_res = f_a ^ f_b;
        OP_SHL:          alu_res = f_a << f_b[4:0];
        default:         alu_res = f_a >> f_b[4:0];
        endcase
    end
    wire alu_c = (f_op == OP_ADD) ? f_sum[32]
               : (f_op == OP_SUB || f_op == OP_CMP) ? !f_diff[32] : 1'b0;
    wire alu_v = (f_op == OP_ADD) ? (f_a[31] == f_b[31]) && (alu_res[31] != f_a[31])
               : (f_op == OP_SUB || f_op == OP_CMP) ? (f_a[31] != f_b[31]) && (alu_res[31] != f_a[31])
               : 1'b0;

    // branch condition
    reg cond_take, cond_ok;
    always @(*) begin
        cond_ok = 1'b1;
        case (f_d)
        8'd0:    cond_take = 1'b1;
        8'd1:    cond_take = fz;
        8'd2:    cond_take = !fz;
        8'd3:    cond_take = fn ^ fv;
        8'd4:    cond_take = !(fn ^ fv);
        8'd5:    cond_take = ferr;
        8'd6:    cond_take = !fc;
        8'd7:    cond_take = fc;
        8'd8:    cond_take = fn;
        8'd9:    cond_take = !fn;
        default: begin cond_take = 1'b0; cond_ok = 1'b0; end
        endcase
    end

    wire [31:0] timer_minus_cnt = timer - cnt;

    // blocking helpers used inside the clocked block
    reg       fin;          // instruction complete: go to DLY / FETCH / PAUSE
    reg       stop_now;
    reg [7:0] dly_v;
    integer   i;

    task fail(input [7:0] code);
        begin
            err_code <= code;
            err_pc   <= pc;
            err_p    <= 1'b1;
            state    <= S_ERROR;
        end
    endtask

    task advance;           // next instruction (error if PC would leave I-mem)
        begin
            if (pc == PC_LAST)
                fail(E_PC);
            else begin
                pc  <= pc + 1'b1;
                fin = 1'b1;
            end
        end
    endtask

    task setr(input [3:0] r, input [31:0] v);
        begin
            if (r != 4'd0)
                R[r] <= v;
        end
    endtask

    initial begin
        state = S_IDLE; pc = 0; ir = 0; sp = 0;
        fz = 0; fn = 0; fc = 0; fv = 0; ferr = 0;
        cnt = 0; dly = 0; tmo = 0; didx = 0; step_mode = 0; stop_pend = 0;
        wr_req = 0; rd_req = 0; bus_addr = 0; d_addr = 0; d_we = 0; d_wdata = 0;
        ev_out = 0; err_code = 0; err_pc = 0; err_addr = 0;
        snap_p = 0; irq_p = 0; done_p = 0; err_p = 0;
        for (i = 0; i < 16; i = i + 1) R[i] = 0;
        for (i = 0; i < 4; i = i + 1) stk[i] = 0;
    end

    always @(posedge clk) begin
        d_we   <= 1'b0;
        snap_p <= 1'b0;
        irq_p  <= 1'b0;
        done_p <= 1'b0;
        err_p  <= 1'b0;
        ev_out <= 8'd0;
        fin      = 1'b0;
        stop_now = stop || sys_halt || stop_pend;
        dly_v    = (state == S_DECODE) ? f_dly : dly;
        if (stop || sys_halt)
            stop_pend <= 1'b1;

        if (rst || reset) begin
            state <= S_IDLE;
            pc <= entry;
            sp <= 3'd0;
            wr_req <= 1'b0;
            rd_req <= 1'b0;
            {fz, fn, fc, fv, ferr} <= 5'd0;
            err_code <= 8'd0;
            step_mode <= 1'b0;
            stop_pend <= 1'b0;
            dly <= 8'd0;
            for (i = 0; i < 16; i = i + 1)
                R[i] <= 32'd0;
        end else begin
            case (state)
            S_IDLE, S_DONE, S_ERROR: begin
                if ((run || sys_run) && !sys_halt) begin
                    pc <= entry; sp <= 3'd0; ferr <= 1'b0;
                    step_mode <= 1'b0; stop_pend <= 1'b0; state <= S_FETCH;
                end else if (step) begin
                    pc <= entry; sp <= 3'd0; ferr <= 1'b0;
                    step_mode <= 1'b1; stop_pend <= 1'b0; state <= S_FETCH;
                end
            end

            S_PAUSE: begin
                if ((run || sys_run) && !sys_halt) begin
                    step_mode <= 1'b0; stop_pend <= 1'b0; state <= S_FETCH;
                end else if (step) begin
                    step_mode <= 1'b1; stop_pend <= 1'b0; state <= S_FETCH;
                end
            end

            S_FETCH: begin                      // I-mem is reading pc
                if (stop_now) begin
                    stop_pend <= 1'b0;
                    state <= S_PAUSE;           // stop at an instruction boundary
                end else
                    state <= S_DECODE;
            end

            S_DECODE: begin
                ir  <= imem_data;
                dly <= f_dly;
                case (f_op)
                OP_NOP, OP_FENCE:               // one transfer at a time: FENCE = NOP
                    advance;

                OP_END: begin
                    done_p <= 1'b1;
                    state <= S_DONE;
                end

                OP_KILL:
                    if (f_d < 8'h10)
                        fail(E_ILLEGAL);
                    else
                        fail(f_d);

                OP_WR, OP_RD:
                    if (f_op == OP_RD && f_mod[1] && !f_d_ok)
                        fail(E_DIDX);
                    else if (f_self) begin
                        err_addr <= f_addr;
                        fail(E_SELF);
                    end else begin
                        bus_addr <= f_addr;
                        didx <= f_didx;
                        tmo <= 32'd0;
                        if (f_op == OP_WR) begin
                            wr_req <= 1'b1; state <= S_BUS_WR;
                        end else begin
                            rd_req <= 1'b1; state <= S_BUS_RD;
                        end
                    end

                OP_LDI: begin setr(f_ra, f_imm); advance; end
                OP_MOV: begin setr(f_ra, f_b);   advance; end
                OP_TMR: begin setr(f_ra, timer); advance; end

                OP_LDD:
                    if (!f_d_ok)
                        fail(E_DIDX);
                    else begin
                        d_addr <= f_didx;
                        state <= S_LDD_A;
                    end

                OP_STD:
                    if (!f_d_ok)
                        fail(E_DIDX);
                    else begin
                        d_addr  <= f_didx;
                        d_wdata <= f_a;
                        d_we    <= 1'b1;
                        advance;
                    end

                OP_ADD, OP_SUB, OP_AND, OP_OR, OP_XOR, OP_SHL, OP_SHR, OP_CMP: begin
                    fz <= (alu_res == 32'd0);
                    fn <= alu_res[31];
                    fc <= alu_c;
                    fv <= alu_v;
                    if (f_op != OP_CMP)
                        setr(f_ra, alu_res);
                    advance;
                end

                OP_JMP:
                    if (!f_tgt_ok)
                        fail(E_PC);
                    else begin
                        pc <= f_tgt; fin = 1'b1;
                    end

                OP_BR:
                    if (!cond_ok)
                        fail(E_ILLEGAL);
                    else if (!f_tgt_ok)
                        fail(E_PC);
                    else if (cond_take) begin
                        pc <= f_tgt; fin = 1'b1;
                    end else
                        advance;

                OP_LOOP:
                    if (f_ra == 4'd0)
                        fail(E_ILLEGAL);
                    else if (!f_tgt_ok)
                        fail(E_PC);
                    else begin
                        R[f_ra] <= f_a - 1'b1;
                        if (f_a != 32'd1) begin
                            pc <= f_tgt; fin = 1'b1;
                        end else
                            advance;
                    end

                OP_CALL:
                    if (sp == 3'd4)
                        fail(E_STACK);
                    else if (!f_tgt_ok || pc == PC_LAST)
                        fail(E_PC);
                    else begin
                        stk[sp[1:0]] <= pc + 1'b1;
                        sp <= sp + 1'b1;
                        pc <= f_tgt; fin = 1'b1;
                    end

                OP_RET:
                    if (sp == 3'd0)
                        fail(E_STACK);
                    else begin
                        pc <= stk[sp[1:0] - 1'b1];
                        sp <= sp - 1'b1;
                        fin = 1'b1;
                    end

                OP_WAIT:
                    if (pc == PC_LAST)
                        fail(E_PC);
                    else begin
                        cnt <= f_b;
                        pc  <= pc + 1'b1;
                        if (f_b == 32'd0)
                            fin = 1'b1;
                        else
                            state <= S_WAIT;
                    end

                OP_WAITT:
                    if (pc == PC_LAST)
                        fail(E_PC);
                    else begin
                        cnt <= f_b;
                        pc  <= pc + 1'b1;
                        state <= S_WAITT;
                    end

                OP_WFE:
                    if (f_d > 8'd7)
                        fail(E_ILLEGAL);
                    else if (pc == PC_LAST)
                        fail(E_PC);
                    else begin
                        pc <= pc + 1'b1;
                        state <= S_WFE;
                    end

                OP_SEV:
                    if (f_d > 8'd7)
                        fail(E_ILLEGAL);
                    else begin
                        ev_out[f_d[2:0]] <= 1'b1;
                        advance;
                    end

                OP_SNAP: begin snap_p <= 1'b1; advance; end
                OP_IRQ:  begin irq_p  <= 1'b1; advance; end

                default:
                    fail(E_ILLEGAL);
                endcase
            end

            S_BUS_WR, S_BUS_RD: begin
                if ((state == S_BUS_WR) ? wr_done : rd_done) begin
                    wr_req <= 1'b0;
                    rd_req <= 1'b0;
                    if (((state == S_BUS_WR) ? wr_resp : rd_resp) != 2'b00) begin
                        ferr <= 1'b1;
                        err_addr <= bus_addr;
                    end
                    if (((state == S_BUS_WR) ? wr_resp : rd_resp) != 2'b00 && err_halt)
                        fail(E_BUS);
                    else begin
                        if (state == S_BUS_RD) begin
                            if (x_mod[1]) begin
                                d_addr  <= didx;
                                d_wdata <= rd_data;
                                d_we    <= 1'b1;
                            end else
                                setr(x_ra, rd_data);
                        end
                        advance;
                    end
                end else if (BUS_TIMEOUT != 0 && tmo == BUS_TIMEOUT - 1) begin
                    wr_req <= 1'b0;             // abandon: the late response is dropped
                    rd_req <= 1'b0;
                    ferr <= 1'b1;
                    err_addr <= bus_addr;
                    if (err_halt)
                        fail(E_TIMEOUT);
                    else
                        advance;
                end else
                    tmo <= tmo + 1'b1;
            end

            S_LDD_A:                            // D-mem reads d_addr this cycle
                state <= S_LDD;

            S_LDD: begin
                setr(x_ra, d_rdata);
                advance;
            end

            S_WAIT:
                if (stop_now) begin
                    stop_pend <= 1'b0; dly <= 8'd0; state <= S_PAUSE;
                end else if (cnt <= 32'd1)
                    fin = 1'b1;
                else
                    cnt <= cnt - 1'b1;

            S_WAITT:                            // wrap-safe: timer - deadline >= 0
                if (stop_now) begin
                    stop_pend <= 1'b0; dly <= 8'd0; state <= S_PAUSE;
                end else if (!timer_minus_cnt[31])
                    fin = 1'b1;

            S_WFE:
                if (stop_now) begin
                    stop_pend <= 1'b0; dly <= 8'd0; state <= S_PAUSE;
                end else if (ev_in[x_d[2:0]])
                    fin = 1'b1;

            S_DLY:
                if (stop_now) begin
                    dly <= 8'd0; stop_pend <= 1'b0; state <= S_PAUSE;
                end else if (dly <= 8'd1) begin
                    dly <= 8'd0;
                    state <= step_mode ? S_PAUSE : S_FETCH;
                end else
                    dly <= dly - 1'b1;

            default:
                state <= S_IDLE;
            endcase

            // instruction complete: count DLY first
            if (fin) begin
                if (dly_v != 8'd0)
                    state <= S_DLY;
                else if (step_mode)
                    state <= S_PAUSE;
                else
                    state <= S_FETCH;
            end
        end
    end


`ifdef FORMAL
    // ---- core properties (formal/run_formal.py, configuration "core") --------------
    // The top-level proof uses formal/cmd_seq_core_abs.v instead of this module;
    // the request behaviour assumed there is proven here.
    reg f_past_valid = 1'b0;
    always @(posedge clk) f_past_valid <= 1'b1;

`ifdef F_CORE_TOP
    // environment of the core inside cmd_seq (proven there): done only answers a
    // pending request
    always @(*) begin
        if (!f_past_valid) assume(rst);
        if (wr_done) assume(wr_req);
        if (rd_done) assume(rd_req);
    end
`endif

    always @(*) if (!rst) begin
        assert(sp <= 3'd4);
        assert(state <= S_ERROR);
        assert(!(wr_req && rd_req));
        assert(wr_req == (state == S_BUS_WR));
        assert(rd_req == (state == S_BUS_RD));
        if (BUS_TIMEOUT != 0 && (wr_req || rd_req)) assert(tmo < BUS_TIMEOUT);
        if (wr_req || rd_req) assert(bus_addr[SEL_LSB +: 8] != SELF_ID);
        assert(R[0] == 32'd0);
        if (state == S_DLY) assert(dly != 8'd0);
    end

    // a request keeps its address and data until it ends; it ends only by done,
    // by the bus timeout or by reset
    always @(posedge clk) if (f_past_valid && !rst && !$past(rst)) begin
        if ($past(wr_req) && wr_req) assert($stable(bus_addr) && $stable(wr_data));
        if ($past(rd_req) && rd_req) assert($stable(bus_addr));
        if ($past(wr_req) && !wr_req)
            assert($past(wr_done) || $past(reset) || $past(tmo) == BUS_TIMEOUT - 1);
        if ($past(rd_req) && !rd_req)
            assert($past(rd_done) || $past(reset) || $past(tmo) == BUS_TIMEOUT - 1);
        if (!$past(wr_req || rd_req) && (wr_req || rd_req))
            assert($past(state) == S_DECODE);
    end

    // STOP / sys_halt pause the program within a bounded time
    reg [15:0] f_stop_wait = 16'd0;
    always @(posedge clk)
        f_stop_wait <= (!rst && !reset && busy && (stop_pend || sys_halt)) ? f_stop_wait + 1'b1 : 16'd0;
    always @(*) if (BUS_TIMEOUT != 0) assert(f_stop_wait <= BUS_TIMEOUT + 4);

    // reachability
    always @(posedge clk) if (!rst) begin
        cover(state == S_DONE);
        cover(state == S_ERROR && err_code == E_TIMEOUT);
        cover(state == S_ERROR && err_code == E_STACK);
        cover(state == S_PAUSE && step_mode);
        cover(state == S_LDD);
        cover(rd_done && x_mod[1]);
    end
`endif
endmodule
`default_nettype wire
