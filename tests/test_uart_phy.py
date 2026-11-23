"""axiluart_phy support: register layout helpers, the IP interface field, hardware validation
logic (against a register-level stand-in of the IP) and the UART interface window."""

import json
import os
import threading

import pytest

from axil_studio.core import axilgen as G
from axil_studio.core import uart_phy as U
from axil_studio.core import uart_validate as V
from axil_studio.core.library import IPLibrary
from axil_studio.core.model import IPBlock, Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "uart_phy")
BASE = 0x80000000


# ---------------------------------------------------------------- register helpers
def test_setup_round_trip():
    for text in ("8N1", "7E2", "6O1", "5M2", "8S1"):
        f = U.Fmt(text)
        v = U.setup_value(50e6, 115200, f)
        d = U.decode_setup(v, 50e6)
        assert str(d["fmt"]) == text and d["div"] == 434 and d["no_flow"]
    assert U.setup_value(50e6, 115200, U.Fmt("8N1")) == 0x400001B2         # the IP's INITIAL_SETUP
    assert abs(U.baud_error(50e6, 115200)) < 0.01
    with pytest.raises(ValueError):
        U.Fmt("9N1")
    with pytest.raises(ValueError):
        U.setup_value(1e3, 1e6, U.Fmt("8N1"))


def test_line_presets_match_the_rtl_readme():
    assert {k: U.line_ctrl(k) for k in U.LINE_TYPES} == {
        "ttl": 0xC0, "rs232": 0xC0, "rs422": 0xC1, "rs485-4w": 0xC2, "rs485-2w": 0xC6, "rs485-2w-echo": 0xCE}
    assert U.line_type_of(0xCE) == "rs485-2w-echo" and U.line_type_of(0xC2) == "rs485-4w"
    assert U.decode_ctrl(0xC6) == dict(de="tx", half=True, echo=False, tx_inv=False, rx_inv=False, tx_en=True, rx_en=True)
    t = U.timing_value(50e6, 1.0, 5.0)
    assert t == 0x00FA0032 and U.decode_timing(t, 50e6)["lead"] == 50
    assert U.cycles(1e9, 50e6) == 0xFFFF


def test_terminal_parsers():
    assert U.parse_hex_bytes("41 42 0x43,4") == b"ABC\x04"
    with pytest.raises(ValueError):
        U.parse_hex_bytes("4G")
    assert U.parse_ascii(r"a\r\n\x41\t\\") == b"a\r\nA\t\\"
    assert U.parse_ascii(r"a\n", escapes=False) == b"a\\n"
    s = U.LineSplitter("auto")
    assert [s.action(b) for b in b"a\r\nb\n"] == [None, "nl", "skip", None, "nl"]
    s = U.LineSplitter("CR+LF")
    assert [s.action(b) for b in b"\r\n\n"] == ["skip", "nl", None]


def test_register_map_fields_do_not_overlap():
    ip = U.axiluart_phy_ip()
    assert ip.panel == U.PANEL_ID
    for r in ip.registers:
        used = 0
        for f in r.fields:
            m = ((1 << f.width) - 1) << f.lsb
            assert not used & m, (r.name, f.name)
            used |= m
    assert {r.name: r.offset for r in ip.registers} == U.OFFSETS


# ---------------------------------------------------------------- IP interface field
def test_panel_survives_project_library_and_spec():
    ip = U.axiluart_phy_ip("uart")
    back = IPBlock.from_dict(ip.to_dict())
    assert back.panel == U.PANEL_ID
    spec = G.spec_from_ipblock(ip)
    assert spec.panel == U.PANEL_ID
    spec2 = G.ModuleSpec.from_dict(spec.to_dict())
    assert spec2.panel == U.PANEL_ID and G.to_ipblock(spec2, 0, with_spec=True).panel == U.PANEL_ID
    plain = G.ModuleSpec("x")
    assert "panel" not in plain.to_dict()


def test_examples_carry_the_interface():
    lib = IPLibrary.load(os.path.join(EX, "uart_phy.axlib.json"))
    e = lib.get("axiluart_phy")
    assert e.ip.panel == U.PANEL_ID and e.definition("UARTX").panel == U.PANEL_ID
    proj = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    ips = proj.address_ips()
    assert [(i.name, i.base, i.panel) for i in ips] == [("UART0", 0x60001000, U.PANEL_ID)]
    from axil_studio.core.model import load_config
    for name in os.listdir(EX):
        if name.endswith(".axcfg.json"):
            regs = {reg for _, reg, _ in load_config(os.path.join(EX, name))}
            assert regs <= {"PHY_CTRL", "PHY_TIMING", "SETUP"}, name
    with open(os.path.join(EX, "bench_rs485_4w.json"), encoding="utf-8") as fh:
        s = V.Settings.from_dict(json.load(fh))
    assert s.line_type == "rs485-4w" and str(s.formats[0]) == "8N1"


def test_panel_registry():
    from axil_studio import panels
    info = panels.get(U.PANEL_ID)
    assert info is not None and info.title
    assert panels.get("") is None and panels.get("nope") is None
    assert U.PANEL_ID in [p.id for p in panels.available()]


# ---------------------------------------------------------------- validation logic
class FakeUart:
    """Register-level stand-in of axiluart_phy for the validation logic: SETUP / PHY registers,
    CFG_ERR on an invalid DE mode and (loopback) TX bytes looped back to RX while TX_EN is set.
    No line timing, no DE pin."""

    def __init__(self, caps=U.ST_HAS_TX | U.ST_HAS_RX, loopback=False):
        self.regs = {U.SETUP: 0x400001B2, U.PHY_CTRL: 0xC0, U.PHY_TIMING: 0x00FA0032}
        self.caps, self.loopback = caps, loopback
        self.cfg_err = False
        self.rx, self.tx_hold = [], []
        self.writes = []

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
        self.writes.append((o, v))
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
    rows = []
    rep = V.Report(on_row=rows.append)
    assert V.run_validation(dev, _settings(), rep) == "done"
    c = rep.counts()
    assert c["FAIL"] == 0 and c["PASS"] >= 8, rep.rows
    assert any(r["test"] == "Line tests" and r["status"] == "SKIP" for r in rows)
    assert dev.regs[U.SETUP] == 0x400001B2 and dev.regs[U.PHY_TIMING] == 0x00FA0032 and dev.regs[U.PHY_CTRL] == 0xC0


def test_loopback_data_tests():
    dev = FakeUart(loopback=True)
    rep = V.Report()
    assert V.run_validation(dev, _settings(loopback=True, formats="8N1,7E1", count=20), rep) == "done"
    names = {r["test"]: r["status"] for r in rep.rows}
    assert names["Loopback data 115200 8N1"] == "PASS" and names["Loopback data 115200 7E1"] == "PASS"
    assert names["TX_EN=0 holds data 115200 8N1"] == "PASS"
    assert rep.counts()["FAIL"] == 0, [r for r in rep.rows if r["status"] == "FAIL"]


def test_broken_register_is_reported():
    dev = FakeUart()
    dev.write = (lambda orig: (lambda a, v: orig(a, v & 0xFFFF00FF if a - BASE == U.PHY_TIMING else v)))(dev.write)
    rep = V.Report()
    V.run_validation(dev, _settings(), rep)
    assert any(r["status"] == "FAIL" and r["test"].startswith("PHY_TIMING") for r in rep.rows)


def test_stop_restores_the_registers():
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
    assert rep.rows[-1]["test"] == "Stopped"
    assert dev.regs[U.SETUP] == 0x400001B2 and dev.regs[U.PHY_CTRL] == 0xC0      # restored after Stop


def test_settings_and_command_line(tmp_path):
    s = V.Settings(bauds="9600 115200", formats="8N1, 7E2", tail_us="3.5", line_type="rs485-2w-echo")
    assert s.bauds == [9600, 115200] and [str(f) for f in s.formats] == ["8N1", "7E2"] and s.tail_us == 3.5
    assert s.half and s.echo and s.de_mode == "tx"
    back = V.Settings.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back.to_dict() == s.to_dict()
    with pytest.raises(ValueError):
        V.Settings(line_type="can")
    with pytest.raises(ValueError):
        V.Settings.from_dict({"bogus": 1})
    cfg = tmp_path / "bench.json"
    cfg.write_text(json.dumps({"bauds": [460800], "line_type": "rs422", "base": "0x60001000"}))
    s, _ = V.parse_args(["--config", str(cfg), "--formats", "8E1"])
    assert s.bauds == [460800] and s.line_type == "rs422" and s.base == 0x60001000 and str(s.formats[0]) == "8E1"


# ---------------------------------------------------------------- UART interface window
def test_interface_opens_from_the_debug_bridge(qapp, no_dialogs):
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(os.path.join(EX, "uart_phy_demo.axproj.json"))
    page = DebugBridgePage(ctx)
    item = page._find_item(("project", "UART0", "SETUP"))
    page.tree.setCurrentItem(item)
    assert page.panel_btn.isVisibleTo(page) and page.panel_btn.text()
    w = page.open_panel()
    assert w is page.open_panel("project", "UART0")            # raised, not opened twice
    # form -> register values
    w.baud.setCurrentText("9600")
    w.parity.setCurrentIndex(w.parity.findData("E"))
    assert w._setup_value() == U.setup_value(50e6, 9600, U.Fmt("8E1"))
    w.line_type.setCurrentIndex(w.line_type.findData("rs485-2w-echo"))
    assert w._ctrl_value() == 0xCE
    w._set_line_form(0xC1, 0x00FA0032)
    assert w.line_type.currentData() == "rs422" and abs(w.lead.value() - 1.0) < 1e-6
    w._set_setup_form(U.setup_value(50e6, 115200, U.Fmt("7O2")))
    assert w.baud.currentText() == "115200" and str(w._fmt()) == "7O2"
    # not connected: board access is off, validation refuses to start
    assert not w.run_btn.isEnabled()
    w.start_validation()
    assert not w.validating
    # back to the Debug Bridge, and the config list
    w.show_in_bridge("PHY_CTRL")
    assert page.reg is not None and page.reg.name == "PHY_CTRL"
    n = len(page.config)
    w.add_to_config()
    assert [r["key"][2] for r in page.config[n:]] == ["PHY_TIMING", "PHY_CTRL", "SETUP"]
    # settings of the board are kept in the project
    w.clk.setValue(100.0)
    w._save_settings(clk_mhz=w.clk.value())
    assert ctx.project.debug["panels"]["UART0"]["clk_mhz"] == 100.0
    w.close()
    page.shutdown()
