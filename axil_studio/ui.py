"""Building blocks shared by the hub and the tools."""

import concurrent.futures

from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QToolBar, QToolButton,
                               QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from axil_studio import icons, theme


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


class ToolHeader(ActionBar):
    """The single bar on top of a tool: icon, name and version on the left, the tool's commands right
    after them, status pills and the main action on the right. Leaves the window's height to the work."""

    def __init__(self, title, icon_name=None, version_label="", tip=""):
        super().__init__()
        self.icon_name = icon_name
        self.lay.setContentsMargins(10, 4, 8, 4)
        self.tile = None
        if icon_name:
            self.tile = QLabel()
            self.tile.setObjectName("IconTile")
            self.tile.setFixedSize(30, 30)
            self.tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lay.addWidget(self.tile)
            self.lay.addSpacing(6)
        name = label(title, "cardTitle")
        if tip:
            name.setToolTip(tip)
        self.lay.addWidget(name)
        if version_label:
            self.lay.addSpacing(6)
            self.lay.addWidget(Pill(version_label))
        self.lay.addSpacing(10)
        # the commands live in a tool bar: when the window is narrow the ones that do not fit move
        # into its » menu instead of forcing the window wider
        self.cmds = QToolBar()
        self.cmds.setObjectName("HeaderCommands")
        self.cmds.setMovable(False)
        self.cmds.setFloatable(False)
        self.cmds.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lay.addWidget(self.cmds, 1)
        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        self.lay.addLayout(self.right)
        self._first_sep = True
        self.retint()

    def retint(self):
        if self.tile:
            self.tile.setPixmap(icons.pixmap(self.icon_name, 18, theme.manager().tokens["accent"]))

    def add(self, text, icon=None, slot=None, tip=None, enabled=True):
        if not self.cmds.actions():
            self.cmds.addSeparator()           # a divider between the name and the first command
        b = button(text, icon, None, slot, tip)
        b.setEnabled(enabled)
        self.cmds.addWidget(b)
        return b

    def sep(self):
        self.cmds.addSeparator()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Qt's overflow button of the commands bar: give it a visible »
        ext = self.cmds.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext is not None and ext.property("axilMore") is None:
            ext.setProperty("axilMore", True)
            ext.setIcon(icons.icon("more", "accent", 14))
            ext.setToolTip("Sığmayan komutlar")

    def stretch(self):
        pass                                    # the bar has its own stretch before the right side

    def add_right(self, widget):
        self.right.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget


class Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Divider")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class IssueList(QToolButton):
    """Validation result in the window's status bar (bottom line): a green check and a summary
    when there is nothing to fix, otherwise a red alert with the number of problems and the first
    one. Hover shows every problem, a click lists them. ToolWindow puts a page's `issues` there."""

    MAX_CHARS = 140

    def __init__(self, ok_text="Hata yok", compact=False):
        super().__init__()
        self.ok_text = ok_text
        self._issues = []
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._show_list)
        self.set_issues([])

    def set_issues(self, issues):
        self._issues = list(issues)
        t = theme.manager().tokens
        if not self._issues:
            text, color, icon = self.ok_text, t["success"], icons.icon("check", "success", 16)
            self.setToolTip(self.ok_text)
        else:
            n = len(self._issues)
            text = (f"{n} hata: " if n > 1 else "") + self._issues[0]
            color, icon = t["danger"], icons.icon("alert", "danger", 16)
            self.setToolTip("\n".join(f"• {m}" for m in self._issues) + "\n\nTıkla: listeyi aç")
        if len(text) > self.MAX_CHARS:
            text = text[:self.MAX_CHARS - 1] + "…"
        self.setText(text)
        self.setIcon(icon)
        self.setStyleSheet(f"QToolButton {{ color: {color}; border: none; background: transparent; padding: 0 6px; }}"
                           f"QToolButton:hover {{ background: {t['hover']}; }}")

    def _show_list(self):
        if not self._issues:
            return
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        for msg in self._issues:
            a = m.addAction(icons.icon("alert", "danger", 16), msg)
            a.setEnabled(False)
        m.exec(self.mapToGlobal(QPoint(0, -m.sizeHint().height())))     # just above the status bar

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
