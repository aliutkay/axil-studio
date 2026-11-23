"""Client of the AXIL Studio IP interface service (protocol 1). One file, standard library only:
copy it next to an interface application, or rely on AXIL Studio, which puts this folder on the
PYTHONPATH of every interface it starts.

    from axil_client import AxilClient, AxilError

    client = AxilClient.from_env()          # AXIL Studio passes the port and the session token
    print(client.ip["name"], hex(client.ip["base"]))
    v = client.read(0x08)                   # offset from the IP's base address
    client.write(0x0C, 0x41)
    client.on_event = lambda ev: print(ev)  # "connection", "ip", "ip_removed", "theme", "raise", "closing"

Every call blocks until AXIL Studio answers; it is safe to call from several threads (the answers
are matched to the requests). Errors from Studio raise AxilError with a code:
    not_connected  the Debug Bridge is not connected to the board
    busy           another IP interface holds the connection (lock)
    bus            the bus access failed (timeout, AXI error ...)
    ip_removed     the IP is no longer in the address map
    bad_request, unknown_op, bad_token, no_session, closed
"""

import json
import os
import socket
import threading

PROTOCOL = 1
ENV_PORT, ENV_TOKEN, ENV_LANG = "AXIL_STUDIO_PORT", "AXIL_STUDIO_TOKEN", "AXIL_STUDIO_LANG"


class AxilError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


class AxilClient:
    def __init__(self, port, token, host="127.0.0.1", timeout=30.0, name=""):
        self.timeout = timeout
        self.sock = socket.create_connection((host, int(port)), timeout=10.0)
        self.sock.settimeout(None)
        self._wlock = threading.Lock()
        self._plock = threading.Lock()
        self._pending = {}          # id -> [event, response]
        self._next = 0
        self._closed = False
        self._on_event = None
        self._early = []            # events that came before on_event was set
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        hello = self.request("hello", token=token, protocol=PROTOCOL, client=name)
        self.protocol = hello.get("protocol")
        self.studio_version = hello.get("studio", "")
        self.ip = hello["ip"]
        self.connected = bool(hello.get("connected"))
        self.port = hello.get("port", "")
        self.lang = hello.get("lang", "en")
        self.theme = hello.get("theme", {})
        self.settings = hello.get("settings", {})

    @staticmethod
    def from_env(name=""):
        """Connect with the port and token AXIL Studio passed to this process."""
        port, token = os.environ.get(ENV_PORT), os.environ.get(ENV_TOKEN)
        if not port or not token:
            raise AxilError("no_studio", "This program is an AXIL Studio IP interface: open it from the "
                                         "UART-to-AXI Debug Bridge.")
        return AxilClient(port, token, name=name)

    # ------------------------------------------------------------ transport
    def _read_loop(self):
        buf = b""
        try:
            while True:
                data = self.sock.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(json.loads(line.decode("utf-8")))
        except (OSError, ValueError):
            pass
        closed_by_us = self._closed
        self._closed = True
        with self._plock:
            for slot in self._pending.values():
                if slot[1] is None:        # an answer that came before the close stays
                    slot[1] = {"ok": False, "error": {"code": "closed", "message": "AXIL Studio closed the connection"}}
                slot[0].set()
        if not closed_by_us:
            self._event({"event": "closing"})

    def _dispatch(self, msg):
        if "event" in msg:
            ev = msg["event"]
            if ev == "connection":
                self.connected = bool(msg.get("connected"))
                self.port = msg.get("port", "")
            elif ev == "ip":
                self.ip = msg.get("ip", self.ip)
            elif ev == "theme":
                self.theme = msg.get("theme", self.theme)
            self._event(msg)
            return
        with self._plock:
            slot = self._pending.get(msg.get("id"))
        if slot is not None:
            slot[1] = msg
            slot[0].set()

    @property
    def on_event(self):
        return self._on_event

    @on_event.setter
    def on_event(self, cb):
        """callable(dict), called on the reader thread. Events that came before it was set (e.g.
        "raise" or "closing" while the window was being built) are delivered now."""
        with self._plock:
            self._on_event = cb
            early, self._early = self._early, []
        for msg in early:
            self._event(msg)

    def _event(self, msg):
        with self._plock:
            cb = self._on_event
            if cb is None:
                self._early.append(msg)
                return
        try:
            cb(msg)
        except Exception:  # noqa: BLE001  (an interface bug must not stop the reader)
            pass

    def request(self, op, timeout=None, **args):
        if self._closed:
            raise AxilError("closed", "AXIL Studio closed the connection")
        with self._plock:
            self._next += 1
            rid = self._next
            slot = [threading.Event(), None]
            self._pending[rid] = slot
        data = (json.dumps(dict(args, id=rid, op=op)) + "\n").encode("utf-8")
        try:
            with self._wlock:
                self.sock.sendall(data)
            if not slot[0].wait(self.timeout if timeout is None else timeout):
                raise AxilError("timeout", f"no answer to '{op}'")
        finally:
            with self._plock:
                self._pending.pop(rid, None)
        msg = slot[1]
        if not msg.get("ok"):
            err = msg.get("error") or {}
            raise AxilError(err.get("code", "error"), err.get("message", ""))
        return msg.get("result")

    def close(self):
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    # ------------------------------------------------------------ register access
    @staticmethod
    def _where(offset, addr):
        return {"addr": addr} if addr is not None else {"offset": offset}

    def read(self, offset=0, addr=None, quiet=False):
        """Read a register: offset from the IP's base, or an absolute address."""
        return self.request("read", quiet=quiet, **self._where(offset, addr))

    def write(self, offset=0, value=0, addr=None, quiet=False):
        self.request("write", value=int(value) & 0xFFFFFFFF, quiet=quiet, **self._where(offset, addr))

    def batch(self, steps, quiet=False):
        """steps: [("read", offset) | ("write", offset, value)] run back to back; returns the values
        read (None for writes). Dicts with addr / offset / value work too."""
        ops = []
        for st in steps:
            if isinstance(st, dict):
                ops.append(st)
            elif st[0] == "read":
                ops.append({"op": "read", "offset": st[1]})
            else:
                ops.append({"op": "write", "offset": st[1], "value": int(st[2]) & 0xFFFFFFFF})
        return self.request("batch", ops=ops, quiet=quiet)

    def read_until(self, offset, mask, value, max_reads=256, addr=None, quiet=True):
        """Read one register up to max_reads times; stops after the first word with
        (word & mask) == value (e.g. a FIFO's EMPTY flag). Returns every word read."""
        return self.request("read_until", mask=mask, value=value, max=max_reads, quiet=quiet,
                            **self._where(offset, addr))

    # ------------------------------------------------------------ Debug Bridge
    def lock(self):
        """Hold the connection: the Debug Bridge's live watch stops and its controls are locked."""
        self.request("lock")

    def unlock(self):
        self.request("unlock")

    def show_register(self, register=None):
        """Select a register of this IP in the Debug Bridge and bring it to the front."""
        self.request("show_register", register=register)

    def add_config(self, rows):
        """rows: [(register name, value)] added to the Debug Bridge config list."""
        self.request("add_config", rows=[{"register": r, "value": int(v)} for r, v in rows])

    def status(self, text):
        self.request("status", text=str(text))

    def log(self, text):
        """A line in the Debug Bridge traffic log."""
        self.request("log", text=str(text))

    def get_settings(self, ip=None):
        """Values kept in the project for this IP instance, or (ip=name) for a peer."""
        if ip:
            return self.request("settings_get", ip=ip)
        self.settings = self.request("settings_get")
        return self.settings

    def set_settings(self, ip=None, **values):
        """Store values (JSON types) in the project for this IP instance, or (ip=name) for a peer;
        a value None removes the key."""
        if ip:
            self.request("settings_set", ip=ip, values=values)
            return
        self.request("settings_set", values=values)
        self.settings.update(values)
        for k in [k for k, v in values.items() if v is None]:
            self.settings.pop(k, None)

    def peers(self):
        """The other IP instances (same project) that use the same interface: their name, base,
        register map and stored settings. Their registers are reached by absolute address."""
        return self.request("peers")

    def refresh_ip(self):
        self.ip = self.request("ip")
        return self.ip


class Link:
    """read(addr) / write(addr, value) on absolute addresses through the client, for code written
    against a plain bus link."""

    def __init__(self, client, quiet=True):
        self.client, self.quiet = client, quiet

    def read(self, addr):
        return self.client.read(addr=addr, quiet=self.quiet)

    def write(self, addr, value):
        self.client.write(addr=addr, value=value, quiet=self.quiet)
