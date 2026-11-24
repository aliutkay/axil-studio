"""AXIL Studio main window (hub) and the stand-alone tool window."""

import argparse
import os
import sys

from PySide6.QtCore import QCoreApplication, QObject, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QScrollArea, QVBoxLayout, QWidget)

from axil_studio import APP_NAME, ORG_NAME, ROOT, icons, theme, ui, version_text
from axil_studio.project import PROJECT_FILTER, ProjectContext
from axil_studio.tools import TOOLS, TOOLS_BY_ID
from axil_studio import errors, i18n
from axil_studio.i18n import tr



# ============================================================================ project menu
class ProjectActions:
    """New / open / save handling shared by the hub and the stand-alone windows."""

    def __init__(self, window: QMainWindow, ctx: ProjectContext):
        self.win, self.ctx = window, ctx

    def build_menu(self, menubar):
        m = menubar.addMenu(tr("&Project"))
        self._act(m, tr("New project"), self.new, QKeySequence.StandardKey.New)
        self._act(m, tr("Open…"), self.open, QKeySequence.StandardKey.Open)
        self.recent_menu = m.addMenu(tr("Recent projects"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._act(m, tr("Save"), self.save, QKeySequence.StandardKey.Save)
        self._act(m, tr("Save as…"), self.save_as, QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        self._act(m, tr("Quit"), self.win.close, QKeySequence.StandardKey.Quit)
        return m

    def build_edit_menu(self, menubar, page=None):
        """Undo / redo. In a tool with its own history (the Register Map Editor's unsaved register
        map) they act on that; everywhere else on the project."""
        m = menubar.addMenu(tr("&Edit"))
        self._page = page
        self.undo_act = self._act(m, tr("Undo"), self.undo, QKeySequence.StandardKey.Undo)
        self.redo_act = self._act(m, tr("Redo"), self.redo, QKeySequence.StandardKey.Redo)
        m.aboutToShow.connect(self._sync_edit)
        return m

    def _history(self):
        target = getattr(self._page, "history_target", None)
        return target() if callable(target) else self.ctx

    def _sync_edit(self):
        h = self._history()
        self.undo_act.setEnabled(h.can_undo())
        self.redo_act.setEnabled(h.can_redo())

    def _say(self, text):
        bar = self.win.statusBar() if hasattr(self.win, "statusBar") else None
        if bar is not None:
            bar.showMessage(text, 3000)

    def undo(self):
        self._say(tr("Undone") if self._history().undo() else tr("Nothing to undo"))

    def redo(self):
        self._say(tr("Redone") if self._history().redo() else tr("Nothing to redo"))

    def _act(self, menu, text, slot, shortcut=None):
        a = QAction(text, self.win)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _fill_recent(self):
        self.recent_menu.clear()
        items = self.ctx.recent()
        if not items:
            a = self.recent_menu.addAction(tr("(empty)"))
            a.setEnabled(False)
        for p in items:
            self.recent_menu.addAction(os.path.basename(p), lambda *_, p=p: self.open(p))

    def confirm_discard(self):
        if not self.ctx.dirty:
            return True
        r = ui.ask_save(self.win, APP_NAME, tr("Project “{0}” has unsaved changes.").format(self.ctx.project.name))
        if r == "save":
            return self.save()
        return r == "discard"

    def new(self):
        if self.confirm_discard():
            self.ctx.new()

    def open(self, path=None):
        if not self.confirm_discard():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self.win, tr("Open project"), os.path.dirname(self.ctx.path or ROOT),
                                                  PROJECT_FILTER)
            if not path:
                return False
        try:
            self.ctx.open(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            QMessageBox.critical(self.win, tr("Could not open the project"), f"{path}\n\n{exc}")
            return False
        return True

    def save(self):
        if not self.ctx.path:
            return self.save_as()
        try:
            self.ctx.save()
        except OSError as exc:
            QMessageBox.critical(self.win, tr("Could not save"), str(exc))
            return False
        return True

    def save_as(self):
        start = self.ctx.path or os.path.join(ROOT, f"{self.ctx.project.name}.axproj.json")
        path, _ = QFileDialog.getSaveFileName(self.win, tr("Save project"), start, PROJECT_FILTER)
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".axproj.json"
        try:
            self.ctx.save(path)
        except OSError as exc:
            QMessageBox.critical(self.win, tr("Could not save"), str(exc))
            return False
        return True


class _ThemeMenu(QObject):
    """Theme actions of one window's View menu. A QObject child of the window, so its connection to
    the theme manager ends with the window (a plain function would outlive the deleted actions)."""

    def __init__(self, window, menu):
        super().__init__(window)
        self.group = []
        for mode in theme.MODES:
            a = QAction(theme.MODE_LABELS[mode], window, checkable=True)
            a.setChecked(theme.manager().mode == mode)
            a.triggered.connect(lambda _=False, mode=mode: theme.manager().set_mode(mode))
            menu.addAction(a)
            self.group.append((mode, a))
        theme.manager().changed.connect(self.sync)

    def sync(self):
        for mode, a in self.group:
            a.setChecked(theme.manager().mode == mode)


def build_view_menu(window, menubar):
    m = menubar.addMenu(tr("&View"))
    window._theme_menu = _ThemeMenu(window, m.addMenu(tr("Theme")))
    build_language_menu(window, m.addMenu(tr("Language")))
    return m


def build_language_menu(window, menu):
    """Interface languages; the choice is used from the next start (offers to restart now)."""
    current = i18n.saved_language()
    group = QActionGroup(menu)
    group.setExclusive(True)
    actions = {}
    for code, name in i18n.LANGUAGES.items():
        a = QAction(name, window, checkable=True)
        a.setChecked(code == current)
        a.triggered.connect(lambda *_, code=code: change_language(window, code, actions))
        group.addAction(a)
        menu.addAction(a)
        actions[code] = a
    if os.environ.get(i18n.ENV):
        menu.addSeparator()
        note = menu.addAction(tr("Set by {0}={1} for this run").format(i18n.ENV, os.environ[i18n.ENV]))
        note.setEnabled(False)


def change_language(window, code, actions=None):
    if code == i18n.saved_language():
        if actions and code in actions:
            actions[code].setChecked(True)
        return
    try:
        i18n.set_saved_language(code)
    except OSError as exc:
        if actions:
            cur = i18n.saved_language()
            if cur in actions:
                actions[cur].setChecked(True)
        QMessageBox.critical(window, APP_NAME, str(exc))
        return
    if actions and code in actions:
        actions[code].setChecked(True)
    if ui.confirm(window, tr("Language"), tr("The language changes when {0} starts again. Restart now?").format(APP_NAME),
                  ok=tr("Restart"), cancel=tr("Later")):
        restart_application()


def _restart_args():
    """Arguments to re-launch the application (handles both studio.py and python -m axil_studio)."""
    main_mod = sys.modules.get("__main__")
    spec = getattr(main_mod, "__spec__", None)
    if spec is not None and spec.name == "axil_studio.__main__":
        return ["-m", "axil_studio"] + sys.argv[1:]
    if sys.argv and os.path.basename(sys.argv[0]) == "__main__.py":
        return ["-m", "axil_studio"] + sys.argv[1:]
    return list(sys.argv)


def restart_application():
    """Close every window (they ask about unsaved changes) and start the program again."""
    from PySide6.QtCore import QProcess
    app = QApplication.instance()
    app.closeAllWindows()
    if any(w.isVisible() for w in app.topLevelWidgets() if isinstance(w, QMainWindow)):
        return                                  # a window was kept open (unsaved changes): no restart
    QProcess.startDetached(sys.executable, _restart_args())
    app.quit()


# ============================================================================ home page
class ToolCard(ui.Card):
    def __init__(self, tool_cls, open_cb):
        super().__init__(hover=True, margins=18, spacing=12)
        self.tool_cls = tool_cls
        self.tile = QLabel()
        self.tile.setObjectName("IconTile")
        self.tile.setFixedSize(42, 42)
        self.tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lay.addWidget(self.tile)
        self.lay.addWidget(ui.label(tool_cls.title, "cardTitle", wrap=True))
        self.lay.addWidget(ui.label(tool_cls.description, "muted", wrap=True), 1)
        row = QHBoxLayout()
        row.addWidget(ui.button(tr("Open"), "external", "primary", open_cb, tr("Open the tool in its own window")))
        row.addStretch(1)
        self.state = ui.Pill(tr("open"), "ok")
        self.state.hide()
        row.addWidget(self.state)
        self.lay.addLayout(row)
        self.setMinimumHeight(220)
        self.retint()

    def retint(self):
        self.tile.setPixmap(icons.pixmap(self.tool_cls.icon_name, 22, theme.manager().tokens["accent"]))


class HomePage(QWidget):
    def __init__(self, hub):
        super().__init__()
        self.setObjectName("Page")
        self.hub, self.ctx = hub, hub.ctx
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        inner = QWidget()
        inner.setObjectName("Page")
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(22)

        head = QVBoxLayout()
        head.setSpacing(4)
        trow = QHBoxLayout()
        trow.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(QSize(40, 40)))
        trow.addWidget(logo)
        trow.addWidget(ui.label(APP_NAME, "title"))
        trow.addWidget(ui.Pill(version_text(), "accent"), 0, Qt.AlignmentFlag.AlignVCenter)
        trow.addStretch(1)
        head.addLayout(trow)
        head.addWidget(ui.label(tr("Register maps, address maps and on-hardware testing for AXI4-Lite slave IPs."),
                                "subtitle", wrap=True))
        lay.addLayout(head)

        # ---- project card
        self.proj = ui.Card(tr("Project"))
        self.proj_name = ui.label("", "cardTitle")
        self.proj_path = ui.label("", "muted", wrap=True)
        self.proj_stats = QHBoxLayout()
        self.proj_stats.setSpacing(6)
        info = QVBoxLayout()
        info.setSpacing(4)
        info.addWidget(self.proj_name)
        info.addWidget(self.proj_path)
        info.addLayout(self.proj_stats)
        row = QHBoxLayout()
        row.addLayout(info, 1)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        btns.addWidget(ui.button(tr("New"), "file", None, hub.actions.new))
        btns.addWidget(ui.button(tr("Open…"), "folder", None, lambda: hub.actions.open()))
        btns.addSpacing(8)
        self.lib_btn = ui.button(tr("Manage IPs"), "library", "primary", lambda: hub.open_tool_window("library"),
                                 tr("IP Library: the project's IPs and their address-map instances, IP libraries (Ctrl+6)"))
        btns.addWidget(self.lib_btn)
        row.addLayout(btns)
        self.proj.lay.addLayout(row)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        self.proj.lay.addLayout(self.recent_box)
        lay.addWidget(self.proj)

        # ---- tool cards
        lay.addWidget(ui.label(tr("TOOLS"), "section"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = []
        # IP Library is not a step of the flow: it opens from the project card ("IP'leri yönet")
        for i, tool in enumerate(t for t in TOOLS if t.tool_id != "library"):
            card = ToolCard(tool, lambda _=False, t=tool: hub.open_tool_window(t.tool_id))
            grid.addWidget(card, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
            self.cards.append(card)
        lay.addLayout(grid)

        # ---- flow hint
        flow = ui.Card(tr("Workflow"), margins=18)
        steps = [
            ("1", "Register Map Editor", tr("Define the IP's registers and bit fields and save it to the project.")),
            ("2", "Slave Interface Wrapper", tr("Generate the slave HDL, testbench, C header and documentation.")),
            ("3", "Address Map Editor", tr("Place IP instances, external slaves and subsystems on the system bus.")),
            ("4", "Interconnect Configurator", tr("Configure the masters and generate the crossbar HDL.")),
            ("5", "UART-to-AXI Debug Bridge", tr("Access the registers on hardware through the same address map.")),
            ("·", "HDL Generator", tr("Set up IP blocks with meaningful values (clock, baud rate …) and write their parameterless HDL Designer blocks.")),
            ("·", tr("Manage IPs (IP Library)"), tr("Reuse IPs across projects through versioned IP libraries.")),
        ]
        for num, name, text in steps:
            r = QHBoxLayout()
            r.setSpacing(12)
            n = ui.Pill(num, "accent")
            n.setFixedWidth(26)
            n.setAlignment(Qt.AlignmentFlag.AlignCenter)
            r.addWidget(n, 0, Qt.AlignmentFlag.AlignTop)
            c = QVBoxLayout()
            c.setSpacing(1)
            c.addWidget(QLabel(f"<b>{name}</b>"))
            c.addWidget(ui.label(text, "muted", wrap=True))
            r.addLayout(c, 1)
            flow.lay.addLayout(r)
        lay.addWidget(flow)
        lay.addStretch(1)

        self.ctx.state_changed.connect(self.refresh)
        self.ctx.modified.connect(lambda *_: self.refresh())
        self.ctx.replaced.connect(self.refresh)
        theme.manager().changed.connect(self.retint)
        self.refresh()

    def retint(self):
        for c in self.cards:
            c.retint()
        ui.refresh_icons(self)

    def refresh(self):
        p = self.ctx.project
        self.proj_name.setText(self.ctx.display_name)
        self.proj_path.setText(self.ctx.path or tr("Not saved"))
        while self.proj_stats.count():
            w = self.proj_stats.takeAt(0).widget()
            if w:
                w.deleteLater()
        nregs = sum(len(ip.registers) for ip in p.ips)
        for text in (tr("{0} IPs").format(len(p.ips)), tr("{0} registers").format(nregs), tr("{0} slaves in the address map").format(len(p.leaf_instances()))):
            self.proj_stats.addWidget(ui.Pill(text))
        self.proj_stats.addStretch(1)

        while self.recent_box.count():
            w = self.recent_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        recent = [r for r in self.ctx.recent() if os.path.abspath(r) != os.path.abspath(self.ctx.path or "")]
        if recent:
            self.recent_box.addWidget(ui.Divider())
            self.recent_box.addWidget(ui.label(tr("RECENT PROJECTS"), "section"))
            for path in recent[:5]:
                b = ui.button(os.path.basename(path), None, "link", lambda _=False, p=path: self.hub.actions.open(p),
                              path)
                b.setSizePolicy(b.sizePolicy().horizontalPolicy(), b.sizePolicy().verticalPolicy())
                self.recent_box.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)


# ============================================================================ hub window
class HubWindow(QMainWindow):
    """Launcher: project + tool cards. Every tool opens in its own window that shares the project."""

    def __init__(self, ctx: ProjectContext):
        super().__init__()
        self.ctx = ctx
        self.actions = ProjectActions(self, ctx)
        self.tool_windows = {}
        self._closing = False
        self.resize(1080, 760)
        self.setMinimumSize(QSize(860, 600))
        self.home = HomePage(self)
        self.setCentralWidget(self.home)

        mb = self.menuBar()
        self.actions.build_menu(mb)
        self.actions.build_edit_menu(mb)
        build_view_menu(self, mb)
        tm = mb.addMenu(tr("&Tools"))
        for i, tool in enumerate(TOOLS):
            a = tm.addAction(tool.title, lambda *_, t=tool: self.open_tool_window(t.tool_id))
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
        hm = mb.addMenu(tr("&Help"))
        hm.addAction(tr("About {0}").format(APP_NAME), self._about)
        hm.addAction(tr("Open log folder"), self._open_logs)
        self.statusBar().showMessage(tr("Ready"))
        ctx.state_changed.connect(self._update_title)
        self._update_title()

    def open_tool_window(self, tool_id):
        w = self.tool_windows.get(tool_id)
        if w is None:
            w = ToolWindow(self.ctx, tool_id, owned=False)
            w.destroyed.connect(lambda *_, k=tool_id: self._closed(k))
            self.tool_windows[tool_id] = w
            g = self.geometry()
            w.move(g.left() + 40 * (1 + list(TOOLS_BY_ID).index(tool_id)), g.top() + 30)
            w.show()
        else:
            w.showNormal()
        w.raise_()
        w.activateWindow()
        self._sync_cards()
        return w

    def _closed(self, tool_id):
        self.tool_windows.pop(tool_id, None)
        if not self._closing:
            self._sync_cards()

    def _sync_cards(self):
        try:
            for c in self.home.cards:
                c.state.setVisible(c.tool_cls.tool_id in self.tool_windows)
        except RuntimeError:   # hub already torn down (application exit)
            pass

    def _update_title(self):
        self.setWindowTitle(f"{APP_NAME} {version_text()} | {self.ctx.display_name}")

    def _open_logs(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        os.makedirs(errors.log_dir(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(errors.log_dir()))

    def _about(self):
        QMessageBox.about(self, tr("About {0}").format(APP_NAME),
                          f"<b>{APP_NAME}</b> {version_text()}<br><br>"
                          + tr("Register maps, address maps and on-hardware testing for AXI4-Lite slave IPs.")
                          + "<br><br>" + tr("Includes ZipCPU wb2axip cores (Apache 2.0, Gisselquist Technology LLC)."))

    def closeEvent(self, ev):
        wins = list(self.tool_windows.values())
        if not all(w.page.can_close() for w in wins) or not self.actions.confirm_discard():
            ev.ignore()
            return
        self._closing = True
        for w in wins:
            w.page.shutdown()
            w.force_close()
        ev.accept()


# ============================================================================ stand-alone tool
class ToolWindow(QMainWindow):
    """One tool in its own window. `owned`: the window owns the project (stand-alone launch)
    rather than sharing the hub's, so it asks to save on close."""

    def __init__(self, ctx: ProjectContext, tool_id, owned=True):
        super().__init__()
        self.ctx, self.owned = ctx, owned
        cls = TOOLS_BY_ID[tool_id]
        self.page = cls(ctx)
        self.tool_title = f"{cls.title} {version_text()}"
        self.setCentralWidget(self.page)
        self.resize(1180, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.actions = ProjectActions(self, ctx)
        self._forced = False
        self.actions.build_menu(self.menuBar())
        self.actions.build_edit_menu(self.menuBar(), self.page)
        self.page.build_menus(self.menuBar())
        build_view_menu(self, self.menuBar())
        issues = getattr(self.page, "issues", None)
        if issues is not None:            # validation lives in the bottom line, not in a panel
            self.statusBar().addPermanentWidget(issues)
        self.statusBar().showMessage(tr("Ready"))
        ctx.state_changed.connect(self._update_title)
        self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{APP_NAME} {self.tool_title} | {self.ctx.display_name}")

    def force_close(self):
        """Close without asking (the hub already asked)."""
        self._forced = True
        self.close()

    def closeEvent(self, ev):
        if not self._forced and (not self.page.can_close() or
                                 (self.owned and not self.actions.confirm_discard())):
            ev.ignore()
            return
        if not self._forced:
            self.page.shutdown()
        ev.accept()


# ============================================================================ entry point
def install_qt_translations(app):
    """Qt's own dialogs and buttons (file dialogs, Yes/No, Save/Cancel ...) in the interface language."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    code = i18n.language()
    app._translators = []
    if code == "en":
        QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
        return
    QLocale.setDefault(QLocale(code))
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in (f"qtbase_{code}", f"qt_{code}"):
        qtr = QTranslator(app)
        if qtr.load(name, path):
            app.installTranslator(qtr)
            app._translators.append(qtr)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="axil_studio", description=APP_NAME)
    ap.add_argument("--tool", choices=sorted(TOOLS_BY_ID), help=tr("open only this tool in its own window"))
    ap.add_argument("project", nargs="?", help=tr(".axproj.json file"))
    args = ap.parse_args(argv)

    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationDisplayName(APP_NAME)
    install_qt_translations(app)
    errors.install()                             # unexpected errors: log file + dialog
    app.setWindowIcon(icons.app_icon())
    if sys.platform.startswith("win"):           # own task-bar entry and icon, not python.exe's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AXILStudio.AXILStudio")
        except (AttributeError, OSError):
            pass
    theme.install(app)

    ctx = ProjectContext(app)
    if args.project:
        try:
            ctx.open(args.project)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(None, tr("Could not open the project"), f"{args.project}\n\n{exc}")

    win = ToolWindow(ctx, args.tool) if args.tool else HubWindow(ctx)
    win.show()
    return app.exec()
