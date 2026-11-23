"""Instruction-level reference model of cmd_seq (core + D-mem banks + IRQ_STAT).

The model is written from the specification (README), not from the RTL, and is
used to check the RTL with random programs: after a program ends, R0-R15, the
flags, PC, the error registers, both D-mem banks, IRQ_STAT, SNAP_CNT, the event
counts and the list of downstream transfers must be equal.

Timing is not modelled (WAIT / DLY only cost time). TMR, WAITT and WFE depend on
time or external signals and are not used by the random generator.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sw"))
import seqasm as A  # noqa: E402

M32 = 0xFFFFFFFF


class Bus:
    """Downstream slaves used by the random test (the testbench has the same map).

    IP select = addr[23:16]; addr[31:24] is ignored; word = addr[7:2].
      1, 2, 3 : RAM, 64 words, initial value init_word(ip, i)
      4       : RAM used only by the pass-through traffic of the testbench
      0x0E    : SLVERR, read data 0xBAD0_0000 | addr[15:0]
      other   : DECERR, read data 0xDEC0_0000 | addr[15:0]
    """

    def __init__(self):
        self.ram = {ip: [init_word(ip, i) for i in range(64)] for ip in (1, 2, 3, 4)}
        self.log = []

    @staticmethod
    def _ip(addr):
        return (addr >> 16) & 0xFF

    def write(self, addr, data):
        ip = self._ip(addr)
        if ip in self.ram:
            self.ram[ip][(addr >> 2) & 63] = data
            resp = 0
        else:
            resp = 2 if ip == 0x0E else 3
        self.log.append(("W", addr, data, resp))
        return resp

    def read(self, addr):
        ip = self._ip(addr)
        if ip in self.ram:
            data, resp = self.ram[ip][(addr >> 2) & 63], 0
        elif ip == 0x0E:
            data, resp = 0xBAD00000 | (addr & 0xFFFF), 2
        else:
            data, resp = 0xDEC00000 | (addr & 0xFFFF), 3
        self.log.append(("R", addr, data, resp))
        return data, resp


def init_word(ip, i):
    return ((0xA5000000 | (ip << 16) | (i << 8) | (i ^ ip)) * 0x9E3779B1) & M32


class Seq:
    def __init__(self, imem_aw=9, dmem_aw=8, self_id=0, sel_lsb=16, bus=None):
        self.imem_aw, self.dmem_aw = imem_aw, dmem_aw
        self.self_id, self.sel_lsb = self_id, sel_lsb
        self.imem = [0] * (1 << imem_aw)
        self.dmem = [[0] * (1 << dmem_aw), [0] * (1 << dmem_aw)]
        self.bus = bus or Bus()
        self.R = [0] * 16
        self.pc = 0
        self.z = self.n = self.c = self.v = self.ferr = 0
        self.stack = []
        self.err_code = self.err_pc = self.err_addr = 0
        self.irq_stat = 0
        self.snap_cnt = 0
        self.pub_bank = 0
        self.snap_en = 0
        self.ev_count = [0] * 8
        self.state = "IDLE"

    # ---- loading ------------------------------------------------------------
    def load(self, prog):
        for pc, w, _ in prog:
            self.imem[pc] = w

    def dmem_write_master(self, idx, value):
        """DMEM_DATA / window write from the AXI side (published bank)."""
        self.dmem[self.pub_bank if self.snap_en else 0][idx] = value & M32

    # ---- helpers --------------------------------------------------------------
    def _setr(self, r, v):
        if r:
            self.R[r] = v & M32

    def _work_bank(self):
        return (1 - self.pub_bank) if self.snap_en else 0

    def _fail(self, code):
        self.err_code, self.err_pc = code, self.pc
        self.irq_stat |= 2
        self.state = "ERROR"

    def _advance(self):
        if self.pc == (1 << self.imem_aw) - 1:
            self._fail(A.E_PC)
            return False
        self.pc += 1
        return True

    def _cond(self, code):
        lt = self.n ^ self.v
        table = {0: 1, 1: self.z, 2: 1 - self.z, 3: lt, 4: 1 - lt, 5: self.ferr,
                 6: 1 - self.c, 7: self.c, 8: self.n, 9: 1 - self.n}
        return table.get(code)

    # ---- execution ----------------------------------------------------------------
    def run(self, entry=0, err_halt=1, snap_en=0, max_steps=100000):
        self.snap_en = snap_en
        self.pc, self.stack, self.ferr = entry, [], 0
        self.state = "RUN"
        for _ in range(max_steps):
            self.step(err_halt)
            if self.state != "RUN":
                return self.state
        raise RuntimeError("program did not end")

    def step(self, err_halt):
        w = self.imem[self.pc]
        f = A.fields(w)
        op, ra, rb, mod, d, imm = f["op"], f["ra"], f["rb"], f["mod"], f["d"], f["imm"]
        a = self.R[ra]
        b = imm if mod & 1 else self.R[rb]
        last = (1 << self.imem_aw) - 1
        d_ok = d < (1 << self.dmem_aw)
        tgt_ok = imm < (1 << self.imem_aw)
        name = A.OPNAME.get(op)

        if name in ("NOP", "FENCE"):
            self._advance()
        elif name == "END":
            self.irq_stat |= 1
            self.state = "DONE"
        elif name == "KILL":
            self._fail(A.E_ILLEGAL if d < A.KILL_MIN else d)
        elif name in ("WR", "RD"):
            addr = (self.R[rb] + imm) & M32
            if name == "RD" and mod & 2 and not d_ok:
                self._fail(A.E_ILLEGAL)
            elif ((addr >> self.sel_lsb) & 0xFF) == self.self_id:
                self.err_addr = addr
                self._fail(A.E_SELF)
            else:
                if name == "WR":
                    resp, data = self.bus.write(addr, a), None
                else:
                    data, resp = self.bus.read(addr)
                if resp:
                    self.ferr, self.err_addr = 1, addr
                if resp and err_halt:
                    self._fail(A.E_BUS)
                else:
                    if name == "RD":
                        if mod & 2:
                            self.dmem[self._work_bank()][d] = data
                        else:
                            self._setr(ra, data)
                    self._advance()
        elif name == "LDI":
            self._setr(ra, imm)
            self._advance()
        elif name == "MOV":
            self._setr(ra, b)
            self._advance()
        elif name == "LDD":
            if not d_ok:
                self._fail(A.E_ILLEGAL)
            else:
                self._setr(ra, self.dmem[self._work_bank()][d])
                self._advance()
        elif name == "STD":
            if not d_ok:
                self._fail(A.E_ILLEGAL)
            else:
                self.dmem[self._work_bank()][d] = a
                self._advance()
        elif name in A.ALU:
            if name == "ADD":
                full = a + b
                res, c = full & M32, full >> 32
                v = int((a >> 31) == (b >> 31) and (res >> 31) != (a >> 31))
            elif name in ("SUB", "CMP"):
                res = (a - b) & M32
                c = int(a >= b)
                v = int((a >> 31) != (b >> 31) and (res >> 31) != (a >> 31))
            else:
                c = v = 0
                sh = b & 31
                res = {"AND": a & b, "OR": a | b, "XOR": a ^ b,
                       "SHL": (a << sh) & M32, "SHR": a >> sh}[name]
            self.z, self.n, self.c, self.v = int(res == 0), res >> 31, c, v
            if name != "CMP":
                self._setr(ra, res)
            self._advance()
        elif name == "JMP":
            if not tgt_ok:
                self._fail(A.E_PC)
            else:
                self.pc = imm
        elif name == "BR":
            take = self._cond(d)
            if take is None:
                self._fail(A.E_ILLEGAL)
            elif not tgt_ok:
                self._fail(A.E_PC)
            elif take:
                self.pc = imm
            else:
                self._advance()
        elif name == "LOOP":
            if ra == 0:
                self._fail(A.E_ILLEGAL)
            elif not tgt_ok:
                self._fail(A.E_PC)
            else:
                self.R[ra] = (a - 1) & M32
                if a != 1:
                    self.pc = imm
                else:
                    self._advance()
        elif name == "CALL":
            if len(self.stack) == 4:
                self._fail(A.E_STACK)
            elif not tgt_ok or self.pc == last:
                self._fail(A.E_PC)
            else:
                self.stack.append(self.pc + 1)
                self.pc = imm
        elif name == "RET":
            if not self.stack:
                self._fail(A.E_STACK)
            else:
                self.pc = self.stack.pop()
        elif name in ("WAIT", "WAITT", "WFE"):
            if name == "WFE" and d > 7:
                self._fail(A.E_ILLEGAL)
            elif self.pc == last:
                self._fail(A.E_PC)
            else:
                self.pc += 1
        elif name == "TMR":
            raise RuntimeError("TMR depends on time and is not modelled")
        elif name == "SEV":
            if d > 7:
                self._fail(A.E_ILLEGAL)
            else:
                self.ev_count[d] += 1
                self._advance()
        elif name == "SNAP":
            self.snap_cnt += 1
            if self.snap_en:
                self.pub_bank ^= 1
            self._advance()
        elif name == "IRQ":
            self.irq_stat |= 4
            self._advance()
        else:
            self._fail(A.E_ILLEGAL)

    # ---- result in the testbench dump format ---------------------------------------
    def result(self):
        flags = self.z | self.n << 1 | self.ferr << 2 | self.v << 3 | self.c << 4
        out = {
            "state": self.state, "pc": self.pc, "flags": flags,
            "err_code": self.err_code, "err_pc": self.err_pc, "err_addr": self.err_addr,
            "irq_stat": self.irq_stat, "snap_cnt": self.snap_cnt,
            "regs": list(self.R), "dmem0": list(self.dmem[0]), "dmem1": list(self.dmem[1]),
            "ev": list(self.ev_count),
            "ram": {ip: list(self.bus.ram[ip]) for ip in (1, 2, 3)},
            "log": list(self.bus.log),
        }
        if self.state == "DONE":   # ERR_CODE keeps its reset value on a normal end
            out["err_pc"] = out["err_pc"]
        return out
