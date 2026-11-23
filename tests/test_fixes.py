"""Regression tests for core, UI, Debug Bridge and axiluart_phy bug fixes."""

import concurrent.futures
import os
import sys

import pytest

from axil_studio.core import axilgen as G
from axil_studio.core import axilpack as P
from axil_studio.core import cheader as CH
from axil_studio.core import hexbus as H
from axil_studio.core import numfmt as N
from axil_studio.core import portparse as PP
from axil_studio.core import xbargen as X
from axil_studio.core.library import IPLibrary
from axil_studio.core.model import IPBlock, Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UART_DIR = os.path.join(ROOT, "interfaces", "axiluart_phy")
SDK_DIR = os.path.join(ROOT, "interfaces", "sdk")
for _p in (SDK_DIR, UART_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uart_phy as U  # noqa: E402
import uart_validate as V  # noqa: E402


# ---------------------------------------------------------------- core / hexbus
def test_hexbus_quiet_line_resets_rx_buffer_and_resumes_logging():
    logged = []
    bus = H.HexbusLink(log_cb=lambda d, t: logged.append((d, t)))

    bus.quiet = True
    for ch in "R00000001\n":
        bus._feed(ch)
    assert bus._rx_line == ""
    assert logged == []

    bus.quiet = False
    for ch in "R00000002\n":
        bus._feed(ch)
    assert bus._rx_line == ""
    assert logged == [("RX", "R00000002")]


def test_hexbus_access_masks_bit1_in_address_packet():
    sent = []

    class FakeSer:
        is_open = True

        def write(self, data):
            sent.append(data.decode("ascii"))
            # feed simulated responses for address ack + read result
            for ch in "A80000005\nR00000042\n":
                bus._feed(ch)

        def flush(self):
            pass

    bus = H.HexbusLink()
    bus.ser = FakeSer()
    bus._running = True
    val = bus.read(0x80000006)
    assert val == 0x42
    # Address packet must encode (0x80000006 & ~3) | 1 = 0x80000005, never bit 1 (relative flag)
    assert sent[0].startswith("A80000005")


# ---------------------------------------------------------------- core / numfmt
def test_numfmt_parse_ascii_space_and_underscore():
    assert N.parse_value("' '", 8, "ascii") == 0x20
    assert N.parse_value("'_' ", 8, "ascii") == 0x5F
    assert N.parse_value("'_ '", 16, "ascii") == (ord("_") << 8) | ord(" ")


def test_numfmt_parse_enum_with_spaces():
    meanings = {0: "idle mode", 1: "run fast"}
    assert N.parse_value("run fast", 4, "hex", meanings=meanings) == 1
    assert N.parse_value("IDLE MODE", 4, "hex", meanings=meanings) == 0


def test_numfmt_parse_nonfinite_raises_value_error():
    for txt in ("inf", "-inf", "nan"):
        with pytest.raises(ValueError):
            N.parse_value(txt, 16, "ufix4")
        with pytest.raises(ValueError):
            N.parse_value(txt, 16, "sfix4")


def test_numfmt_sdec_rejects_unsigned_overflow():
    assert N.parse_value("127", 8, "sdec") == 127
    assert N.parse_value("-128", 8, "sdec") == 128
    with pytest.raises(ValueError):
        N.parse_value("128", 8, "sdec")
    with pytest.raises(ValueError):
        N.parse_value("200", 8, "sdec")


# ---------------------------------------------------------------- core / generators & parsers
def test_axilgen_validate_32bit_offset_and_unknown_kind():
    s = G.ModuleSpec(module="my_mod", registers=[
        G.RegSpec("R0", 0x100000000, fields=[G.FieldSpec("F0", 0, 1, "RO")]),
        G.RegSpec("R1", 0x0, fields=[G.FieldSpec("F1", 0, 1, "BOGUS")]),
    ])
    errs = s.validate()
    assert any("32 bits" in e for e in errs)
    assert any("BOGUS" in e for e in errs)


def test_axilpack_truncated_module_raises_parse_error():
    with pytest.raises(P.ParseError):
        P.parse_module("module foo")
    with pytest.raises(P.ParseError):
        P.parse_module("module foo #")
    with pytest.raises(P.ParseError):
        P.parse_module("module foo #(parameter W=1")


def test_portparse_invalid_verilog_radix_returns_none():
    assert PP.evaluate("8'b2", {}) is None
    assert PP.evaluate("16#ZZ#", {}) is None
    assert PP.evaluate("8'h1F", {}) == 0x1F


def test_xbargen_rejects_hdl_keyword_module_name():
    spec = X.XbarSpec(module="input", slaves=[X.XbarSlave("s0", 0x0, 0xFFFFF000)])
    errs = spec.validate()
    assert any("keyword" in e.lower() for e in errs)


def test_cheader_sanitizes_comment_delimiters():
    ip = IPBlock.from_dict({
        "name": "my_ip /* evil */",
        "version": "1.0 */ bad",
        "base": 0x1000,
        "registers": [{"name": "CTRL", "offset": 0, "fields": []}],
    })
    proj = Project(name="demo */ break", ips=[ip])
    hdr = CH.gen_system_header(proj)
    assert "*/ bad" not in hdr
    assert "*/ break" not in hdr
    assert "/* evil */" not in hdr


def test_bundled_gpio_demo_validates_cleanly():
    path = os.path.join(ROOT, "examples", "gpio_demo.axproj.json")
    proj = Project.load(path)
    for ip in proj.ips:
        if ip.regspec:
            spec = G.ModuleSpec.from_dict(ip.regspec)
            assert spec.validate() == []


# ---------------------------------------------------------------- UI / project / dialogs
def test_project_library_changes_participate_in_undo(qapp, tmp_path):
    from axil_studio.project import ProjectContext

    ctx = ProjectContext()
    lib = IPLibrary(name="testlib", path=str(tmp_path / "lib.axlib.json"))
    assert not ctx.dirty
    ctx.use_library(lib)
    assert ctx.dirty
    assert len(ctx.project.libraries) == 1
    assert ctx.undo()
    assert ctx.project.libraries == []


def test_async_shutdown_ignores_cancelled_future(qapp):
    from axil_studio.ui import Async

    a = Async()
    fut = concurrent.futures.Future()
    fut.cancel()
    called = []
    a._deliver(fut, lambda v: called.append(v), lambda e: called.append(e), None)
    assert called == []
    a.shutdown()
    a._deliver(fut, lambda v: called.append(v), lambda e: called.append(e), None)
    assert called == []


def test_slave_dialog_rejects_out_of_range_span(qapp, no_dialogs):
    from axil_studio.dialogs import SlaveDialog

    dlg = SlaveDialog(None, X.XbarSlave("ext0", 0x0, 0xFFFFF000, 0x1000, external=True), "SLAVE0", 4, True)
    dlg.span.setText("0x20000000")   # 512 MB > 256 MB
    with pytest.raises(ValueError):
        dlg.apply()

    dlg.span.setText("-0x100")
    with pytest.raises(ValueError):
        dlg.apply()
    dlg.close()


def test_field_map_dialog_rejects_frac_ge_width(qapp, no_dialogs):
    from axil_studio.dialogs import FieldMapDialog

    f = G.FieldSpec("GAIN", 0, 8, "RW", fmt="ufix4")
    dlg = FieldMapDialog(None, f, "RW")
    dlg.fmt.frac.setValue(8)
    with pytest.raises(ValueError):
        dlg.apply()
    dlg.close()


def test_hub_restart_args_for_module_launch(monkeypatch):
    from axil_studio import hub

    monkeypatch.setattr(sys, "argv", ["/path/to/axil_studio/__main__.py", "demo.axproj.json"])
    assert hub._restart_args() == ["-m", "axil_studio", "demo.axproj.json"]

    monkeypatch.setattr(sys, "argv", ["/path/to/studio.py", "demo.axproj.json"])
    assert hub._restart_args() == ["/path/to/studio.py", "demo.axproj.json"]


def test_regmap_save_persists_renamed_project_ip(qapp, no_dialogs):
    from axil_studio.project import ProjectContext
    from axil_studio.tools.regmap import RegMapEditorPage

    ctx = ProjectContext()
    spec = G.ModuleSpec(module="old_ip", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
    ])
    ctx.project.ips.append(G.to_ipblock(spec, 0, with_spec=True))
    page = RegMapEditorPage(ctx)
    page.edit_project_ip("old_ip")
    page.name_ed.setText("new_ip")
    page.name_ed.textEdited.emit("new_ip")
    assert page.save()
    assert ctx.project.get_ip("new_ip") is not None
    assert ctx.project.get_ip("old_ip") is None
    page.close()


def test_debug_bridge_set_extra_preserves_interfaces_and_exclusive_blocks_io(qapp, no_dialogs):
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage

    ctx = ProjectContext()
    ctx.project.debug = {"interfaces": {"uart0": {"clk_mhz": 50.0}}}
    page = DebugBridgePage(ctx)

    page._set_extra([])
    assert ctx.project.debug == {"interfaces": {"uart0": {"clk_mhz": 50.0}}}

    # When an exclusive lock is active, register reads/writes must be ignored
    class FakeConnectedLink:
        connected = True

        def close(self):
            pass

    page.link = FakeConnectedLink()
    page._exclusive = "session-1"
    called = []
    page.bus.run = lambda *a, **k: called.append(a)
    page.read_reg()
    page._write(0x1, "test")
    assert called == []
    page.shutdown()


# ---------------------------------------------------------------- axiluart_phy
def test_uart_phy_parse_bauds_rejects_nonfinite():
    with pytest.raises(ValueError):
        U.parse_bauds("115200, inf")
    with pytest.raises(ValueError):
        U.parse_bauds("nan")


def test_uart_validate_zero_access_time_and_timing_clamp():
    class FakeLink:
        def __init__(self):
            self.regs = {
                U.SETUP: 0x00000010,
                U.PHY_CTRL: 0x000000C1,
                U.PHY_TIMING: 0x00000000,
                U.PHY_STATUS: U.ST_HAS_DE | U.ST_HAS_RE | U.ST_HAS_TX | U.ST_HAS_RX | U.ST_DE,
                U.FIFO: 0x04000400,
            }

        def read(self, addr):
            return self.regs.get(addr - 0x1000, 0)

        def write(self, addr, value):
            off = addr - 0x1000
            if off == U.PHY_STATUS:
                self.regs[off] &= ~U.ST_CFG_ERR
            else:
                self.regs[off] = value

    dut = V.Dut(FakeLink(), 0x1000, 50e6)
    dut.access_s = 0.0
    rep = V.Report()
    s = V.Settings(base=0x1000, clk=50e6, line_type="rs485-2w", loopback=True)
    val = V.Validator(dut, None, rep, s)
    val.caps = U.ST_HAS_DE
    # Must not raise ZeroDivisionError when access_s is 0.0
    val.test_phy(115200, U.Fmt("8N1"))
    assert rep.rows

    # Very high clock + large lead/tail must clamp to 16-bit register fields cleanly
    s2 = V.Settings(base=0x1000, clk=1e9, lead_us=1000.0, tail_us=1000.0)
    t = U.timing_value(s2.clk, s2.lead_us, s2.tail_us)
    assert (t & 0xFFFF) == 0xFFFF and ((t >> 16) & 0xFFFF) == 0xFFFF


def test_uart_validate_run_pair_handles_restore_link_error():
    class BrokenLink:
        def __init__(self):
            self.reads = 0

        def read(self, addr):
            self.reads += 1
            if self.reads <= 6:
                return 0
            raise V.LinkError("link lost")

        def write(self, addr, value):
            raise V.LinkError("write failed")

    rep = V.Report()
    s = V.Settings(base=0x1000, clk=50e6,
                   pair={"a": "u0", "a_base": 0x1000, "b": "u1", "b_base": 0x2000, "tx": "a"},
                   pc="none", swap=False)
    with pytest.raises(V.LinkError):
        V._run_pair(BrokenLink(), s, rep, None, {}, lambda b: 0)


def test_uart_ui_swap_directions_does_not_mutate_before_ok_and_restores_val_form(qapp, no_dialogs):
    import uart_ui as UI

    class DummyClient:
        def __init__(self):
            self.ip = {"name": "uart0", "base": 0x1000}
            self.connected = True
            self.port = None
            self.settings = {"clk_mhz": 50.0, "pair": {"peer": "uart1", "role": "tx", "tx_de": "high"}}
            self.on_event = lambda ev: None
            self.calls = []

        def peers(self):
            return [{"name": "uart1", "base": 0x2000, "settings": {"pair": {"peer": "uart0", "role": "rx", "tx_de": "high"}}}]

        def batch(self, ops, quiet=False):
            self.calls.append(ops)
            raise RuntimeError("bus failed")

        def set_settings(self, **kw):
            pass

        def status(self, text):
            pass

    c = DummyClient()
    w = UI.UartWindow(c)
    w.peers_list = c.peers()
    w._pair_changed()

    def sync_fail(fn, ok=None, err=None):
        try:
            res = fn()
        except Exception as exc:
            if err:
                err(exc)
        else:
            if ok:
                ok(res)
    w.bus.run = sync_fail

    w.swap_directions()
    assert w.pair["role"] == "tx"
    assert w.role_cb.currentData() == "tx"

    # _set_val_form must restore pc, lines and swap
    s = V.Settings(base=0x1000, clk=50e6, pc="two", lines={"uart0": "COM1", "uart1": "COM2"}, swap=False)
    w._set_val_form(s)
    assert w.v_pc.currentData() == "two"
    assert w._val_lines == {"uart0": "COM1", "uart1": "COM2"}
    assert w.v_swap.isChecked() is False

    # _baud must handle non-finite input without crashing
    w.baud.setCurrentText("inf")
    assert w._baud() is None
    w.bus.shutdown()
    w.close()


# ---------------------------------------------------------------- Round 2 fixes
def test_xbargen_validation_and_xbar_ports_resilience():
    # Reserved dependency names, double underscore, trailing underscore, base/mask bounds
    for bad in ("axilxbar", "addrdecode", "skidbuffer", "easyaxil", "my__xbar", "my_xbar_"):
        s = X.XbarSpec(module=bad, slaves=[X.XbarSlave("s0", 0x0, 0xFFFFF000)])
        assert s.validate(), f"expected validation error for {bad}"

    s_oob = X.XbarSpec(module="ok_xbar", slaves=[
        X.XbarSlave("s0", 0x100000000, 0xFFFFF000),
        X.XbarSlave("s1", 0x0, 0x0),
        X.XbarSlave("s2", 0x1000, 0xFFFFFFFF),
    ])
    errs = s_oob.validate()
    assert any("32 bits" in e for e in errs)
    assert sum("contiguous" in e for e in errs) >= 2

    # xbar_ports must not crash on invalid module name, bad style, or >64 slaves
    bad_spec = X.XbarSpec(
        module="input",
        masters=[X.XbarMaster(f"m{i}") for i in range(20)],
        style="bogus",
        slaves=[X.XbarSlave(f"s{i}", i * 0x1000, 0xFFFFF000) for i in range(70)],
    )
    ports = X.xbar_ports(bad_spec)
    assert ports


def test_hdlspec_validates_parameter_types():
    from axil_studio.core import hdlspec as HS

    s = X.XbarSpec(module="ok_xbar", slaves=[X.XbarSlave("s0", 0x0, 0xFFFFF000)])
    spec = HS.xbar_hdlspec(s)
    spec["parameters"]["OPT_LOWPOWER"] = 2
    assert any("OPT_LOWPOWER" in e for e in HS.validate(spec))

    spec["parameters"]["OPT_LOWPOWER"] = True
    assert any("OPT_LOWPOWER" in e for e in HS.validate(spec))

    spec["parameters"]["OPT_LOWPOWER"] = 0
    spec["parameters"]["OPT_LINGER"] = -1
    assert any("OPT_LINGER" in e for e in HS.validate(spec))

    spec["parameters"]["OPT_LINGER"] = 4
    assert HS.validate(spec) == []


def test_portparse_unpacked_arrays_and_signed_vhdl_integer_range():
    v_src = """
    module arr_mod (
        input wire [7:0] bytes [0:3],
        output logic [3:0] sv_arr [4]
    );
    endmodule
    """
    _, v_ports = PP.parse_ports(v_src, "verilog")
    by_name = {p.name: p for p in v_ports}
    assert by_name["bytes"].width == 32
    assert by_name["sv_arr"].width == 16

    vhdl_src = """
    entity rng_mod is
      port (
        s4 : in integer range -8 to 7;
        s1 : in integer range -1 to 0;
        u3 : in integer range 0 to 7;
        s5 : in integer range -9 to 7
      );
    end entity;
    """
    _, vhdl_ports = PP.parse_ports(vhdl_src, "vhdl")
    v_by = {p.name: p for p in vhdl_ports}
    assert v_by["s4"].width == 4
    assert v_by["s1"].width == 1
    assert v_by["u3"].width == 3
    assert v_by["s5"].width == 5


def test_axilpack_missing_master_wstrb_defaults_to_all_ones():
    src = """
    module mini_master (
        input wire ACLK,
        input wire ARESETN,
        output wire M_AXI_AWVALID,
        input  wire M_AXI_AWREADY,
        output wire [31:0] M_AXI_AWADDR,
        output wire [2:0] M_AXI_AWPROT,
        output wire M_AXI_WVALID,
        input  wire M_AXI_WREADY,
        output wire [31:0] M_AXI_WDATA,
        input  wire M_AXI_BVALID,
        output wire M_AXI_BREADY,
        input  wire [1:0] M_AXI_BRESP,
        output wire M_AXI_ARVALID,
        input  wire M_AXI_ARREADY,
        output wire [31:0] M_AXI_ARADDR,
        output wire [2:0] M_AXI_ARPROT,
        input  wire M_AXI_RVALID,
        output wire M_AXI_RREADY,
        input  wire [31:0] M_AXI_RDATA,
        input  wire [1:0] M_AXI_RRESP
    );
    endmodule
    """
    mod = P.parse_module(src)
    wrapped = P.gen_pack_wrapper(mod)
    assert "4'hF" in wrapped


def test_cheader_escapes_version_syncs_spec_and_formats_4gb_size():
    from axil_studio.core.model import IPInstance

    spec = G.ModuleSpec(module="my_ip", version='1.0 "beta" \\ x', registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
        G.RegSpec("NEW_REG", 0x4, fields=[G.FieldSpec("VAL", 0, 8, "RO")]),
    ])
    ip = G.to_ipblock(spec, 0x0, with_spec=True)
    # Simulate stale ip.registers missing NEW_REG
    ip.registers = ip.registers[:1]
    inst = IPInstance(name="my_ip0", ip="my_ip", base=0x0, window=1 << 32)
    proj = Project(name="demo", ips=[ip], instances=[inst])
    hdr = CH.gen_system_header(proj)
    assert r'"1.0 \"beta\" \\ x"' in hdr
    assert "MY_IP0_NEW_REG" in hdr
    assert "0x100000000ULL" in hdr


def test_regmap_xlsx_handles_unknown_kind_overlap_and_quotes(tmp_path):
    from axil_studio.core import regmap_xlsx as RX

    spec = G.ModuleSpec(module='ip_"quoted"', registers=[
        G.RegSpec("R0", 0x0, fields=[
            G.FieldSpec("A", 0, 8, "WEIRD"),
            G.FieldSpec("B", 4, 8, "RW"),      # overlaps A
            G.FieldSpec("C", 30, 8, "RO"),     # exceeds 31
        ]),
    ])
    ip = G.to_ipblock(spec, 0, with_spec=True)
    out = tmp_path / "out.xlsx"
    RX.export_xlsx([ip], str(out))
    assert out.exists() and out.stat().st_size > 0


def test_addrmap_validation_case_insensitive_move_rollback_and_add_to_project(tmp_path):
    from axil_studio.core import addrmap as A
    from axil_studio.core.model import IPInstance

    spec = G.ModuleSpec(module="uart", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
    ])
    ip = G.to_ipblock(spec, 0, with_spec=True)
    proj = Project(
        name="p",
        ips=[ip],
        interconnect={"module": "input"},
        instances=[
            IPInstance(name="UART_0", ip="uart", base=0x0, window=0x1000),
            IPInstance(name="uart_0", ip="uart", base=0x1000, window=0x1000),
            IPInstance(name="bad_win", ip="uart", base=0x2000, window=0x3000),
        ],
    )
    assert A.unique_name(proj, "uart") == "uart_1"
    errs = A.validate(proj)
    assert any("twice" in e for e in errs)
    assert any("power of two" in e for e in errs)
    assert any("interconnect" in e.lower() for e in errs)

    # Moving a huge slave into a tight bridge must roll back cleanly on 32-bit overflow
    proj2 = Project(name="p2", ips=[ip], interconnect={"module": "top_xbar"})
    br = IPInstance(
        name="sub",
        base=0xFFFF0000,
        fixed=True,
        window=0x10000,
        bridge=True,
        children=[IPInstance(name="s_in", ip="uart", base=0xFFFF0000, window=0x1000)],
    )
    big = IPInstance(name="big_ext", ip="", base=0x0, span=0x10000000, window=0x10000000)
    proj2.instances = [big, br]
    with pytest.raises(ValueError):
        A.move(proj2, big, br, 0)
    assert big in proj2.instances
    assert big not in br.children

    # add_to_project must readdress when an existing IP's span grows
    proj_path = str(tmp_path / "grow.axproj.json")
    proj3 = Project(name="p3", ips=[ip], instances=[
        IPInstance(name="uart0", ip="uart", base=0x0),
        IPInstance(name="uart1", ip="uart", base=0x1000),
    ])
    proj3.save(proj_path)
    big_spec = G.ModuleSpec(module="uart", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
        G.RegSpec("FAR", 0x2000, fields=[G.FieldSpec("V", 0, 1, "RW")]),
    ])
    G.add_to_project(big_spec, proj_path, replace=True)
    reloaded = Project.load(proj_path)
    assert reloaded.instances[1].base >= 0x4000


def test_hexbus_reset_rx_write_error_and_33bit_addr():
    class FailSer:
        is_open = True

        def write(self, data):
            raise OSError("USB unplugged")

        def flush(self):
            pass

    bus = H.HexbusLink()
    bus._tok = "R"
    bus._digits = "1234"
    bus._rx_line = "R1234"
    bus._reset_rx()
    assert bus._tok is None and bus._digits == "" and bus._rx_line == ""

    bus.ser = FailSer()
    bus._running = True
    bus._cur_addr = 0x1000
    with pytest.raises(H.HexbusError):
        bus.read(0x1000)
    assert bus._cur_addr is None
    assert bus._running is False

    # 33-bit address must be masked to 32 bits in packet
    sent = []

    class OkSer:
        is_open = True

        def write(self, data):
            sent.append(data.decode("ascii"))
            for ch in "A00000005\nR00000009\n":
                bus._feed(ch)

        def flush(self):
            pass

    bus.ser = OkSer()
    bus._running = True
    assert bus.read(0x1_0000_0004) == 9
    assert sent[0].startswith("A5R")


def test_numfmt_unsigned_rejects_negative_and_all_views_signed_fix():
    for fmt in ("udec", "ufix4"):
        with pytest.raises(ValueError):
            N.parse_value("-1", 8, fmt)

    # -0.5 in 8-bit Q4 (0xF8 = 248 -> -8 / 16 = -0.5)
    views = dict(N.all_views(0xF8, 8, frac=4, signed_fix=True))
    assert any("Q" in k and "-0.5" in v for k, v in views.items())


def test_debug_bridge_poll_dedup_config_last_written_and_baud_check(qapp, no_dialogs):
    from axil_studio.core.model import IPInstance
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage

    spec = G.ModuleSpec(module="dut", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
    ])
    ctx = ProjectContext()
    ctx.project.ips.append(G.to_ipblock(spec, 0x1000, with_spec=True))
    ctx.project.instances.append(IPInstance(name="dut0", ip="dut", base=0x1000))

    page = DebugBridgePage(ctx)
    try:
        # Invalid baud (<= 0) must not crash or open serial
        page.port_cb.addItem("COM99", "COM99")
        page.port_cb.setCurrentIndex(page.port_cb.count() - 1)
        page.baud_cb.setCurrentText("0")
        page.toggle_connection()
        assert not page.link.connected

        # _append_log tolerates link without .ser attribute
        class SerlessLink:
            connected = True

            def close(self):
                pass

        page.link = SerlessLink()
        page._append_log("TX", "R")

        # _poll deduplicates identical hardware addresses
        read_addrs = []

        class RecordingLink:
            connected = True
            ser = None

            def read(self, addr):
                read_addrs.append(addr)
                return 0x55

            def write(self, addr, value):
                pass

            def close(self):
                pass

        page.link = RecordingLink()
        page.bus.run = lambda fn, ok=None, err=None: ok(fn()) if ok else fn()
        page.watch = [
            {"key": ("project", "dut0", "CTRL"), "value": None, "count": 0, "flash": False, "history": []},
            {"key": ("project", "dut0", "CTRL"), "value": None, "count": 0, "flash": False, "history": []},
        ]
        page._poll()
        assert read_addrs == [0x1000]

        # send_config records last_written and deduplicates read-back
        read_addrs.clear()
        page.config = [
            {"key": ("project", "dut0", "CTRL"), "value": 0x1},
            {"key": ("project", "dut0", "CTRL"), "value": 0x1},
        ]
        page.send_config()
        assert page.last_written[0x1000] == 0x1
        assert read_addrs == [0x1000]
    finally:
        page.shutdown()
        page.close()


def test_dialogs_project_rename_and_regmap_retype(qapp, no_dialogs, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from axil_studio.dialogs import BridgeDialog, FieldDialog, RegDialog
    from axil_studio.project import ProjectContext, rename_interface_settings
    from axil_studio.tools.regmap import RegMapEditorPage

    # RegDialog rejects >= 0x100000000
    rdlg = RegDialog(None, G.RegSpec("R0", 0), "Edit")
    rdlg.off.setText("0x100000000")
    with pytest.raises(ValueError):
        rdlg.apply()
    rdlg.close()

    # FieldDialog zeroes reset when switching RW -> RO
    fdlg = FieldDialog(None, G.FieldSpec("F0", 0, 8, "RW", reset=0x5A), "Edit")
    fdlg.kind.setCurrentIndex(fdlg.kind.findData("RO"))
    res = fdlg.apply()
    assert res.kind == "RO" and res.reset == 0
    fdlg.close()

    # BridgeDialog rejects >32-bit base and unaligned fixed auto-window bridge
    bdlg = BridgeDialog(None, "Bridge", "sub", 0x0, 0x0, 0, True, 0x1000)
    bdlg.base.setText("0x100000000")
    with pytest.raises(ValueError):
        bdlg.apply()
    bdlg.base.setText("0x00000800")  # not aligned to 0x1000
    with pytest.raises(ValueError):
        bdlg.apply()
    bdlg.close()

    # rename_interface_settings migrates key and peer reference
    proj = Project(name="p", debug={
        "interfaces": {
            "uart0": {"clk_mhz": 50.0, "pair": {"peer": "uart1", "role": "tx"}},
            "uart1": {"clk_mhz": 50.0, "pair": {"peer": "uart0", "role": "rx"}},
        }
    })
    rename_interface_settings(proj, "uart0", "uart_main")
    ifaces = proj.debug["interfaces"]
    assert "uart0" not in ifaces and "uart_main" in ifaces
    assert ifaces["uart1"]["pair"]["peer"] == "uart_main"

    # retype_unplaced clears incompatible reset / irq / sync
    ctx = ProjectContext()
    page = RegMapEditorPage(ctx)
    try:
        f = G.FieldSpec("SIG", 0, 1, "RW", reset=1)
        page.spec.unplaced = [f]
        page._refresh_unplaced({0})
        for target_kind in ("RO", "W1C", "WO"):
            if target_kind == "W1C":
                f.sync = True
            elif target_kind == "WO":
                f.irq = True
            monkeypatch.setattr(QInputDialog, "getItem", lambda *a, _k=target_kind, **k: (_k, True))
            page.retype_unplaced()
            if target_kind == "RO":
                assert f.kind == "RO" and f.reset == 0
            elif target_kind == "W1C":
                assert f.kind == "W1C" and f.sync is False
            elif target_kind == "WO":
                assert f.kind == "WO" and f.irq is False
    finally:
        page.close()


def test_uart_validate_round2_fixes():
    import threading

    # Dut.configure sets SETUP_NO_FLOW and rejects divider overflow
    writes = {}

    class Link:
        def read(self, addr):
            return writes.get(addr - 0x1000, 0)

        def write(self, addr, val):
            writes[addr - 0x1000] = val

    dut = V.Dut(Link(), 0x1000, 50e6)
    dut.configure(115200, U.Fmt("8N1"))
    assert writes[U.SETUP] & U.SETUP_NO_FLOW

    dut_fast = V.Dut(Link(), 0x1000, 1e9)
    with pytest.raises(ValueError):
        dut_fast.configure(50, U.Fmt("8N1"))

    # test_break always clears break_condition even if Cancelled is raised
    class FakePort:
        def __init__(self):
            self.break_condition = False

    class FakeLine:
        def __init__(self):
            self.port = FakePort()

        def flush(self):
            pass

    cancel = threading.Event()
    cancel.set()
    dut_cancel = V.Dut(Link(), 0x1000, 50e6, cancel=cancel)
    dut_cancel.baud = 115200
    dut_cancel.fmt = U.Fmt("8N1")
    rep = V.Report()
    s = V.Settings(base=0x1000, clk=50e6)
    val = V.Validator(dut_cancel, FakeLine(), rep, s)
    with pytest.raises(V.Cancelled):
        val.test_break(115200, U.Fmt("8N1"))
    assert val.line.port.break_condition is False

    # timing_for clamps negative values to 0..0xFFFF
    s_neg = V.Settings(base=0x1000, clk=50e6, lead_us=-5.0, tail_us=-10.0)
    val_neg = V.Validator(dut, None, rep, s_neg)
    assert val_neg.timing_for(115200) == 0

    # _sweep_setup returns False if _pc_format fails
    class FailLine:
        def configure(self, baud, fmt):
            raise OSError("unsupported")

    s_sw = V.Settings(base=0x1000, clk=50e6, bauds=[115200], formats=[U.Fmt("8N1")])
    val_sw = V.Validator(dut, FailLine(), rep, s_sw)
    assert val_sw._sweep_setup() is False

    # test_phy works at 1 GHz ACLK without overflowing 24-bit SETUP divider
    class PhyLink:
        def __init__(self):
            self.regs = {
                U.PHY_CTRL: 0xC1,
                U.PHY_STATUS: U.ST_HAS_DE | U.ST_DE,
                U.RXREG: U.RX_EMPTY,
            }

        def read(self, addr):
            return self.regs.get(addr - 0x1000, 0)

        def write(self, addr, value):
            self.regs[addr - 0x1000] = value

    dut_1g = V.Dut(PhyLink(), 0x1000, 1e9)
    dut_1g.access_s = 0.001
    rep_1g = V.Report()
    val_1g = V.Validator(dut_1g, None, rep_1g, V.Settings(base=0x1000, clk=1e9, line_type="rs485-2w", loopback=True))
    val_1g.caps = U.ST_HAS_DE
    val_1g.test_phy(115200, U.Fmt("8N1"))
    assert rep_1g.rows


# ---------------------------------------------------------------- Round 3 fixes
def test_hexbus_lock_guards_access_and_close():
    import threading

    sent = []
    barrier = threading.Event()

    class BlockingSer:
        is_open = True

        def write(self, data):
            txt = data.decode("ascii")
            sent.append(txt)
            if txt.startswith("A1001"):
                barrier.set()
                for ch in "A1001\nR00000011\n":
                    bus._feed(ch)
            elif txt.startswith("A2001"):
                for ch in "A2001\nR00000022\n":
                    bus._feed(ch)
            else:
                for ch in "R00000099\n":
                    bus._feed(ch)

        def close(self):
            pass

    bus = H.HexbusLink()
    bus.ser = BlockingSer()
    bus._running = True
    assert bus.read(0x1000) == 0x11
    assert bus.read(0x2000) == 0x22
    assert bus.read(0x2000) == 0x99
    assert sent == ["A1001R\n", "A2001R\n", "R\n"]
    bus.close()
    assert bus.ser is None and bus._cur_addr is None
    with pytest.raises(H.HexbusError):
        bus.read(0x2000)


def test_cheader_avoids_base_size_macro_redefinition_and_cli_missing_file():
    import shutil
    import subprocess
    import tempfile
    from axil_studio.core.model import IPInstance

    spec = G.ModuleSpec(module="dma", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
        G.RegSpec("BASE", 0x4, fields=[G.FieldSpec("ADDR", 0, 32, "RW")]),
        G.RegSpec("SIZE", 0x8, fields=[G.FieldSpec("LEN", 0, 32, "RW")]),
    ])
    ip = G.to_ipblock(spec, 0, with_spec=True)
    proj = Project(name="soc", ips=[ip], instances=[IPInstance(name="dma0", ip="dma", base=0x40000000)])
    hdr = CH.gen_system_header(proj)
    assert "#define DMA0_BASE  0x40000000u" in hdr
    assert "#define DMA0_REG_BASE  (DMA0_BASE + DMA_BASE_OFFSET)" in hdr
    assert "#define DMA0_REG_SIZE  (DMA0_BASE + DMA_SIZE_OFFSET)" in hdr

    cc = shutil.which("clang") or shutil.which("cc")
    if cc:
        with tempfile.TemporaryDirectory() as td:
            hp = os.path.join(td, "soc_map.h")
            cp = os.path.join(td, "t.c")
            with open(hp, "w") as fh:
                fh.write(hdr)
            with open(cp, "w") as fh:
                fh.write('#include "soc_map.h"\nunsigned x = DMA0_REG_BASE + DMA0_REG_SIZE + DMA0_CTRL;\n')
            res = subprocess.run([cc, "-fsyntax-only", "-Wall", "-Wextra", "-Werror", cp],
                                 capture_output=True, text=True)
            assert res.returncode == 0, res.stderr

    assert CH.main(["nonexistent_project_file.axproj.json"]) == 1


def test_numfmt_high_frac_round_trip_and_strict_unsigned_and_ascii():
    for w, frac in ((31, 30), (32, 31), (24, 23)):
        mask = (1 << w) - 1
        for raw in (0, 1, (1 << (w - 1)) - 1, 1 << (w - 1), mask):
            for base in ("ufix", "sfix"):
                fmt = f"{base}{frac}"
                txt = N.format_value(raw, w, fmt)
                assert N.parse_value(txt, w, fmt) == raw

    with pytest.raises(ValueError):
        N.parse_value("''", 8, "ascii")
    for fmt in ("hex", "bin", "oct"):
        with pytest.raises(ValueError):
            N.parse_value("-1", 8, fmt)
    with pytest.raises(ValueError):
        N.parse_value("-0x1", 8, "hex")


def test_axilpack_comma_separated_ansi_ports():
    src = """
    module multi_decl (
        input wire ACLK, ARESETN,
        input wire S_AXI_AWVALID, S_AXI_WVALID, S_AXI_BREADY, S_AXI_ARVALID, S_AXI_RREADY,
        output wire S_AXI_AWREADY, S_AXI_WREADY, S_AXI_BVALID, S_AXI_ARREADY, S_AXI_RVALID,
        input wire [31:0] S_AXI_AWADDR, S_AXI_WDATA, S_AXI_ARADDR,
        input wire [2:0] S_AXI_AWPROT, S_AXI_ARPROT,
        input wire [3:0] S_AXI_WSTRB,
        output wire [1:0] S_AXI_BRESP, S_AXI_RRESP,
        output wire [31:0] S_AXI_RDATA
    );
    endmodule
    """
    info = P.parse_module(src)
    assert len(info["ports"]) == 21
    wrapped = P.gen_pack_wrapper(info)
    assert "multi_decl_pk" in wrapped


def test_addrmap_4gb_window_set_base_overflow_and_module_parity():
    from axil_studio.core import addrmap as A
    from axil_studio.core.model import IPInstance

    # >= 4 GiB window must fail addrmap.validate()
    proj = Project(name="p", instances=[
        IPInstance(name="huge", ip="", base=0x0, span=0x1000, window=1 << 33),
    ])
    errs = A.validate(proj)
    assert any("32-bit" in e for e in errs)

    # set_base must reject moving a bridge so a child would wrap past 4 GiB
    br = IPInstance(name="sub", base=0x0, fixed=True, bridge=True, children=[
        IPInstance(name="c0", ip="", base=0x2000, fixed=True, span=0x1000, window=0x1000),
    ])
    proj2 = Project(name="p2", instances=[br])
    with pytest.raises(ValueError):
        A.set_base(proj2, br, 0xFFFFF000)
    assert br.base == 0x0 and br.children[0].base == 0x2000

    # Module name parity with xbargen: __ / trailing _ / axilxbar_pk rejected
    for bad_mod in ("bad__xbar", "bad_xbar_", "axilxbar_pk"):
        proj3 = Project(name="p3", interconnect={"module": bad_mod}, instances=[
            IPInstance(name="s0", ip="", base=0x0, span=0x1000, window=0x1000),
        ])
        assert A.validate(proj3), f"expected addrmap.validate() to reject {bad_mod}"


def test_hdlspec_lgmaxburst_lt2_and_malformed_json():
    from axil_studio.core import hdlspec as HS

    s = X.XbarSpec(module="ok_xbar", slaves=[X.XbarSlave("s0", 0x0, 0xFFFFF000)])
    spec = HS.xbar_hdlspec(s)
    for bad_mb in (0, 1):
        spec["parameters"]["LGMAXBURST"] = bad_mb
        assert any("LGMAXBURST" in e for e in HS.validate(spec))
    spec["parameters"]["LGMAXBURST"] = 2
    assert HS.validate(spec) == []

    # Malformed non-dict JSON structures must return validation errors, never crash with AttributeError
    assert HS.validate([])
    assert HS.validate({"format": HS.IP_FORMAT, "module": "m", "registers": [None]})


def test_debug_bridge_rejects_33bit_and_negative_writes_and_raw_addr(qapp, no_dialogs):
    from axil_studio.core.model import IPInstance
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage

    spec = G.ModuleSpec(module="dut", registers=[
        G.RegSpec("CTRL", 0x0, fields=[G.FieldSpec("EN", 0, 1, "RW")]),
    ])
    ctx = ProjectContext()
    ctx.project.ips.append(G.to_ipblock(spec, 0x1000, with_spec=True))
    ctx.project.instances.append(IPInstance(name="dut0", ip="dut", base=0x1000))
    page = DebugBridgePage(ctx)
    try:
        writes = []

        class RecLink:
            connected = True
            ser = None

            def read(self, addr):
                return 0

            def write(self, addr, value):
                writes.append((addr, value))

            def close(self):
                pass

        page.link = RecLink()
        page.bus.run = lambda fn, ok=None, err=None: ok(fn()) if ok else fn()
        ip = ctx.project.address_ips()[0]
        page.ip, page.reg, page.group = ip, ip.registers[0], "project"

        for bad in ("0x100000000", "-1", "-0x10"):
            page.value_ed.setText(bad)
            page.write_reg()
        assert writes == []

        page.value_ed.setText("0x12345678")
        page.write_reg()
        assert writes == [(0x1000, 0x12345678)]

        page.raw = None
        page.raw_ed.setText("0x100000000")
        page._raw_addr_changed()
        assert page.raw is None
        page.raw_ed.setText("-4")
        page._raw_addr_changed()
        assert page.raw is None
    finally:
        page.shutdown()
        page.close()


def test_interfaces_discover_and_host_handle_malformed_inputs(qapp, no_dialogs, tmp_path):
    from axil_studio import interfaces as ifc
    from axil_studio.interfaces import host as ifhost
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage

    # Malformed non-object manifest & null protocol must be recorded as errors, never crash discover()
    d1 = tmp_path / "bad_list"
    d1.mkdir()
    (d1 / ifc.MANIFEST).write_text("[]", encoding="utf-8")
    d2 = tmp_path / "bad_proto"
    d2.mkdir()
    (d2 / ifc.MANIFEST).write_text('{"format":"axil-interface","id":"x","command":["echo"],"protocol":null}', encoding="utf-8")
    found, errors = ifc.discover([str(tmp_path)])
    assert len(errors) >= 2

    # InterfaceService._handle must reply with error (not crash) on hello for removed IP and non-dict batch op
    ctx = ProjectContext()
    page = DebugBridgePage(ctx)
    try:
        svc = ifhost.InterfaceService(page)
        replies = []
        svc._write = lambda sock, msg: replies.append(msg)

        class FakeSock:
            def disconnectFromHost(self):
                pass

        info = ifc.Interface("dummy", "Dummy", ["echo"], str(tmp_path))
        sess = ifhost.Session("tok1", info, "project", "missing_ip")
        svc.sessions["tok1"] = sess
        state = {"session": None}
        svc._handle(FakeSock(), state, {"id": 1, "op": "hello", "token": "tok1"})
        assert replies and replies[-1]["ok"] is False and replies[-1]["error"]["code"] == "ip_removed"

        # Non-dict batch op must return bad_request instead of AttributeError
        class ConnectedLink:
            connected = True
            def close(self):
                pass
        page.link = ConnectedLink()
        state["session"] = sess
        svc._handle(FakeSock(), state, {"id": 2, "op": "batch", "ops": [123]})
        assert replies[-1]["ok"] is False and replies[-1]["error"]["code"] == "bad_request"
        svc.shutdown()
    finally:
        page.shutdown()
        page.close()


def test_regmap_unknown_kind_and_save_history_and_address_map_guarded_rollback(qapp, no_dialogs):
    from axil_studio.core.model import IPInstance
    from axil_studio.project import ProjectContext
    from axil_studio.tools.address_map import AddressMapPage
    from axil_studio.tools.regmap import RegMapEditorPage

    ctx = ProjectContext()
    page = RegMapEditorPage(ctx)
    try:
        # Unknown field kind must not crash _refresh_fields()
        page.spec = G.ModuleSpec(module="weird_ip", registers=[
            G.RegSpec("R0", 0x0, fields=[G.FieldSpec("F0", 0, 8, "UNKNOWN_KIND")]),
        ])
        page._load_spec()
        assert page.fields.topLevelItemCount() >= 1
        assert page.fields.topLevelItem(0).text(0) == "F0"

        # _set_name pushes to history so undo after a subsequent edit returns to the saved name
        page._set_name("renamed_ip")
        page._set_clean()
        page.spec.desc = "changed"
        page._mark()
        page.undo()
        assert page.spec.module == "renamed_ip"
        assert not page.dirty
    finally:
        page.close()

    # AddressMapPage._guarded restores proj.debug and _collapsed on fit-error rollback
    ctx2 = ProjectContext()
    ctx2.project.debug = {"interfaces": {"s0": {"clk_mhz": 50.0}}}
    br = IPInstance(name="sub", base=0x0, fixed=True, window=0x1000, bridge=True, children=[
        IPInstance(name="s0", ip="", base=0x0, span=0x1000, window=0x1000),
    ])
    ctx2.project.instances = [br]
    ap = AddressMapPage(ctx2)
    try:
        def bad_edit():
            ap._rename(br.children[0], "s0_renamed")
            br.children[0].window = 0x2000  # overflows hand-sized 0x1000 bridge
            return True

        assert ap._guarded(bad_edit) is False
        assert ctx2.project.instances[0].children[0].name == "s0"
        assert "s0" in ctx2.project.debug["interfaces"]
        assert "s0_renamed" not in ctx2.project.debug["interfaces"]
    finally:
        ap.close()


def test_uart_validate_tx_only_snapshot_order_and_uart_phy_cycles(qapp, no_dialogs, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    import uart_ui as UI

    # cycles() / timing_value() reject NaN, inf, and negative us with ValueError
    for bad in (-1.0, float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            U.timing_value(50e6, bad, 0.0)

    # Dut.snapshot() writes PHY_TIMING before PHY_CTRL; PairDut restores RX before TX
    written = []

    class RecLink:
        def __init__(self):
            self.regs = {}

        def read(self, addr):
            return self.regs.get(addr, 0)

        def write(self, addr, val):
            written.append((addr, val))
            self.regs[addr] = val

    link = RecLink()
    dut = V.Dut(link, 0x1000, 50e6)
    snap = dut.snapshot()
    dut.restore(snap)
    offs = [a - 0x1000 for a, _ in written]
    assert offs.index(U.PHY_TIMING) < offs.index(U.PHY_CTRL)

    written.clear()
    pdut = V.PairDut(link, tx_base=0x1000, rx_base=0x2000, clk_hz=50e6)
    psnap = pdut.snapshot()
    pdut.restore(psnap)
    rx_ctrl_i = next(i for i, (a, _) in enumerate(written) if a == 0x2000 + U.PHY_CTRL)
    tx_ctrl_i = next(i for i, (a, _) in enumerate(written) if a == 0x1000 + U.PHY_CTRL)
    assert rx_ctrl_i < tx_ctrl_i

    # TX-only core (HAS_RX=0): Validator._en_bits() must set CTRL_TX_EN only
    rep = V.Report()
    val = V.Validator(dut, None, rep, V.Settings(base=0x1000, clk=50e6))
    val.caps = U.ST_HAS_TX | U.ST_HAS_DE
    assert (val.normal_ctrl() & (U.CTRL_TX_EN | U.CTRL_RX_EN)) == U.CTRL_TX_EN

    # UartWindow open_preset / open_val_settings must handle non-dict JSON without crashing or mutating clk
    class DummyClient:
        ip = {"name": "uart0", "base": 0x1000}
        connected = False
        port = None
        settings = {"clk_mhz": 50.0}
        on_event = lambda self, ev: None
        def peers(self): return []
        def set_settings(self, **kw): pass
        def status(self, text): pass

    w = UI.UartWindow(DummyClient())
    try:
        bad_json = tmp_path / "list.json"
        bad_json.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad_json), ""))
        w.open_preset()
        w.open_val_settings()

        bad_val = tmp_path / "bad_val.json"
        bad_val.write_text('{"clk": 100000000, "bauds": []}', encoding="utf-8")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad_val), ""))
        before_clk = w.clk.value()
        w.open_val_settings()
        assert w.clk.value() == before_clk
    finally:
        w.bus.shutdown()
        w.close()
