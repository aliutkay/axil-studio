"""Application services: interface languages, undo / redo, the error handler, the icon."""

import os
import subprocess
import sys
import threading

import pytest

from axil_studio import i18n
from axil_studio.core import axilgen as G
from tests.conftest import ROOT
from tests.helpers import hier_project


# ------------------------------------------------------------------ languages
def test_every_text_is_translated():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import i18n_check
    assert i18n_check.check() == 0


def test_tr_follows_the_language():
    try:
        i18n.use("tr")
        assert i18n.tr("Undo") == "Geri al"
        assert i18n.tr("{0} added ({1})").format("a", "b") == "a eklendi (b)"
        assert i18n.tr("a text nobody translated") == "a text nobody translated"
        i18n.use("en")
        assert i18n.tr("Undo") == "Undo"
    finally:
        i18n.use("en")


def test_saved_language(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert i18n.saved_language() == "en"
    i18n.set_saved_language("tr")
    assert i18n.saved_language() == "tr"
    with pytest.raises(ValueError):
        i18n.set_saved_language("xx")


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_every_window_opens_in_every_language(lang, tmp_path):
    """A fresh process: module-level texts are made in the chosen language."""
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from axil_studio import theme, hub\n"
        "theme.install(app)\n"
        "from axil_studio.project import ProjectContext\n"
        "ctx = ProjectContext(); ctx.open(%r)\n"
        "h = hub.HubWindow(ctx)\n"
        "for t in hub.TOOLS: hub.ToolWindow(ctx, t.tool_id)\n"
        "print(h.menuBar().actions()[0].text())\n") % (ROOT, os.path.join(ROOT, "examples", "gpio_demo.axproj.json"))
    env = dict(os.environ, AXIL_STUDIO_LANG=lang, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    out = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]
    assert out.stdout.strip() == {"en": "&Project", "tr": "&Proje"}[lang]


# ------------------------------------------------------------------ undo / redo
@pytest.fixture
def ctx(qapp, no_dialogs):
    from axil_studio.project import ProjectContext
    c = ProjectContext()
    c.project = hier_project()
    c.history.reset(c._snapshot())
    return c


def names(ctx, bus):
    return [i.name for i in ctx.project.level(ctx.project.get_instance(bus) if bus else None)]


def test_project_undo_redo(ctx):
    from axil_studio.tools.address_map import AddressMapPage
    page = AddressMapPage(ctx)
    before = names(ctx, "SBB_1")
    page._move_to(ctx.project.get_instance("ip_4"), ctx.project.get_instance("SBB_1"), None)
    moved = names(ctx, "SBB_1")
    assert moved != before and ctx.dirty and ctx.can_undo()
    assert ctx.undo() and names(ctx, "SBB_1") == before and not ctx.dirty
    assert page.tree.topLevelItemCount() == 3                     # the tool followed the undo
    assert ctx.redo() and names(ctx, "SBB_1") == moved
    assert not ctx.redo()


def test_saving_marks_the_clean_point(ctx, tmp_path):
    ctx.add_instance("tst_reg")
    ctx.save(str(tmp_path / "p.axproj.json"))
    assert not ctx.dirty
    ctx.undo()
    assert ctx.dirty
    ctx.redo()
    assert not ctx.dirty


def test_register_map_editor_has_its_own_history(ctx, monkeypatch):
    from axil_studio.tools import regmap
    page = regmap.RegMapEditorPage(ctx)
    page.edit_project_ip("tst_reg")
    project_before = ctx.history.pos
    monkeypatch.setattr(regmap.RegDialog, "ask", staticmethod(lambda *a, **k: G.RegSpec("NEW", 8)))
    monkeypatch.setattr(regmap.FieldDialog, "ask", staticmethod(lambda *a, **k: G.FieldSpec("F", 0, 4, "RW")))
    page.add_reg()                                                # register, then its first field
    assert [r.name for r in page.spec.registers][-1] == "NEW" and page.dirty
    assert page.undo() and page.spec.registers[-1].fields == []   # field gone
    assert page.undo() and "NEW" not in [r.name for r in page.spec.registers] and not page.dirty
    assert not page.undo()
    assert page.redo() and page.redo() and page.spec.registers[-1].fields[0].name == "F"
    assert ctx.history.pos == project_before                     # the project was not touched


# ------------------------------------------------------------------ error handler
def test_unexpected_errors_are_logged_and_shown(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from axil_studio import errors
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    for h in list(errors.log.handlers):
        errors.log.removeHandler(h)
    try:
        errors.install()
        b = QPushButton()
        b.clicked.connect(lambda: 1 / 0)
        b.click()                                                 # an exception in a Qt slot
        qapp.processEvents()
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(None)
    assert shown and "ZeroDivisionError" in shown[0]
    with open(errors.log_path(), encoding="utf-8") as fh:
        assert "ZeroDivisionError" in fh.read()


def test_application_icon(qapp):
    from axil_studio import icons
    ic = icons.app_icon()
    assert not ic.isNull() and ic.pixmap(64, 64).width() == 64
    assert os.path.exists(os.path.join(ROOT, "axil_studio", "assets", "axil_studio.ico"))
