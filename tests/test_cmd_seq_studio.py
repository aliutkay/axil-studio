"""cmd_seq in AXIL Studio: the command sequencer subsystem type, its register map in the address
map, the generated outputs, the assembler symbols and the cmd_seq interface window."""

import filecmp
import os
import sys
import time

import pytest

from axil_studio.core import addrmap as A
from axil_studio.core import cheader, cmdseq
from axil_studio.core.library import IPLibrary
from axil_studio.core.model import IPInstance, Project
from tests.cmdseq_fake import FakeSeqLink
from tests.test_interfaces import _dispose, _in_thread, _pump_until

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "cmd_seq")
DEMO = os.path.join(EX, "cmd_seq_demo.axproj.json")
IFACE = os.path.join(ROOT, "interfaces", "cmd_seq")
sys.path.insert(0, IFACE)
sys.path.insert(1, os.path.join(ROOT, "interfaces", "sdk"))
import axil_client  # noqa: E402


# ---------------------------------------------------------------- model and address map
def test_subsystem_type_round_trip_and_rules():
    p = Project.load(DEMO)
    seq = p.get_instance("SEQ0")
    assert seq.sequencer and seq.self_window == cmdseq.OWN_WINDOW and not A.validate(p)
    seq.seq = {"IMEM_AW": 10, "PT_TIMEOUT": 64}
    again = Project.from_data(p.to_data()).get_instance("SEQ0")
    assert again.sequencer and again.seq == {"IMEM_AW": 10, "PT_TIMEOUT": 64}
    assert cmdseq.settings(again)["IMEM_AW"] == 10 and cmdseq.settings(again)["DMEM_AW"] == 8
    plain = A.add_bridge(p, name="GRP")                   # not every subsystem is a sequencer
    plain.children.append(IPInstance("E1", "", 0, 0, False, 0x10))
    A.readdress(p)
    assert not plain.sequencer and not A.validate(p)
    # rules: 64 KB own window, 64 KB-aligned base, parameter ranges
    seq.self_window = 0x1000
    assert any("64 KB" in e for e in A.validate(p))
    seq.self_window = cmdseq.OWN_WINDOW
    seq.seq = {"DMEM_AW": 14}
    assert any("DMEM_AW" in e for e in A.validate(p))
    seq.seq = None
    seq.base = 0x03008000
    assert any("64 KB boundary" in e for e in A.validate(p))


def test_cmd_seq_is_not_a_plain_slave():
    p = Project.load(DEMO)
    p.ips.append(cmdseq.register_map())
    p.instances.append(IPInstance("BAD", "cmd_seq"))
    A.readdress(p)
    assert any("subsystem type" in e for e in A.validate(p))


def test_own_registers_in_address_ips_and_headers():
    p = Project.load(DEMO)
    ips = {i.name: i for i in p.address_ips()}
    assert ips["SEQ0"].base == 0x03000000 and ips["SEQ0"].panel == "cmd_seq"
    assert ips["SEQ0"].get_register("STATUS").offset == 0x0C and ips["UART0"].base == 0x03010000
    h = cheader.gen_system_header(p)
    assert "#define CMD_SEQ_CTRL_OFFSET  0x08u" in h and "#define SEQ0_CTRL  (SEQ0_BASE + CMD_SEQ_CTRL_OFFSET)" in h
    inc = cheader.gen_asm_include(p)
    assert ".equ SEQ0_CTRL  0x03000008" in inc and ".equ UART0_PHY_CTRL  0x03010010" in inc
    assert ".equ SEQ0_CTRL_RUN_MASK   0x00000001" in inc


def test_library_matches_the_register_map():
    lib = IPLibrary.load(os.path.join(EX, "cmd_seq.axlib.json"))
    ip = lib.get("cmd_seq").ip
    ref = cmdseq.register_map()
    assert [r.to_dict() for r in ip.registers] == [r.to_dict() for r in ref.registers]
    assert ip.panel == "cmd_seq" and ip.version == cmdseq.VERSION


def test_instance_template():
    p = Project.load(DEMO)
    seq = p.get_instance("SEQ0")
    seq.seq = {"DMEM_AW": 6}
    t = cmdseq.instance_template(seq, p)
    assert ".SELF_ID     (8'h00)" in t and ".DMEM_AW     (6)" in t and "MASTER0 of axil_xbar_SEQ0" in t
    assert t.count(".S_AXI_") == 21 and t.count(".M_AXI_") == 19


def test_seqasm_copy_is_the_same():
    assert filecmp.cmp(os.path.join(ROOT, "hdl", "cmd_seq", "sw", "seqasm.py"),
                       os.path.join(IFACE, "seqasm.py"), shallow=False)


def test_symbols_and_include(tmp_path):
    import seq_core as S
    import seqasm
    p = Project.load(DEMO)
    inc = tmp_path / "demo_map.inc"
    inc.write_text(cheader.gen_asm_include(p))
    src = 'LDI R1, 0xC6\nWR [UART0_PHY_CTRL], R1\nRD R2, [ADC_BASE+4]\nEND\n'
    a, _ = seqasm.assemble('.include "demo_map.inc"\n' + src, base_dir=str(tmp_path))
    # the interface gets the same names from the service's address map
    entries = [{"name": i.name, "base": i.base,
                "registers": [r.to_dict() for r in (p.definition_of(i).registers if p.definition_of(i) else [])]}
               for i, _ in p.walk()]
    b, _, lines = S.assemble(src, 9, 8, S.symbols(entries))
    assert [w for _, w, _ in a] == [w for _, w, _ in b] and lines == {0: 1, 1: 2, 2: 3, 3: 4}


# ---------------------------------------------------------------- service and window
@pytest.fixture
def seq_bridge(qapp):
    from axil_studio.interfaces.host import InterfaceService
    from axil_studio.project import ProjectContext
    from axil_studio.tools.debug_bridge import DebugBridgePage
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(DEMO)
    page = DebugBridgePage(ctx)
    page.link.close()
    page.link = FakeSeqLink(0x03000000)
    page._update_controls()
    page.service = InterfaceService(page)
    page.service._listen()
    yield page
    page.shutdown()


def _session(page, ip="SEQ0"):
    import secrets
    from axil_studio.interfaces.host import Session
    tok = secrets.token_hex(8)
    page.service.sessions[tok] = Session(tok, page.ifaces["cmd_seq"], "project", ip)
    return tok


def test_service_address_map(qapp, seq_bridge):
    page = seq_bridge
    assert "cmd_seq" in page.ifaces
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    assert c.ip["name"] == "SEQ0" and c.ip["panel"] == "cmd_seq"
    m = _in_thread(qapp, c.address_map)
    kinds = {e["name"]: (e["kind"], e["parent"]) for e in m}
    assert kinds["SEQ0"] == ("cmd_seq", "") and kinds["UART0"] == ("ip", "SEQ0") and kinds["ADC"] == ("external", "SEQ0")
    assert _in_thread(qapp, lambda: c.read(0x00)) == 0x5EC00200
    c.close()


def _wait(qapp, w):
    assert _pump_until(qapp, lambda: w.bus.pending == 0, 10)


def test_window_assemble_load_run_step(qapp, seq_bridge, no_dialogs):
    import look
    import seq_ui
    page = seq_bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    look.use_language("en")
    w = seq_ui.SeqWindow(c)
    _wait(qapp, w)
    assert w.imem_aw == 9 and w.dmem_aw == 8 and w.dmem.rowCount() == 256 and len(w.syms) > 50
    w.editor.setPlainText("        LDI R1, 0\nloop:   ADD R1, 1\n        STD D[3], R1\n"
                          "        CMP R1, 5\n        BR LO, loop\n        RD R2, [UART0_PHY_CTRL]\n        END\n")
    assert w.assemble() and len(w.prog) == 7
    # load: STOP / RESET, the words, then IMEM_CNT / IMEM_SUM are checked
    w.load()
    _wait(qapp, w)
    link = page.link
    assert link.cnt == 7 and link.m.imem[6] == w.prog[6][1]
    assert w.card_pill.text() == "I-mem = this program"
    assert page.service.lock_owner is None                  # the lock is given back
    # run to the end
    w.auto.setChecked(False)
    w.pulse(seq_ui.S.RUN)
    w.auto.setChecked(False)
    _wait(qapp, w)
    w.refresh()
    _wait(qapp, w)
    assert w.last["status"].state == "DONE" and w.last["regs"][1] == 5
    assert w.st_vals["state"].text() == "Done"
    w.tabs.setCurrentIndex(2)
    _wait(qapp, w)
    assert w.dmem.item(3, 2).text() == "0x00000005"
    # step and run to a line
    w.pulse(seq_ui.S.RESET)
    _wait(qapp, w)
    w.pulse(seq_ui.S.STEP)
    _wait(qapp, w)
    assert w.last["status"].state == "PAUSED" and w.last["status"].pc == 1
    cur = w.editor.textCursor()
    cur.setPosition(w.editor.document().findBlockByNumber(5).position())
    w.editor.setTextCursor(cur)
    w.run_to_line()
    _wait(qapp, w)
    w.refresh()
    _wait(qapp, w)
    assert w.last["status"].pc == 5 and w.last["regs"][1] == 5 and w.st_vals["line"].text().startswith("6:")
    # an assembler error points at its line
    w.editor.setPlainText("LDI R1, 1\nFOO R2\n")
    assert not w.assemble() and w._err_line == 2
    _dispose(qapp, w, c)


def test_window_reports_a_bad_load(qapp, seq_bridge, no_dialogs, monkeypatch):
    import look
    import seq_ui
    page = seq_bridge
    c = _in_thread(qapp, lambda: axil_client.AxilClient(page.service.port, _session(page)))
    look.use_language("en")
    w = seq_ui.SeqWindow(c)
    _wait(qapp, w)
    w.editor.setPlainText("NOP\nEND\n")
    assert w.assemble()
    orig = page.link.write

    def lossy(addr, value):                      # the first instruction word is lost
        if addr == 0x03000024 and value == 0 and page.link.sum == 0 and page.link.phase == 0:
            return orig(addr, 0x5)
        return orig(addr, value)
    monkeypatch.setattr(page.link, "write", lossy)
    w.load()
    _wait(qapp, w)
    assert w.msg.text() == "Load failed" and w.card_pill.text() == "I-mem ≠ this program"
    _dispose(qapp, w, c)


def test_interface_texts_are_translated():
    import json
    import re
    with open(os.path.join(IFACE, "seq_ui.py"), encoding="utf-8") as fh:
        src = fh.read()
    keys = set(re.findall(r'tr\("((?:[^"\\]|\\.)*)"\)', src))
    keys = {k.encode().decode("unicode_escape").encode("latin-1").decode("utf-8") for k in keys}
    keys |= {"Command sequencer", "Idle", "Running", "Paused", "Done", "Error", "Cancel"}
    with open(os.path.join(IFACE, "texts_tr.json"), encoding="utf-8") as fh:
        tr_ = json.load(fh)
    assert sorted(keys - set(tr_)) == []
