#!/usr/bin/env python3
"""
uart_validate.py - Hardware validation for axiluart_phy (axiluart_v2 +
uart_phy_adapter) through the hexbus UART-to-AXI4-Lite bridge.

Connections
  --hexbus PORT   debug UART of the hexbus bridge (register access)
  --line PORT     optional PC adapter (USB-RS485 / RS-232) on the UART line
  --loopback      no PC adapter: the UART's TX is wired to its own RX

With --line the PC acts as the far end: data is checked in both directions
and parity, frame, break and overflow errors are injected. With --loopback
data paths are checked through the FPGA itself. Without either, only the
register tests run.

The line interface (PHY_CTRL / PHY_TIMING) is set from --line-type,
--de, --lead-us and --tail-us before the tests, never taken from the board.
All registers are restored when the run ends.

Examples
  python uart_validate.py --hexbus COM5 --line COM7 --line-type rs485-4w --lead-us 10
  python uart_validate.py --config bench.json --bauds 460800,921600
  python uart_validate.py --hexbus COM5 --line COM7 --save-config bench.json
  python uart_validate.py --simulate                 (offline dry run)
"""

import argparse
import json
import random
import re
import sys
import time

from hexbus import HexbusError, HexbusLink

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

# ----------------------------------------------------------------------------
# Register map (axiluart_phy)
# ----------------------------------------------------------------------------
SETUP, FIFO, RXREG, TXREG = 0x00, 0x04, 0x08, 0x0C
PHY_CTRL, PHY_TIMING, PHY_STATUS = 0x10, 0x14, 0x18

RX_EMPTY, RX_PERR, RX_FERR, RX_BREAK, RX_OVFL = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
RX_BYTE_PERR, RX_BYTE_FERR = 1 << 13, 1 << 14
SETUP_NO_FLOW = 1 << 30

CTRL_DE_LOW, CTRL_DE_HIGH, CTRL_DE_TX = 0, 1, 2
CTRL_HALF, CTRL_ECHO, CTRL_TX_INV, CTRL_RX_INV = 1 << 2, 1 << 3, 1 << 4, 1 << 5
CTRL_TX_EN, CTRL_RX_EN, CTRL_APPLY_NOW = 1 << 6, 1 << 7, 1 << 31

ST_HAS_TX, ST_HAS_RX, ST_HAS_DE, ST_HAS_RE, ST_HAS_FLOW = 1, 2, 4, 8, 16
ST_DRIVING, ST_DE, ST_RE_N, ST_PENDING, ST_CFG_ERR = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12


# ----------------------------------------------------------------------------
# Line types: DE mode and duplex used between tests
LINE_TYPES = {
    "ttl":      dict(de="low",  half=False, desc="TTL / LVCMOS UART, no transceiver enable"),
    "rs232":    dict(de="low",  half=False, desc="RS-232, full duplex"),
    "rs422":    dict(de="high", half=False, desc="RS-422 point-to-point, driver always on"),
    "rs485-4w": dict(de="tx",   half=False, desc="RS-485 4-wire, driver on while transmitting"),
    "rs485-2w": dict(de="tx",   half=True,  desc="RS-485 2-wire shared bus, half duplex"),
}
DE_CODES = {"low": CTRL_DE_LOW, "high": CTRL_DE_HIGH, "tx": CTRL_DE_TX}

# Settings that may come from --config (JSON) and are overridden by the command line
CONFIG_KEYS = ("hexbus", "hexbus_baud", "line", "loopback", "base", "clk", "bauds", "formats",
               "count", "quick", "burst_all", "line_type", "de", "lead_us", "tail_us", "report")


# ----------------------------------------------------------------------------
# Line format
# ----------------------------------------------------------------------------
class Fmt:
    """Character format such as 8N1, 7E1, 8M2."""

    def __init__(self, text):
        m = re.fullmatch(r"([5-8])([NEOMS])([12])", text.upper())
        if not m:
            raise ValueError(f"invalid format '{text}' (e.g. 8N1, 7E1, 8O2)")
        self.text = text.upper()
        self.data, self.parity, self.stop = int(m.group(1)), m.group(2), int(m.group(3))

    def __str__(self):
        return self.text

    @property
    def mask(self):
        return (1 << self.data) - 1

    @property
    def bits(self):
        return 1 + self.data + (self.parity != "N") + self.stop

    def setup_bits(self):
        par = {"N": 0, "O": 0b100, "E": 0b101, "S": 0b110, "M": 0b111}[self.parity]
        return ((8 - self.data) << 28) | ((self.stop == 2) << 27) | (par << 24)

    def parity_bit(self, value):
        ones = bin(value & self.mask).count("1") & 1
        return {"E": ones, "O": ones ^ 1, "M": 1, "S": 0}.get(self.parity)

    def with_parity(self, parity):
        return Fmt(f"{self.data}{parity}{self.stop}")

    def serial_kwargs(self):
        return dict(bytesize={5: serial.FIVEBITS, 6: serial.SIXBITS,
                              7: serial.SEVENBITS, 8: serial.EIGHTBITS}[self.data],
                    parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                            "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
                            "S": serial.PARITY_SPACE}[self.parity],
                    stopbits=serial.STOPBITS_ONE if self.stop == 1 else serial.STOPBITS_TWO)


# ----------------------------------------------------------------------------
# Device under test
# ----------------------------------------------------------------------------
class Dut:
    def __init__(self, link, base, clk_hz):
        self.link, self.base, self.clk_hz = link, base, clk_hz
        self.baud, self.fmt = None, None
        self.access_s = 0.002          # measured hexbus round trip, see measure_access()

    def measure_access(self, n=30):
        t0 = time.monotonic()
        for _ in range(n):
            self.rd(PHY_STATUS)
        self.access_s = (time.monotonic() - t0) / n
        return self.access_s

    def rd(self, off):
        return self.link.read(self.base + off)

    def wr(self, off, value):
        self.link.write(self.base + off, value & 0xFFFFFFFF)

    @property
    def frame_s(self):
        return self.fmt.bits / self.baud

    def configure(self, baud, fmt):
        """Program SETUP (resets RX and TX) and wait for the receiver to resync."""
        self.baud, self.fmt = baud, fmt
        self.wr(SETUP, round(self.clk_hz / baud) | fmt.setup_bits())
        time.sleep(max(0.02, 3 * self.frame_s))

    def fifo(self):
        v = self.rd(FIFO)
        return dict(rx_fill=(v >> 2) & 0x3FF, rx_lg=(v >> 12) & 0xF,
                    tx_free=(v >> 18) & 0x3FF, tx_lg=(v >> 28) & 0xF)

    def send(self, data, timeout=10.0):
        """Write bytes to TXREG, never overrunning the TX FIFO."""
        data, deadline = bytes(data), time.monotonic() + timeout
        while data:
            free = self.fifo()["tx_free"]
            if free == 0:
                if time.monotonic() > deadline:
                    raise HexbusError("TX FIFO stays full")
                time.sleep(self.frame_s)
                continue
            for b in data[:free]:
                self.wr(TXREG, b)
            data = data[free:]

    def recv(self, n, timeout):
        """Read up to n bytes from RXREG. Returns list of (byte, rxreg_word).
        The timeout is extended to cover n hexbus round trips, since a slow
        debug link (e.g. a 16 ms USB latency timer) dominates over the line."""
        timeout = max(timeout, n * self.access_s * 2 + 0.5)
        out, deadline = [], time.monotonic() + timeout
        while len(out) < n and time.monotonic() < deadline:
            v = self.rd(RXREG)
            if v & RX_EMPTY:
                time.sleep(0.002)
                continue
            out.append((v & 0xFF, v))
        return out

    def drain(self):
        for _ in range(4096):
            if self.rd(RXREG) & RX_EMPTY:
                return

    def rx_reset(self):
        self.wr(RXREG, RX_OVFL)
        time.sleep(max(0.01, 3 * self.frame_s))

    def clear_flags(self):
        self.wr(RXREG, RX_PERR | RX_FERR)

    def set_phy(self, ctrl, timing=None):
        if timing is not None:
            self.wr(PHY_TIMING, timing)
        self.wr(PHY_CTRL, ctrl | CTRL_APPLY_NOW)


# ----------------------------------------------------------------------------
# PC side of the line
# ----------------------------------------------------------------------------
class Line:
    def __init__(self, port):
        self.port = port

    def configure(self, baud, fmt):
        self.port.baudrate = baud
        for k, v in fmt.serial_kwargs().items():
            setattr(self.port, k, v)
        self.port.reset_input_buffer()

    def write(self, data):
        self.port.write(bytes(data))
        self.port.flush()

    def read(self, n, timeout):
        buf, deadline = bytearray(), time.monotonic() + timeout
        while len(buf) < n and time.monotonic() < deadline:
            buf += self.port.read(n - len(buf))
        return bytes(buf)

    def idle(self, seconds):
        time.sleep(seconds)
        self.port.reset_input_buffer()


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
class Report:
    COLORS = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m", "INFO": "\033[36m"}

    def __init__(self, color):
        self.rows, self.color = [], color

    def add(self, status, name, detail=""):
        self.rows.append({"status": status, "test": name, "detail": detail})
        tag = f"[{status}]"
        if self.color:
            tag = f"{self.COLORS[status]}{tag}\033[0m"
        print(f"{tag:>6} {name}" + (f" — {detail}" if detail else ""), flush=True)

    def check(self, cond, name, detail=""):
        self.add("PASS" if cond else "FAIL", name, detail)
        return cond

    def summary(self):
        counts = {s: sum(r["status"] == s for r in self.rows) for s in self.COLORS}
        print("\n" + "  ".join(f"{k}: {v}" for k, v in counts.items()))
        return counts["FAIL"] == 0


def pattern(n, mask, seed):
    """Edge values first, then pseudo-random bytes."""
    rnd = random.Random(seed)
    edge = [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x7F, 0xFE, 0x0F, 0xF0, 0x0D, 0x0A]
    data = edge + [rnd.randrange(256) for _ in range(max(0, n - len(edge)))]
    return bytes(b & mask for b in data[:n])


def flag_names(v):
    names = [(RX_PERR, "PERR"), (RX_FERR, "FERR"), (RX_BREAK, "BREAK"), (RX_OVFL, "OVERFLOW"),
             (RX_BYTE_PERR, "BYTE_PERR"), (RX_BYTE_FERR, "BYTE_FERR")]
    return " ".join(n for b, n in names if v & b) or "none"


def diff_detail(sent, got):
    """First mismatch with a short hex window, plus byte counts."""
    counts = f"sent {len(sent)} B, got {len(got)} B"
    for i, (a, b) in enumerate(zip(sent, got)):
        if a != b:
            lo, hi = max(0, i - 2), i + 6
            win_s = " ".join(f"{x:02X}" for x in sent[lo:hi])
            win_g = " ".join(f"{x:02X}" for x in got[lo:hi])
            return (f"byte {i}: sent 0x{a:02X} got 0x{b:02X} ({counts}); "
                    f"sent[{lo}:] {win_s} | got[{lo}:] {win_g}")
    return counts


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------
class Validator:
    def __init__(self, dut, line, rep, args):
        self.dut, self.line, self.rep, self.args = dut, line, rep, args
        self.loopback = args.loopback
        self.caps = 0
        self.depth = self.tx_depth = 16

    # -------------------------------------------------------------- registers
    def test_registers(self):
        d, r = self.dut, self.rep
        st = d.rd(PHY_STATUS)
        self.caps = st & 0x1F
        names = [n for b, n in ((ST_HAS_TX, "TX"), (ST_HAS_RX, "RX"), (ST_HAS_DE, "DE"),
                                (ST_HAS_RE, "/RE"), (ST_HAS_FLOW, "RTS/CTS")) if st & b]
        r.check(st & (ST_HAS_TX | ST_HAS_RX) != 0, "PHY_STATUS capabilities",
                f"0x{st:08X} ({', '.join(names) or 'none'})")

        f = d.fifo()
        self.depth = 1 << f["rx_lg"]
        self.tx_depth = 1 << f["tx_lg"]
        r.check(f["rx_lg"] >= 2 and f["tx_lg"] >= 2, "FIFO depth",
                f"RX {1 << f['rx_lg']} B, TX {1 << f['tx_lg']} B")

        no_flow = 0 if self.caps & ST_HAS_FLOW else SETUP_NO_FLOW
        for value in (0x00ABCDEF, 0x0F123456):
            d.wr(SETUP, value)
            got = d.rd(SETUP)
            r.check(got == (value | no_flow), f"SETUP write/read 0x{value:08X}",
                    f"read 0x{got:08X}" + ("" if got == (value | no_flow) else
                                          " (byte lanes lost: check WSTRB / AWSIZE)"))

        for value in (0x12345678, 0x00FA0032):
            d.wr(PHY_TIMING, value)
            r.check(d.rd(PHY_TIMING) == value, f"PHY_TIMING write/read 0x{value:08X}")

        modes = [CTRL_DE_LOW] + ([CTRL_DE_HIGH, CTRL_DE_TX] if self.caps & ST_HAS_DE else [])
        for m in modes:
            want = CTRL_TX_EN | CTRL_RX_EN | m
            d.set_phy(want)
            got = d.rd(PHY_CTRL)
            r.check(got == want, f"PHY_CTRL DE_MODE={m}", f"read 0x{got:08X}")

        before = d.rd(PHY_CTRL)
        d.wr(PHY_CTRL, CTRL_TX_EN | CTRL_RX_EN | 3)       # DE_MODE 3 is never valid
        st = d.rd(PHY_STATUS)
        r.check(st & ST_CFG_ERR and d.rd(PHY_CTRL) == before, "Invalid PHY_CTRL rejected",
                "CFG_ERR set, setting unchanged")
        d.wr(PHY_STATUS, ST_CFG_ERR)
        r.check(not d.rd(PHY_STATUS) & ST_CFG_ERR, "CFG_ERR write-one-to-clear")

        if not self.caps & ST_HAS_DE:
            d.wr(PHY_CTRL, CTRL_TX_EN | CTRL_RX_EN | CTRL_DE_HIGH | CTRL_APPLY_NOW)
            r.check(d.rd(PHY_STATUS) & ST_CFG_ERR, "DE mode rejected without HAS_DE")
            d.wr(PHY_STATUS, ST_CFG_ERR)

    # -------------------------------------------------------------- data
    def chunk(self):
        return max(1, self.depth - 2)

    def test_data(self, baud, fmt):
        d, r, name = self.dut, self.rep, f"{baud} {fmt}"
        n = self.args.count
        t_frame = d.frame_s

        if self.line:
            # FPGA -> PC
            sent = pattern(n, fmt.mask, baud)
            self.line.idle(0.01)
            d.send(sent)
            got = self.line.read(n, n * t_frame * 2 + 1.0)
            r.check(got == sent, f"TX data {name}", diff_detail(sent, got))

            # PC -> FPGA, in FIFO-sized chunks so hexbus can keep up
            sent = pattern(n, fmt.mask, baud + 1)
            got, flags = bytearray(), 0
            for i in range(0, n, self.chunk()):
                part = sent[i:i + self.chunk()]
                self.line.write(part)
                rx = d.recv(len(part), len(part) * t_frame * 3 + 0.5)
                got += bytes(b for b, _ in rx)
                for _, v in rx:
                    flags |= v
            flags |= d.rd(RXREG)                       # sticky flags after the transfer
            bad = flags & (RX_BYTE_PERR | RX_BYTE_FERR | RX_OVFL | RX_PERR | RX_FERR | RX_BREAK)
            ok = got == sent and not bad
            detail = diff_detail(sent, got)
            if bad:
                detail += ", flags: " + flag_names(bad)
            r.check(ok, f"RX data {name}", detail)
            d.clear_flags()

        if self.loopback:
            sent = pattern(n, fmt.mask, baud + 2)
            got, flags = bytearray(), 0
            for i in range(0, n, self.chunk()):
                part = sent[i:i + self.chunk()]
                d.send(part)
                rx = d.recv(len(part), len(part) * t_frame * 3 + 0.5)
                got += bytes(b for b, _ in rx)
                for _, v in rx:
                    flags |= v
            flags |= d.rd(RXREG)
            bad = flags & (RX_BYTE_PERR | RX_BYTE_FERR | RX_OVFL | RX_PERR | RX_FERR | RX_BREAK)
            detail = diff_detail(sent, got) + (", flags: " + flag_names(bad) if bad else "")
            r.check(got == sent and not bad, f"Loopback data {name}", detail)
            d.clear_flags()

    def test_burst(self, baud, fmt):
        """Back-to-back characters at full line speed, as many as the RX FIFO
        holds. The FIFO is read only after the burst, so the hexbus speed does
        not limit the line rate. With --line the PC sends the whole burst in
        one write; with --loopback the TX FIFO is filled while TX_EN = 0 and
        released at once, which guarantees zero gaps between characters."""
        d, r = self.dut, self.rep
        n = min(self.depth - 1, 1023)
        name = f"{baud} {fmt}, {n} B"
        runs = []
        if self.line:
            runs.append(("PC→FPGA", None))
        if self.loopback and self.caps & ST_HAS_TX:
            runs.append(("TX→RX loopback", None))
        for label, _ in runs:
            sent = pattern(n, fmt.mask, baud + 5)
            d.drain()
            d.clear_flags()
            if label.startswith("PC"):
                self.line.write(sent)
            else:
                normal = self.normal_ctrl()
                d.set_phy(normal & ~CTRL_TX_EN)
                d.send(sent[:self.tx_depth])
                d.set_phy(normal)
                if n > self.tx_depth:
                    d.send(sent[self.tx_depth:])
            time.sleep(n * d.frame_s * 1.3 + 0.1)
            fill = d.fifo()["rx_fill"]
            rx = d.recv(n, n * d.frame_s + 0.5)
            got = bytes(b for b, _ in rx)
            flags = d.rd(RXREG)
            for _, v in rx:
                flags |= v
            bad = flags & (RX_BYTE_PERR | RX_BYTE_FERR | RX_OVFL | RX_PERR | RX_FERR | RX_BREAK)
            detail = f"FIFO held {fill} B; " + diff_detail(sent, got)
            if bad:
                detail += ", flags: " + flag_names(bad)
            r.check(got == sent and not bad, f"Burst RX {label} {name}", detail)
            d.clear_flags()

    # -------------------------------------------------------------- error injection (PC adapter)
    def _pc_format(self, baud, fmt):
        try:
            self.line.configure(baud, fmt)
            return True
        except (ValueError, serial.SerialException) as exc:
            self.rep.add("SKIP", f"adapter format {fmt}", str(exc))
            return False

    def test_parity_errors(self, baud, fmt):
        """FPGA and PC use opposite parity: every byte must carry BYTE_PERR."""
        if fmt.parity == "N":
            return
        d, r = self.dut, self.rep
        opposite = {"E": "O", "O": "E", "M": "S", "S": "M"}[fmt.parity]
        if not self._pc_format(baud, fmt.with_parity(opposite)):
            return
        sent = pattern(min(self.chunk(), 12), fmt.mask, 7)
        d.drain()
        d.clear_flags()
        self.line.write(sent)
        rx = d.recv(len(sent), len(sent) * d.frame_s * 3 + 0.5)
        data_ok = bytes(b for b, _ in rx) == sent
        all_flag = len(rx) == len(sent) and all(v & RX_BYTE_PERR for _, v in rx)
        r.check(data_ok and all_flag, f"Parity error detected {baud} {fmt} vs PC {opposite}",
                f"{sum(bool(v & RX_BYTE_PERR) for _, v in rx)}/{len(sent)} bytes flagged")
        sticky = d.rd(RXREG)
        r.check(sticky & RX_PERR, f"Sticky PERR {baud} {fmt}")
        d.clear_flags()
        r.check(not d.rd(RXREG) & RX_PERR, f"PERR cleared {baud} {fmt}")
        self.line.configure(baud, fmt)

    def test_tx_parity(self, baud, fmt):
        """Check the parity bit the FPGA transmits. The PC reads one extra data
        bit and no parity, so the FPGA parity bit lands in the PC's top data
        bit. Only possible for 5-7 data bits; the generator is shared by all
        widths, so this also covers 8-bit formats."""
        if fmt.parity == "N" or fmt.data == 8:
            return
        d, r = self.dut, self.rep
        pc = Fmt(f"{fmt.data + 1}N1")
        if not self._pc_format(baud, pc):
            return
        sent = pattern(16, fmt.mask, 17)
        self.line.idle(0.01)
        d.send(sent)
        got = self.line.read(len(sent), len(sent) * d.frame_s * 2 + 1.0)
        expect = bytes(b | (fmt.parity_bit(b) << fmt.data) for b in sent)
        wrong = [i for i, (a, b) in enumerate(zip(expect, got)) if a != b]
        detail = "parity bit correct on every byte" if got == expect else (
            f"{len(wrong)} wrong, e.g. 0x{sent[wrong[0]]:02X}: parity bit "
            f"{(got[wrong[0]] >> fmt.data) & 1}, expected {fmt.parity_bit(sent[wrong[0]])}"
            if wrong else diff_detail(expect, got))
        r.check(got == expect, f"TX parity bit {baud} {fmt}", detail)
        self.line.configure(baud, fmt)

    def test_frame_errors(self, baud, fmt):
        """FPGA expects no parity, PC adds even parity: the PC parity bit lands
        on the FPGA stop bit, so bytes with an even number of ones (parity 0)
        must show BYTE_FERR and the others must not."""
        if fmt.parity != "N" or fmt.stop != 1:
            return
        d, r = self.dut, self.rep
        if not self._pc_format(baud, fmt.with_parity("E")):
            return
        # 0x00 is avoided: with a 0 parity bit it is 10 low bits and reads as a break
        samples = [0x41, 0x43, 0x30, 0x31, 0x03, 0x01, 0x55, 0x57]
        d.drain()
        d.clear_flags()
        wrong = []
        for b in samples:
            b &= fmt.mask
            self.line.write([b])
            rx = d.recv(1, 10 * d.frame_s + 0.3)
            time.sleep(4 * d.frame_s)                      # rxuart resyncs after a frame error
            expect = fmt.parity_bit(b) is None and (bin(b).count("1") % 2 == 0)
            got = bool(rx) and bool(rx[0][1] & RX_BYTE_FERR)
            if not rx or got != expect:
                wrong.append(f"0x{b:02X}:{'none' if not rx else ('FERR' if got else 'ok')}")
        r.check(not wrong, f"Frame error detected {baud} {fmt} vs PC {fmt.with_parity('E')}",
                "per-byte FERR matches parity bit" if not wrong else "mismatch " + ", ".join(wrong))
        r.check(d.rd(RXREG) & RX_FERR, f"Sticky FERR {baud} {fmt}")
        d.clear_flags()
        r.check(not d.rd(RXREG) & RX_FERR, f"FERR cleared {baud} {fmt}")
        self.line.configure(baud, fmt)

    def test_break(self, baud, fmt):
        d, r = self.dut, self.rep
        try:
            self.line.port.break_condition = True
        except (AttributeError, serial.SerialException, OSError) as exc:
            r.add("SKIP", f"Break {baud} {fmt}", f"adapter: {exc}")
            return
        time.sleep(max(0.05, 4 * d.frame_s))
        seen = bool(d.rd(RXREG) & RX_BREAK)
        self.line.port.break_condition = False
        time.sleep(max(0.05, 4 * d.frame_s))
        r.check(seen, f"Break detected {baud} {fmt}")
        r.check(not d.rd(RXREG) & RX_BREAK, f"Break released {baud} {fmt}")
        d.drain()
        d.clear_flags()
        self.line.write(b"\x5A")
        rx = d.recv(1, 10 * d.frame_s + 0.3)
        r.check(rx and rx[0][0] == 0x5A & fmt.mask, f"RX recovers after break {baud} {fmt}")

    def test_overflow(self, baud, fmt):
        d, r = self.dut, self.rep
        d.drain()
        extra = 8
        self.line.write(pattern(self.depth + extra, fmt.mask, 3))
        time.sleep((self.depth + extra) * d.frame_s * 1.5 + 0.05)
        fill = d.fifo()["rx_fill"]
        flags = d.rd(RXREG)
        r.check(flags & RX_OVFL, f"Overflow detected {baud} {fmt}",
                f"RX FIFO fill {fill}/{self.depth} after {self.depth + extra} bytes")
        r.check(d.rd(RXREG) & RX_OVFL, f"Overflow sticky {baud} {fmt}")
        d.rx_reset()
        v = d.rd(RXREG)
        r.check(v & RX_EMPTY and not v & RX_OVFL and d.fifo()["rx_fill"] == 0,
                f"RX reset clears FIFO and OVFL {baud} {fmt}")

    # -------------------------------------------------------------- PHY behaviour
    def test_phy(self, baud, fmt):
        d, r = self.dut, self.rep
        normal = self.normal_ctrl()
        probe = self.line is not None or self.loopback

        if probe and self.caps & ST_HAS_TX:
            d.set_phy(normal & ~CTRL_TX_EN)
            msg = pattern(3, fmt.mask, 11)
            d.send(msg)
            time.sleep(10 * d.frame_s + 0.05)
            if self.line:
                early = self.line.read(3, 0.1)
            else:
                early = bytes(b for b, _ in d.recv(3, 0.1))
            r.check(not early, f"TX_EN=0 holds data {baud} {fmt}", f"{len(early)} B leaked")
            d.set_phy(normal)
            if self.line:
                late = self.line.read(3, 30 * d.frame_s + 0.5)
            else:
                late = bytes(b for b, _ in d.recv(3, 30 * d.frame_s + 0.5))
            r.check(late == msg, f"TX_EN=1 releases queued data {baud} {fmt}", diff_detail(msg, late))

        if self.line and self.caps & ST_HAS_RX:
            d.drain()
            d.set_phy(normal & ~CTRL_RX_EN)
            self.line.write(pattern(3, fmt.mask, 12))
            time.sleep(10 * d.frame_s + 0.05)
            got = d.recv(3, 0.05)
            d.set_phy(normal)
            time.sleep(3 * d.frame_s)
            r.check(not got, f"RX_EN=0 ignores line {baud} {fmt}", f"{len(got)} B received")
            self.line.write(b"\x33")
            rx = d.recv(1, 10 * d.frame_s + 0.3)
            r.check(rx and rx[0][0] == 0x33 & fmt.mask, f"RX_EN=1 receives again {baud} {fmt}")

        if self.caps & ST_HAS_DE and probe:
            # One character must last much longer than a hexbus round trip so a
            # PHY_STATUS read lands while DE is high. The PC adapter is left at
            # its own baud rate (it may not support very low rates); whatever it
            # receives meanwhile is flushed afterwards.
            de_baud = int(max(50, min(1200, fmt.bits / (8 * d.access_s))))
            d.configure(de_baud, fmt)
            self.apply_phy(de_baud, (normal & ~0x3) | CTRL_DE_TX)
            seen = driving = False
            sent = 0
            for b in pattern(4, fmt.mask, 13):
                d.wr(TXREG, b)
                sent += 1
                for _ in range(3):
                    st = d.rd(PHY_STATUS)
                    seen |= bool(st & ST_DE)
                    driving |= bool(st & ST_DRIVING)
                if seen:
                    break
            time.sleep(sent * d.frame_s * 1.5 + 0.05)
            idle_low = not d.rd(PHY_STATUS) & ST_DE
            r.check(seen and idle_low, f"DE follows transmission ({de_baud} baud {fmt})",
                    f"DE seen high: {seen}, DRIVING seen: {driving}, low when idle: {idle_low}")
            d.configure(baud, fmt)
            self.apply_phy(baud)
            if self.line:
                self.line.idle(0.02)
            else:
                d.drain()
            keep = normal & ~0x3
            d.set_phy(keep | CTRL_DE_HIGH)
            hi = bool(d.rd(PHY_STATUS) & ST_DE)
            d.set_phy(keep | CTRL_DE_LOW)
            lo = not d.rd(PHY_STATUS) & ST_DE
            d.set_phy(normal)
            r.check(hi and lo, "DE_MODE high/low drive the pin", f"high={hi} low={lo}")

    def test_two_wire(self, baud, fmt):
        """Half duplex on a shared 2-wire bus: echo suppression and echo mode."""
        d, r = self.dut, self.rep
        if not self.caps & ST_HAS_DE:
            r.add("SKIP", "2-wire half duplex", "no DE pin")
            return
        base = CTRL_TX_EN | CTRL_RX_EN | CTRL_DE_TX | CTRL_HALF
        msg = pattern(4, fmt.mask, 21)
        for echo in (False, True):
            self.apply_phy(baud, base | (CTRL_ECHO if echo else 0))
            d.drain()
            self.line.idle(0.02)
            d.send(msg)
            pc = self.line.read(len(msg), 20 * d.frame_s + 0.5)
            own = bytes(b for b, _ in d.recv(len(msg), 20 * d.frame_s + 0.3))
            r.check(pc == msg, f"2-wire PC receives (echo={echo}) {baud} {fmt}", diff_detail(msg, pc))
            r.check(own == (msg if echo else b""), f"2-wire own echo {'kept' if echo else 'suppressed'} "
                    f"{baud} {fmt}", f"{len(own)} B seen by FPGA")
        self.apply_phy(baud)

    def normal_ctrl(self):
        """PHY_CTRL used between tests, from --line-type / --de."""
        ctrl = CTRL_TX_EN | CTRL_RX_EN
        if self.caps & ST_HAS_DE:
            ctrl |= DE_CODES[self.args.de]
        if self.args.half:
            ctrl |= CTRL_HALF
        return ctrl

    def timing_for(self, baud):
        """PHY_TIMING from --lead-us / --tail-us. Tail 'auto' = one bit time."""
        clk = self.args.clk
        lead = round(self.args.lead_us * 1e-6 * clk)
        tail = clk / baud if self.args.tail_us == "auto" else float(self.args.tail_us) * 1e-6 * clk
        return (min(0xFFFF, round(tail)) << 16) | min(0xFFFF, lead)

    def apply_phy(self, baud, ctrl=None):
        self.dut.set_phy(self.normal_ctrl() if ctrl is None else ctrl, timing=self.timing_for(baud))

    def test_throughput(self):
        d, r = self.dut, self.rep
        dt = d.measure_access()
        r.add("INFO", "Hexbus access time", f"{dt * 1000:.2f} ms per register read, "
              f"max ~{1 / dt:.0f} bytes/s through RXREG")
        if dt > 0.005:
            r.add("INFO", "Slow debug link", "USB-UART latency timer is probably 16 ms; set it "
                  "to 1 ms (Device Manager > port > Advanced) to speed up hexbus ~10x")

    # -------------------------------------------------------------- DE timing sweep
    EDGE = bytes([0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x7F, 0xFE])

    def _sweep_setup(self):
        a = self.args
        self.sw_baud, self.sw_fmt = a.bauds[-1], a.formats[0]
        self._pc_format(self.sw_baud, self.sw_fmt)
        self.dut.configure(self.sw_baud, self.sw_fmt)
        self.sw_ctrl = CTRL_TX_EN | CTRL_RX_EN | CTRL_DE_TX | (CTRL_HALF if a.half else 0)
        self.sw_bit_us = 1e6 / self.sw_baud
        self.sw_steps = 0

    def _sweep_timing(self, us):
        a, clk = self.args, self.args.clk
        lead_us = us if a.sweep == "lead" else a.lead_us
        tail_us = us if a.sweep == "tail" else (self.sw_bit_us if a.tail_us == "auto" else a.tail_us)
        return ((min(0xFFFF, round(tail_us * 1e-6 * clk)) << 16)
                | min(0xFFFF, round(lead_us * 1e-6 * clk)))

    def _sweep_try(self, us, data, trials):
        """Send `data` `trials` times with the given lead/tail. Returns (ok, detail)."""
        d = self.dut
        d.set_phy(self.sw_ctrl, timing=self._sweep_timing(us))
        self.sw_steps += 1
        for t in range(trials):
            self.line.idle(0.005)
            d.send(data)
            got = self.line.read(len(data), len(data) * d.frame_s * 2 + 0.5)
            got += self.line.read(64, 0.01)                # extra (noise) bytes
            if got != data:
                return False, diff_detail(data, got)
        return True, ""

    def _log_step(self, phase, us, ok, detail):
        clk = round(us * 1e-6 * self.args.clk)
        print(f"   [{phase}] {self.args.sweep} {us:9.3f} us ({clk:6d} clk): "
              f"{'PASS' if ok else 'FAIL'}" + ("" if ok else f"  {detail}"))

    def sweep(self):
        """Smallest DE lead (or tail) giving error-free TX with DE switched per
        transmission, assuming a longer lead never hurts:
          1. coarse: double the value with 8 edge-case bytes until it passes
          2. fine:   bisect the last failing/passing pair to --sweep-resolution
          3. verify: full --count x --sweep-trials at the result, stepping up
                     if a longer run still shows errors
        --sweep-mode linear keeps the old fixed-list scan."""
        d, r, a = self.dut, self.rep, self.args
        if not self.line:
            r.add("SKIP", "DE sweep", "needs --line (PC adapter)")
            return
        if not self.caps & ST_HAS_DE:
            r.add("SKIP", "DE sweep", "HAS_DE=0")
            return
        self._sweep_setup()
        baud, fmt, clk = self.sw_baud, self.sw_fmt, a.clk
        res_us = 1e6 / clk                                  # one ACLK cycle
        max_us = 0xFFFF * res_us
        t0 = time.monotonic()
        print(f"== Smart DE {a.sweep} sweep at {baud} baud {fmt}" if a.sweep_mode == "smart"
              else f"== Linear DE {a.sweep} sweep at {baud} baud {fmt}")

        full = pattern(a.count, fmt.mask, 100)
        if a.sweep_mode == "linear":
            found = None
            for us in (0, 0.5, 1, 1.5, 2, 3, 4, 5, 7, 10, 15, 20, 30, 50, 75, 100,
                       150, 200, 300, 500, 1000):
                if us > max_us:
                    break
                ok, det = self._sweep_try(us, full, a.sweep_trials)
                self._log_step("scan", us, ok, det)
                if ok:
                    found = us
                    break
        else:
            # 1. coarse: 0, then doubling from 0.25 us
            lo, hi, us = None, None, 0.0
            ok, det = self._sweep_try(0.0, self.EDGE, 1)
            self._log_step("coarse", 0.0, ok, det)
            if ok:
                hi = 0.0
            else:
                lo, us = 0.0, 0.25
                while us <= max_us:
                    ok, det = self._sweep_try(us, self.EDGE, 1)
                    self._log_step("coarse", us, ok, det)
                    if ok:
                        hi = us
                        break
                    lo, us = us, us * 2
                if hi is None and lo < max_us:
                    ok, det = self._sweep_try(max_us, self.EDGE, 1)
                    self._log_step("coarse", max_us, ok, det)
                    hi = max_us if ok else None
            # 2. fine: bisect (lo fails, hi passes) until the gap is below
            #    --sweep-resolution % of hi (or one ACLK cycle)
            if hi is not None and lo is not None:
                while hi - lo > max(res_us * 1.5, hi * a.sweep_resolution / 100):
                    mid = round((lo + hi) / 2 / res_us) * res_us
                    if mid <= lo or mid >= hi:
                        break
                    ok, det = self._sweep_try(mid, self.EDGE, 2)
                    self._log_step("fine", mid, ok, det)
                    if ok:
                        hi = mid
                    else:
                        lo = mid
            # 3. verify with the full pattern, stepping up 10 % on failure
            found = None
            us = hi
            while us is not None and us <= max_us:
                ok, det = self._sweep_try(us, full, a.sweep_trials)
                self._log_step("verify", us, ok, det)
                if ok:
                    found = us
                    break
                us = max(us * 1.1, us + 4 * res_us)

        dt = time.monotonic() - t0
        if found is None:
            r.add("FAIL", f"DE {a.sweep} sweep {baud} {fmt}",
                  f"no value up to {max_us:.0f} us works with DE switched per transmission "
                  f"({self.sw_steps} steps, {dt:.1f} s): check the DE wiring, bias on the "
                  "FPGA->PC pair and the transceiver data rate, or use DE high")
            return
        rec = max(2 * found, found + 1)
        rec_clk = min(0xFFFF, round(rec * 1e-6 * clk))
        tail_clk = round((self.sw_bit_us if a.tail_us == "auto" else a.tail_us) * 1e-6 * clk)
        lead_clk = round(a.lead_us * 1e-6 * clk)
        def_timing = ((tail_clk << 16) | rec_clk) if a.sweep == "lead" else ((rec_clk << 16) | lead_clk)
        r.add("PASS", f"DE {a.sweep} sweep {baud} {fmt}",
              f"minimum {found:.3f} us ({round(found * 1e-6 * clk)} clk) in {self.sw_steps} steps, "
              f"{dt:.1f} s; recommended {rec:.2f} us = {rec_clk} clk "
              f"(DEF_TIMING {def_timing} / 0x{def_timing:08X}, --{a.sweep}-us {rec:.2f})")

    # -------------------------------------------------------------- runner
    def run(self):
        d, r = self.dut, self.rep
        saved = [d.rd(SETUP), d.rd(PHY_CTRL), d.rd(PHY_TIMING)]
        try:
            print("== Register tests")
            self.test_registers()
            self.test_throughput()
            if self.args.de != "low" and not self.caps & ST_HAS_DE:
                r.add("INFO", "DE mode", f"'{self.args.de}' requested but HAS_DE=0: using DE low")
            self.apply_phy(self.args.bauds[0])
            if self.args.sweep:
                self.sweep()
                return
            if not (self.line or self.loopback):
                r.add("SKIP", "Line tests", "use --line PORT or --loopback")
                return
            for baud in self.args.bauds:
                for fmt in self.args.formats:
                    print(f"== {baud} baud {fmt}")
                    if self.line and not self._pc_format(baud, fmt):
                        continue
                    d.configure(baud, fmt)
                    self.apply_phy(baud)
                    if self.line:
                        self.line.idle(0.01)
                    self.test_data(baud, fmt)
                    if fmt is self.args.formats[0] or self.args.burst_all:
                        self.test_burst(baud, fmt)
                    if self.line and not self.args.quick:
                        self.test_parity_errors(baud, fmt)
                        self.test_tx_parity(baud, fmt)
                        self.test_frame_errors(baud, fmt)
                    if (baud, fmt.text) == (self.args.bauds[0], self.args.formats[0].text):
                        if self.line:
                            self.test_break(baud, fmt)
                            self.test_overflow(baud, fmt)
                            if self.args.half:
                                self.test_two_wire(baud, fmt)
                        self.test_phy(baud, fmt)
        finally:
            try:
                d.wr(SETUP, saved[0])
                d.wr(PHY_TIMING, saved[2])
                d.wr(PHY_CTRL, saved[1] | CTRL_APPLY_NOW)
                d.drain()
                d.clear_flags()
            except HexbusError as exc:
                print(f"warning: could not restore registers: {exc}")


# ----------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("connections")
    g.add_argument("--config", help="JSON file with any of the options below (command line wins)")
    g.add_argument("--save-config", metavar="FILE", help="write the effective settings to FILE and exit")
    g.add_argument("--hexbus", help="hexbus debug COM port")
    g.add_argument("--hexbus-baud", type=int, default=115200)
    g.add_argument("--line", help="PC adapter COM port on the UART line")
    g.add_argument("--loopback", action="store_true", help="UART TX wired to its own RX")
    g.add_argument("--simulate", action="store_true", help="offline dry run against a model")

    g = ap.add_argument_group("device")
    g.add_argument("--base", type=lambda s: int(s, 0), default=0x80000000, help="UART base address")
    g.add_argument("--clk", type=float, default=50e6, help="ACLK frequency in Hz")

    g = ap.add_argument_group("line interface (written to PHY_CTRL / PHY_TIMING before the tests)")
    g.add_argument("--line-type", choices=LINE_TYPES, default="rs485-4w",
                   help="; ".join(f"{k}: {v['desc']}" for k, v in LINE_TYPES.items()))
    g.add_argument("--de", choices=DE_CODES, help="override the DE mode of --line-type")
    g.add_argument("--lead-us", type=float, default=10.0,
                   help="DE to start bit, microseconds (default 10; >= 2x transceiver tZH)")
    g.add_argument("--tail-us", default="auto",
                   help="stop bit to DE release, microseconds, or 'auto' = one bit time (default)")

    g = ap.add_argument_group("test matrix")
    g.add_argument("--bauds", default="9600,19200,38400,57600,115200")
    g.add_argument("--formats", default="8N1,8E1,8O1,8N2,7E1,7O1,8M1,8S1",
                   help="data bits 5-8, parity N/E/O/M/S, stop 1-2, e.g. 5N1,6E1,7O2")
    g.add_argument("--count", type=int, default=64, help="bytes per data test")
    g.add_argument("--quick", action="store_true", help="skip parity/frame injection")
    g.add_argument("--burst-all", action="store_true",
                   help="run the full-FIFO burst test for every format, not only the first")

    g = ap.add_argument_group("DE timing sweep (instead of the normal tests)")
    g.add_argument("--sweep", choices=("lead", "tail"),
                   help="find the smallest working DE lead/tail at the LAST --bauds value "
                        "and the first --formats value, DE switched per transmission")
    g.add_argument("--sweep-trials", type=int, default=3,
                   help="error-free runs of --count bytes needed in the final check")
    g.add_argument("--sweep-resolution", type=float, default=5.0,
                   help="smart mode: stop bisecting when the gap is below this %% (default 5)")
    g.add_argument("--sweep-mode", choices=("smart", "linear"), default="smart",
                   help="smart: coarse doubling, bisection, then full verify (default); "
                        "linear: fixed list, full test at every step")
    g.add_argument("--sim-de-delay-us", type=float, default=0.0, help=argparse.SUPPRESS)

    g = ap.add_argument_group("output")
    g.add_argument("--report", help="write results to a JSON file")
    g.add_argument("--no-color", action="store_true")
    return ap


def parse_args(argv):
    ap = build_parser()
    pre, _ = ap.parse_known_args(argv)
    if pre.config:
        with open(pre.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        unknown = set(cfg) - set(CONFIG_KEYS)
        if unknown:
            ap.error(f"unknown keys in {pre.config}: {', '.join(sorted(unknown))}")
        for key in ("bauds", "formats"):
            if isinstance(cfg.get(key), list):
                cfg[key] = ",".join(str(x) for x in cfg[key])
        if isinstance(cfg.get("base"), str):
            cfg["base"] = int(cfg["base"], 0)
        ap.set_defaults(**cfg)
    args = ap.parse_args(argv)

    lt = LINE_TYPES[args.line_type]
    args.de = args.de or lt["de"]
    args.half = lt["half"]
    if args.tail_us != "auto":
        args.tail_us = float(args.tail_us)

    if args.save_config:
        out = {k: getattr(args, k) for k in CONFIG_KEYS}
        out["base"] = f"0x{args.base:08X}"
        out["bauds"] = [int(b) for b in args.bauds.split(",") if b]
        out["formats"] = [f for f in args.formats.split(",") if f]
        with open(args.save_config, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"settings written to {args.save_config}")
        sys.exit(0)

    args.bauds = [int(b) for b in args.bauds.split(",") if b]
    args.formats = [Fmt(f) for f in args.formats.split(",") if f]
    return args


def main(argv=None):
    args = parse_args(argv)
    rep = Report(color=not args.no_color and sys.stdout.isatty())

    link, line_port = HexbusLink(timeout=2.0), None
    try:
        if args.simulate:
            from uart_validate_sim import SimBench
            bench = SimBench(args.base, args.clk, two_wire=args.half, loopback=args.loopback,
                             de_delay_us=args.sim_de_delay_us)
            link.attach(bench.hexbus)
            if not args.loopback:
                line_port = bench.line
        else:
            if serial is None:
                sys.exit("pyserial is required: pip install pyserial")
            if not args.hexbus:
                sys.exit("--hexbus PORT is required (or --simulate)")
            link.open(args.hexbus, args.hexbus_baud)
            if args.line:
                line_port = serial.Serial(args.line, 9600, timeout=0.05)
        link.reset()
        dut = Dut(link, args.base, args.clk)
        line = Line(line_port) if line_port is not None else None
        tail = "1 bit" if args.tail_us == "auto" else f"{args.tail_us:g} us"
        print(f"UART @ 0x{args.base:08X}, ACLK {args.clk / 1e6:g} MHz, "
              f"{'PC adapter ' + (args.line or 'SIM') if line else 'no PC adapter'}"
              f"{', loopback' if args.loopback else ''}")
        print(f"Line {args.line_type}: DE {args.de}, {'half' if args.half else 'full'} duplex, "
              f"lead {args.lead_us:g} us, tail {tail}")
        Validator(dut, line, rep, args).run()
    except HexbusError as exc:
        rep.add("FAIL", "Hexbus communication", str(exc))
    finally:
        link.close()
        if line_port is not None:
            line_port.close()

    ok = rep.summary()
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({"passed": ok, "results": rep.rows}, fh, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
