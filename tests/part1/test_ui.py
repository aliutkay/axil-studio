"""Part 1 through the tools: a user action in a tool must give the expected spec, and every tool's
read-only Spec view must equal the spec the command line derives from the same project."""

import copy
import json
import os
import subprocess
import sys

import pytest
from PySide6.QtTest import QTest

from axil_studio.core import addrmap as A
from axil_studio.core import axilgen as G
from axil_studio.core import hdlspec as H
from axil_studio.core.model import Project
from tests.conftest import DATA, ROOT
from tests.helpers import hier_project


@pytest.fixture
def ctx(qapp, no_dialogs):
    from axil_studio.project import ProjectContext
    c = ProjectContext()
    c.project = hier_project()
    return c


def queue(monkeypatch, cls, answers):
    """Make cls.ask return the given answers in turn (instead of opening the dialog)."""
    it = iter(answers)
    monkeypatch.setattr(cls, "ask", staticmethod(lambda *a, **k: next(it)))


# ------------------------------------------------------------------ Register Map Editor
def test_register_map_editor_builds_the_expected_regspec(ctx, monkeypatch):
    from axil_studio.tools import regmap
    page = regmap.RegMapEditorPage(ctx)
    page.new_spec()
    page.name_ed.selectAll()
    QTest.keyClicks(page.name_ed, "uart_lite")
    page.version_ed.selectAll()
    QTest.keyClicks(page.version_ed, "2.1.0")
    queue(monkeypatch, regmap.RegDialog, [G.RegSpec("CTRL", 0, "control", wr_strobe=True),
                                          G.RegSpec("STATUS", 4)])
    queue(monkeypatch, regmap.FieldDialog, [G.FieldSpec("EN", 0, 1, "RW", 1),
                                            G.FieldSpec("DIV", 8, 8, "RW", 4),
                                            G.FieldSpec("BUSY", 0, 1, "RO", sync=True)])
    page.add_reg()                     # CTRL, then its first field EN
    page.add_field()                   # DIV in the selected register
    page.add_reg()                     # STATUS, then BUSY
    assert page._save_project("uart_lite")

    want = G.ModuleSpec("uart_lite", "", [
        G.RegSpec("CTRL", 0, "control", wr_strobe=True, fields=[G.FieldSpec("EN", 0, 1, "RW", 1),
                                                                 G.FieldSpec("DIV", 8, 8, "RW", 4)]),
        G.RegSpec("STATUS", 4, fields=[G.FieldSpec("BUSY", 0, 1, "RO", sync=True)])], True, "2.1.0").to_dict()
    assert page.spec.to_dict() == want
    assert ctx.project.get_ip("uart_lite").regspec == want            # what the project keeps
    page.show_spec()
    shown = json.loads(page._spec_win.view.text.toPlainText())
    assert shown == H.ip_hdlspec(G.ModuleSpec.from_dict(want))
    assert [f["name"] for f in shown["registers"][0]["fields"]] == ["EN", "DIV"]
    assert shown["registers"][0]["wr_strobe"] and shown["registers"][1]["fields"][0]["sync"]


def test_register_map_editor_spec_window_follows_edits(ctx, monkeypatch):
    from axil_studio.tools import regmap
    page = regmap.RegMapEditorPage(ctx)
    page.edit_project_ip("tst_reg")
    page.show_spec()
    view = page._spec_win.view
    assert json.loads(view.text.toPlainText()) == H.project_hdlspecs(ctx.project)["ips"][0]
    page.name_ed.selectAll()
    QTest.keyClicks(page.name_ed, "renamed_ip")
    assert json.loads(view.text.toPlainText())["module"] == "renamed_ip"
    assert view.text.isReadOnly()


# ------------------------------------------------------------------ Slave Interface Wrapper
def test_wrapper_spec_view_and_generation(ctx, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from axil_studio.tools.wrapper import WrapperPage
    page = WrapperPage(ctx)
    page.load_project_ip("tst_reg", quiet=True)
    page.tabs.show_page(page.spec_view)
    shown = json.loads(page.spec_view.text.toPlainText())
    assert shown == H.project_hdlspecs(ctx.project)["ips"][0]
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "gui")))
    page.generate()
    assert H.load(str(tmp_path / "gui" / "tst_reg.hdlspec.json")) == shown
    H.generate(shown, str(tmp_path / "ref"), with_spec=False)    # Verilog from the shown HDL spec alone
    for name in sorted(os.listdir(tmp_path / "ref")):
        assert (tmp_path / "gui" / name).read_bytes() == (tmp_path / "ref" / name).read_bytes(), name


# ------------------------------------------------------------------ Address Map Editor
def test_address_map_edits_reach_the_crossbar_specs(ctx, monkeypatch):
    from axil_studio.tools import address_map as AM
    page = AM.AddressMapPage(ctx)
    p = ctx.project
    b1 = p.get_instance("SUB_1")
    k = p.get_instance("ip_3")
    k.fixed = True
    p.get_instance("SUB_0").fixed = True           # so that it stays where it is when it grows
    ctx.mark_modified()
    # a typed bridge address (inside SUB_0): SUB_1 and its locked child move together
    new_base = b1.base + 2 * A.window_of(p, b1)
    queue(monkeypatch, AM.BridgeDialog, [("SUB_1", new_base, 0, 0, True, "", None)])
    old_off = k.base - b1.base
    page.edit_slave(b1)
    b1, k = p.get_instance("SUB_1"), p.get_instance("ip_3")
    assert (b1.base, k.base - b1.base, k.fixed) == (new_base, old_off, True)
    top, s0, s1 = [H.xbar_hdlspec(s) for s in H.project_xbar_specs(p)]
    assert s0["slaves"][2]["addr"] == "0x%08X" % new_base                        # SUB_1 is SLAVE2 of SUB_0
    assert s1["slaves"][0]["addr"] == "0x%08X" % k.base
    # a refused edit leaves everything unchanged (the name too)
    before = H.project_hdlspecs(p)
    names = [i.name for i in p.all_instances()]
    queue(monkeypatch, AM.BridgeDialog, [("OTHER", b1.base, 0, 0x100, False, "", None)])   # window too small
    page.edit_slave(b1)
    assert H.project_hdlspecs(p) == before and [i.name for i in p.all_instances()] == names
    # moving an entry to another bus: SUB_1's interconnect gets a third slave
    page._move_to(p.get_instance("ip_4"), p.get_instance("SUB_1"), None)
    assert len(H.xbar_hdlspec(H.project_xbar_specs(p)[2])["slaves"]) == 3
    # a second slave in SUB_2 gives it a crossbar of its own
    assert len(H.project_xbar_specs(p)) == 3
    page._move_to(p.get_instance("ip_0"), p.get_instance("SUB_2"), None)
    assert [s.module for s in H.project_xbar_specs(p)][-1] == "axil_xbar_SUB_2"


# ------------------------------------------------------------------ Interconnect Configurator
def test_interconnect_spec_view_and_generation_match_the_command_line(ctx, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from axil_studio.tools.interconnect import InterconnectPage
    page = InterconnectPage(ctx)
    page.tabs.show_page(page.spec_view)
    page.spec_scope.setCurrentIndex(page.spec_scope.findData("all"))
    path = tmp_path / "h.axproj.json"
    ctx.save(str(path))
    dump = json.loads(subprocess.run([sys.executable, "-m", "axil_studio.core.axilgen", "--dump-spec", str(path)],
                                     cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    assert json.loads(page.spec_view.text.toPlainText()) == {"interconnects": dump["interconnects"]}
    assert "name" not in page.spec_view.text.toPlainText()                    # port names are not HDL data
    page.select_level("SUB_0")
    page.spec_scope.setCurrentIndex(page.spec_scope.findData("level"))
    assert json.loads(page.spec_view.text.toPlainText()) == dump["interconnects"][1]
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "gui")))
    (tmp_path / "gui").mkdir()
    page.generate()
    subprocess.run([sys.executable, "-m", "axil_studio.core.axilgen", "--xbar", str(path), "-o",
                    str(tmp_path / "cli")], cwd=ROOT, check=True, capture_output=True)
    names = sorted(os.listdir(tmp_path / "cli"))
    assert sorted(os.listdir(tmp_path / "gui")) == names
    for n in names:
        assert (tmp_path / "gui" / n).read_bytes() == (tmp_path / "cli" / n).read_bytes(), n


def test_project_round_trip_through_the_tools(ctx, tmp_path):
    """Saving and reopening a project in the hub gives the same HDL specs."""
    before = H.project_hdlspecs(ctx.project)
    path = str(tmp_path / "p.axproj.json")
    ctx.save(path)
    ctx.open(path)
    assert H.project_hdlspecs(ctx.project) == before
    assert H.project_hdlspecs(Project.load(path)) == before


def test_data_specs_load_in_the_wrapper(ctx):
    from axil_studio.tools.wrapper import WrapperPage
    page = WrapperPage(ctx)
    for name in sorted(os.listdir(os.path.join(DATA, "ip"))):
        spec = G.ModuleSpec.load(os.path.join(DATA, "ip", name))
        page.load_spec(copy.deepcopy(spec), "test")
        assert page.spec_data() == H.ip_hdlspec(spec)


# ------------------------------------------------------------------ commands per tool
def _commands(page):
    from PySide6.QtWidgets import QAbstractButton
    return {b.text() for b in page.header.findChildren(QAbstractButton)}


def test_excel_export_only_in_the_map_editors(ctx, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from axil_studio.core import regmap_xlsx
    from axil_studio.tools import address_map, interconnect, regmap, wrapper
    from axil_studio.i18n import tr
    xl = tr("Export to Excel…")
    rme = regmap.RegMapEditorPage(ctx)
    assert xl in _commands(rme) and tr("Add to address map") not in _commands(rme)
    assert xl in _commands(address_map.AddressMapPage(ctx))
    assert xl not in _commands(wrapper.WrapperPage(ctx))
    assert xl not in _commands(interconnect.InterconnectPage(ctx))
    spec = G.spec_from_ipblock(ctx.project.ips[0])
    assert not any(n.endswith(".xlsx") for n in wrapper.output_files(spec))
    if regmap_xlsx.available():
        rme.spec = copy.deepcopy(spec)
        rme._load_spec()
        out = tmp_path / "r.xlsx"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
        rme.export_xlsx()
        assert out.exists()


def test_subsystem_with_one_slave_has_no_crossbar_in_the_configurator(ctx):
    from axil_studio.tools.interconnect import InterconnectPage
    page = InterconnectPage(ctx)
    page.view_cb.setCurrentIndex(page.view_cb.findData("level"))
    page.select_level("SUB_2")
    assert page._system_view() and not page.module_ed.isEnabled()
    assert page.spec_data("level")["crossbar"] is None
    assert [k for k, _ in page._files() if k[1] == "main"] == [("", "main"), ("SUB_0", "main"), ("SUB_1", "main")]
    page.add_master()                                          # an extra master: now it needs one
    assert not page._system_view() and page.module_ed.isEnabled()
    assert page.spec_data("level")["module"] == "axil_xbar_SUB_2"
