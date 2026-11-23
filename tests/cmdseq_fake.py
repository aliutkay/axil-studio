"""A cmd_seq block on a fake Debug Bridge link (tests): the own registers at `base` with the
behaviour of hdl/cmd_seq (loader, CTRL pulses, STATUS, D-mem window), the program executed
by the reference model (hdl/cmd_seq/verif/seqmodel.py). Other addresses are plain memory."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hdl", "cmd_seq", "verif"))
import seqmodel as M  # noqa: E402

MAX_RUN = 100000


class FakeSeqLink:
    def __init__(self, base, imem_aw=9, dmem_aw=8):
        self.base, self.imem_aw, self.dmem_aw = base, imem_aw, dmem_aw
        self.mem = {}
        self.ops = []
        self.quiet = False
        self.connected = True
        self.m = M.Seq(imem_aw, dmem_aw)
        self.state = "IDLE"               # IDLE RUN PAUSE DONE ERROR
        self.bits = 0x40
        self.entry = 0
        self.ptr, self.phase, self.lo, self.cnt, self.sum = 0, 0, 0, 0, 0
        self.dptr = 0
        self.irq = 0
        self.timer = 0

    def close(self):
        self.connected = False

    # ---- core control ---------------------------------------------------------------
    def _start(self):
        self.m.pc, self.m.stack, self.m.ferr = self.entry, [], 0
        self.m.snap_en = int(bool(self.bits & 0x10))

    def _step(self):
        self.m.state = "RUN"
        self.m.step(int(bool(self.bits & 0x40)))
        if self.m.state == "DONE":
            self.state = "DONE"
        elif self.m.state == "ERROR":
            self.state = "ERROR"
        return self.m.state == "RUN"

    def _ctrl(self, v):
        self.bits = v & 0xF0
        self.m.snap_en = int(bool(self.bits & 0x10))
        if v & 0x04:                                  # RESET
            self.state = "IDLE"
            self.m.R = [0] * 16
            return
        if v & 0x02 and self.state == "RUN":          # STOP
            self.state = "PAUSE"
        if v & 0x01 and self.state != "RUN":          # RUN
            if self.state in ("IDLE", "DONE", "ERROR"):
                self._start()
            self.state = "RUN"
            for _ in range(MAX_RUN):
                if not self._step():
                    break
        elif v & 0x08 and self.state != "RUN":        # STEP
            if self.state in ("IDLE", "DONE", "ERROR"):
                self._start()
            self.state = "PAUSE" if self._step() else self.state
        old = self.m.irq_stat
        self.irq |= old
        self.m.irq_stat = 0

    def _status(self):
        st = {"RUN": 1, "DONE": 2, "ERROR": 4, "PAUSE": 8}.get(self.state, 0)
        st |= self.phase << 4
        m = self.m
        st |= (m.z << 8) | (m.n << 9) | (m.ferr << 10) | (m.v << 11) | (m.c << 12)
        return st | (m.pc << 16)

    def _bank(self):
        return self.m.pub_bank if self.bits & 0x10 else 0

    # ---- bus ----------------------------------------------------------------------------
    def _own(self, addr):
        return ((addr >> 16) & 0xFF) == ((self.base >> 16) & 0xFF) and (addr & ~0xFFFFFF) == (self.base & ~0xFFFFFF)

    def read(self, addr):
        self.ops.append(("r", addr))
        if not self._own(addr):
            return self.mem.get(addr, 0)
        o = addr & 0xFFFC
        self.timer += 7
        m = self.m
        regs = {0x00: 0x5EC00200, 0x04: (16 << 16) | (self.dmem_aw << 8) | self.imem_aw,
                0x08: self.bits, 0x0C: self._status(), 0x10: self.entry, 0x14: m.err_code,
                0x18: m.err_pc, 0x1C: m.err_addr, 0x20: self.ptr, 0x28: self.cnt, 0x2C: self.sum,
                0x30: self.dptr, 0x38: m.snap_cnt, 0x3C: self.timer, 0x40: self.irq}
        if o in regs:
            return regs[o] & 0xFFFFFFFF
        if 0x100 <= o < 0x140:
            return m.R[(o - 0x100) // 4]
        if 0x400 <= o < 0x400 + 4 * (1 << self.dmem_aw):
            return m.dmem[self._bank()][(o - 0x400) // 4]
        raise IOError("DECERR")

    def write(self, addr, value):
        self.ops.append(("w", addr, value))
        if not self._own(addr):
            self.mem[addr] = value
            return
        o = addr & 0xFFFC
        busy = self.state in ("RUN", "PAUSE")
        if o == 0x08:
            self._ctrl(value)
        elif o == 0x10:
            self.entry = value & ((1 << self.imem_aw) - 1)
        elif o == 0x20:
            self.ptr, self.phase, self.cnt, self.sum = value & ((1 << self.imem_aw) - 1), 0, 0, 0
        elif o == 0x24:
            if busy:
                raise IOError("SLVERR: loading while running")
            self.sum = (self.sum + value) & 0xFFFFFFFF
            if not self.phase:
                self.lo, self.phase = value, 1
            else:
                self.m.imem[self.ptr] = (value << 32) | self.lo
                self.ptr, self.cnt, self.phase = self.ptr + 1, self.cnt + 1, 0
        elif o == 0x30:
            self.dptr = value
        elif o == 0x34:
            self.m.dmem[self._bank()][self.dptr] = value
            self.dptr += 1
        elif o == 0x40:
            self.irq &= ~value
        elif 0x400 <= o < 0x400 + 4 * (1 << self.dmem_aw):
            self.m.dmem[self._bank()][(o - 0x400) // 4] = value
        else:
            raise IOError("SLVERR")
