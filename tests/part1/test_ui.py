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
from axil_studio.core import specs
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
    assert shown == specs.ip_view(G.ModuleSpec.from_dict(want), 0)    # not placed yet: base 0


def test_register_map_editor_spec_window_follows_edits(ctx, monkeypatch):
    from axil_studio.tools import regmap
    page = regmap.RegMapEditorPage(ctx)
    page.edit_project_ip("tst_reg")
    page.show_spec()
    view = page._spec_win.view
    assert json.loads(view.text.toPlainText())["generation"]["base"] == "0x%08X" % ctx.project.get_instance("ip_0").base
    page.name_ed.selectAll()
    QTest.keyClicks(page.name_ed, "renamed_ip")
    assert json.loads(view.text.toPlainText())["regspec"]["module"] == "renamed_ip"
    assert view.text.isReadOnly()


# ------------------------------------------------------------------ Slave Interface Wrapper
def test_wrapper_spec_view_and_generation(ctx, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from axil_studio.tools.wrapper import WrapperPage
    page = WrapperPage(ctx)
    page.load_project_ip("tst_reg", quiet=True)
    page.tabs.show_page(page.spec_view)
    shown = json.loads(page.spec_view.text.toPlainText())
    assert shown == {k: v for k, v in specs.project_view(ctx.project)["ips"][0].items() if k != "instances"}
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "gui")))
    page.generate()
    spec = G.ModuleSpec.from_dict(shown["regspec"])
    G.generate_all(spec, str(tmp_path / "ref"), int(shown["generation"]["base"], 16))
    for name in sorted(os.listdir(tmp_path / "ref")):
        if name.endswith(".xlsx"):
            continue                                   # holds a time stamp
        assert (tmp_path / "gui" / name).read_bytes() == (tmp_path / "ref" / name).read_bytes(), name


# ------------------------------------------------------------------ Address Map Editor
def test_address_map_edits_reach_the_crossbar_specs(ctx, monkeypatch):
    from axil_studio.tools import address_map as AM
    page = AM.AddressMapPage(ctx)
    p = ctx.project
    b1 = p.get_instance("SBB_1")
    k = p.get_instance("ip_3")
    k.fixed = True
    p.get_instance("SBB_0").fixed = True           # so that it stays where it is when it grows
    ctx.mark_modified()
    # a typed bridge address (inside SBB_0): SBB_1 and its locked child move together
    new_base = b1.base + 2 * A.window_of(p, b1)
    queue(monkeypatch, AM.BridgeDialog, [("SBB_1", new_base, 0, 0, True)])
    old_off = k.base - b1.base
    page.edit_slave(b1)
    b1, k = p.get_instance("SBB_1"), p.get_instance("ip_3")
    assert (b1.base, k.base - b1.base, k.fixed) == (new_base, old_off, True)
    top, s0, s1 = specs.project_xbar_specs(p)
    assert next(s.base for s in s0.slaves if s.name == "SBB_1") == new_base
    assert [s.base for s in s1.slaves] == [k.base]
    # a refused edit leaves the specs unchanged (name included)
    before = specs.project_view(p)
    queue(monkeypatch, AM.BridgeDialog, [("OTHER", b1.base, 0, 0x100, False)])   # window too small
    page.edit_slave(b1)
    assert specs.project_view(p) == before
    # moving an entry to another bus
    page._move_to(p.get_instance("ip_4"), p.get_instance("SBB_1"), None)
    assert [s.name for s in specs.project_xbar_specs(p)[2].slaves] == ["ip_3", "ip_4"]


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
    page.select_level("SBB_0")
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
    """Saving and reopening a project in the hub gives the same specs."""
    before = specs.project_view(ctx.project)
    path = str(tmp_path / "p.axproj.json")
    ctx.save(path)
    ctx.open(path)
    assert specs.project_view(ctx.project) == before
    assert specs.project_view(Project.load(path)) == before


def test_data_specs_load_in_the_wrapper(ctx):
    from axil_studio.tools.wrapper import WrapperPage
    page = WrapperPage(ctx)
    for name in sorted(os.listdir(os.path.join(DATA, "ip"))):
        spec = G.ModuleSpec.load(os.path.join(DATA, "ip", name))
        page.load_spec(copy.deepcopy(spec), "test")
        assert page.spec_data()["regspec"] == spec.to_dict()
