"""Hardware validation of axiluart_phy (axiluart_v2 + uart_phy_adapter) through the hexbus
UART-to-AXI4-Lite bridge.

Connections
  hexbus link   the Debug Bridge connection (register access)
  line port     optional PC adapter (USB-RS485 / RS-232) on the UART line: the PC is the far end,
                data is checked in both directions and parity, frame, break and overflow errors
                are injected
  loopback      no PC adapter: the UART's TX is wired to its own RX
Without a line port or loopback only the register tests run.

The line interface (PHY_CTRL / PHY_TIMING) is set from the line type, DE mode and lead / tail
times before the tests, never taken from the board; every register is restored when the run
ends, also after Stop.

The tests run on one thread and report each result through a callback (the Validation tab of the
UART interface). The link is anything with read(addr) / write(addr, value) that raises LinkError
on a failed access; in the UART interface it goes through AXIL Studio's Debug Bridge.
"""

import dataclasses
import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from uart_phy import (CTRL_APPLY_NOW, CTRL_RX_INV, CTRL_TX_INV, CTRL_DE_HIGH, CTRL_DE_LOW, CTRL_DE_TX, CTRL_ECHO, CTRL_HALF,
                       CTRL_RX_EN, CTRL_TX_EN, DE_CODES, FIFO, LINE_TYPES, PHY_CTRL, PHY_STATUS, PHY_TIMING,
                       RX_BREAK, RX_BYTE_FERR, RX_BYTE_PERR, RX_EMPTY, RX_FERR, RX_OVFL, RX_PERR, RXREG, SETUP,
                       SETUP_BAUD_MASK, SETUP_NO_FLOW, ST_CFG_ERR, ST_DE, ST_DRIVING, ST_HAS_DE, ST_HAS_FLOW, ST_HAS_RE, ST_HAS_RX,
                       ST_HAS_TX, TXREG, Fmt, flag_names, parse_bauds, parse_formats, setup_value)

try:
    import serial
    SERIAL_ERRORS = (serial.SerialException,)
except ImportError:  # pragma: no cover
    serial = None
    SERIAL_ERRORS = ()


class Cancelled(Exception):
    """The run was stopped."""


class LinkError(Exception):
    """A register access failed (the link raises it, or an error derived from it)."""


# ----------------------------------------------------------------------------
# Settings (also the JSON settings file)
# ----------------------------------------------------------------------------
CONFIG_KEYS = ("hexbus", "hexbus_baud", "line", "loopback", "base", "clk", "bauds", "formats",
               "count", "quick", "burst_all", "line_type", "de", "lead_us", "tail_us", "report")


@dataclass
class Settings:
    base: int = 0x80000000          # UART base address
    clk: float = 50e6               # ACLK frequency (Hz)
    line: str = ""                  # PC adapter port on the UART line ("" = none)
    loopback: bool = False          # TX wired to RX, no adapter
    line_type: str = "rs485-4w"
    de: str = ""                    # "" = the line type's DE mode
    lead_us: float = 10.0
    tail_us: object = "auto"        # float or "auto" = one bit time
    bauds: List[int] = field(default_factory=lambda: [9600, 19200, 38400, 57600, 115200])
    formats: List[Fmt] = field(default_factory=lambda: parse_formats("8N1,8E1,8O1,8N2,7E1,7O1,8M1,8S1"))
    count: int = 64
    quick: bool = False
    burst_all: bool = False
    sweep: Optional[str] = None     # None | "lead" | "tail": DE timing sweep instead of the tests
    sweep_mode: str = "smart"
    sweep_trials: int = 3
    sweep_resolution: float = 5.0
    # 4-wire pair of two IPs: {"a": name, "a_base": int, "b": name, "b_base": int, "tx": "a" | "b"}
    pair: Optional[dict] = None
    pc: str = "one"                 # pair: "one" COM for both directions | "two" COMs (one per bus) | "none"
    lines: dict = field(default_factory=dict)    # pc "two": {IP name: COM port on its bus}
    swap: bool = True               # pair: run again with the directions swapped
    # command line only
    hexbus: str = ""
    hexbus_baud: int = 115200
    report: str = ""

    def __post_init__(self):
        self.normalize()

    def normalize(self):
        if isinstance(self.bauds, str):
            self.bauds = parse_bauds(self.bauds)
        if isinstance(self.formats, str):
            self.formats = parse_formats(self.formats)
        self.formats = [f if isinstance(f, Fmt) else Fmt(f) for f in self.formats]
        if isinstance(self.base, str):
            self.base = int(self.base, 0)
        if self.tail_us not in ("auto", None, ""):
            self.tail_us = float(self.tail_us)
        else:
            self.tail_us = "auto"
        if self.line_type not in LINE_TYPES:
            raise ValueError(f"unknown line type '{self.line_type}'")
        if self.de and self.de not in DE_CODES:
            raise ValueError(f"unknown DE mode '{self.de}'")
        if not self.bauds:
            raise ValueError("no baud rate given")
        if not self.formats:
            raise ValueError("no format given")
        return self

    @property
    def de_mode(self):
        return self.de or LINE_TYPES[self.line_type]["de"]

    @property
    def half(self):
        return LINE_TYPES[self.line_type]["half"]

    @property
    def echo(self):
        return LINE_TYPES[self.line_type]["echo"]

    def to_dict(self):
        d = {k: getattr(self, k) for k in CONFIG_KEYS if hasattr(self, k)}
        d.update(base=f"0x{self.base:08X}", formats=[str(f) for f in self.formats], bauds=list(self.bauds))
        for k in ("sweep", "sweep_mode", "sweep_trials", "sweep_resolution", "pair", "pc", "lines", "swap"):
            d[k] = getattr(self, k)
        return d

    @staticmethod
    def from_dict(d):
        names = {f.name for f in dataclasses.fields(Settings)}
        unknown = set(d) - names
        if unknown:
            raise ValueError("unknown keys: " + ", ".join(sorted(unknown)))
        return Settings(**{k: v for k, v in d.items() if k in names})


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
class Report:
    """Collects the results; `on_row(row)` and `on_log(text)` see each one as it comes."""
    STATUSES = ("PASS", "FAIL", "SKIP", "INFO")

    def __init__(self, on_row=None, on_log=None, cancel: threading.Event = None):
        self.rows = []
        self.on_row = on_row or (lambda row: None)
        self.on_log = on_log or (lambda text: None)
        self.cancel = cancel or threading.Event()
        self.section_name = ""

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def section(self, name):
        self.section_name = name
        self.on_log(f"== {name}")
        self.check_cancel()

    def log(self, text):
        self.on_log("   " + text)

    def add(self, status, name, detail=""):
        row = {"status": status, "test": name, "detail": detail, "section": self.section_name,
               "time": time.strftime("%H:%M:%S")}
        self.rows.append(row)
        self.on_row(row)
        self.on_log(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

    def check(self, cond, name, detail=""):
        self.add("PASS" if cond else "FAIL", name, detail)
        return cond

    def counts(self):
        return {s: sum(r["status"] == s for r in self.rows) for s in self.STATUSES}

    @property
    def passed(self):
        return self.counts()["FAIL"] == 0

    def to_dict(self, settings=None, stopped=False):
        d = {"passed": self.passed and not stopped, "stopped": stopped, "counts": self.counts(),
             "results": self.rows}
        if settings is not None:
            d["settings"] = settings.to_dict()
        return d


# ----------------------------------------------------------------------------
# Device under test
# ----------------------------------------------------------------------------
class Dut:
    def __init__(self, link, base, clk_hz, cancel: threading.Event = None):
        self.link, self.base, self.clk_hz = link, base, clk_hz
        self.baud, self.fmt = None, None
        self.access_s = 0.002          # measured hexbus round trip, see measure_access()
        self.cancel = cancel or threading.Event()
        self.cancellable = True        # False while the registers are being restored

    def _check(self):
        if self.cancellable and self.cancel.is_set():
            raise Cancelled()

    def measure_access(self, n=30):
        t0 = time.monotonic()
        for _ in range(n):
            self.rd(PHY_STATUS)
        self.access_s = (time.monotonic() - t0) / n
        return self.access_s

    def rd(self, off):
        self._check()
        return self.link.read(self.base + off)

    def wr(self, off, value):
        self._check()
        self.link.write(self.base + off, value & 0xFFFFFFFF)

    @property
    def frame_s(self):
        return self.fmt.bits / self.baud

    def sleep(self, seconds):
        """time.sleep that stops early on Stop."""
        if self.cancellable:
            if self.cancel.wait(max(0.0, seconds)):
                raise Cancelled()
        else:
            time.sleep(max(0.0, seconds))

    def configure(self, baud, fmt):
        """Program SETUP (resets RX and TX) and wait for the receiver to resync."""
        self.baud, self.fmt = baud, fmt
        self.wr(SETUP, setup_value(self.clk_hz, baud, fmt, no_flow=True))
        self.sleep(max(0.02, 3 * self.frame_s))

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
                    raise LinkError("TX FIFO stays full")
                self.sleep(self.frame_s)
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
                self.sleep(0.002)
                continue
            out.append((v & 0xFF, v))
        return out

    def drain(self):
        for _ in range(4096):
            if self.rd(RXREG) & RX_EMPTY:
                return

    def rx_reset(self):
        self.wr(RXREG, RX_OVFL)
        self.sleep(max(0.01, 3 * self.frame_s))

    def clear_flags(self):
        self.wr(RXREG, RX_PERR | RX_FERR)

    def set_phy(self, ctrl, timing=None):
        if timing is not None:
            self.wr(PHY_TIMING, timing)
        self.wr(PHY_CTRL, ctrl | CTRL_APPLY_NOW)

    # registers kept before the run and written back at its end
    def snapshot(self):
        return [(self.base, off, self.link.read(self.base + off)) for off in (SETUP, PHY_CTRL, PHY_TIMING)]

    def restore(self, snap):
        for base, off, v in snap:
            self.link.write(base + off, (v | CTRL_APPLY_NOW) if off == PHY_CTRL else v)


class PairDut(Dut):
    """Two axiluart_phy IPs used as one 4-wire link: the TX IP drives its transceiver and only
    sends, the RX IP only receives. The tests see one UART: TXREG and the TX half of FIFO go to the
    TX IP, RXREG and the RX half to the RX IP, SETUP / PHY_TIMING to both, and a PHY_CTRL value is
    split into the two roles (DE, TX_EN, TX polarity -> TX IP; RX_EN, RX polarity -> RX IP)."""

    def __init__(self, link, tx_base, rx_base, clk_hz, cancel=None, names=("TX", "RX")):
        super().__init__(link, tx_base, clk_hz, cancel)
        self.tx_base, self.rx_base = tx_base, rx_base
        self.names = names

    def _r(self, base, off):
        self._check()
        return self.link.read(base + off)

    def _w(self, base, off, value):
        self._check()
        self.link.write(base + off, value & 0xFFFFFFFF)

    def rd(self, off):
        if off == RXREG:
            return self._r(self.rx_base, off)
        if off == FIFO:
            return (self._r(self.rx_base, FIFO) & 0xFFFF) | (self._r(self.tx_base, FIFO) & 0xFFFF0000)
        return self._r(self.tx_base, off)

    def wr(self, off, value):
        if off == TXREG:
            self._w(self.tx_base, off, value)
        elif off == RXREG:
            self._w(self.rx_base, off, value)
        elif off == PHY_CTRL:
            tx, rx = self.split_ctrl(value)
            self._w(self.rx_base, off, rx)            # the receiver first: never two drivers
            self._w(self.tx_base, off, tx)
        else:                                         # SETUP, PHY_TIMING, PHY_STATUS (W1C)
            self._w(self.rx_base, off, value)
            self._w(self.tx_base, off, value)

    @staticmethod
    def split_ctrl(value):
        apply = value & CTRL_APPLY_NOW
        tx = value & (3 | CTRL_TX_EN | CTRL_TX_INV) | apply
        rx = value & (CTRL_RX_EN | CTRL_RX_INV) | apply          # DE low, TX off
        return tx, rx

    def caps(self):
        """Capabilities both IPs have (PHY_STATUS [4:0])."""
        return self._r(self.tx_base, PHY_STATUS) & self._r(self.rx_base, PHY_STATUS) & 0x1F

    def snapshot(self):
        return [(b, off, self.link.read(b + off)) for b in (self.tx_base, self.rx_base)
                for off in (SETUP, PHY_CTRL, PHY_TIMING)]


# ----------------------------------------------------------------------------
# PC side of the line
# ----------------------------------------------------------------------------
def serial_kwargs(fmt):
    return dict(bytesize={5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}[fmt.data],
                parity={"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
                        "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}[fmt.parity],
                stopbits=serial.STOPBITS_ONE if fmt.stop == 1 else serial.STOPBITS_TWO)


class Line:
    """The PC end of the UART line: `port` sends to the FPGA, `rport` (default the same port)
    receives from it. Two ports: a 4-wire link made of two 2-wire buses, one adapter on each."""

    def __init__(self, port, rport=None):
        self.port = port
        self.rport = rport if rport is not None else port

    def configure(self, baud, fmt):
        for p in {id(self.port): self.port, id(self.rport): self.rport}.values():
            p.baudrate = baud
            for k, v in serial_kwargs(fmt).items():
                setattr(p, k, v)
            p.reset_input_buffer()

    def write(self, data):
        self.port.write(bytes(data))
        self.port.flush()

    def read(self, n, timeout):
        buf, deadline = bytearray(), time.monotonic() + timeout
        while len(buf) < n and time.monotonic() < deadline:
            buf += self.rport.read(n - len(buf))
        return bytes(buf)

    def idle(self, seconds):
        time.sleep(seconds)
        self.rport.reset_input_buffer()


def pattern(n, mask, seed):
    """Edge values first, then pseudo-random bytes."""
    rnd = random.Random(seed)
    edge = [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x7F, 0xFE, 0x0F, 0xF0, 0x0D, 0x0A]
    data = edge + [rnd.randrange(256) for _ in range(max(0, n - len(edge)))]
    return bytes(b & mask for b in data[:n])


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




class Validator:
    def __init__(self, dut, line, rep, args):
        self.dut, self.line, self.rep, self.args = dut, line, rep, args
        self.loopback = args.loopback
        self.pair = isinstance(dut, PairDut)
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
            self.dut.sleep(n * d.frame_s * 1.3 + 0.1)
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
        except (ValueError, OSError) + SERIAL_ERRORS as exc:
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
            self.dut.sleep(4 * d.frame_s)                      # rxuart resyncs after a frame error
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
        except (AttributeError, OSError) + SERIAL_ERRORS as exc:
            r.add("SKIP", f"Break {baud} {fmt}", f"adapter: {exc}")
            return
        try:
            self.dut.sleep(max(0.05, 4 * d.frame_s))
            seen = bool(d.rd(RXREG) & RX_BREAK)
        finally:
            try:
                self.line.port.break_condition = False
            except (AttributeError, OSError) + SERIAL_ERRORS:
                pass
        self.dut.sleep(max(0.05, 4 * d.frame_s))
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
        self.dut.sleep((self.depth + extra) * d.frame_s * 1.5 + 0.05)
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
            self.dut.sleep(10 * d.frame_s + 0.05)
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
            self.dut.sleep(10 * d.frame_s + 0.05)
            got = d.recv(3, 0.05)
            d.set_phy(normal)
            self.dut.sleep(3 * d.frame_s)
            r.check(not got, f"RX_EN=0 ignores line {baud} {fmt}", f"{len(got)} B received")
            self.line.write(b"\x33")
            rx = d.recv(1, 10 * d.frame_s + 0.3)
            r.check(rx and rx[0][0] == 0x33 & fmt.mask, f"RX_EN=1 receives again {baud} {fmt}")

        if self.caps & ST_HAS_DE and probe:
            # One character must last much longer than a hexbus round trip so a
            # PHY_STATUS read lands while DE is high. The PC adapter is left at
            # its own baud rate (it may not support very low rates); whatever it
            # receives meanwhile is flushed afterwards.
            min_baud = max(50, math.ceil(d.clk_hz / SETUP_BAUD_MASK))
            de_baud = int(max(min_baud, min(1200, fmt.bits / (8 * max(1e-6, d.access_s)))))
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
            self.dut.sleep(sent * d.frame_s * 1.5 + 0.05)
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
            ctrl |= DE_CODES[self.args.de_mode]
        if self.args.half:
            ctrl |= CTRL_HALF | (CTRL_ECHO if self.args.echo else 0)
        return ctrl

    def timing_for(self, baud):
        """PHY_TIMING from --lead-us / --tail-us. Tail 'auto' = one bit time."""
        clk = self.args.clk
        lead = max(0, min(0xFFFF, round(self.args.lead_us * 1e-6 * clk)))
        tail = clk / baud if self.args.tail_us == "auto" else float(self.args.tail_us) * 1e-6 * clk
        return (max(0, min(0xFFFF, round(tail))) << 16) | lead

    def apply_phy(self, baud, ctrl=None):
        self.dut.set_phy(self.normal_ctrl() if ctrl is None else ctrl, timing=self.timing_for(baud))

    def test_throughput(self):
        d, r = self.dut, self.rep
        dt = d.measure_access()
        rate = 1 / max(1e-9, dt)
        r.add("INFO", "Hexbus access time", f"{dt * 1000:.2f} ms per register read, "
              f"max ~{rate:.0f} bytes/s through RXREG")
        if dt > 0.005:
            r.add("INFO", "Slow debug link", "USB-UART latency timer is probably 16 ms; set it "
                  "to 1 ms (Device Manager > port > Advanced) to speed up hexbus ~10x")

    # -------------------------------------------------------------- DE timing sweep
    EDGE = bytes([0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x7F, 0xFE])

    def _sweep_setup(self):
        a = self.args
        self.sw_baud, self.sw_fmt = a.bauds[-1], a.formats[0]
        if not self._pc_format(self.sw_baud, self.sw_fmt):
            return False
        self.dut.configure(self.sw_baud, self.sw_fmt)
        self.sw_ctrl = CTRL_TX_EN | CTRL_RX_EN | CTRL_DE_TX | (CTRL_HALF if a.half else 0)
        self.sw_bit_us = 1e6 / self.sw_baud
        self.sw_steps = 0
        return True

    def _sweep_timing(self, us):
        a, clk = self.args, self.args.clk
        lead_us = us if a.sweep == "lead" else a.lead_us
        tail_us = us if a.sweep == "tail" else (self.sw_bit_us if a.tail_us == "auto" else a.tail_us)
        return ((max(0, min(0xFFFF, round(tail_us * 1e-6 * clk))) << 16)
                | max(0, min(0xFFFF, round(lead_us * 1e-6 * clk))))

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
        self.rep.log(f"[{phase}] {self.args.sweep} {us:9.3f} us ({clk:6d} clk): "
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
            r.add("SKIP", "DE sweep", "needs a line adapter (--line)")
            return
        if not self.caps & ST_HAS_DE:
            r.add("SKIP", "DE sweep", "HAS_DE=0")
            return
        if not self._sweep_setup():
            return
        baud, fmt, clk = self.sw_baud, self.sw_fmt, a.clk
        res_us = 1e6 / clk                                  # one ACLK cycle
        max_us = 0xFFFF * res_us
        t0 = time.monotonic()
        self.rep.section(f"Smart DE {a.sweep} sweep at {baud} baud {fmt}" if a.sweep_mode == "smart"
                         else f"Linear DE {a.sweep} sweep at {baud} baud {fmt}")

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
        rec_clk = max(0, min(0xFFFF, round(rec * 1e-6 * clk)))
        tail_clk = max(0, min(0xFFFF, round((self.sw_bit_us if a.tail_us == "auto" else a.tail_us) * 1e-6 * clk)))
        lead_clk = max(0, min(0xFFFF, round(a.lead_us * 1e-6 * clk)))
        def_timing = ((tail_clk << 16) | rec_clk) if a.sweep == "lead" else ((rec_clk << 16) | lead_clk)
        r.add("PASS", f"DE {a.sweep} sweep {baud} {fmt}",
              f"minimum {found:.3f} us ({round(found * 1e-6 * clk)} clk) in {self.sw_steps} steps, "
              f"{dt:.1f} s; recommended {rec:.2f} us = {rec_clk} clk "
              f"(DEF_TIMING {def_timing} / 0x{def_timing:08X}, --{a.sweep}-us {rec:.2f})")

    # -------------------------------------------------------------- runner
    def run(self):
        d, r = self.dut, self.rep
        saved = d.snapshot()
        try:
            if self.pair:              # the register tests ran on each IP before
                self.caps = d.caps()
                f = d.fifo()
                self.depth, self.tx_depth = 1 << f["rx_lg"], 1 << f["tx_lg"]
            else:
                r.section("Register tests")
                self.test_registers()
            self.test_throughput()
            if self.args.de_mode != "low" and not self.caps & ST_HAS_DE:
                r.add("INFO", "DE mode", f"'{self.args.de_mode}' requested but HAS_DE=0: using DE low")
            self.apply_phy(self.args.bauds[0])
            if self.args.sweep:
                self.sweep()
                return
            if not (self.line or self.loopback):
                r.add("SKIP", "Line tests", "needs a line adapter (--line) or loopback")
                return
            ran_once = False
            for baud in self.args.bauds:
                for fmt in self.args.formats:
                    r.section(f"{getattr(self, 'section_prefix', '') + ' · ' if getattr(self, 'section_prefix', '') else ''}"
                              f"{baud} baud {fmt}")
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
                    if not ran_once:
                        ran_once = True
                        if self.line:
                            self.test_break(baud, fmt)
                            self.test_overflow(baud, fmt)
                            if self.args.half:
                                self.test_two_wire(baud, fmt)
                        self.test_phy(baud, fmt)
        finally:
            d.cancellable = False           # the registers are restored even after Stop
            try:
                d.restore(saved)
                d.drain()
                d.clear_flags()
            except LinkError as exc:
                r.add("INFO", "Restore registers", f"could not restore the registers: {exc}")



# ----------------------------------------------------------------------------
# Running
# ----------------------------------------------------------------------------
def run_validation(link, settings: Settings, report: Report, line_port=None, ports=None):
    """Run the tests (or the DE sweep) on an open link. `line_port` is an open pyserial port on the
    UART line, or None; `ports` ({IP name: open port}) the two adapters of a pair with pc "two".
    Raises nothing: communication errors and Stop end up in the report; returns "done", "stopped"
    or "error"."""
    tail = "1 bit" if settings.tail_us == "auto" else f"{settings.tail_us:g} us"
    try:
        if settings.pair:
            return _run_pair(link, settings, report, line_port, ports or {}, tail)
        dut = Dut(link, settings.base, settings.clk, report.cancel)
        line = Line(line_port) if line_port is not None else None
        report.on_log(f"UART @ 0x{settings.base:08X}, ACLK {settings.clk / 1e6:g} MHz, "
                      f"{'PC adapter ' + settings.line if line else 'no PC adapter'}"
                      f"{', loopback' if settings.loopback else ''}")
        report.on_log(f"Line {settings.line_type}: DE {settings.de_mode}, {'half' if settings.half else 'full'} "
                      f"duplex, lead {settings.lead_us:g} us, tail {tail}")
        Validator(dut, line, report, settings).run()
    except Cancelled:
        report.add("INFO", "Stopped", "the run was stopped; the registers were restored")
        return "stopped"
    except LinkError as exc:
        report.add("FAIL", "Hexbus communication", str(exc))
        return "error"
    except SERIAL_ERRORS + (OSError,) as exc:
        report.add("FAIL", "Line adapter", str(exc))
        return "error"
    return "done"


def _run_pair(link, s, report, line_port, ports, tail):
    """4-wire pair: register tests on each IP, then the line tests with the stored roles and,
    unless switched off, again with the directions swapped."""
    pr = s.pair
    ips = {"a": (pr["a"], int(pr["a_base"])), "b": (pr["b"], int(pr["b_base"]))}
    first = pr.get("tx", "a")
    report.on_log(f"4-wire pair {ips['a'][0]} @ 0x{ips['a'][1]:08X} + {ips['b'][0]} @ 0x{ips['b'][1]:08X}, "
                  f"ACLK {s.clk / 1e6:g} MHz, TX DE {s.de_mode}, lead {s.lead_us:g} us, tail {tail}, "
                  f"PC: {s.pc}")
    # each IP's own registers
    for key in ("a", "b"):
        name, base = ips[key]
        dut = Dut(link, base, s.clk, report.cancel)
        v = Validator(dut, None, report, s)
        snap = dut.snapshot()
        try:
            report.section(f"Register tests {name}")
            v.test_registers()
        finally:
            dut.cancellable = False
            try:
                dut.restore(snap)
            except LinkError as exc:
                report.add("INFO", "Restore registers", f"could not restore the registers: {exc}")
            dut.cancellable = True
    passes = [first] + ([("b" if first == "a" else "a")] if s.swap else [])
    for n, tx in enumerate(passes):
        rx = "b" if tx == "a" else "a"
        (tx_name, tx_base), (rx_name, rx_base) = ips[tx], ips[rx]
        label = f"{tx_name} TX → {rx_name} RX"
        line, loop = None, False
        if s.pc == "one":
            if n > 0:
                report.section(label)
                report.add("SKIP", f"Swapped directions {label}",
                           "one 4-wire adapter cannot follow swapped directions (its pairs are fixed): "
                           "use one adapter per bus or the external loopback")
                continue
            line = Line(line_port) if line_port is not None else None
        elif s.pc == "two":
            wport, rport = ports.get(rx_name), ports.get(tx_name)     # PC sends on the RX IP's bus
            line = Line(wport, rport) if wport is not None and rport is not None else None
        else:
            loop = True                                               # FPGA bus to FPGA bus
        report.section(label)
        dut = PairDut(link, tx_base, rx_base, s.clk, report.cancel, (tx_name, rx_name))
        run = dataclasses.replace(s, loopback=loop)
        v = Validator(dut, line, report, run)
        v.section_prefix = label
        v.run()
    return "done"


def open_line(port):
    """Open the PC adapter on the UART line (the tests set its baud rate and format)."""
    if serial is None:
        raise LinkError("pyserial is not installed (pip install pyserial)")
    return serial.Serial(port, 9600, timeout=0.05)
