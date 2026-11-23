// Random-program testbench: loads one program written by random_test.py, runs it
// (optionally with pass-through traffic on the upstream port) and dumps the
// final state. random_test.py compares the dump with the reference model.
//   +in=<file>   program and settings      +out=<file>  dump
`timescale 1ns/1ps
`default_nettype none
module tb_random;
    parameter TB_IMEM_AW = 9, TB_DMEM_AW = 8, TB_PT_TIMEOUT = 1024, TB_BUS_TIMEOUT = 1024;
    parameter TB_MAXD = 3, TB_SLOW_LAT = 20, TB_SEED = 1;

`include "tb_common.vh"

    reg [8*256-1:0] in_name, out_name;
    integer fin, fout, n, i, rc, nd, pt_mode, ctrl, entry, max_cycles;
    reg [63:0] w;
    reg [31:0] v, status, shadow [0:63], pt_errors;
    reg [1:0]  r;
    reg        busy;
    integer    kind, idx;

    initial begin
        if (!$value$plusargs("in=%s", in_name) || !$value$plusargs("out=%s", out_name)) begin
            $display("usage: +in=<file> +out=<file>");
            $finish;
        end
        fin  = $fopen(in_name, "r");
        fout = $fopen(out_name, "w");
        rc = $fscanf(fin, "%d %d %h %d %d %d\n", n, entry, ctrl, pt_mode, nd, max_cycles);
        slave.log_fd = fout;
        for (i = 0; i < 64; i = i + 1) shadow[i] = slave.ram[4][i];
        pt_errors = 0;

        tick(3); rst_n = 1; tick(2);
        csr_w(16'h0008, ctrl & 32'hF0);                 // settings without RUN
        csr_w(16'h0020, 0);                             // IMEM_PTR
        for (i = 0; i < n; i = i + 1) begin
            rc = $fscanf(fin, "%h\n", w);
            csr_w(16'h0024, w[31:0]);
            csr_w(16'h0024, w[63:32]);
        end
        csr_w(16'h0030, 0);                             // DMEM_PTR
        for (i = 0; i < nd; i = i + 1) begin
            rc = $fscanf(fin, "%h\n", v);
            csr_w(16'h0034, v);
        end
        csr_r(16'h0028, v);
        if (v != n) $fdisplay(fout, "fail IMEM_CNT %0d != %0d", v, n);
        csr_w(16'h0010, entry);
        csr_w(16'h0008, ctrl | 1);                      // RUN

        busy = 1;
        while (busy) begin
            if (cyc > max_cycles) begin
                $fdisplay(fout, "fail timeout");
                busy = 0;
            end
            kind = pt_mode ? {$random(seed_m)} % 4 : 0;
            idx  = {$random(seed_m)} % 64;
            if (kind == 1) begin                         // pass-through write to IP 4
                v = $random(seed_m);
                axi_write(32'h0704_0000 | (idx << 2), v, r);
                if (r == 2'b00) shadow[idx] = v;
                else if (r != 2'b10) pt_errors = pt_errors + 1;
            end else if (kind == 2) begin                // pass-through read from IP 4
                axi_read(32'h0904_0000 | (idx << 2), v, r);
                if (r != 2'b00 || v != shadow[idx]) pt_errors = pt_errors + 1;
            end else if (kind == 3) begin                // D-mem window read
                axi_read(SELF | 16'h0400 | (idx << 2), v, r);
                if (r != 2'b00) pt_errors = pt_errors + 1;
            end
            csr_r(16'h000C, status);
            busy = status[0];
        end
        tick(5);

        for (i = 0; i < 64; i = i + 1)
            if (slave.ram[4][i] != shadow[i]) pt_errors = pt_errors + 1;

        csr_r(16'h000C, v); $fdisplay(fout, "status %h", v);
        csr_r(16'h0014, v); $fdisplay(fout, "err_code %h", v);
        csr_r(16'h0018, v); $fdisplay(fout, "err_pc %h", v);
        csr_r(16'h001C, v); $fdisplay(fout, "err_addr %h", v);
        csr_r(16'h0040, v); $fdisplay(fout, "irq_stat %h", v);
        csr_r(16'h0038, v); $fdisplay(fout, "snap_cnt %h", v);
        for (i = 0; i < 16; i = i + 1) begin
            csr_r(16'h0100 + 4 * i, v); $fdisplay(fout, "reg %0d %h", i, v);
        end
        for (i = 0; i < (1 << TB_DMEM_AW); i = i + 1) begin
            $fdisplay(fout, "d0 %0d %h", i, dut.dmem[i]);
            $fdisplay(fout, "d1 %0d %h", i, dut.dmem[(1 << TB_DMEM_AW) + i]);
        end
        for (i = 0; i < 64; i = i + 1) begin
            $fdisplay(fout, "ram 1 %0d %h", i, slave.ram[1][i]);
            $fdisplay(fout, "ram 2 %0d %h", i, slave.ram[2][i]);
            $fdisplay(fout, "ram 3 %0d %h", i, slave.ram[3][i]);
        end
        for (i = 0; i < 8; i = i + 1) $fdisplay(fout, "ev %0d %0d", i, ev_count[i]);
        $fdisplay(fout, "pt_errors %0d", pt_errors);
        $fdisplay(fout, "proto_errors %0d", proto_errors);
        $fdisplay(fout, "cycles %0d", cyc);
        $fclose(fout);
        $finish;
    end

    integer ev_count [0:7];
    integer e;
    initial for (e = 0; e < 8; e = e + 1) ev_count[e] = 0;
    always @(posedge clk)
        for (e = 0; e < 8; e = e + 1)
            if (ev_out[e]) ev_count[e] = ev_count[e] + 1;
endmodule
`default_nettype wire
