// Directed tests of cmd_seq: corner cases that random programs do not reach
// (timeouts, abandoned transfers, STOP / STEP / RESET timing, loader and CSR
// rules, pass-through integrity, SNAP banks, IRQ, sys_halt / sys_run).
//   +progs=<dir>   folder with the assembled programs (<name>.hex)
`timescale 1ns/1ps
`default_nettype none
module tb_directed;
    parameter TB_IMEM_AW = 9, TB_DMEM_AW = 8, TB_PT_TIMEOUT = 32, TB_BUS_TIMEOUT = 400;
    parameter TB_MAXD = 2, TB_SLOW_LAT = 200, TB_SEED = 5;

`include "tb_common.vh"

    integer errors = 0, checks = 0;
    reg [8*256-1:0] progdir;
    reg [63:0] pm [0:511];
    reg [31:0] v, st;
    reg [1:0]  r;
    integer i, n;

    task check(input [8*48-1:0] what, input [31:0] got, input [31:0] exp);
        begin
            checks = checks + 1;
            if (got !== exp) begin
                errors = errors + 1;
                $display("FAIL %0s: got 0x%08h, expected 0x%08h", what, got, exp);
            end
        end
    endtask

    task reset_all;
        begin
            @(negedge clk); rst_n = 0; sys_halt = 0; sys_run = 0; ev_in = 0;
            tick(3);
            @(negedge clk); rst_n = 1;
            tick(2);
        end
    endtask

    function integer count_lines(input [8*300-1:0] path);
        integer fd, c, k;
        reg [63:0] tmp;
        begin
            fd = $fopen(path, "r");
            c = 0;
            if (fd == 0) $display("FAIL cannot open %0s", path);
            else begin
                while ($fscanf(fd, "%h\n", tmp) == 1) c = c + 1;
                $fclose(fd);
            end
            count_lines = c;
        end
    endfunction

    task load(input [8*32-1:0] name);
        reg [8*300-1:0] path;
        begin
            for (i = 0; i < 512; i = i + 1) pm[i] = 64'hx;
            $sformat(path, "%0s/%0s.hex", progdir, name);
            $readmemh(path, pm, 0, count_lines(path) - 1);
            csr_w(16'h0020, 0);
            n = 0;
            while (n < 512 && pm[n] !== 64'hx) begin
                csr_w(16'h0024, pm[n][31:0]);
                csr_w(16'h0024, pm[n][63:32]);
                n = n + 1;
            end
        end
    endtask

    task start(input [31:0] entry, input [31:0] ctrl);
        begin
            csr_w(16'h0010, entry);
            csr_w(16'h0008, ctrl | 1);
        end
    endtask

    task wait_idle(input integer max);
        integer t;
        begin
            t = 0;
            st = 1;
            while (st[0] && t < max) begin
                csr_r(16'h000C, st);
                t = t + 1;
            end
        end
    endtask

    function [31:0] reg_ofs(input integer k);
        reg_ofs = 32'h0100 + 4 * k;
    endfunction

    initial begin
        if (!$value$plusargs("progs=%s", progdir)) progdir = "build";
        reset_all;

        // ---------------------------------------------------------------- CSR rules
        csr_r(16'h0000, v); check("ID", v, 32'h5EC0_0200);
        csr_r(16'h0004, v); check("CAPS", v, 32'h0010_0809);
        axi_write_s(SELF | 16'h0008, 32'h40, 4'h3, 0, r); check("partial strobe -> SLVERR", r, 2);
        axi_write(SELF | 16'h0000, 0, r); check("write ID -> SLVERR", r, 2);
        axi_write(SELF | 16'h0104, 0, r); check("write R1 -> SLVERR", r, 2);
        axi_write(SELF | 16'h0050, 0, r); check("write unmapped -> DECERR", r, 3);
        axi_read(SELF | 16'h0050, v, r);  check("read unmapped -> DECERR", r, 3);
        axi_read(SELF | 16'h0800, v, r);  check("read past D-mem -> DECERR", r, 3);
        axi_read(32'hFF00_0008, v, r);    check("block bits ignored", r, 0);
        check("CTRL after reset (ERR_HALT)", v, 32'h40);

        // ---------------------------------------------------------------- LDD
        load("ldd"); start(0, 32'h40); wait_idle(200);
        check("ldd DONE", st[2:0], 3'b010);
        csr_r(reg_ofs(2), v); check("LDD R2 = D[1]", v, 32'h22);
        csr_r(reg_ofs(3), v); check("LDD R3 = D[0]", v, 32'h11);
        csr_r(reg_ofs(4), v); check("LDD R4 = D[1]", v, 32'h22);

        // ---------------------------------------------------------------- conditions
        reset_all;
        load("cond"); start(0, 32'h40); wait_idle(500);
        csr_r(reg_ofs(1), v); check("conditions 0x7FFFFFFF vs -1", v, 32'h155);
        csr_r(reg_ofs(4), v); check("conditions 5 vs 5", v, 32'h293);
        csr_r(reg_ofs(6), v); check("conditions -1 vs 0x7FFFFFFF", v, 32'h18D);

        // ---------------------------------------------------------------- STOP / STEP
        load("stop"); start(0, 32'h40);
        tick(40); csr_w(16'h0008, 32'h42); tick(4);
        csr_r(16'h000C, st); check("STOP in WAIT: PAUSED", st[3:0], 4'b1000);
        check("STOP in WAIT: PC", st[31:16], 1);
        csr_w(16'h0008, 32'h41); tick(40); csr_w(16'h0008, 32'h42); tick(4);
        csr_r(16'h000C, st); check("STOP in DLY: PAUSED", st[3], 1);
        check("STOP in DLY: PC", st[31:16], 2);
        csr_w(16'h0008, 32'h41); tick(40);
        csr_r(16'h000C, st); check("WFE waits", st[0], 1);
        ev_in[3] = 1; tick(2); ev_in[3] = 0; tick(30);
        csr_w(16'h0008, 32'h42); tick(20);
        csr_r(16'h000C, st); check("STOP during a bus write: still BUSY", st[0], 1);
        check("WR_DRAIN stays 0 (not abandoned)", st[5], 0);
        tick(300);
        csr_r(16'h000C, st); check("STOP after the write: PAUSED", st[3], 1);
        check("STOP after the write: PC", st[31:16], 5);
        csr_w(16'h0008, 32'h48); tick(10);        // STEP
        csr_r(16'h000C, st); check("STEP: PAUSED at 6", {st[3], st[31:16]}, {1'b1, 16'd6});
        csr_r(reg_ofs(5), v); check("STEP executed LDI", v, 32'h55);
        csr_w(16'h0008, 32'h41); tick(10);
        csr_r(16'h000C, st); check("RUN after STEP: DONE", st[2:0], 3'b010);

        // ---------------------------------------------------------------- RESET during a transfer
        reset_all;
        load("orphan"); start(0, 32'h40);
        tick(40);
        csr_w(16'h0008, 32'h44);                  // RESET
        csr_r(16'h000C, st); check("RESET: WR_DRAIN", st[5], 1);
        start(4, 32'h40); wait_idle(1000);
        check("second program DONE", st[2:0], 3'b010);
        csr_r(reg_ofs(5), v); check("second program read back its write", v, 32'h1234);
        check("second program write reached IP1", slave.ram[1][2], 32'h1234);
        tick(300);
        check("abandoned write completed on the bus", slave.ram[1][1], 32'hAAAA);
        csr_r(16'h000C, st); check("WR_DRAIN cleared", st[5], 0);

        // ---------------------------------------------------------------- bus timeout (core)
        reset_all;
        load("hung"); start(0, 32'h40); wait_idle(500);
        check("timeout: ERROR", st[2:0], 3'b100);
        csr_r(16'h0014, v); check("timeout: ERR_CODE 5", v, 5);
        csr_r(16'h0018, v); check("timeout: ERR_PC", v, 1);
        csr_r(16'h001C, v); check("timeout: ERR_ADDR", v, 32'h000C0000);
        check("timeout: RD_DRAIN", st[6], 1);
        reset_all;
        load("hung"); start(0, 32'h00); wait_idle(500);
        check("timeout, ERR_HALT=0: DONE", st[2:0], 3'b010);
        check("timeout, ERR_HALT=0: ERR flag", st[10], 1);
        csr_r(reg_ofs(3), v); check("timeout, ERR_HALT=0: BR ERR taken", v, 32'h77);

        // ---------------------------------------------------------------- pass-through
        reset_all;
        axi_write_s(32'h4201_0010, 32'hCAFE_F00D, 4'h5, 3'b101, r);
        check("PT write OKAY", r, 0);
        check("PT AWADDR unchanged", slave.last_awaddr, 32'h4201_0010);
        check("PT AWPROT unchanged", slave.last_awprot, 3'b101);
        check("PT WSTRB unchanged", slave.last_wstrb, 4'h5);
        check("PT WDATA unchanged", slave.last_wdata, 32'hCAFE_F00D);
        axi_read_p(32'h1101_0010, 3'b011, v, r);
        check("PT ARADDR unchanged", slave.last_araddr, 32'h1101_0010);
        check("PT ARPROT unchanged", slave.last_arprot, 3'b011);
        check("PT read data", v, slave.ram[1][4]);
        axi_read(32'h000E_0024, v, r); check("PT SLVERR", r, 2); check("PT SLVERR data", v, 32'hBAD0_0024);
        axi_read(32'h0010_0028, v, r); check("PT DECERR", r, 3); check("PT DECERR data", v, 32'hDEC0_0028);
        axi_write(32'h000E_0000, 1, r); check("PT write SLVERR", r, 2);

        // writes are closed while the program runs (PT_WR_RUN = 0)
        load("busy"); start(0, 32'h40); tick(5);
        v = slave.ram[2][5];
        axi_write(32'h0002_0014, 32'h1111_2222, r); check("PT write while running -> SLVERR", r, 2);
        tick(20); check("blocked PT write not executed", slave.ram[2][5], v);
        axi_read(32'h0002_0014, v, r); check("PT read while running", r, 0);
        csr_w(16'h0008, 32'h60);                 // PT_WR_RUN, keeps running
        axi_write(32'h0002_0014, 32'h3333_4444, r); check("PT write with PT_WR_RUN", r, 0);
        check("PT write executed", slave.ram[2][5], 32'h3333_4444);
        csr_w(16'h0008, 32'h04);                  // RESET

        // a PT write the engine never took (busy with a slow core write) is not executed
        load("slowwr"); start(0, 32'h60);         // ERR_HALT + PT_WR_RUN
        tick(30);
        v = slave.ram[2][6];
        axi_write(32'h0002_0018, 32'h5555_6666, r); check("PT write timeout -> SLVERR", r, 2);
        tick(400);
        check("timed-out PT write not executed", slave.ram[2][6], v);

        // a hung slave does not lock the upstream port
        reset_all;
        axi_read(32'h000C_0000, v, r); check("PT read of hung slave -> SLVERR", r, 2);
        csr_r(16'h000C, st); check("own window still answers (RD_DRAIN)", st[6], 1);
        axi_read(32'h0001_0000, v, r); check("next PT read times out too", r, 2);
        axi_write(32'h000C_0000, 1, r); check("PT write to hung slave -> SLVERR", r, 2);
        csr_r(16'h0000, v); check("own window after hung write", v, 32'h5EC0_0200);

        // ---------------------------------------------------------------- loader
        reset_all;
        load("busy"); start(0, 32'h40); tick(5);
        axi_write(SELF | 16'h0024, 0, r); check("IMEM_DATA while running -> SLVERR", r, 2);
        csr_w(16'h0008, 32'h42); tick(5);
        axi_write(SELF | 16'h0024, 0, r); check("IMEM_DATA while paused -> SLVERR", r, 2);
        csr_w(16'h0008, 32'h44);
        csr_w(16'h0020, 32'h1FF);
        axi_write(SELF | 16'h0024, 32'h1, r); check("IMEM last word lo", r, 0);
        csr_r(16'h000C, st); check("IMEM_PHASE after one word", st[4], 1);
        axi_write(SELF | 16'h0024, 32'h2, r); check("IMEM last word hi", r, 0);
        axi_write(SELF | 16'h0024, 32'h3, r); check("IMEM full -> SLVERR", r, 2);
        csr_r(16'h0028, v); check("IMEM_CNT", v, 1);
        csr_r(16'h002C, v); check("IMEM_SUM", v, 3);
        csr_w(16'h0030, 32'hFF);
        axi_write(SELF | 16'h0034, 32'h9, r); check("DMEM last word", r, 0);
        axi_write(SELF | 16'h0034, 32'h9, r); check("DMEM full -> SLVERR", r, 2);
        axi_read(SELF | 16'h07FC, v, r); check("D[255] via window", v, 32'h9);

        // ---------------------------------------------------------------- errors
        reset_all;
        csr_w(16'h0020, 32'h1FF); csr_w(16'h0024, 0); csr_w(16'h0024, 0);   // NOP at 511
        start(511, 32'h40); wait_idle(100);
        csr_r(16'h0014, v); check("PC past I-mem -> 6", v, 6);
        csr_r(16'h0018, v); check("PC past I-mem: ERR_PC", v, 511);
        load("errors");
        start(1, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("JMP outside I-mem -> 6", v, 6);
        start(2, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("LOOP R0 -> 1", v, 1);
        start(3, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("KILL 0x42", v, 32'h42);
        start(4, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("KILL 5 -> 1", v, 1);
        start(5, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("own window -> 3", v, 3);
        csr_r(16'h001C, v); check("own window: ERR_ADDR", v, 32'h0300_0010);
        start(6, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("bad condition -> 1", v, 1);
        start(7, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("undefined opcode -> 1", v, 1);
        start(8, 32'h40); wait_idle(100); csr_r(16'h0014, v); check("RET on empty stack -> 4", v, 4);
        csr_r(16'h0018, v); check("RET: ERR_PC", v, 8);

        // ---------------------------------------------------------------- SNAP
        reset_all;
        load("snap");
        csr_w(16'h0010, 0);
        csr_w(16'h0008, 32'h58); tick(10);        // SNAP_EN + ERR_HALT + STEP
        csr_w(16'h0008, 32'h58); tick(10);
        csr_w(16'h0008, 32'h58); tick(10);        // after the first SNAP
        axi_read(SELF | 16'h0400, v, r); check("published D[0] after 1st SNAP", v, 1);
        csr_w(16'h0008, 32'h51); wait_idle(100);
        axi_read(SELF | 16'h0400, v, r); check("published D[0] after 2nd SNAP", v, 2);
        axi_read(SELF | 16'h0404, v, r); check("published D[1] after 2nd SNAP", v, 9);
        csr_r(16'h0038, v); check("SNAP_CNT", v, 2);

        // ---------------------------------------------------------------- IRQ
        reset_all;
        load("irq"); start(0, 32'hC0); wait_idle(100);
        csr_r(16'h0040, v); check("IRQ_STAT DONE + IRQ", v, 5);
        check("irq output", irq, 1);
        csr_w(16'h0040, 1); csr_r(16'h0040, v); check("W1C DONE", v, 4);
        csr_w(16'h0040, 4); csr_r(16'h0040, v); check("W1C IRQ", v, 0);
        check("irq output low", irq, 0);

        // ---------------------------------------------------------------- sys_halt / sys_run
        reset_all;
        load("loop"); csr_w(16'h0010, 0);
        @(negedge clk); sys_halt = 1; sys_run = 1; @(negedge clk); sys_run = 0;
        tick(5); csr_r(16'h000C, st); check("sys_run ignored while sys_halt", st[3:0], 0);
        @(negedge clk); sys_halt = 0; sys_run = 1; @(negedge clk); sys_run = 0;
        tick(40); @(negedge clk); sys_halt = 1; tick(30);
        csr_r(16'h000C, st); check("sys_halt: PAUSED", st[3], 1);
        @(negedge clk); sys_halt = 0; sys_run = 1; @(negedge clk); sys_run = 0;
        wait_idle(500);
        check("sys_run resumes: DONE", st[2:0], 3'b010);
        csr_r(reg_ofs(1), v); check("loop result", v, 5);

        check("upstream protocol errors", proto_errors, 0);
        $display("tb_directed: %0d checks, %0d errors: %0s", checks, errors,
                 errors ? "FAILED" : "PASSED");
        $finish;
    end

    initial begin
        #20000000;
        $display("tb_directed: TIMEOUT");
        $finish;
    end
endmodule
`default_nettype wire
