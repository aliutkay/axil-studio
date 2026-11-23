#!/usr/bin/env python3
"""Random co-simulation of cmd_seq against the reference model (seqmodel.py).

Each test generates a random terminating program (straight code, forward branches,
counted loops, calls, bus reads / writes to good and failing slaves, D-mem, SNAP,
events, injected errors), assembles it with seqasm, runs it on the RTL (Icarus
Verilog, random slave delays, optional pass-through traffic on the upstream port)
and on the model, and compares the complete final state and the list of
downstream transfers.

  python3 random_test.py -n 200 --seed 1
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, HERE)
import seqasm as A  # noqa: E402
import seqmodel as M  # noqa: E402

EDGE = [0, 1, 2, 0x7FFFFFFF, 0x80000000, 0x80000001, 0xFFFFFFFF, 0xFFFFFFFE, 31, 32]


class Gen:
    """Random program generator. Registers: R1-R7 data, R8-R10 bases of IP 1-3,
    R11 base of the SLVERR slave, R12-R15 loop counters."""

    def __init__(self, rng, err_halt):
        self.r = rng
        self.err_halt = err_halt
        self.n = 0
        self.lines = []
        self.subs = []
        self.inject = False

    def label(self):
        self.n += 1
        return f"L{self.n}"

    def imm(self):
        r = self.r
        k = r.random()
        if k < 0.3:
            return r.choice(EDGE)
        if k < 0.6:
            return r.randrange(0, 64)
        return r.getrandbits(32)

    def src(self):
        return f"R{self.r.randrange(0, 16)}"

    def dst(self):
        return f"R{self.r.randrange(1, 8)}"

    def opb(self):
        return self.src() if self.r.random() < 0.5 else f"0x{self.imm():X}"

    def addr(self, allow_err):
        r = self.r
        off = 4 * r.randrange(0, 64)
        k = r.random()
        if allow_err and k < 0.08:
            return f"[R11+0x{off:X}]"                      # SLVERR slave
        if allow_err and k < 0.12:
            return f"[0x{r.randrange(0, 256):02X}0F_{off:04X}]"   # DECERR, any block bits
        if k < 0.6:
            return f"[R{r.choice((8, 9, 10))}+0x{off:X}]"
        return f"[0x{r.randrange(0, 256):02X}0{r.randrange(1, 4)}_{off:04X}]"

    def dref(self):
        """D[n], or indexed D[n+Rx]: mostly by a loop counter (small), sometimes by any register
        (usually out of range: error 7)."""
        r = self.r
        k = r.random()
        if k < 0.6:
            return f"D[{r.randrange(0, 16)}]"
        if k < 0.95:
            return f"D[{r.randrange(0, 12)}+R{r.choice((12, 13, 14))}]"
        return f"D[R{r.randrange(0, 16)}]"

    def dly(self, s):
        return s + (f" @{self.r.randrange(1, 4)}" if self.r.random() < 0.1 else "")

    def simple(self):
        r = self.r
        allow_err = not self.err_halt or r.random() < 0.1
        k = r.randrange(0, 100)
        if k < 12:
            return f"LDI {self.dst()}, 0x{self.imm():X}"
        if k < 18:
            return f"MOV {self.dst()}, {self.opb()}"
        if k < 42:
            return f"{r.choice(sorted(A.ALU - {'CMP'}))} {self.dst()}, {self.opb()}"
        if k < 50:
            return f"CMP {self.src()}, {self.opb()}"
        if k < 55:
            return f"LDD {self.dst()}, {self.dref()}"
        if k < 60:
            return f"STD {self.dref()}, {self.src()}"
        if k < 70:
            return f"RD {self.dst()}, {self.addr(allow_err)}"
        if k < 74:
            return f"RD {self.dref()}, {self.addr(allow_err)}"
        if k < 84:
            return f"WR {self.addr(allow_err)}, {self.src()}"
        if k < 86:
            return "SNAP"
        if k < 88:
            return f"SEV {r.randrange(0, 8)}"
        if k < 89:
            return "IRQ"
        if k < 91:
            return "FENCE"
        if k < 93:
            return f"WAIT {r.randrange(0, 6)}"
        return "NOP"

    def body(self, depth, size):
        out = []
        pending = []            # forward labels still to be placed
        for _ in range(size):
            r = self.r
            k = r.random()
            if pending and r.random() < 0.3:
                out.append(pending.pop() + ":")
            if depth == 0 and self.inject and r.random() < 0.1:
                out += self.error()
                self.inject = False
                continue
            if k < 0.08 and depth < 3:
                cnt = f"R{12 + depth}"
                lab = self.label()
                out.append(f"LDI {cnt}, {r.randrange(1, 4)}")
                out.append(lab + ":")
                out += self.body(depth + 1, r.randrange(1, 6))
                out.append(f"LOOP {cnt}, {lab}")
            elif k < 0.16:
                lab = self.label()
                cond = r.choice(sorted(A.CONDS) + ["EQ", "NE"])
                out.append(f"BR {cond}, {lab}")
                pending.append(lab)
            elif k < 0.28:                    # flag probe: the branch result is visible in R7
                lab = self.label()
                x = self.dst()
                out.append(f"LDI {x}, 0x{r.choice(EDGE + [self.imm()]):X}")
                out.append(f"{r.choice(('CMP', 'SUB', 'ADD', 'CMP'))} {x}, 0x{r.choice(EDGE + [self.imm()]):X}")
                cond = r.choice(sorted(A.CONDS))
                out.append(f"BR {cond}, {lab}")
                out.append(f"XOR R7, 0x{r.getrandbits(32) | 1:X}")
                out.append(lab + ":")
            elif k < 0.32 and self.subs:
                out.append(f"CALL {r.choice(self.subs)}")
            else:
                out.append(self.dly(self.simple()))
        for lab in pending:
            out.append(lab + ":")
        return out

    def error(self):
        """One injected error (the program may continue when it is a bus error and
        ERR_HALT = 0)."""
        r = self.r
        k = r.randrange(0, 8)
        if k == 0:
            return [f"KILL 0x{r.randrange(0x10, 0x100):02X}"]
        if k == 1:
            return [f".word64 0x{A.encode(r.choice((3, 4, 5, 0x0A, 0x25, 0x3F))):016X}"]
        if k == 2:
            return [f".word64 0x{A.encode(0x21, d=r.randrange(10, 256), imm=0):016X}"]  # bad cond
        if k == 3:
            return [f".word64 0x{A.encode(0x02, d=r.randrange(0, 0x10)):016X}"]        # KILL < 0x10
        if k == 4:
            return [f"WR [0x{r.randrange(0, 256):02X}00_00{4 * r.randrange(0, 16):02X}], R1"]  # self
        if k == 5:
            return ["RET"]                                                            # underflow
        if k == 6:
            return ["CALL deep1"]                                                     # overflow
        lab = self.label()
        return [f"RD R1, [R11+0x{4 * r.randrange(0, 64):X}]", f"BR ERR, {lab}", "LDI R1, 0x111",
                f"{lab}:"]

    def program(self):
        r = self.r
        self.subs = [f"sub{i}" for i in range(r.randrange(0, 3))]
        lines = ["LDI R8, 0x0001_0000", "LDI R9, 0x0502_0000", "LDI R10, 0xFF03_0000",
                 "LDI R11, 0x000E_0000"]
        self.inject = r.random() < 0.35
        main = self.body(0, r.randrange(5, 60))
        if self.inject:                     # not placed yet: at the end
            main += self.error()
        lines += main + ["END"]
        for s in self.subs:
            lines += [f"{s}:"] + [self.dly(self.simple()) for _ in range(r.randrange(1, 6))] + ["RET"]
        lines += ["deep1: CALL deep2", "deep2: CALL deep3", "deep3: CALL deep4", "deep4: CALL deep5",
                  "deep5: RET"]
        return "\n".join(lines) + "\n"


def parse_dump(text):
    out = {"regs": [0] * 16, "dmem0": {}, "dmem1": {}, "ram": {1: {}, 2: {}, 3: {}},
           "ev": [0] * 8, "log": [], "fail": []}
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "log":
            out["log"].append((p[1], int(p[2], 16), int(p[3], 16), int(p[4])))
        elif p[0] == "reg":
            out["regs"][int(p[1])] = int(p[2], 16)
        elif p[0] in ("d0", "d1"):
            out["dmem" + p[0][1]][int(p[1])] = int(p[2], 16)
        elif p[0] == "ram":
            out["ram"][int(p[1])][int(p[2])] = int(p[3], 16)
        elif p[0] == "ev":
            out["ev"][int(p[1])] = int(p[2])
        elif p[0] == "fail":
            out["fail"].append(line)
        elif p[0] in ("pt_errors", "proto_errors", "cycles"):
            out[p[0]] = int(p[1])
        else:
            out[p[0]] = int(p[1], 16)
    return out


def compare(rtl, exp, dmem_size):
    diffs = list(rtl["fail"])
    st = rtl["status"]
    state = "DONE" if st & 2 else "ERROR" if st & 4 else f"status {st:08X}"
    if state != exp["state"]:
        diffs.append(f"state rtl={state} model={exp['state']}")
    checks = [("pc", st >> 16, exp["pc"]), ("flags", (st >> 8) & 0x1F, exp["flags"]),
              ("err_code", rtl["err_code"], exp["err_code"]),
              ("irq_stat", rtl["irq_stat"], exp["irq_stat"]),
              ("snap_cnt", rtl["snap_cnt"], exp["snap_cnt"]),
              ("err_addr", rtl["err_addr"], exp["err_addr"])]
    if exp["state"] == "ERROR":
        checks.append(("err_pc", rtl["err_pc"], exp["err_pc"]))
    for name, a, b in checks:
        if a != b:
            diffs.append(f"{name} rtl=0x{a:X} model=0x{b:X}")
    for i in range(16):
        if rtl["regs"][i] != exp["regs"][i]:
            diffs.append(f"R{i} rtl=0x{rtl['regs'][i]:08X} model=0x{exp['regs'][i]:08X}")
    for bank in ("dmem0", "dmem1"):
        for i in range(dmem_size):
            if rtl[bank].get(i) != exp[bank][i]:
                diffs.append(f"{bank}[{i}] rtl=0x{rtl[bank].get(i, 0):08X} model=0x{exp[bank][i]:08X}")
    for ip in (1, 2, 3):
        for i in range(64):
            if rtl["ram"][ip][i] != exp["ram"][ip][i]:
                diffs.append(f"IP{ip}[{i}] rtl=0x{rtl['ram'][ip][i]:08X} model=0x{exp['ram'][ip][i]:08X}")
    if rtl["ev"] != exp["ev"]:
        diffs.append(f"events rtl={rtl['ev']} model={exp['ev']}")
    if rtl["log"] != exp["log"]:
        n = next((i for i, (a, b) in enumerate(zip(rtl["log"], exp["log"])) if a != b),
                 min(len(rtl["log"]), len(exp["log"])))
        diffs.append(f"bus transfer {n} differs: rtl={rtl['log'][n:n + 2]} model={exp['log'][n:n + 2]} "
                     f"(rtl {len(rtl['log'])}, model {len(exp['log'])} transfers)")
    if rtl.get("pt_errors"):
        diffs.append(f"{rtl['pt_errors']} pass-through errors")
    if rtl.get("proto_errors"):
        diffs.append(f"{rtl['proto_errors']} upstream protocol errors")
    return diffs


def build(workdir, imem_aw, dmem_aw, maxd, seed):
    exe = os.path.join(workdir, f"tb_random_{maxd}_{seed}.vvp")
    cmd = ["iverilog", "-g2012", "-o", exe, "-I", HERE,
           f"-Ptb_random.TB_IMEM_AW={imem_aw}", f"-Ptb_random.TB_DMEM_AW={dmem_aw}",
           f"-Ptb_random.TB_MAXD={maxd}", f"-Ptb_random.TB_SEED={seed}",
           os.path.join(HERE, "tb_random.v"), os.path.join(HERE, "axil_slave_model.v"),
           os.path.join(ROOT, "rtl", "cmd_seq.v"), os.path.join(ROOT, "rtl", "cmd_seq_core.v")]
    subprocess.run(cmd, check=True)
    return exe


def run_one(exe, workdir, rng, test_no, imem_aw=9, dmem_aw=8, keep=False):
    err_halt = rng.random() < 0.6
    snap_en = rng.random() < 0.4
    pt_mode = int(rng.random() < 0.5)
    pt_wr_run = rng.random() < 0.5
    src = Gen(rng, err_halt).program()
    prog, entry = A.assemble(src, imem_aw, dmem_aw)
    dinit = [rng.getrandbits(32) for _ in range(16)]
    ctrl = (A.CTRL_ERR_HALT if err_halt else 0) | (A.CTRL_SNAP_EN if snap_en else 0) | \
           (A.CTRL_PT_WR_RUN if pt_wr_run else 0)

    model = M.Seq(imem_aw, dmem_aw)
    model.snap_en = int(snap_en)
    for i, v in enumerate(dinit):
        model.dmem_write_master(i, v)
    model.load(prog)
    model.run(entry, int(err_halt), int(snap_en))
    exp = model.result()

    fin = os.path.join(workdir, f"t{test_no}.in")
    fout = os.path.join(workdir, f"t{test_no}.out")
    with open(fin, "w") as fh:
        fh.write(f"{len(prog)} {entry} {ctrl:x} {pt_mode} {len(dinit)} 400000\n")
        fh.write(A.hex_text(prog))
        fh.write("".join(f"{v:08x}\n" for v in dinit))
    subprocess.run(["vvp", "-n", exe, f"+in={fin}", f"+out={fout}"], check=True,
                   stdout=subprocess.DEVNULL)
    with open(fout) as fh:
        rtl = parse_dump(fh.read())
    diffs = compare(rtl, exp, 1 << dmem_aw)
    if diffs and keep:
        with open(os.path.join(workdir, f"t{test_no}.s"), "w") as fh:
            fh.write(src)
    info = {"err_halt": err_halt, "snap_en": snap_en, "pt": pt_mode, "state": exp["state"],
            "err_code": exp["err_code"], "n": len(prog), "transfers": len(exp["log"]),
            "cycles": rtl.get("cycles", 0)}
    return diffs, info, src


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=100, help="number of programs")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--maxd", type=int, default=3, help="max random slave delay (0 = always ready)")
    ap.add_argument("--keep", metavar="DIR", help="keep the work files (and failing programs) here")
    a = ap.parse_args(argv)

    workdir = a.keep or tempfile.mkdtemp(prefix="cmdseq_")
    os.makedirs(workdir, exist_ok=True)
    rng = random.Random(a.seed)
    exe = build(workdir, 9, 8, a.maxd, a.seed)
    failed = 0
    stats = {"DONE": 0, "ERROR": 0}
    codes = {}
    transfers = cycles = 0
    for t in range(a.n):
        diffs, info, src = run_one(exe, workdir, rng, t, keep=True)
        stats[info["state"]] += 1
        if info["state"] == "ERROR":
            codes[info["err_code"]] = codes.get(info["err_code"], 0) + 1
        transfers += info["transfers"]
        cycles += info["cycles"]
        if diffs:
            failed += 1
            print(f"test {t} FAILED ({info}):")
            for d in diffs[:12]:
                print("   ", d)
            print(f"    program: {os.path.join(workdir, f't{t}.s')}")
    code_txt = ", ".join(f"{k:#x}:{v}" for k, v in sorted(codes.items()))
    print(f"random: {a.n} programs, {failed} failed; {stats['DONE']} DONE, {stats['ERROR']} ERROR "
          f"(codes {code_txt}); {transfers} downstream transfers, {cycles} cycles")
    if not a.keep and not failed:
        shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
