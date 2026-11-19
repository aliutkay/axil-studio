"""Building blocks shared by the hub and the tools."""

import concurrent.futures
import os
import subprocess
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from axil_studio import ROOT, icons, theme


def label(text="", role=None, wrap=False):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if wrap:
        w.setWordWrap(True)
    return w


def button(text, icon=None, variant=None, slot=None, tip=None):
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon:
        b.setProperty("iconName", icon)
        b.setIcon(icons.icon(icon, "accent_text" if variant == "primary" else "text", 16))
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def refresh_icons(root: QWidget):
    """Re-tint every button icon after a theme change."""
    for b in root.findChildren(QPushButton):
        name = b.property("iconName")
        if name:
            b.setIcon(icons.icon(name, "accent_text" if b.property("variant") == "primary" else "text", 16))


class Card(QFrame):
    """Rounded surface with an optional title row."""

    def __init__(self, title=None, subtitle=None, hover=False, margins=16, spacing=10):
        super().__init__()
        self.setObjectName("Card")
        if hover:
            self.setProperty("hover", "true")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(spacing)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(title, "cardTitle"))
            if subtitle:
                col.addWidget(label(subtitle, "muted", wrap=True))
            self.header.addLayout(col, 1)
            self.lay.addLayout(self.header)

    def add_action(self, widget):
        self.header.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        return widget


class Pill(QLabel):
    """Small status badge; state: "", "ok", "err", "accent"."""

    def __init__(self, text="", state=""):
        super().__init__(text)
        self.setProperty("role", "pill")
        self.set(text, state)

    def set(self, text, state=""):
        self.setText(text)
        self.setProperty("state", state)
        theme.repolish(self)


class PageHeader(QWidget):
    """Title + subtitle on the left, action buttons on the right."""

    def __init__(self, title, subtitle="", icon_name=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.icon_name = icon_name
        self.tile = None
        if icon_name:
            self.tile = QLabel()
            self.tile.setObjectName("IconTile")
            self.tile.setFixedSize(44, 44)
            self.tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "title"))
        if subtitle:
            col.addWidget(label(subtitle, "subtitle", wrap=True))
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)
        self.retint()

    def retint(self):
        if self.tile:
            self.tile.setPixmap(icons.pixmap(self.icon_name, 24, theme.manager().tokens["accent"]))

    def add(self, widget):
        self.actions.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget


class ActionBar(QFrame):
    """Row of flat command buttons under a page header."""

    def __init__(self):
        super().__init__()
        self.setObjectName("ActionBar")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(6, 4, 6, 4)
        self.lay.setSpacing(2)

    def add(self, text, icon=None, slot=None, tip=None, enabled=True):
        b = button(text, icon, None, slot, tip)
        b.setEnabled(enabled)
        self.lay.addWidget(b)
        return b

    def sep(self):
        s = QFrame()
        s.setObjectName("ActionSep")
        s.setFixedHeight(22)
        self.lay.addWidget(s)

    def stretch(self):
        self.lay.addStretch(1)


class Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Divider")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class IssueList(QListWidget):
    """Validation results: green check when empty, one row per problem otherwise."""

    def __init__(self, ok_text="Sorun yok", compact=False):
        super().__init__()
        self.ok_text = ok_text
        self.compact = compact  # height follows the number of rows (up to 4)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setWordWrap(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(120)
        self._issues = []

    def set_issues(self, issues):
        self._issues = list(issues)
        self.clear()
        if not self._issues:
            it = QListWidgetItem(icons.icon("check", "success", 16), self.ok_text)
            it.setForeground(theme.manager().color("success"))
            self.addItem(it)
        for msg in self._issues:
            it = QListWidgetItem(icons.icon("alert", "danger", 16), msg)
            self.addItem(it)
        if self.compact:
            rows = min(self.count(), 4)
            self.setFixedHeight(rows * max(self.sizeHintForRow(0), 22) + 2 * self.frameWidth() + 8)

    def retint(self):
        self.set_issues(self._issues)


class Async(QObject):
    """Runs blocking calls (serial I/O, file generation) on one worker thread and
    delivers the result back on the GUI thread: run(fn, on_ok, on_err)."""

    _done = Signal(object, object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._done.connect(self._deliver)

    def run(self, fn, on_ok=None, on_err=None):
        fut = self._pool.submit(fn)
        fut.add_done_callback(lambda f: self._done.emit(f, on_ok, on_err, None))

    @staticmethod
    def _deliver(fut, on_ok, on_err, _):
        exc = fut.exception()
        if exc is not None:
            if on_err:
                on_err(exc)
        elif on_ok:
            on_ok(fut.result())

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


def _box(parent, title, text, icon, buttons, default):
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(icon)
    made = {}
    for key, label, role in buttons:
        made[key] = box.addButton(label, role)
    box.setDefaultButton(made[default])
    box.exec()
    clicked = box.clickedButton()
    return next((k for k, b in made.items() if b is clicked), None)


def ask_save(parent, title, text):
    """Unsaved changes: "save", "discard" or None (cancel). Turkish button texts."""
    from PySide6.QtWidgets import QMessageBox
    R = QMessageBox.ButtonRole
    return _box(parent, title, text, QMessageBox.Icon.Warning,
                [("save", "Kaydet", R.AcceptRole), ("discard", "Kaydetme", R.DestructiveRole),
                 ("cancel", "Vazgeç", R.RejectRole)], "save") if text else None


def confirm(parent, title, text, ok="Sil", cancel="Vazgeç"):
    """Yes/no question with explicit button texts; True when `ok` is chosen."""
    from PySide6.QtWidgets import QMessageBox
    R = QMessageBox.ButtonRole
    return _box(parent, title, text, QMessageBox.Icon.Question,
                [("ok", ok, R.AcceptRole), ("cancel", cancel, R.RejectRole)], "ok") == "ok"


def launch_legacy(script):
    """Open one of the original Tkinter tools while its features are being ported."""
    subprocess.Popen([sys.executable, os.path.join(ROOT, script)], cwd=ROOT)


class DetachableTabs(QTabWidget):
    """Tab widget whose pages can be moved into their own window (for a second monitor):
    double-click a tab, use its context menu or the corner button. Closing the window puts the
    page back where it was. Use show_page() / is_shown() instead of tab indexes."""

    def __init__(self, owner_title=""):
        super().__init__()
        self.owner_title = owner_title
        self.setDocumentMode(True)
        self._windows = {}          # page -> (window, title, index)
        self.tabBar().tabBarDoubleClicked.connect(self.detach)
        self.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self._menu)
        self.detach_btn = button("Ayrı pencere", "external", "ghost", lambda: self.detach(self.currentIndex()),
                                 "Bu sekmeyi ayrı bir pencerede aç (çift tık da olur)")
        self.setCornerWidget(self.detach_btn, Qt.Corner.TopRightCorner)

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        i = self.tabBar().tabAt(pos)
        if i < 0:
            return
        m = QMenu(self)
        m.addAction("Ayrı pencerede aç", lambda: self.detach(i))
        m.exec(self.tabBar().mapToGlobal(pos))

    def detach(self, i):
        if i < 0 or self.count() <= 1:
            return            # keep at least one page in the main window
        page, title = self.widget(i), self.tabText(i)
        self.removeTab(i)
        win = _DetachedWindow(self, page, title)
        self._windows[page] = (win, title, i)
        geo = self.window().geometry()
        win.resize(max(700, self.width()), max(500, self.height()))
        win.move(geo.left() + 60, geo.top() + 60)
        win.show()
        self.detach_btn.setEnabled(self.count() > 1)

    def reattach(self, page):
        if page not in self._windows:
            return
        win, title, i = self._windows.pop(page)
        self.insertTab(min(i, self.count()), page, title)
        page.show()
        self.setCurrentWidget(page)
        self.detach_btn.setEnabled(self.count() > 1)

    def is_shown(self, page):
        """True when the page is visible: the current tab or in its own window."""
        return page in self._windows or self.currentWidget() is page

    def show_page(self, page):
        if page in self._windows:
            win = self._windows[page][0]
            win.showNormal()
            win.raise_()
            win.activateWindow()
        else:
            self.setCurrentWidget(page)


class _DetachedWindow(QWidget):
    def __init__(self, tabs, page, title):
        super().__init__(tabs, Qt.WindowType.Window)      # child window: closes with the tool window
        self.tabs, self.page = tabs, page
        self.setObjectName("Page")
        self.setWindowTitle(f"{title} — {tabs.owner_title}" if tabs.owner_title else title)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label(title, "cardTitle"))
        top.addStretch(1)
        top.addWidget(button("Pencereye geri al", "arrow", "ghost", self.close, "Sekmeyi ana pencereye geri koy"))
        lay.addLayout(top)
        lay.addWidget(page, 1)
        page.show()

    def closeEvent(self, ev):
        self.layout().removeWidget(self.page)
        self.tabs.reattach(self.page)
        super().closeEvent(ev)
