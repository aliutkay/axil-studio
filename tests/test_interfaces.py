"""IP interfaces: manifest discovery, the Debug Bridge's local service and its client (SDK), a real
interface process, and the axiluart_phy interface (register helpers, validation logic against a
register-level stand-in of the IP, the window's form)."""

import json
import os
import secrets
import sys
import textwrap
import threading
import time

import pytest

from axil_studio import interfaces as ifc
from axil_studio.core import axilgen as G
from axil_studio.core.library import IPLibrary
from axil_studio.core.model import IPBlock, Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "uart_phy")
UART_DIR = os.path.join(ROOT, "interfaces", "axiluart_phy")
for p in (ifc.SDK_DIR, UART_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import axil_client  # noqa: E402
import uart_phy as U  # noqa: E402
import uart_validate as V  # noqa: E402

BASE = 0x80000000


def _manifest(folder, iid, script="app.py", title="Test", proto=1):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, ifc.MANIFEST), "w", encoding="utf-8") as fh:
        json.dump({"format": ifc.FORMAT, "protocol": proto, "id": iid, "version": "0.1",
                   "title": {"en": title, "tr": title + " TR"}, "command": ["{python}", script]}, fh)


# ---------------------------------------------------------------- discovery
def test_builtin_interface_is_found():
    found, errors = ifc.discover()
    assert not errors
    it = found["axiluart_phy"]
    assert it.source == "builtin" and it.label("tr") == "UART arayüzü" and it.label("en") == "UART interface"
    argv = it.argv()
    assert argv[0] == sys.executable and argv[1] == os.path.join(UART_DIR, "uart_ui.py")


def test_search_order_and_errors(tmp_path):
    mine, lib_dir = tmp_path / "mine", tmp_path / "libs"
    _manifest(str(mine / "uart_dev"), "axiluart_phy", title="Dev")
    _manifest(str(lib_dir / "deep" / "x"), "other")
    (lib_dir / "my.axlib.json").write_text("{}")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / ifc.MANIFEST).write_text(json.dumps({"format": ifc.FORMAT, "id": "nocmd"}))
    found, errors = ifc.discover([str(mine), str(tmp_path / "bad")], [str(lib_dir / "my.axlib.json")])
    assert found["axiluart_phy"].label() == "Dev" and found["axiluart_phy"].source == "settings"
    assert found["axiluart_phy"].shadowed == [UART_DIR]                # the built-in copy is not used
    assert found["other"].source == "library"
    assert len(errors) == 1 and "command" in errors[0]


# ---------------------------------------------------------------- the IP interface id
def test_panel_survives_project_library_and_spec():
    lib = IPLibrary.load(os.path.join(EX, "uart_phy.axlib.json"))
    ip = lib.get("axiluart_phy").definition("uart")
    assert ip.panel == U.PANEL_ID
    assert IPBlock.from_dict(ip.to_dict()).panel == U.PANEL_ID
    spec = G.spec_from_ipblock(ip)
    spec2 = G.ModuleSpec.from_dict(spec.to_dict())
    assert spec2.panel == U.PANEL_ID and G.to_ipblock(spec2, 0, with_spec=True).panel == U.PANEL_ID
    assert "panel" not in G.ModuleSpec("x").to_dict()
    proj = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    assert [(i.name, i.base, i.panel) for i in proj.address_ips()] == [("UART0", 0x60001000, U.PANEL_ID),
                                                                       ("UART1", 0x60002000, U.PANEL_ID)]


# ---------------------------------------------------------------- service and client
class FakeLink:
    """Stands in for the hexbus link of the Debug Bridge."""

    def __init__(self):
        self.mem = {}
        self.ops = []
        self.quiet = False
        self.connected = True

    def read(self, addr):
        self.ops.append(("r", addr))
        return self.mem.get(addr, 0x100 if addr & 0xFF == 0x08 else 0)

    def write(self, addr, value):
        self.ops.append(("w", addr, value))
        self.mem[addr] = value

    def close(self):
        self.connected = False


@pytest.fixture
def bridge(qapp):
    from axil_studio.interfaces.host import InterfaceService
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    page = DebugBridgePage(ctx)
    page.link.close()
    page.link = FakeLink()
    page._update_controls()
    page.service = InterfaceService(page)
    page.service._listen()
    yield page
    page.shutdown()


def _session(page, ip="UART0"):
    from axil_studio.interfaces.host import Session
    tok = secrets.token_hex(8)
    page.service.sessions[tok] = Session(tok, page.ifaces["axiluart_phy"], "project", ip)
    return tok


def _in_thread(qapp, fn, timeout=10):
    """Run a blocking client call while the GUI event loop (the service) keeps running."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc
    th = threading.Thread(target=run)
    th.start()
    end = time.time() + timeout
    while th.is_alive() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.002)
    assert not th.is_alive(), "client call did not finish"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def test_service_requests(qapp, bridge, no_dialogs):
    page = bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    assert c.ip["name"] == "UART0" and c.ip["base"] == 0x60001000 and c.connected and c.ip["panel"] == "axiluart_phy"
    assert {r["name"] for r in c.ip["registers"]} >= {"SETUP", "PHY_CTRL"}
    _in_thread(qapp, lambda: c.write(0x10, 0xC6))
    assert page.link.mem[0x60001010] == 0xC6
    assert _in_thread(qapp, lambda: c.read(0x10)) == 0xC6
    assert page.reads[0x60001010][0] == 0xC6                       # shown in the Debug Bridge views
    assert _in_thread(qapp, lambda: c.batch([("write", 0x14, 5), ("read", 0x14)])) == [None, 5]
    assert _in_thread(qapp, lambda: c.read(addr=0x60001014)) == 5
    words = _in_thread(qapp, lambda: c.read_until(0x08, 0x100, 0x100, 10))
    assert words == [0x100]
    # lock: the Debug Bridge's own access stops; another interface is refused
    _in_thread(qapp, c.lock)
    assert page._exclusive is not None and not page.conn_btn.isEnabled()
    c2 = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    with pytest.raises(axil_client.AxilError) as e:
        _in_thread(qapp, lambda: c2.read(0))
    assert e.value.code == "busy"
    _in_thread(qapp, c.unlock)
    assert page._exclusive is None
    # Debug Bridge helpers and settings kept in the project
    _in_thread(qapp, lambda: c.show_register("PHY_CTRL"))
    assert page.reg.name == "PHY_CTRL"
    n = len(page.config)
    _in_thread(qapp, lambda: c.add_config([("PHY_CTRL", 0xC2)]))
    assert page.config[n]["key"] == ("project", "UART0", "PHY_CTRL") and page.config[n]["value"] == 0xC2
    with pytest.raises(axil_client.AxilError):
        _in_thread(qapp, lambda: c.add_config([("NOPE", 1)]))
    _in_thread(qapp, lambda: c.set_settings(clk_mhz=100.0))
    assert page.ctx.project.debug["interfaces"]["UART0"]["clk_mhz"] == 100.0
    assert _in_thread(qapp, c.get_settings)["clk_mhz"] == 100.0
    # not connected
    page.link.connected = False
    with pytest.raises(axil_client.AxilError) as e:
        _in_thread(qapp, lambda: c.read(0))
    assert e.value.code == "not_connected"
    c.close()
    c2.close()


def test_events_reach_the_client(qapp, bridge):
    page = bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    events = []
    c.on_event = events.append
    page.link.connected = False
    page._update_controls()                         # connection_changed -> event
    page.ctx.mark_modified("test")                  # project change -> ip event
    end = time.time() + 5
    while len(events) < 2 and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    kinds = [e["event"] for e in events]
    assert "connection" in kinds and "ip" in kinds and not c.connected
    c.close()


def test_bad_token_is_refused(qapp, bridge):
    with pytest.raises(axil_client.AxilError) as e:
        _in_thread(qapp, lambda: axil_client.AxilClient(bridge.service.port, "nope"))
    assert e.value.code == "bad_token"


def test_interface_process_is_started_and_talks(qapp, tmp_path, monkeypatch):
    """A separate program found through the settings folders: started by the Debug Bridge, it
    writes a register through the service and exits."""
    from axil_studio.interfaces import host as H
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage
    folder = tmp_path / "probe"
    _manifest(str(folder), "probe_if", "probe.py")
    (folder / "probe.py").write_text(textwrap.dedent("""
        from axil_client import AxilClient
        c = AxilClient.from_env("probe")
        c.write(0x4, c.read(0x0) + 1)
        c.log("probe done " + c.ip["name"])
        c.close()
    """))
    monkeypatch.setattr(H, "settings_folders", lambda: [str(tmp_path)])
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    ctx.project.ips[0].panel = "probe_if"
    page = DebugBridgePage(ctx)
    page.link.close()
    page.link = FakeLink()
    page.link.mem[0x60001000] = 41
    page._update_controls()
    s = page.open_panel("project", "UART0")
    assert s is not None
    end = time.time() + 20
    while s.running and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert not s.running, s.stderr
    assert page.link.mem.get(0x60001004) == 42, s.stderr
    assert "probe done UART0" in page.log.toPlainText()
    page.shutdown()


def test_missing_interface_is_reported(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    ctx.project.ips[0].panel = "not_installed"
    page = DebugBridgePage(ctx)
    page.tree.setCurrentItem(page._find_item(("project", "UART0", "SETUP")))
    assert page.panel_btn.isVisibleTo(page)
    assert page.open_panel() is None and "not_installed" in shown[0]
    page.shutdown()


# ---------------------------------------------------------------- axiluart_phy helpers
def test_setup_round_trip():
    for text in ("8N1", "7E2", "6O1", "5M2", "8S1"):
        d = U.decode_setup(U.setup_value(50e6, 115200, U.Fmt(text)), 50e6)
        assert str(d["fmt"]) == text and d["div"] == 434 and d["no_flow"]
    assert U.setup_value(50e6, 115200, U.Fmt("8N1")) == 0x400001B2         # the IP's INITIAL_SETUP
    with pytest.raises(ValueError):
        U.Fmt("9N1")
    with pytest.raises(ValueError):
        U.setup_value(1e3, 1e6, U.Fmt("8N1"))


def test_line_presets_match_the_rtl_readme():
    assert {k: U.line_ctrl(k) for k in U.LINE_TYPES} == {
        "ttl": 0xC0, "rs232": 0xC0, "rs422": 0xC1, "rs485-4w": 0xC2, "rs485-2w": 0xC6, "rs485-2w-echo": 0xCE}
    assert U.line_type_of(0xCE) == "rs485-2w-echo"
    t = U.timing_value(50e6, 1.0, 5.0)
    assert t == 0x00FA0032 and U.decode_timing(t, 50e6)["lead"] == 50


def test_terminal_parsers():
    assert U.parse_hex_bytes("41 42 0x43,4") == b"ABC\x04"
    assert U.parse_ascii(r"a\r\n\x41\t\\") == b"a\r\nA\t\\"
    s = U.LineSplitter("auto")
    assert [s.action(b) for b in b"a\r\nb\n"] == [None, "nl", "skip", None, "nl"]


def test_library_register_map_matches_the_offsets():
    ip = IPLibrary.load(os.path.join(EX, "uart_phy.axlib.json")).get("axiluart_phy").ip
    assert {r.name: r.offset for r in ip.registers} == U.OFFSETS
    for r in ip.registers:
        used = 0
        for f in r.fields:
            m = ((1 << f.width) - 1) << f.lsb
            assert not used & m, (r.name, f.name)
            used |= m


# ---------------------------------------------------------------- validation logic
class FakeUart:
    """Register-level stand-in of axiluart_phy: SETUP / PHY registers, CFG_ERR on an invalid DE
    mode and (loopback) TX bytes looped back to RX while TX_EN is set. No line timing, no DE pin."""

    def __init__(self, caps=U.ST_HAS_TX | U.ST_HAS_RX, loopback=False):
        self.regs = {U.SETUP: 0x400001B2, U.PHY_CTRL: 0xC0, U.PHY_TIMING: 0x00FA0032}
        self.caps, self.loopback = caps, loopback
        self.cfg_err = False
        self.rx, self.tx_hold = [], []

    def _release(self):
        if self.regs[U.PHY_CTRL] & U.CTRL_TX_EN and self.loopback:
            self.rx += self.tx_hold
            self.tx_hold = []

    def read(self, addr):
        o = addr - BASE
        if o == U.FIFO:
            return (min(len(self.rx), 0x3FF) << 2) | (4 << 12) | ((16 - len(self.tx_hold)) << 18) | (4 << 28)
        if o == U.RXREG:
            return self.rx.pop(0) if self.rx else U.RX_EMPTY
        if o == U.PHY_STATUS:
            return self.caps | (U.ST_CFG_ERR if self.cfg_err else 0)
        return self.regs.get(o, 0)

    def write(self, addr, v):
        o = addr - BASE
        if o == U.SETUP:
            self.regs[o] = v | U.SETUP_NO_FLOW
            self.rx.clear()
        elif o == U.PHY_CTRL:
            if v & 3 == 3 or (v & 3 and not self.caps & U.ST_HAS_DE):
                self.cfg_err = True
                return
            self.regs[o] = v & 0xFF
            self._release()
        elif o == U.PHY_TIMING:
            self.regs[o] = v
        elif o == U.PHY_STATUS and v & U.ST_CFG_ERR:
            self.cfg_err = False
        elif o == U.TXREG:
            self.tx_hold.append(v & 0xFF)
            self._release()
        elif o == U.RXREG and v & U.RX_OVFL:
            self.rx.clear()


def _settings(**kw):
    kw.setdefault("bauds", [115200])
    kw.setdefault("formats", "8N1")
    return V.Settings(base=BASE, line_type="rs232", **kw)


def test_register_tests_pass_and_registers_are_restored():
    dev = FakeUart()
    rep = V.Report()
    assert V.run_validation(dev, _settings(), rep) == "done"
    assert rep.counts()["FAIL"] == 0 and rep.counts()["PASS"] >= 8, rep.rows
    assert dev.regs[U.SETUP] == 0x400001B2 and dev.regs[U.PHY_CTRL] == 0xC0


def test_loopback_data_tests():
    dev = FakeUart(loopback=True)
    rep = V.Report()
    assert V.run_validation(dev, _settings(loopback=True, formats="8N1,7E1", count=20), rep) == "done"
    names = {r["test"]: r["status"] for r in rep.rows}
    assert names["Loopback data 115200 8N1"] == "PASS" and names["Loopback data 115200 7E1"] == "PASS"
    assert rep.counts()["FAIL"] == 0, [r for r in rep.rows if r["status"] == "FAIL"]


def test_link_errors_and_stop():
    class Broken(FakeUart):
        def read(self, addr):
            raise V.LinkError("bus timeout")
    rep = V.Report()
    assert V.run_validation(Broken(), _settings(), rep) == "error"
    dev = FakeUart(loopback=True)
    cancel = threading.Event()
    rep = V.Report(cancel=cancel)
    calls = {"n": 0}
    orig = dev.read

    def read(a):
        calls["n"] += 1
        if calls["n"] == 40:
            cancel.set()
        return orig(a)
    dev.read = read
    assert V.run_validation(dev, _settings(loopback=True), rep) == "stopped"
    assert dev.regs[U.SETUP] == 0x400001B2 and dev.regs[U.PHY_CTRL] == 0xC0      # restored after Stop


def test_validation_settings():
    s = V.Settings(bauds="9600 115200", formats="8N1, 7E2", tail_us="3.5", line_type="rs485-2w-echo")
    assert s.bauds == [9600, 115200] and s.tail_us == 3.5 and s.half and s.echo and s.de_mode == "tx"
    assert V.Settings.from_dict(json.loads(json.dumps(s.to_dict()))).to_dict() == s.to_dict()
    with pytest.raises(ValueError):
        V.Settings(line_type="can")
    with open(os.path.join(EX, "bench_rs485_4w.json"), encoding="utf-8") as fh:
        assert V.Settings.from_dict(json.load(fh)).line_type == "rs485-4w"


# ---------------------------------------------------------------- UART window
def test_uart_window_form(qapp, bridge, no_dialogs):
    import look
    import uart_ui
    page = bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    look.use_language("en")
    w = uart_ui.UartWindow(c)
    w.baud.setCurrentText("9600")
    w.parity.setCurrentIndex(w.parity.findData("E"))
    assert w._setup_value() == U.setup_value(50e6, 9600, U.Fmt("8E1"))
    w.line_type.setCurrentIndex(w.line_type.findData("rs485-2w-echo"))
    assert w._ctrl_value() == 0xCE
    w._set_line_form(0xC1, 0x00FA0032)
    assert w.line_type.currentData() == "rs422" and abs(w.lead.value() - 1.0) < 1e-6
    w._set_setup_form(U.setup_value(50e6, 115200, U.Fmt("7O2")))
    assert w.baud.currentText() == "115200" and str(w._fmt()) == "7O2"
    # writes go through the Debug Bridge link
    w.write_line()
    end = time.time() + 5
    while 0x60001010 not in page.link.mem and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert page.link.mem[0x60001010] == U.line_ctrl("rs422") and page.link.mem[0x60001014] == 0x00FA0032
    assert _pump_until(qapp, lambda: w.bus.pending == 0)
    _dispose(qapp, w, c)


# ---------------------------------------------------------------- 4-wire pair
def test_peers_and_peer_settings(qapp, bridge):
    page = bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    peers = _in_thread(qapp, c.peers)
    assert [(p["name"], p["base"]) for p in peers] == [("UART1", 0x60002000)]
    _in_thread(qapp, lambda: c.set_settings(ip="UART1", pair={"peer": "UART0", "role": "rx"}))
    assert page.ctx.project.debug["interfaces"]["UART1"]["pair"]["role"] == "rx"
    assert _in_thread(qapp, lambda: c.get_settings(ip="UART1"))["pair"]["peer"] == "UART0"
    _in_thread(qapp, lambda: c.set_settings(ip="UART1", pair=None))
    assert "pair" not in page.ctx.project.debug["interfaces"]["UART1"]
    with pytest.raises(axil_client.AxilError):
        _in_thread(qapp, lambda: c.set_settings(ip="NOPE", x=1))
    c.close()


def _dispose(qapp, w, c):
    """Close an interface window and delete it now (not at interpreter exit)."""
    from PySide6.QtCore import QEvent
    w.close()
    w.bus.shutdown()
    c.close()
    w.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _pump_until(qapp, cond, t=5):
    end = time.time() + t
    while not cond() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)
    return cond()


def test_pair_window(qapp, bridge, no_dialogs, monkeypatch):
    import look
    import uart_ui
    page = bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    look.use_language("en")
    w = uart_ui.UartWindow(c)
    assert _pump_until(qapp, lambda: w.peers_list)
    w.mode_cb.setCurrentIndex(1)
    w.role_cb.setCurrentIndex(w.role_cb.findData("tx"))
    w.txde_cb.setCurrentIndex(w.txde_cb.findData("high"))
    w.make_pair()
    assert _pump_until(qapp, lambda: w.paired)
    def ifs():
        return page.ctx.project.debug["interfaces"]
    assert ifs()["UART0"]["pair"] == {"peer": "UART1", "role": "tx", "tx_de": "high"}
    assert ifs()["UART1"]["pair"] == {"peer": "UART0", "role": "rx", "tx_de": "high"}
    assert w._bases() == (0x60001000, 0x60002000) and not w.line_type.isEnabled()
    # write all: timing, receiver first, then the driver, then SETUP on both
    page.link.ops.clear()
    w.write_all()
    assert _pump_until(qapp, lambda: ("w", 0x60002000, w._setup_value()) in page.link.ops)
    ctrl = [op for op in page.link.ops if op[0] == "w" and op[1] & 0xFF == U.PHY_CTRL]
    assert ctrl[0] == ("w", 0x60002010, U.CTRL_RX_EN) and ctrl[1] == ("w", 0x60001010, U.CTRL_TX_EN | U.CTRL_DE_HIGH)
    # swap: UART1 drives now; UART0 (the new receiver) is set first
    page.link.ops.clear()
    w.swap_directions()
    assert _pump_until(qapp, lambda: ifs()["UART0"]["pair"]["role"] == "rx"
                       and ifs()["UART1"]["pair"]["role"] == "tx" and w.pair["role"] == "rx")
    ctrl = [op for op in page.link.ops if op[0] == "w" and op[1] & 0xFF == U.PHY_CTRL]
    assert ctrl[0] == ("w", 0x60001010, U.CTRL_RX_EN) and ctrl[1] == ("w", 0x60002010, U.CTRL_TX_EN | U.CTRL_DE_HIGH)
    assert ifs()["UART1"]["pair"]["role"] == "tx" and w._bases() == (0x60002000, 0x60001000)
    # validation settings of the pair
    w.v_pc.setCurrentIndex(w.v_pc.findData("none"))
    s = w._val_settings()
    assert s.pair == {"a": "UART0", "a_base": 0x60001000, "b": "UART1", "b_base": 0x60002000, "tx": "b"}
    assert s.pc == "none" and s.line_type == "rs422" and not s.loopback
    # unpair
    w.unpair()
    assert _pump_until(qapp, lambda: not ifs()["UART0"].get("pair") and not ifs()["UART1"].get("pair"))
    assert _pump_until(qapp, lambda: w.bus.pending == 0)
    _dispose(qapp, w, c)


class _Bus:
    """Two FakeUart-like channels whose buses are wired to each other (external loopback)."""

    class Ch(FakeUart):
        def _release(self):
            c = self.regs[U.PHY_CTRL]
            if c & U.CTRL_TX_EN and c & 3 and self.peer.regs[U.PHY_CTRL] & U.CTRL_RX_EN:
                self.peer.rx += self.tx_hold
                self.tx_hold = []

        def write(self, addr, v):
            super().write(addr, v)
            if addr - BASE == U.PHY_CTRL:
                self.peer._release()

    def __init__(self):
        self.a, self.b = self.Ch(caps=0x0F), self.Ch(caps=0x0F)
        self.a.peer, self.b.peer = self.b, self.a

    def _ch(self, addr):
        return (self.a, addr - 0x1000) if addr < 0x2000 else (self.b, addr - 0x2000)

    def read(self, addr):
        ch, off = self._ch(addr)
        return ch.read(BASE + off)

    def write(self, addr, v):
        ch, off = self._ch(addr)
        ch.write(BASE + off, v)


def test_pair_validation_both_directions():
    bus = _Bus()
    s = V.Settings(base=0x1000, line_type="rs422", bauds=[115200], formats="8N1", count=16,
                   pair={"a": "UART0", "a_base": 0x1000, "b": "UART1", "b_base": 0x2000, "tx": "a"}, pc="none")
    rep = V.Report()
    assert V.run_validation(bus, s, rep) == "done"
    names = {(r["section"].split(" · ")[0], r["test"]): r["status"] for r in rep.rows}
    assert names[("UART0 TX → UART1 RX", "Loopback data 115200 8N1")] == "PASS"
    assert names[("UART1 TX → UART0 RX", "Loopback data 115200 8N1")] == "PASS"
    assert names[("Register tests UART1", "FIFO depth")] == "PASS"
    assert bus.a.regs[U.PHY_CTRL] == 0xC0 and bus.b.regs[U.PHY_CTRL] == 0xC0      # restored
    # one 4-wire adapter cannot follow swapped directions
    rep = V.Report()
    V.run_validation(_Bus(), V.Settings(**dict(s.__dict__, pc="one")), rep)
    assert any(r["status"] == "SKIP" and r["test"].startswith("Swapped directions") for r in rep.rows)


def test_split_ctrl():
    tx, rx = V.PairDut.split_ctrl(U.CTRL_TX_EN | U.CTRL_RX_EN | U.CTRL_DE_TX | U.CTRL_TX_INV | U.CTRL_RX_INV)
    assert tx == U.CTRL_TX_EN | U.CTRL_DE_TX | U.CTRL_TX_INV and rx == U.CTRL_RX_EN | U.CTRL_RX_INV
