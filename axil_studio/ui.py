"""Building blocks shared by the hub and the tools."""

import concurrent.futures
import json

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QToolButton,
                               QPushButton, QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from axil_studio import icons, theme
from axil_studio.i18n import tr


def label(text="", role=None, wrap=False):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if wrap:
        w.setWordWrap(True)
    return w


class HelpButton(QToolButton):
    """A small "?" next to a title: its explanation shows on hover and stays on click, so panels
    carry short labels instead of paragraphs."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.help_text = text
        self.setObjectName("HelpButton")
        self.setText("?")
        self.setAutoRaise(True)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setToolTip(self._html())
        self.clicked.connect(self._show)

    def set_text(self, text):
        self.help_text = text
        self.setToolTip(self._html())
        self.setVisible(bool(text))

    def _html(self):
        body = self.help_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        return f"<div style='max-width: 360px'>{body}</div>"

    def _show(self):
        from PySide6.QtWidgets import QToolTip
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self._html(), self)


def section(title, help_text=None):
    """A section title (role "section"), with a help button when there is more to say."""
    if not help_text:
        return label(title, "section")
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(label(title, "section"))
    lay.addWidget(HelpButton(help_text))
    lay.addStretch(1)
    return box


def note(text, help_text=None):
    """A one-line muted note, with a help button for the longer explanation."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    box.label = label(text, "muted", wrap=True)
    lay.addWidget(box.label, 1)
    box.help = HelpButton(help_text or "")
    box.help.setVisible(bool(help_text))
    lay.addWidget(box.help, 0, Qt.AlignmentFlag.AlignTop)
    return box


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


def row_icon(name, hover_token):
    """Faint icon for a list row; it takes its full colour while the mouse is on it."""
    t = theme.manager().tokens
    faint = theme.mix(t["muted"], t["surface"], 0.45)
    ic = QIcon()
    ic.addPixmap(icons.pixmap(name, 15, faint), QIcon.Mode.Normal)
    ic.addPixmap(icons.pixmap(name, 15, t[hover_token]), QIcon.Mode.Active)
    return ic


class RowActions(QWidget):
    """Pencil (edit) and bin (delete) at the end of a list row."""

    def __init__(self, on_edit=None, on_delete=None, edit_tip=tr("Edit"), delete_tip=tr("Delete")):
        super().__init__()
        self.setObjectName("RowActions")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 4, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        self.buttons = []
        for name, token, slot, tip in (("edit", "accent", on_edit, edit_tip), ("trash", "danger", on_delete, delete_tip)):
            if slot is None:
                continue
            b = QToolButton()
            b.setObjectName("RowAction")
            b.setAutoRaise(True)
            b.setFixedSize(24, 22)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("iconSpec", (name, token))
            b.clicked.connect(slot)
            lay.addWidget(b)
            self.buttons.append(b)
        self.retint()
        theme.manager().changed.connect(self.retint)

    def retint(self):
        for b in self.buttons:
            b.setIcon(row_icon(*b.property("iconSpec")))

    @property
    def edit_button(self):
        return next((b for b in self.buttons if b.property("iconSpec")[0] == "edit"), None)

    @property
    def delete_button(self):
        return next((b for b in self.buttons if b.property("iconSpec")[0] == "trash"), None)


ROW_ACTIONS_WIDTH = 56


def tree_row_actions(tree, item, col, on_edit=None, on_delete=None, **tips):
    """Put RowActions into a QTreeWidget row; a click selects that row first."""
    def run(fn):
        def go():
            tree.setCurrentItem(item)
            fn()
        return go if fn else None
    w = RowActions(run(on_edit), run(on_delete), **tips)
    tree.setItemWidget(item, col, w)
    return w


def table_row_actions(table, row, col, on_edit=None, on_delete=None, **tips):
    """Put RowActions into a QTableWidget row; a click selects that row first."""
    anchor = table.item(row, 0)

    def run(fn):
        def go():
            r = table.row(anchor) if anchor is not None else row
            table.setCurrentCell(r, 0)
            table.selectRow(r)
            fn()
        return go if fn else None
    w = RowActions(run(on_edit), run(on_delete), **tips)
    table.setCellWidget(row, col, w)
    return w


def actions_column(header, col):
    """A fixed, narrow last column for the row icons."""
    from PySide6.QtWidgets import QHeaderView
    header.setStretchLastSection(False)
    header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
    header.resizeSection(col, ROW_ACTIONS_WIDTH)


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
    after them, status and the main action on the right. Commands that do not fit the window's width
    are hidden and offered in the » menu (instead of forcing the window wider)."""

    SPACING = 2

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
            self.lay.addSpacing(8)
            self.lay.addWidget(label(version_label, "muted"))
        self.lay.addSpacing(10)
        self.cmds_w = QWidget()
        self.cmds_w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cmds_lay = QHBoxLayout(self.cmds_w)
        self.cmds_lay.setContentsMargins(0, 0, 0, 0)
        self.cmds_lay.setSpacing(self.SPACING)
        self.cmds_lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.cmds_lay.addStretch(1)
        self.cmds_w.installEventFilter(self)
        self.lay.addWidget(self.cmds_w, 1)
        self.more = QToolButton()
        self.more.setObjectName("MoreButton")
        self.more.setAutoRaise(True)
        self.more.setFixedSize(28, 28)
        self.more.setToolTip(tr("Commands that do not fit"))
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.clicked.connect(self._show_more)
        self.more.hide()
        self.lay.addWidget(self.more)
        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        self.lay.addSpacing(6)
        self.lay.addLayout(self.right)
        self._items = []                        # command buttons and separators, in order
        self.retint()

    def retint(self):
        if self.tile:
            self.tile.setPixmap(icons.pixmap(self.icon_name, 18, theme.manager().tokens["accent"]))
        self.more.setIcon(icons.icon("more", "accent", 16))

    def _insert(self, w):
        self.cmds_lay.insertWidget(self.cmds_lay.count() - 1, w)     # before the stretch
        self._items.append(w)
        QTimer.singleShot(0, self._fit)

    def add(self, text, icon=None, slot=None, tip=None, enabled=True):
        if not self._items:
            self.sep()                          # a divider between the name and the first command
        b = button(text, icon, None, slot, tip)
        b.setEnabled(enabled)
        self._insert(b)
        return b

    def sep(self):
        s = QFrame()
        s.setObjectName("ActionSep")
        s.setFixedHeight(22)
        self._insert(s)

    def stretch(self):
        pass                                    # the bar has its own stretch before the right side

    def add_right(self, widget):
        self.right.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget

    def eventFilter(self, obj, ev):
        if obj is self.cmds_w and ev.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def _fit(self):
        """Show the commands from the left while they fit; the rest go to the » menu."""
        if not self._items:
            return
        avail = self.cmds_w.width()
        widths = [w.sizeHint().width() for w in self._items]
        total = sum(widths) + self.SPACING * (len(widths) - 1)
        if total + self.SPACING <= avail + (self.more.width() + 6 if self.more.isVisible() else 0):
            cut = len(self._items)
        else:
            room = avail - self.SPACING - (0 if self.more.isVisible() else self.more.width() + 6)
            used, cut = 0, 0
            for i, w in enumerate(widths):
                need = used + w + (self.SPACING if i else 0)
                if need > room:
                    break
                used, cut = need, i + 1
        for i, w in enumerate(self._items):
            w.setVisible(i < cut)
        if 0 < cut < len(self._items) and isinstance(self._items[cut - 1], QFrame) \
                and not isinstance(self._items[cut - 1], QPushButton):
            self._items[cut - 1].hide()         # no separator right before the » button
        self.more.setVisible(cut < len(self._items))

    def hidden_commands(self):
        return [w for w in self._items if isinstance(w, QPushButton) and not w.isVisible()]

    def _show_more(self):
        self.more_menu().exec(self.more.mapToGlobal(QPoint(0, self.more.height())))

    def more_menu(self):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        hidden = [w for w in self._items if not w.isVisible()]
        for w in hidden:
            if isinstance(w, QPushButton):
                a = m.addAction(w.icon(), w.text(), w.click)
                a.setEnabled(w.isEnabled())
                a.setToolTip(w.toolTip())
            elif m.actions() and not m.actions()[-1].isSeparator():
                m.addSeparator()
        return m


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

    def __init__(self, ok_text=tr("No errors"), compact=False):
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
            text = (tr("{0} errors:").format(n).rstrip() + " " if n > 1 else "") + self._issues[0]
            color, icon = t["danger"], icons.icon("alert", "danger", 16)
            self.setToolTip("\n".join(f"• {m}" for m in self._issues) + tr("\n\nClick: open the list"))
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
        self._closed = False
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._done.connect(self._deliver)

    def run(self, fn, on_ok=None, on_err=None):
        if self._closed:
            return
        fut = self._pool.submit(fn)

        def done(f):
            if self._closed:
                return
            try:
                self._done.emit(f, on_ok, on_err, None)
            except RuntimeError:
                pass
        fut.add_done_callback(done)

    def _deliver(self, fut, on_ok, on_err, _):
        if self._closed or fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            if on_err:
                on_err(exc)
        elif on_ok:
            on_ok(fut.result())

    def shutdown(self):
        self._closed = True
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
                [("save", tr("Save"), R.AcceptRole), ("discard", tr("Don't save"), R.DestructiveRole),
                 ("cancel", tr("Cancel"), R.RejectRole)], "save") if text else None


def confirm(parent, title, text, ok=tr("Delete"), cancel=tr("Cancel")):
    """Yes/no question with explicit button texts; True when `ok` is chosen."""
    from PySide6.QtWidgets import QMessageBox
    R = QMessageBox.ButtonRole
    return _box(parent, title, text, QMessageBox.Icon.Question,
                [("ok", ok, R.AcceptRole), ("cancel", cancel, R.RejectRole)], "ok") == "ok"


class SpecView(QWidget):
    """Read-only view of an HDL spec (JSON, see core/hdlspec.py): exactly what the Verilog is made
    from, nothing can be edited here. Copy to the clipboard; save via `save_cb`."""

    def __init__(self, note, save_cb=None, save_text=tr("Save…"), save_tip=None, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.top = QHBoxLayout()
        self.note = label(note, "muted", wrap=True)
        self.top.addWidget(self.note, 1)
        self.copy_btn = button(tr("Copy"), "copy", "ghost", self.copy, tr("Copy the JSON to the clipboard"))
        self.top.addWidget(self.copy_btn)
        self.save_btn = None
        if save_cb:
            self.save_btn = button(save_text, "save", "ghost", lambda *_: save_cb(), save_tip)
            self.top.addWidget(self.save_btn)
        lay.addLayout(self.top)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setProperty("mono", "true")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setObjectName("SpecText")
        lay.addWidget(self.text, 1)
        self.data = None

    @staticmethod
    def dumps(data):
        return json.dumps(data, indent=2, ensure_ascii=False)

    def set_data(self, data):
        """Show `data` (a dict, or a message string when there is no spec)."""
        self.data = data
        text = data if isinstance(data, str) else self.dumps(data)
        if text == self.text.toPlainText():
            return
        bar = self.text.verticalScrollBar().value()
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(bar)
        self.copy_btn.setEnabled(not isinstance(data, str))
        if self.save_btn is not None:
            self.save_btn.setEnabled(not isinstance(data, str))

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.text.toPlainText())


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
        self.detach_btn = button(tr("Separate window"), "external", "ghost", lambda: self.detach(self.currentIndex()),
                                 tr("Open this tab in a separate window (double-click works too)"))
        self.detach_btn.setObjectName("DetachButton")
        self.setCornerWidget(self.detach_btn, Qt.Corner.TopRightCorner)

    def _menu(self, pos):
        from PySide6.QtWidgets import QMenu
        i = self.tabBar().tabAt(pos)
        if i < 0:
            return
        m = QMenu(self)
        m.addAction(tr("Open in a separate window"), lambda: self.detach(i))
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
        top.addWidget(button(tr("Back to the window"), "arrow", "ghost", self.close, tr("Put the tab back into the main window")))
        lay.addLayout(top)
        lay.addWidget(page, 1)
        page.show()

    def closeEvent(self, ev):
        self.layout().removeWidget(self.page)
        self.tabs.reattach(self.page)
        super().closeEvent(ev)
