"""
Hexbus host link for the ZipCPU hexbus AXI4-Lite debug bridge.

Protocol (as implemented in dbgbus/hexbus/rtl):
  Host -> FPGA : 'A'<hex addr>  set address (bit0 = 1 -> no auto-increment)
                 'W'<hex data>  write 32-bit word
                 'R'            read 32-bit word
                 'T'            reset the bridge
                 A command is executed when the next non-hex character
                 arrives, so every request is terminated with '\n'.
  FPGA -> Host : 'A'+8 hex  address set     'K'+8 hex  write acknowledged
                 'R'+8 hex  read data       'E'        bus error
                 'T'        reset           'I'        interrupt
                 'Z'        idle            CR/LF      separators
"""

import queue
import threading
import time
from axil_studio.i18n import tr

try:
    import serial
    import serial.tools.list_ports
    SERIAL_IMPORT_ERROR = None
except ImportError as _exc:  # pragma: no cover
    serial = None
    SERIAL_IMPORT_ERROR = str(_exc)

SIM_PORT = "SIMULATOR"
_HEX = "0123456789abcdef"
_WORD_TOKENS = "RKAS"      # followed by up to 8 hex digits
_SINGLE_TOKENS = "TEIZ"    # stand-alone characters


class HexbusError(Exception):
    pass


def list_ports():
    ports = [SIM_PORT]
    if serial is not None:
        ports = [p.device for p in serial.tools.list_ports.comports()] + ports
    return ports


class HexbusLink:
    """Thread-safe request/response link to the hexbus bridge."""

    def __init__(self, log_cb=None, interrupt_cb=None, timeout=1.0):
        self.log_cb = log_cb or (lambda direction, text: None)
        self.interrupt_cb = interrupt_cb or (lambda: None)
        self.timeout = timeout
        self.ser = None
        self._lock = threading.RLock()
        self._rx = queue.Queue()
        self._reader = None
        self._running = False
        self._tok = None
        self._digits = ""
        self._rx_line = ""
        self._cur_addr = None   # address currently loaded in the bridge
        self.quiet = False      # suppress traffic logging (e.g. background polling)
        self._line_quiet = False

    # ------------------------------------------------------------ connection
    @property
    def connected(self):
        """False once the port is closed or the reader stopped (port removed ...)."""
        return self.ser is not None and self._running

    def _reset_rx(self):
        self._tok = None
        self._digits = ""
        self._rx_line = ""
        self._line_quiet = False
        while not self._rx.empty():
            try:
                self._rx.get_nowait()
            except queue.Empty:
                break

    def open(self, port, baud):
        self.close()
        self._cur_addr = None
        self._reset_rx()
        if port == SIM_PORT:
            self.ser = SimulatedHexbus()
        else:
            if serial is None:
                raise HexbusError("pyserial is not installed (pip install pyserial)")
            self.ser = serial.Serial(port, baud, bytesize=8, parity="N",
                                     stopbits=1, timeout=0.05)
        self._start_reader()

    def attach(self, ser):
        """Use an already opened pyserial-like object (e.g. a simulator)."""
        self.close()
        self._cur_addr = None
        self._reset_rx()
        self.ser = ser
        self._start_reader()

    def _start_reader(self):
        self._running = True
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    def close(self):
        self._running = False
        if self._reader is not None:
            self._reader.join(timeout=0.5)
            self._reader = None
        with self._lock:
            self._cur_addr = None
            if self.ser is not None:
                try:
                    self.ser.close()
                finally:
                    self.ser = None

    # ------------------------------------------------------------ receive path
    def _reader_loop(self):
        while self._running and self.ser is not None:
            try:
                data = self.ser.read(256)
            except Exception as exc:  # port removed, etc.
                self._running = False           # `connected` is False before anyone hears about it
                self.log_cb("ERR", tr("Serial read failed: {0}").format(exc))
                break
            for b in data:
                self._feed(chr(b & 0x7F))

    def _feed(self, c):
        if c in "\r\n":
            self._finish()
            if self._rx_line and not self._line_quiet:
                self.log_cb("RX", self._rx_line)
            self._rx_line = ""
            return
        if not self._rx_line:
            self._line_quiet = self.quiet   # quiet state when the line started
        self._rx_line += c
        if c in _WORD_TOKENS:
            self._finish()
            self._tok, self._digits = c, ""
        elif c in _SINGLE_TOKENS:
            self._finish()
            self._emit(c, None)
            if c == "Z":  # idle characters arrive without line breaks
                self._rx_line = self._rx_line[:-1]
        elif c in _HEX and self._tok is not None:
            self._digits += c
            if len(self._digits) == 8:
                self._finish()
        else:
            self._finish()

    def _finish(self):
        if self._tok is not None:
            value = int(self._digits, 16) if self._digits else 0
            self._emit(self._tok, value)
            self._tok, self._digits = None, ""

    def _emit(self, tok, value):
        if tok == "Z":
            self.log_cb("IDLE", "Z")
            return
        if tok == "I":
            self.interrupt_cb()
            return
        self._rx.put((tok, value))

    # ------------------------------------------------------------ transactions
    def _transact(self, text, count, what=None, log_text=None, drain_on_error=False):
        label = text.strip() if log_text is None else log_text
        with self._lock:
            if self.ser is None:
                raise HexbusError(tr("Not connected"))
            if not self._running:
                raise HexbusError(tr("Connection lost (serial port read failed); reconnect"))
            while not self._rx.empty():
                self._rx.get_nowait()
            if not self.quiet:
                self.log_cb("TX", label)
            try:
                self.ser.write(text.encode("ascii"))
            except Exception as exc:
                self._running = False
                raise HexbusError(tr("Serial write failed: {0}").format(exc)) from exc
            tokens = []
            deadline = time.monotonic() + self.timeout
            while len(tokens) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HexbusError(tr("Timeout waiting for the response to '{0}'").format(label))
                try:
                    tok = self._rx.get(timeout=remaining)
                except queue.Empty:
                    continue
                tokens.append(tok)
                if tok[0] == "E":
                    if drain_on_error:
                        while len(tokens) < count:
                            rem = min(0.05, deadline - time.monotonic())
                            if rem <= 0:
                                break
                            try:
                                tokens.append(self._rx.get(timeout=rem))
                            except queue.Empty:
                                break
                    if what:
                        raise HexbusError(tr("AXI bus error (SLVERR / DECERR) on {0}").format(what))
                    raise HexbusError(tr("AXI bus error on '{0}'").format(label))
            return tokens

    def reset(self):
        with self._lock:
            self._cur_addr = None
            tokens = self._transact("T\n", 1)
            if tokens[0][0] != "T":
                raise HexbusError(tr("Unexpected reset response {0}").format(tokens[0]))

    def _access(self, addr, cmd, expect):
        """Run one bus access. The address (no auto-increment) is only sent
        when it differs from the one already loaded in the bridge."""
        target = (addr & 0xFFFFFFFC) | 1
        with self._lock:
            prefix = "" if self._cur_addr == target else f"A{target:x}"
            try:
                what = (tr("read of 0x{0:08X}") if cmd == "R" else tr("write of 0x{0:08X}")).format(addr & 0xFFFFFFFC)
                tokens = self._transact(prefix + cmd + "\n", 2 if prefix else 1, what)
            except HexbusError:
                self._cur_addr = None
                raise
            if (prefix and tokens[0][0] != "A") or tokens[-1][0] != expect:
                self._cur_addr = None
                raise HexbusError(tr("Unexpected response {0}").format(tokens))
            self._cur_addr = target
            return tokens[-1][1]

    def write(self, addr, value):
        self._access(addr, f"W{value & 0xFFFFFFFF:x}", "K")

    def write_many(self, addr, values, window=32):
        """Write multiple 32-bit words to the same address using pipelined
        fixed-width hexbus writes (`W%08x\\n`, 10 bytes each) and verifying every
        `K` write acknowledgement.

        Fixed 8-digit zero-padded hex guarantees each incoming write (10 bytes)
        is much longer than the bridge's `K\\r\\n` reply (3 bytes), so the bridge
        TX UART drains well before the next command arrives even without a TX
        FIFO. When the bridge address changes, the first word is written with
        `write()` to drain the 11-byte `Axxxxxxxx\\r\\n` echo before pipelining.
        """
        vals = [int(v) & 0xFFFFFFFF for v in values]
        if not vals:
            return
        if len(vals) == 1:
            self.write(addr, vals[0])
            return
        aligned = addr & 0xFFFFFFFC
        target = aligned | 1
        what = tr("write of 0x{0:08X}").format(aligned)
        step = max(1, int(window))
        with self._lock:
            if self._cur_addr != target:
                self.write(addr, vals[0])
                start = 1
            else:
                start = 0
            for i in range(start, len(vals), step):
                chunk = vals[i:i + step]
                payload = "".join(f"W{v:08x}\n" for v in chunk)
                label = f"W x{len(chunk)} @ 0x{aligned:08X}"
                try:
                    tokens = self._transact(
                        payload, len(chunk), what,
                        log_text=label, drain_on_error=True,
                    )
                except HexbusError:
                    self._cur_addr = None
                    raise
                if any(tok[0] != "K" for tok in tokens):
                    self._cur_addr = None
                    raise HexbusError(tr("Unexpected response {0}").format(tokens))

    def read(self, addr):
        return self._access(addr, "R", "R")


def run_batch(link, steps):
    """Run [('read', addr, _), ('write', addr, value), ...] on `link`.

    Consecutive writes to the same address are coalesced into `link.write_many`
    when available, preserving exact execution order and returning `(out, reads)`.
    """
    out, reads = [], []
    i, n = 0, len(steps)
    has_many = hasattr(link, "write_many")
    while i < n:
        kind, addr, value = steps[i]
        if kind == "read":
            v = link.read(addr)
            out.append(v)
            reads.append((addr, v))
            i += 1
        else:
            j = i + 1
            if has_many:
                while j < n and steps[j][0] == "write" and steps[j][1] == addr:
                    j += 1
            if j - i >= 2:
                link.write_many(addr, [steps[k][2] for k in range(i, j)])
                out.extend([None] * (j - i))
            else:
                link.write(addr, value)
                out.append(None)
            i = j
    return out, reads


class SimulatedHexbus:
    """Offline model of hexbus + ZipCPU axilgpio at 0x60000000.

    Any other address behaves as plain 32-bit memory, which allows new IPs
    to be exercised without hardware.
    """

    GPIO_BASE = 0x60000000
    UART_BASE = 0x60001000   # axiluart_phy model, TX looped back to RX

    def __init__(self):
        self._out = bytearray()
        self._lock = threading.Lock()
        self._mem = {}
        self._gpio_out = 0
        self._changed = 0
        self._mask = 0
        self._last_in = self._gpio_in()
        self._reset_state()
        self._last_idle = time.monotonic()
        self._uart_setup = 0x400001B2
        self._uart_rx = []          # RXREG words: data | per-byte flags
        self._uart_phy = {0x10: 0xC2, 0x14: 0x00FA0032}
        self._uart_next_msg = time.monotonic() + 3.0

    def _reset_state(self):
        self._addr, self._inc, self._cmd, self._word = 0, True, None, 0

    @staticmethod
    def _gpio_in():
        return int(time.monotonic() * 2) & 0x1F

    # -- pyserial-like interface
    def write(self, data):
        with self._lock:
            for b in data:
                self._process(chr(b & 0x7F))
        return len(data)

    def read(self, n=1):
        time.sleep(0.01)
        with self._lock:
            if not self._out and time.monotonic() - self._last_idle > 2.0:
                self._out += b"Z"
                self._last_idle = time.monotonic()
            chunk, self._out = bytes(self._out[:n]), self._out[n:]
        return chunk

    def close(self):
        pass

    # -- protocol model
    def _respond(self, text):
        self._out += (text + "\r\n").encode("ascii")

    def _process(self, c):
        if c == "T":
            self._reset_state()
            self._respond("T")
        elif c in "RWAS":
            self._execute()
            self._cmd, self._word = c, 0
        elif c in _HEX:
            self._word = ((self._word << 4) | _HEX.index(c)) & 0xFFFFFFFF
        else:
            self._execute()
            self._cmd = None

    def _execute(self):
        cmd, word = self._cmd, self._word
        self._cmd = None
        if cmd == "A":
            if word & 2:
                self._addr = (self._addr + (word & ~3)) & 0xFFFFFFFC
            else:
                self._addr = word & 0xFFFFFFFC
            self._inc = not (word & 1)
            self._respond(f"A{self._addr | (0 if self._inc else 1):08x}")
        elif cmd == "W":
            self._bus_write(self._addr, word)
            self._respond("K00000000")
            if self._inc:
                self._addr = (self._addr + 4) & 0xFFFFFFFC
        elif cmd == "R":
            self._respond(f"R{self._bus_read(self._addr):08x}")
            if self._inc:
                self._addr = (self._addr + 4) & 0xFFFFFFFC

    def _update_inputs(self):
        now = self._gpio_in()
        self._changed |= now ^ self._last_in
        self._last_in = now

    def _uart_tick(self):
        now = time.monotonic()
        if now >= self._uart_next_msg:
            self._uart_next_msg = now + 5.0
            self._uart_rx += list(b"SIM: hello from UART0\r\n")
            del self._uart_rx[:-16 * 4]

    def _uart_write(self, off, value):
        if off == 0x00:
            self._uart_setup = (value & 0x3FFFFFFF) | 0x40000000
            self._uart_rx.clear()
        elif off == 0x0C and not value & 0x1200:
            self._uart_rx.append(value & 0xFF)
        elif off in (0x10, 0x14):
            self._uart_phy[off] = value & (0xFF if off == 0x10 else 0xFFFFFFFF)

    def _uart_read(self, off):
        self._uart_tick()
        if off == 0x00:
            return self._uart_setup
        if off == 0x04:
            n = min(len(self._uart_rx), 1023)
            return (4 << 28) | (16 << 18) | (3 << 16) | (4 << 12) | (n << 2) \
                | ((n >= 8) << 1) | (n > 0)
        if off == 0x08:
            if not self._uart_rx:
                return 0x100
            return self._uart_rx.pop(0) & 0x60FF
        if off == 0x18:
            return 0x0F
        return self._uart_phy.get(off, 0)

    def _bus_write(self, addr, value):
        uoff = addr - self.UART_BASE
        if 0 <= uoff < 0x20:
            self._uart_write(uoff, value)
            return
        off = addr - self.GPIO_BASE
        if 0 <= off < 0x20:
            self._update_inputs()
            if off == 0x00:
                self._gpio_out = value
            elif off == 0x04:
                self._gpio_out |= value
            elif off == 0x08:
                self._gpio_out &= ~value
            elif off == 0x0C:
                self._gpio_out ^= value
            elif off == 0x14:
                self._changed &= ~value
            elif off == 0x18:
                self._mask = value & 0x1F
            self._gpio_out &= 0x3FFFFFFF
        else:
            self._mem[addr] = value & 0xFFFFFFFF

    def _bus_read(self, addr):
        uoff = addr - self.UART_BASE
        if 0 <= uoff < 0x20:
            return self._uart_read(uoff)
        off = addr - self.GPIO_BASE
        if 0 <= off < 0x20:
            self._update_inputs()
            return {0x00: self._gpio_out, 0x04: self._gpio_out,
                    0x08: self._gpio_out, 0x0C: self._gpio_out,
                    0x10: self._last_in, 0x14: self._changed,
                    0x18: self._mask,
                    0x1C: self._changed & ~self._mask & 0x1F}[off & 0x1C]
        return self._mem.get(addr, 0)
