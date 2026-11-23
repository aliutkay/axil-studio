"""axiluart_phy: register layout, line formats, line presets and terminal helpers.

axiluart_phy (hdl/uart_phy) is the ZipCPU axiluart (patched as axiluart_v2) with a line
interface adapter: DE / /RE sequencing for RS-422 / RS-485 transceivers, half duplex, echo,
polarity and TX / RX enable. This module holds everything the UART interface and the hardware
validation need that does not touch Qt or a serial port. The register map itself comes from
AXIL Studio (the IP's register map, examples/uart_phy/uart_phy.axlib.json).
"""

import re

PANEL_ID = "axiluart_phy"

# ---------------------------------------------------------------- register offsets
SETUP, FIFO, RXREG, TXREG = 0x00, 0x04, 0x08, 0x0C
PHY_CTRL, PHY_TIMING, PHY_STATUS = 0x10, 0x14, 0x18
OFFSETS = {"SETUP": SETUP, "FIFO": FIFO, "RXREG": RXREG, "TXREG": TXREG,
           "PHY_CTRL": PHY_CTRL, "PHY_TIMING": PHY_TIMING, "PHY_STATUS": PHY_STATUS}

# RXREG
RX_EMPTY, RX_PERR, RX_FERR, RX_BREAK, RX_OVFL = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12
RX_BYTE_PERR, RX_BYTE_FERR = 1 << 13, 1 << 14          # flags of this byte (axiluart_v2)
RX_ERRORS = RX_BYTE_PERR | RX_BYTE_FERR | RX_OVFL | RX_PERR | RX_FERR | RX_BREAK
# TXREG
TX_BREAK, TX_RESET = 1 << 9, 1 << 12
# FIFO
TX_FREE_SHIFT, TX_FREE_MASK = 18, 0x3FF
# SETUP
SETUP_BAUD_MASK = 0xFFFFFF
SETUP_NO_FLOW = 1 << 30
# PHY_CTRL
CTRL_DE_LOW, CTRL_DE_HIGH, CTRL_DE_TX = 0, 1, 2
CTRL_HALF, CTRL_ECHO, CTRL_TX_INV, CTRL_RX_INV = 1 << 2, 1 << 3, 1 << 4, 1 << 5
CTRL_TX_EN, CTRL_RX_EN, CTRL_APPLY_NOW = 1 << 6, 1 << 7, 1 << 31
# PHY_STATUS
ST_HAS_TX, ST_HAS_RX, ST_HAS_DE, ST_HAS_RE, ST_HAS_FLOW = 1, 2, 4, 8, 16
ST_DRIVING, ST_DE, ST_RE_N, ST_PENDING, ST_CFG_ERR = 1 << 8, 1 << 9, 1 << 10, 1 << 11, 1 << 12

DE_CODES = {"low": CTRL_DE_LOW, "high": CTRL_DE_HIGH, "tx": CTRL_DE_TX}
DE_NAMES = {v: k for k, v in DE_CODES.items()}

# Line types: DE mode and duplex. The PHY_CTRL value is the one in the README table.
LINE_TYPES = {
    "ttl":      dict(de="low",  half=False, echo=False, label="TTL / LVCMOS",
                     desc="No transceiver enable"),
    "rs232":    dict(de="low",  half=False, echo=False, label="RS-232",
                     desc="Full duplex, no transceiver enable"),
    "rs422":    dict(de="high", half=False, echo=False, label="RS-422 point-to-point",
                     desc="Driver always on"),
    "rs485-4w": dict(de="tx",   half=False, echo=False, label="RS-422 multidrop / RS-485 4-wire",
                     desc="Driver on while transmitting"),
    "rs485-2w": dict(de="tx",   half=True,  echo=False, label="RS-485 2-wire",
                     desc="Shared bus, half duplex: own transmission not received"),
    "rs485-2w-echo": dict(de="tx", half=True, echo=True, label="RS-485 2-wire with echo",
                          desc="Half duplex, own transmission received (collision check, LIN)"),
}

PARITIES = {"N": 0, "O": 0b100, "E": 0b101, "S": 0b110, "M": 0b111}
PARITY_OF = {v: k for k, v in PARITIES.items()}


# ---------------------------------------------------------------- line format
class Fmt:
    """Character format such as 8N1, 7E1, 8M2: data bits 5-8, parity N/E/O/M/S, stop bits 1-2."""

    def __init__(self, text):
        m = re.fullmatch(r"([5-8])([NEOMS])([12])", str(text).strip().upper())
        if not m:
            raise ValueError(f"invalid format '{text}' (e.g. 8N1, 7E1, 8O2)")
        self.text = m.group(0)
        self.data, self.parity, self.stop = int(m.group(1)), m.group(2), int(m.group(3))

    def __str__(self):
        return self.text

    def __eq__(self, other):
        return isinstance(other, Fmt) and other.text == self.text

    def __hash__(self):
        return hash(self.text)

    @property
    def mask(self):
        return (1 << self.data) - 1

    @property
    def bits(self):
        """Bits per character on the line."""
        return 1 + self.data + (self.parity != "N") + self.stop

    def setup_bits(self):
        """SETUP[29:24]: data bits, stop bits and parity."""
        return ((8 - self.data) << 28) | ((self.stop == 2) << 27) | (PARITIES[self.parity] << 24)

    def parity_bit(self, value):
        """The parity bit sent with `value` (None without parity)."""
        ones = bin(value & self.mask).count("1") & 1
        return {"E": ones, "O": ones ^ 1, "M": 1, "S": 0}.get(self.parity)

    def with_parity(self, parity):
        return Fmt(f"{self.data}{parity}{self.stop}")


def parse_formats(text):
    """'8N1, 7E1' -> [Fmt, Fmt]"""
    return [Fmt(t) for t in re.split(r"[\s,;]+", str(text).strip()) if t]


def parse_bauds(text):
    """'9600, 115200' -> [9600, 115200]"""
    out = []
    for t in re.split(r"[\s,;]+", str(text).strip()):
        if t:
            v = int(float(t))
            if v <= 0:
                raise ValueError(f"invalid baud rate '{t}'")
            out.append(v)
    return out


# ---------------------------------------------------------------- SETUP
def setup_value(clk_hz, baud, fmt, no_flow=True):
    """SETUP word for a baud rate and format: [23:0] = ACLK / baud."""
    div = round(clk_hz / baud)
    if not 1 <= div <= SETUP_BAUD_MASK:
        raise ValueError(f"baud rate {baud} is out of range for ACLK {clk_hz / 1e6:g} MHz")
    return div | fmt.setup_bits() | (SETUP_NO_FLOW if no_flow else 0)


def decode_setup(value, clk_hz=None):
    """dict(div, fmt, no_flow, baud): baud only when clk_hz is given."""
    div = value & SETUP_BAUD_MASK
    data = 8 - ((value >> 28) & 3)
    stop = 2 if value >> 27 & 1 else 1
    par = PARITY_OF.get((value >> 24) & 0b111, "N")      # 0b000..0b011: parity off
    out = dict(div=div, fmt=Fmt(f"{data}{par}{stop}"), no_flow=bool(value & SETUP_NO_FLOW))
    if clk_hz:
        out["baud"] = clk_hz / div if div else 0.0
    return out


def baud_error(clk_hz, baud):
    """Relative error (%) of the baud rate the divider really gives."""
    div = round(clk_hz / baud)
    return (clk_hz / div - baud) / baud * 100 if div else 100.0


# ---------------------------------------------------------------- PHY_CTRL / PHY_TIMING
def ctrl_value(de="low", half=False, echo=False, tx_inv=False, rx_inv=False, tx_en=True, rx_en=True):
    v = DE_CODES[de]
    v |= CTRL_HALF if half else 0
    v |= CTRL_ECHO if echo and half else 0
    v |= CTRL_TX_INV if tx_inv else 0
    v |= CTRL_RX_INV if rx_inv else 0
    v |= CTRL_TX_EN if tx_en else 0
    v |= CTRL_RX_EN if rx_en else 0
    return v


def decode_ctrl(value):
    return dict(de=DE_NAMES.get(value & 3, "invalid"), half=bool(value & CTRL_HALF), echo=bool(value & CTRL_ECHO),
                tx_inv=bool(value & CTRL_TX_INV), rx_inv=bool(value & CTRL_RX_INV),
                tx_en=bool(value & CTRL_TX_EN), rx_en=bool(value & CTRL_RX_EN))


def line_ctrl(line_type, de=None):
    """PHY_CTRL of a line type (TX and RX enabled); `de` overrides its DE mode."""
    lt = LINE_TYPES[line_type]
    return ctrl_value(de or lt["de"], lt["half"], lt["echo"])


def line_type_of(ctrl):
    """The line type whose PHY_CTRL is `ctrl` (polarity and enables ignored), or None."""
    want = ctrl & (3 | CTRL_HALF | CTRL_ECHO)
    for key in LINE_TYPES:
        if line_ctrl(key) & (3 | CTRL_HALF | CTRL_ECHO) == want:
            return key
    return None


def cycles(us, clk_hz):
    """Microseconds -> ACLK cycles, limited to the 16-bit PHY_TIMING fields."""
    return max(0, min(0xFFFF, round(us * 1e-6 * clk_hz)))


def timing_value(clk_hz, lead_us, tail_us):
    """PHY_TIMING: [15:0] lead (DE on -> start bit), [31:16] tail (stop bit end -> DE off)."""
    return (cycles(tail_us, clk_hz) << 16) | cycles(lead_us, clk_hz)


def decode_timing(value, clk_hz):
    lead, tail = value & 0xFFFF, value >> 16 & 0xFFFF
    return dict(lead=lead, tail=tail, lead_us=lead / clk_hz * 1e6, tail_us=tail / clk_hz * 1e6)


def status_names(value):
    """Capability and state flags of PHY_STATUS that are set."""
    names = [("HAS_TX", ST_HAS_TX), ("HAS_RX", ST_HAS_RX), ("HAS_DE", ST_HAS_DE), ("HAS_RE", ST_HAS_RE),
             ("HAS_FLOW", ST_HAS_FLOW), ("DRIVING", ST_DRIVING), ("DE", ST_DE), ("RE_N", ST_RE_N),
             ("PENDING", ST_PENDING), ("CFG_ERR", ST_CFG_ERR)]
    return [n for n, b in names if value & b]


def decode_fifo(value):
    return dict(rx_avail=bool(value & 1), rx_fill=(value >> 2) & 0x3FF, rx_lg=(value >> 12) & 0xF,
                tx_space=bool(value >> 16 & 1), tx_free=(value >> TX_FREE_SHIFT) & TX_FREE_MASK,
                tx_lg=(value >> 28) & 0xF)


def flag_names(v):
    names = [(RX_PERR, "PERR"), (RX_FERR, "FERR"), (RX_BREAK, "BREAK"), (RX_OVFL, "OVERFLOW"),
             (RX_BYTE_PERR, "BYTE_PERR"), (RX_BYTE_FERR, "BYTE_FERR")]
    return " ".join(n for b, n in names if v & b) or "none"


# ---------------------------------------------------------------- terminal input
TX_ENDINGS = {"CR+LF": b"\r\n", "LF": b"\n", "CR": b"\r"}


def parse_hex_bytes(text):
    """'41 42 0x43,44' -> b'ABCD'. Raises ValueError on malformed input."""
    out = bytearray()
    for tok in (t for t in re.split(r"[\s,;:]+", text.strip()) if t):
        tok = tok[2:] if tok.lower().startswith("0x") else tok
        if not re.fullmatch(r"[0-9a-fA-F]+", tok):
            raise ValueError(f"invalid hex: {tok}")
        if len(tok) % 2:
            tok = "0" + tok
        out += bytes.fromhex(tok)
    return bytes(out)


def parse_ascii(text, escapes=True):
    """Text -> bytes (latin-1); with escapes: \\n \\r \\t \\0 \\e \\\\ and \\xNN."""
    if not escapes:
        return text.encode("latin-1", errors="replace")
    out = bytearray()
    simple = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, "e": 27}
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in simple:
                out.append(simple[n])
                i += 2
                continue
            if n == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", text[i + 2:i + 4]):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
        out += c.encode("latin-1", errors="replace")
        i += 1
    return bytes(out)


class LineSplitter:
    """Where received (or sent) text breaks into lines.
    mode "auto": CR, LF and CR+LF each end one line; "LF", "CR", "CR+LF": only that ending."""

    def __init__(self, mode="auto"):
        self.mode = mode
        self.prev_cr = False

    def action(self, b):
        """'nl' to break the line, 'skip' to hide the byte, None to show it."""
        prev_cr, self.prev_cr = self.prev_cr, b == 13
        m = self.mode
        if m == "auto":
            if b == 13:
                return "nl"
            if b == 10:
                return "skip" if prev_cr else "nl"
        elif m == "LF":
            if b == 10:
                return "nl"
            if b == 13:
                return "skip"
        elif m == "CR":
            if b == 13:
                return "nl"
            if b == 10:
                return "skip"
        elif m == "CR+LF":
            if b == 13:
                return "skip"
            if b == 10 and prev_cr:
                return "nl"
        return None
