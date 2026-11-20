"""AXIL Studio main window (hub) and the stand-alone tool window."""

import argparse
import os
import sys

from PySide6.QtCore import QCoreApplication, QSize, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from axil_studio import APP_NAME, APP_VERSION, ORG_NAME, ROOT, icons, theme, ui, version_text
from axil_studio.project import PROJECT_FILTER, ProjectContext
from axil_studio.tools import TOOLS, TOOLS_BY_ID

EXAMPLE_PROJECT = os.path.join(ROOT, "examples", "gpio_demo.axproj.json")


# ============================================================================ project menu
class ProjectActions:
    """New / open / save handling shared by the hub and the stand-alone windows."""

    def __init__(self, window: QMainWindow, ctx: ProjectContext):
        self.win, self.ctx = window, ctx

    def build_menu(self, menubar):
        m = menubar.addMenu("&Proje")
        self._act(m, "Yeni proje", self.new, QKeySequence.StandardKey.New)
        self._act(m, "Aç…", self.open, QKeySequence.StandardKey.Open)
        self.recent_menu = m.addMenu("Son açılanlar")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._act(m, "Kaydet", self.save, QKeySequence.StandardKey.Save)
        self._act(m, "Farklı kaydet…", self.save_as, QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        self._act(m, "Çıkış", self.win.close, QKeySequence.StandardKey.Quit)
        return m

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
            a = self.recent_menu.addAction("(boş)")
            a.setEnabled(False)
        for p in items:
            self.recent_menu.addAction(os.path.basename(p), lambda p=p: self.open(p))

    def confirm_discard(self):
        if not self.ctx.dirty:
            return True
        r = ui.ask_save(self.win, APP_NAME, f"“{self.ctx.project.name}” projesinde kaydedilmemiş değişiklikler var.")
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
            path, _ = QFileDialog.getOpenFileName(self.win, "Proje aç", os.path.dirname(self.ctx.path or ROOT),
                                                  PROJECT_FILTER)
            if not path:
                return False
        try:
            self.ctx.open(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            QMessageBox.critical(self.win, "Proje açılamadı", f"{path}\n\n{exc}")
            return False
        return True

    def save(self):
        if not self.ctx.path:
            return self.save_as()
        try:
            self.ctx.save()
        except OSError as exc:
            QMessageBox.critical(self.win, "Kaydedilemedi", str(exc))
            return False
        return True

    def save_as(self):
        start = self.ctx.path or os.path.join(ROOT, f"{self.ctx.project.name}.axproj.json")
        path, _ = QFileDialog.getSaveFileName(self.win, "Projeyi kaydet", start, PROJECT_FILTER)
        if not path:
            return False
        if not path.endswith(".json"):
            path += ".axproj.json"
        try:
            self.ctx.save(path)
        except OSError as exc:
            QMessageBox.critical(self.win, "Kaydedilemedi", str(exc))
            return False
        return True


def build_view_menu(window, menubar):
    m = menubar.addMenu("&Görünüm")
    tm = m.addMenu("Tema")
    group = []
    for mode in theme.MODES:
        a = QAction(theme.MODE_LABELS[mode], window, checkable=True)
        a.setChecked(theme.manager().mode == mode)
        a.triggered.connect(lambda _=False, mode=mode: theme.manager().set_mode(mode))
        tm.addAction(a)
        group.append((mode, a))

    def sync():
        for mode, a in group:
            a.setChecked(theme.manager().mode == mode)
    theme.manager().changed.connect(sync)
    return m


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
        self.lay.addWidget(ui.Pill(version_text(tool_cls.version)), 0, Qt.AlignmentFlag.AlignLeft)
        self.lay.addWidget(ui.label(tool_cls.description, "muted", wrap=True), 1)
        row = QHBoxLayout()
        row.addWidget(ui.button("Aç", "external", "primary", open_cb, "Aracı kendi penceresinde aç"))
        row.addStretch(1)
        self.state = ui.Pill("açık", "ok")
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
        trow.addWidget(ui.label(APP_NAME, "title"))
        trow.addWidget(ui.Pill(version_text(), "accent"), 0, Qt.AlignmentFlag.AlignVCenter)
        trow.addStretch(1)
        head.addLayout(trow)
        head.addWidget(ui.label("AXI4-Lite slave IP'leri için register tanımı, adres haritası ve donanım üzerinde test.",
                                "subtitle", wrap=True))
        lay.addLayout(head)

        # ---- project card
        self.proj = ui.Card("Proje")
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
        btns.addWidget(ui.button("Yeni", "file", None, hub.actions.new))
        btns.addWidget(ui.button("Aç…", "folder", None, lambda: hub.actions.open()))
        btns.addWidget(ui.button("Örnek proje", "play", "ghost", lambda: hub.actions.open(EXAMPLE_PROJECT)))
        row.addLayout(btns)
        self.proj.lay.addLayout(row)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        self.proj.lay.addLayout(self.recent_box)
        lay.addWidget(self.proj)

        # ---- tool cards
        lay.addWidget(ui.label("ARAÇLAR", "section"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.cards = []
        for i, tool in enumerate(TOOLS):
            card = ToolCard(tool, lambda _=False, t=tool: hub.open_tool_window(t.tool_id))
            grid.addWidget(card, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
            self.cards.append(card)
        lay.addLayout(grid)

        # ---- flow hint
        flow = ui.Card("İş akışı", margins=18)
        steps = [
            ("1", "Register Map Editor", "IP'nin register haritasını tasarla ve projeye kaydet (adres yok)."),
            ("2", "Slave Interface Wrapper", "Register haritasından HDL, testbench, header ve dokümanı üret."),
            ("3", "Address Map Editor", "IP'leri adres haritasına yerleştir; aynı IP birden çok kez kullanılabilir."),
            ("4", "Interconnect Configurator", "Master'ları ve port stilini seç, axilxbar wrapper'ını üret."),
            ("5", "UART-to-AXI Debug Bridge", "Aynı adres haritasıyla donanımdaki registerlara eriş."),
            ("·", "IP Library", "Projedeki tüm IP'leri gör; IP'leri kütüphanelere kaydet, kütüphanelerden projeye al."),
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
        self.proj_path.setText(self.ctx.path or "Kaydedilmedi")
        while self.proj_stats.count():
            w = self.proj_stats.takeAt(0).widget()
            if w:
                w.deleteLater()
        nregs = sum(len(ip.registers) for ip in p.ips)
        for text in (f"{len(p.ips)} IP", f"{nregs} register", f"adres haritasında {len(p.instances)} slave"):
            self.proj_stats.addWidget(ui.Pill(text))
        self.proj_stats.addStretch(1)

        while self.recent_box.count():
            w = self.recent_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        recent = [r for r in self.ctx.recent() if os.path.abspath(r) != os.path.abspath(self.ctx.path or "")]
        if recent:
            self.recent_box.addWidget(ui.Divider())
            self.recent_box.addWidget(ui.label("SON AÇILANLAR", "section"))
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
        build_view_menu(self, mb)
        tm = mb.addMenu("&Araçlar")
        for i, tool in enumerate(TOOLS):
            a = tm.addAction(tool.title, lambda t=tool: self.open_tool_window(t.tool_id))
            a.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
        hm = mb.addMenu("&Yardım")
        hm.addAction(f"{APP_NAME} hakkında", self._about)
        self.statusBar().showMessage("Hazır")
        ctx.state_changed.connect(self._update_title)
        self._update_title()

    def show_tool(self, key):
        """Kept for callers of the old sidebar API."""
        if key != "home":
            self.open_tool_window(key)

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
        self.setWindowTitle(f"{self.ctx.display_name} — {APP_NAME} {version_text()}")

    def _about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> {version_text()}<br><br>"
                          + "<br>".join(f"{t.title} — {version_text(t.version)}" for t in TOOLS))

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
        self.tool_title = f"{cls.title} {version_text(cls.version)}"
        self.setCentralWidget(self.page)
        self.resize(1180, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.actions = ProjectActions(self, ctx)
        self._forced = False
        self.actions.build_menu(self.menuBar())
        self.page.build_menus(self.menuBar())
        build_view_menu(self, self.menuBar())
        self.statusBar().showMessage("Hazır")
        ctx.state_changed.connect(self._update_title)
        self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{self.tool_title} — {self.ctx.display_name} — {APP_NAME}")

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
def install_turkish(app):
    """Turkish texts for Qt's own dialogs and buttons (file dialogs, Yes/No, Save/Cancel ...)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    QLocale.setDefault(QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey))
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    app._translators = []
    for name in ("qtbase_tr", "qt_tr"):
        tr = QTranslator(app)
        if tr.load(name, path):
            app.installTranslator(tr)
            app._translators.append(tr)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="axil_studio", description=APP_NAME)
    ap.add_argument("--tool", choices=sorted(TOOLS_BY_ID), help="yalnızca bu aracı ayrı pencerede aç")
    ap.add_argument("project", nargs="?", help=".axproj.json dosyası")
    args = ap.parse_args(argv)

    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationDisplayName(APP_NAME)
    install_turkish(app)
    theme.install(app)

    ctx = ProjectContext(app)
    if args.project:
        try:
            ctx.open(args.project)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(None, "Proje açılamadı", f"{args.project}\n\n{exc}")

    win = ToolWindow(ctx, args.tool) if args.tool else HubWindow(ctx)
    win.show()
    return app.exec()
