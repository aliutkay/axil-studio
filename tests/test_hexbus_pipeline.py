"""Unit tests for pipelined HexbusLink.write_many and run_batch coalescing."""

import threading
import time
import pytest

from axil_studio.core import hexbus as H


class _RecordingSerial:
    """In-memory hexbus responder that mirrors real hardware (3-byte `K\\r\\n`)
    and records every exact `ser.write(...)` call."""

    def __init__(self, *, reply_style="hw", error_on_write=None,
                 drop_write=None, bad_token_on_write=None,
                 stop_after_error=False):
        self._lock = threading.Lock()
        self._out = bytearray()
        self.tx_calls = []       # list of decoded ASCII strings per write() call
        self.writes = []         # list of (addr, value) observed on the bus
        self.mem = {}
        self._addr = 0
        self._inc = True
        self._cmd = None
        self._word = 0
        self._write_idx = 0
        self._reply_style = reply_style
        self._error_on_write = error_on_write
        self._drop_write = drop_write
        self._bad_token_on_write = bad_token_on_write
        self._stop_after_error = stop_after_error
        self._stopped = False

    def write(self, data):
        text = data.decode("ascii")
        with self._lock:
            self.tx_calls.append(text)
            for c in text:
                self._process(c)
        return len(data)

    def read(self, n=256):
        with self._lock:
            if self._out:
                chunk, self._out = bytes(self._out[:n]), self._out[n:]
                return chunk
        time.sleep(0.002)
        return b""

    def close(self):
        pass

    def _emit(self, text):
        self._out += text.encode("ascii")

    def _process(self, c):
        if c == "T":
            self._addr, self._inc, self._cmd, self._word = 0, True, None, 0
            self._emit("T\r\n")
        elif c in "RWAS":
            self._execute()
            self._cmd, self._word = c, 0
        elif c in H._HEX:
            self._word = ((self._word << 4) | H._HEX.index(c)) & 0xFFFFFFFF
        else:
            self._execute()
            self._cmd = None

    def _execute(self):
        cmd, word = self._cmd, self._word
        self._cmd = None
        if cmd == "A":
            self._addr = word & 0xFFFFFFFC
            self._inc = not (word & 1)
            self._emit(f"A{self._addr | (0 if self._inc else 1):08x}\r\n")
        elif cmd == "W":
            idx = self._write_idx
            self._write_idx += 1
            if self._stopped:
                return
            if self._error_on_write is not None and idx == self._error_on_write:
                self._emit("E\r\n")
                if self._stop_after_error:
                    self._stopped = True
                return
            if self._drop_write is not None and idx == self._drop_write:
                return
            if self._bad_token_on_write is not None and idx == self._bad_token_on_write:
                self._emit("Rdeadbeef\r\n")
                return
            self.writes.append((self._addr, word))
            self.mem[self._addr] = word
            if self._reply_style == "hw":
                self._emit("K\r\n")
            elif self._reply_style == "compact":
                self._emit("K")
            else:
                self._emit("K00000000\r\n")
            if self._inc:
                self._addr = (self._addr + 4) & 0xFFFFFFFC
        elif cmd == "R":
            val = self.mem.get(self._addr, 0)
            self._emit(f"R{val:08x}\r\n")
            if self._inc:
                self._addr = (self._addr + 4) & 0xFFFFFFFC


def test_write_many_compact_k_with_idle_and_interrupt():
    """Back-to-back `KKK\\r\\n` replies (plus interleaved `Z` idle / `I` IRQ)
    count every `K` accurately."""
    ser = _RecordingSerial(reply_style="compact")
    irqs = []
    link = H.HexbusLink(interrupt_cb=lambda: irqs.append(1), timeout=1.0)
    link.attach(ser)
    try:
        # Wrap write() to append Z, I, and trailing \r\n after each burst.
        orig_write = ser.write

        def burst_write(data):
            n = orig_write(data)
            with ser._lock:
                ser._out += b"ZI\r\n"
            return n

        ser.write = burst_write
        vals = list(range(1, 21))
        link.write_many(0x40000024, vals, window=8)
        assert [v for _a, v in ser.writes] == vals
        assert len(irqs) >= 1
    finally:
        link.close()


def test_write_many_wire_format_and_windowing():
    """First write drains the 11-byte A echo; remaining writes use fixed 10-byte
    `W%08x\\n` lines in chunks of `window`."""
    ser = _RecordingSerial(reply_style="hw")
    logged = []
    link = H.HexbusLink(log_cb=lambda d, t: logged.append((d, t)), timeout=1.0)
    link.attach(ser)
    try:
        # Include 0x0 and small values to verify zero-padding to 8 hex digits.
        vals = [0x12345678, 0x0, 0x5, 0xDEADBEEF] + [i * 0x101 for i in range(66)]
        assert len(vals) == 70
        link.write_many(0x40000024, vals, window=32)

        assert [v for _a, v in ser.writes] == vals
        assert all(a == 0x40000024 for a, _v in ser.writes)

        # Call 0: initial A + W0 (1 write); then 32 + 32 + 5 = 69 pipelined writes.
        assert len(ser.tx_calls) == 4
        assert ser.tx_calls[0] == "A40000025W12345678\n"

        for chunk_idx, expected_n in ((1, 32), (2, 32), (3, 5)):
            lines = ser.tx_calls[chunk_idx].splitlines(keepends=True)
            assert len(lines) == expected_n
            for line in lines:
                assert len(line) == 10, f"expected 10-byte W%08x\\n, got {line!r}"
                assert line.startswith("W") and line.endswith("\n")

        # Specifically check 0x0 and 0x5 are zero-padded to 8 hex digits
        first_chunk_lines = ser.tx_calls[1].splitlines(keepends=True)
        assert first_chunk_lines[0] == "W00000000\n"
        assert first_chunk_lines[1] == "W00000005\n"
        assert first_chunk_lines[2] == "Wdeadbeef\n"

        # A second write_many to the SAME address skips the initial A write!
        ser.tx_calls.clear()
        more = [0xAA000001, 0xAA000002, 0xAA000003]
        link.write_many(0x40000024, more, window=32)
        assert ser.tx_calls == ["Waa000001\nWaa000002\nWaa000003\n"]
        assert ser.mem[0x40000024] == 0xAA000003

        # Concise TX log entries (not 32 raw multiline lines)
        tx_logs = [t for d, t in logged if d == "TX"]
        assert "W x32 @ 0x40000024" in tx_logs
        assert "W x5 @ 0x40000024" in tx_logs
    finally:
        link.close()


def test_write_many_against_simulated_hexbus():
    """Works end-to-end with SimulatedHexbus (which replies `K00000000\\r\\n`)."""
    link = H.HexbusLink(timeout=2.0)
    link.open(H.SIM_PORT, 115200)
    try:
        vals = [i for i in range(100)]
        link.write_many(0x40000024, vals, window=32)
        assert link.read(0x40000024) == 99
    finally:
        link.close()


def test_write_many_empty_and_single():
    ser = _RecordingSerial(reply_style="hw")
    link = H.HexbusLink(timeout=1.0)
    link.attach(ser)
    try:
        link.write_many(0x40000024, [])
        assert ser.tx_calls == []
        link.write_many(0x40000024, [0x42])
        assert ser.tx_calls == ["A40000025W42\n"]
        assert ser.writes == [(0x40000024, 0x42)]
    finally:
        link.close()


def test_write_many_bus_error_drains_and_recovers():
    """A mid-window SLVERR (`E`) raises HexbusError, resets `_cur_addr`, drains
    in-flight trailing replies, and leaves the link clean for the next read."""
    ser = _RecordingSerial(reply_style="hw", error_on_write=3)
    link = H.HexbusLink(timeout=1.0)
    link.attach(ser)
    try:
        ser.mem[0x40000004] = 0xCAFEBABE
        with pytest.raises(H.HexbusError, match=r"0x40000024"):
            link.write_many(0x40000024, [1, 2, 3, 4, 5, 6, 7], window=32)
        assert link._cur_addr is None
        # Next read must succeed immediately with no stale `K` left in `_rx`.
        assert link.read(0x40000004) == 0xCAFEBABE
    finally:
        link.close()


def test_write_many_bus_error_when_target_stops_replying():
    """Even if the target stops sending after `E`, drain exits quickly and raises."""
    ser = _RecordingSerial(reply_style="hw", error_on_write=2, stop_after_error=True)
    link = H.HexbusLink(timeout=1.0)
    link.attach(ser)
    try:
        t0 = time.monotonic()
        with pytest.raises(H.HexbusError, match=r"0x40000024"):
            link.write_many(0x40000024, [1, 2, 3, 4, 5], window=32)
        assert time.monotonic() - t0 < 0.5
        assert link._cur_addr is None
    finally:
        link.close()


def test_write_many_unexpected_token_resets_addr():
    ser = _RecordingSerial(reply_style="hw", bad_token_on_write=2)
    link = H.HexbusLink(timeout=1.0)
    link.attach(ser)
    try:
        with pytest.raises(H.HexbusError):
            link.write_many(0x40000024, [1, 2, 3, 4], window=32)
        assert link._cur_addr is None
    finally:
        link.close()


def test_write_many_timeout_resets_addr():
    ser = _RecordingSerial(reply_style="hw", drop_write=2)
    link = H.HexbusLink(timeout=0.15)
    link.attach(ser)
    try:
        with pytest.raises(H.HexbusError, match="Timeout"):
            link.write_many(0x40000024, [1, 2, 3, 4], window=32)
        assert link._cur_addr is None
    finally:
        link.close()


def test_run_batch_coalesces_consecutive_same_address_writes():
    """run_batch groups >=2 consecutive writes to the same address into write_many,
    preserves order across mixed ops, and returns exact (out, reads)."""
    class _SpyLink:
        def __init__(self):
            self.calls = []
            self.mem = {0x10: 0x1111, 0x20: 0x2222}

        def read(self, addr):
            self.calls.append(("read", addr))
            return self.mem[addr]

        def write(self, addr, value):
            self.calls.append(("write", addr, value))
            self.mem[addr] = value

        def write_many(self, addr, values):
            self.calls.append(("write_many", addr, list(values)))
            for v in values:
                self.mem[addr] = v

    link = _SpyLink()
    steps = [
        ("write", 0x20, 0),
        ("write", 0x24, 1),
        ("write", 0x24, 2),
        ("write", 0x24, 3),
        ("read", 0x10, 0),
        ("write", 0x24, 4),
        ("write", 0x34, 5),
        ("write", 0x34, 6),
        ("read", 0x20, 0),
    ]
    out, reads = H.run_batch(link, steps)
    assert out == [None, None, None, None, 0x1111, None, None, None, 0]
    assert reads == [(0x10, 0x1111), (0x20, 0)]
    assert link.calls == [
        ("write", 0x20, 0),
        ("write_many", 0x24, [1, 2, 3]),
        ("read", 0x10),
        ("write", 0x24, 4),
        ("write_many", 0x34, [5, 6]),
        ("read", 0x20),
    ]


def test_run_batch_fallback_without_write_many():
    """Links without write_many (e.g., simple mocks) still run via write()."""
    class _PlainLink:
        def __init__(self):
            self.calls = []

        def read(self, addr):
            self.calls.append(("read", addr))
            return 0xABCD

        def write(self, addr, value):
            self.calls.append(("write", addr, value))

    link = _PlainLink()
    out, reads = H.run_batch(link, [
        ("write", 0x24, 10),
        ("write", 0x24, 20),
        ("read", 0x28, 0),
    ])
    assert out == [None, None, 0xABCD]
    assert reads == [(0x28, 0xABCD)]
    assert link.calls == [
        ("write", 0x24, 10),
        ("write", 0x24, 20),
        ("read", 0x28),
    ]
