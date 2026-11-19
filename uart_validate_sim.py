"""
Offline model for uart_validate.py --simulate.

SimBench couples a hexbus bridge model with an axiluart_phy model and a
pyserial-like PC adapter on the same UART line. Characters are modelled at
frame level: parity and stop bits are compared between the PC format and the
FPGA SETUP word, so parity / frame errors, break and overflow behave like the
RTL (per-byte BYTE_PERR / BYTE_FERR, sticky PERR / FERR / OVFL).
"""

import random
import threading
import time

from hexbus import SimulatedHexbus

FMT_PARITY = {0b000: "N", 0b001: "N", 0b010: "N", 0b011: "N",
              0b100: "O", 0b101: "E", 0b110: "S", 0b111: "M"}


def _parity_bit(kind, value, mask):
    ones = bin(value & mask).count("1") & 1
    return {"E": ones, "O": ones ^ 1, "M": 1, "S": 0}.get(kind)


class SimUartPhy:
    def __init__(self, clk_hz, lglen=4, caps=0x07):
        self.clk_hz, self.depth, self.caps = clk_hz, 1 << lglen, caps
        self.lglen = lglen
        self.setup = 0x400001B2
        self.ctrl, self.timing = 0xC2, 0x00FA0032
        self.cfg_err = False
        self.rx = []                       # words: data | per-byte flags
        self.perr = self.ferr = self.ovfl = False
        self.break_active = False
        self.tx_hold = []                  # bytes waiting while TX_EN = 0
        self.driving_until = 0.0
        self.line = None
        self.two_wire = False
        self.loopback = False
        self.de_delay_us = 0.0      # transceiver driver-enable delay (0 = ideal)

    # -- line format helpers
    @property
    def baud(self):
        return self.clk_hz / max(1, self.setup & 0xFFFFFF)

    @property
    def data_bits(self):
        return 8 - ((self.setup >> 28) & 3)

    @property
    def parity(self):
        return FMT_PARITY[(self.setup >> 24) & 7]

    @property
    def stop(self):
        return 2 if self.setup & (1 << 27) else 1

    @property
    def frame_s(self):
        return (1 + self.data_bits + (self.parity != "N") + self.stop) / self.baud

    # -- PC -> FPGA
    def rx_char(self, value, pc):
        if not self.ctrl & 0x80 or self.break_active:
            return
        mask = (1 << self.data_bits) - 1
        if abs(pc["baud"] - self.baud) / self.baud > 0.03 or pc["data"] != self.data_bits:
            word = random.randrange(256) & mask | (1 << 14)
            self.ferr = True
            self._push(word)
            return
        if self.ctrl & 0x20:               # RX_INV: garbage
            self._push((~value & mask) | (1 << 14))
            self.ferr = True
            return
        tail = ([_parity_bit(pc["parity"], value, mask)] if pc["parity"] != "N" else [])
        tail += [1] * pc["stop"] + [1, 1]
        i, perr = 0, False
        if self.parity != "N":
            perr = tail[0] != _parity_bit(self.parity, value, mask)
            i = 1
        ferr = tail[i] == 0
        self.perr |= perr
        self.ferr |= ferr
        self._push((value & mask) | (perr << 13) | (ferr << 14))

    def _push(self, word):
        if len(self.rx) >= self.depth:
            self.ovfl = True
        else:
            self.rx.append(word)

    def set_break(self, active):
        self.break_active = active
        if active:
            self.rx.clear()
            self.perr = self.ferr = False

    # -- FPGA -> PC
    def tx_char(self, value):
        if not self.ctrl & 0x40:
            self.tx_hold.append(value)
            return
        self.driving_until = max(self.driving_until, time.monotonic()) + self.frame_s
        if self.line is not None and not self.ctrl & 0x10:
            # A driver that turns on later than LEAD eats the start of the start
            # bit; past half a bit the PC samples one bit late (0x00 -> 0x80).
            lead_us = (self.timing & 0xFFFF) / self.clk_hz * 1e6
            late = (self.ctrl & 3) == 2 and self.de_delay_us - lead_us > 0.5e6 / self.baud
            if late:
                value = (value >> 1) | (1 << (self.data_bits - 1))
            self.line.from_fpga(value, self)
        half, echo = self.ctrl & 0x04, self.ctrl & 0x08
        if (self.two_wire or self.loopback) and (not half or echo):
            own = {"baud": self.baud, "data": self.data_bits, "parity": self.parity, "stop": self.stop}
            self.rx_char(value, own)

    # -- registers
    def read(self, off):
        if off == 0x00:
            return self.setup
        if off == 0x04:
            n = len(self.rx)
            return ((self.lglen << 28) | (self.depth << 18) | (1 << 16) | (self.lglen << 12)
                    | (n << 2) | ((n >= self.depth // 2) << 1) | (n > 0))
        if off == 0x08:
            sticky = (self.perr << 9) | (self.ferr << 10) | (self.break_active << 11) | (self.ovfl << 12)
            if not self.rx:
                return 0x100 | sticky
            return self.rx.pop(0) | sticky
        if off == 0x0C:
            return 0
        if off == 0x10:
            return self.ctrl
        if off == 0x14:
            return self.timing
        if off == 0x18:
            driving = bool(time.monotonic() < self.driving_until and self.ctrl & 0x40)
            mode = self.ctrl & 3
            de = bool(mode == 1 or (mode == 2 and driving))
            return (self.caps | (bool(driving) << 8) | (de << 9) | (self.cfg_err << 12))
        return 0

    def write(self, off, value):
        if off == 0x00:
            self.setup = (value & 0x3FFFFFFF) | (0 if self.caps & 16 else 0x40000000)
            self.rx.clear()
            self.perr = self.ferr = self.ovfl = False
        elif off == 0x08:
            if value & (1 << 9):
                self.perr = False
            if value & (1 << 10):
                self.ferr = False
            if value & (1 << 12):
                self.rx.clear()
                self.ovfl = False
        elif off == 0x0C:
            self.tx_char(value & 0xFF)
        elif off == 0x10:
            new = value & 0xFF
            de = new & 3
            valid = de != 3 and (self.caps & 4 or de == 0) and (not new & 8 or new & 4)
            if not valid:
                self.cfg_err = True
                return
            self.ctrl = new
            if self.ctrl & 0x40 and self.tx_hold:
                held, self.tx_hold = self.tx_hold, []
                for b in held:
                    self.tx_char(b)
        elif off == 0x14:
            self.timing = value
        elif off == 0x18:
            if value & (1 << 12):
                self.cfg_err = False


class SimHexbus(SimulatedHexbus):
    def __init__(self, base, uart):
        super().__init__()
        self.uart_base, self.uart = base, uart

    def read(self, n=1):
        # ~1 ms per access, close to a real 115200-baud hexbus link
        time.sleep(0.0005)
        with self._lock:
            chunk, self._out = bytes(self._out[:n]), self._out[n:]
        return chunk

    def _bus_read(self, addr):
        off = addr - self.uart_base
        if 0 <= off < 0x20:
            return self.uart.read(off & 0x1C)
        return super()._bus_read(addr)

    def _bus_write(self, addr, value):
        off = addr - self.uart_base
        if 0 <= off < 0x20:
            self.uart.write(off & 0x1C, value)
            return
        super()._bus_write(addr, value)


class SimLinePort:
    """pyserial-like PC adapter on the simulated UART line."""

    def __init__(self, uart):
        self.uart = uart
        self.baudrate, self.bytesize, self.parity, self.stopbits = 9600, 8, "N", 1
        self.timeout = 0.05
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._break = False

    @property
    def fmt(self):
        return {"baud": self.baudrate, "data": int(self.bytesize),
                "parity": str(self.parity), "stop": int(self.stopbits)}

    def from_fpga(self, value, uart):
        mine = self.fmt
        same_baud = abs(mine["baud"] - uart.baud) / uart.baud < 0.03
        mask = (1 << uart.data_bits) - 1
        if same_baud and mine["data"] == uart.data_bits:
            out = value & mask
        elif (same_baud and mine["data"] == uart.data_bits + 1 and mine["parity"] == "N"
              and uart.parity != "N"):
            # PC reads the FPGA parity bit as its top data bit
            out = (value & mask) | (_parity_bit(uart.parity, value, mask) << uart.data_bits)
        else:
            out = random.randrange(256)
        with self._lock:
            self._buf.append(out)

    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, active):
        self._break = active
        self.uart.set_break(active)

    def write(self, data):
        for b in bytes(data):
            self.uart.rx_char(b, self.fmt)
        return len(data)

    def flush(self):
        pass

    def read(self, n):
        deadline = time.monotonic() + (self.timeout or 0)
        while True:
            with self._lock:
                if self._buf or time.monotonic() >= deadline:
                    chunk, self._buf = bytes(self._buf[:n]), self._buf[n:]
                    return chunk
            time.sleep(0.002)

    def reset_input_buffer(self):
        with self._lock:
            self._buf.clear()

    def close(self):
        pass


class SimBench:
    def __init__(self, base, clk_hz, two_wire=False, loopback=False, caps=0x07, lglen=4,
                 de_delay_us=0.0):
        self.uart = SimUartPhy(clk_hz, lglen=lglen, caps=caps)
        self.uart.de_delay_us = de_delay_us
        self.uart.two_wire = two_wire
        self.uart.loopback = loopback
        self.hexbus = SimHexbus(base, self.uart)
        self.line = SimLinePort(self.uart)
        self.uart.line = self.line
