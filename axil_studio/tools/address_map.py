"""Address Map Editor: where every IP instance sits on the bus.

The address map is a tree of IP instances (model.IPInstance): the top list is the top
interconnect's slave ports in order; a Sub-bus Bridge is one slave whose window is forwarded
to the interconnect behind it, with its own slaves (any depth). Addresses are global. An IP
definition can be placed any number of times, each instance with its own name and base. Each
entry's address is either automatic (consecutive, aligned, in list order: from the start
address, inside a bridge after its own window) or fixed (typed by hand, marked with a lock).
External slaves (memories, ready-made IPs without a register map in the project) are entered
with their size. Entries are moved between buses by drag & drop or the context menu. IPs are
added through the IP Library; every address edit in the studio opens this tool.
"""

import copy
import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QMenu, QMessageBox, QSplitter, QToolButton, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QWidget)

from axil_studio.core import addrmap as A
from axil_studio.core import regmap_xlsx
from axil_studio.core import xbargen as X
from axil_studio import ROOT, icons, theme, ui
from axil_studio.dialogs import BridgeDialog, SlaveDialog, size_text, window_choices
from axil_studio.tools.base import ToolPage
from axil_studio.core.model import IPInstance, hex32, parse_int
from axil_studio.i18n import tr

# tree columns
C_PORT, C_NAME, C_IP, C_ALOCK, C_ADDR, C_SLOCK, C_SIZE, C_ACT = range(8)
LOCK_W = 34                              # lock columns: the lock sits at the right, next to its value
LOCK_BTN = 20
LOCK_RIGHT = 2                           # gap between the lock and its value
ROLE = Qt.ItemDataRole.UserRole          # instance name ("" for a bridge's own-window row)
BRIDGE = A.BRIDGE_LABEL


def _weight(inst):
    if not inst.bridge:
        return 1
    return max(1, sum(_weight(c) for c in inst.children) + (1 if inst.self_window else 0))


def _depth(items):
    return max([0] + [1 + _depth(i.children) for i in items if i.bridge])


# ============================================================================ memory map strip
class MemoryMap(QWidget):
    """The address map in address order: one block per slave with its base and size, a gap marker
    where a bus is not contiguous, overlapping windows in red. A Sub-bus Bridge is a framed block
    with its own window and its slaves inside. Click selects, double-click edits."""

    slaveClicked = Signal(str)
    slaveActivated = Signal(str)

    GAP = 18
    TITLE = 18

    def __init__(self):
        super().__init__()
        self.project = None
        self.selected = None
        self._boxes = []
        self.setMinimumHeight(92)
        self.setMouseTracking(True)

    def set_project(self, project, selected=None):
        self.project = project
        self.selected = selected
        self.setMinimumHeight(92 + 30 * _depth(project.instances) if project else 92)
        self.update()

    def _bad(self, bridge):
        bad = A.overlapping(self.project, bridge)
        if bridge is not None:
            bad |= A.outside(self.project, bridge)
        return bad

    def paintEvent(self, _):
        t = theme.manager().tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 18, -1, -22)
        self._boxes = []
        items = self.project.instances if self.project else []
        if not items:
            p.setPen(QPen(QColor(t["border"]), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(t["muted"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr("Address map is empty"))
            return
        self._level(p, t, None, r, 0)
        sl = A.xbar_slaves(self.project)
        small = theme.mono_font()
        small.setPixelSize(10)
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        n = len(self.project.leaf_instances())
        lo = min(s.base for s in sl)
        hi = max(s.base + s.window for s in sl) - 1
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16), Qt.AlignmentFlag.AlignRight,
                   tr("{0} slaves · {1} {2} · {3} – {4}").format(n, len(self.project.bridges()), BRIDGE, hex32(lo), hex32(hi)))

    def _level(self, p, t, bridge, r, depth):
        """Draw the entries of one bus (and a bridge's own window) side by side in `r`."""
        proj = self.project
        items = list(proj.level(bridge))
        sl = A.xbar_slaves(proj, bridge)
        bad = self._bad(bridge)
        entries = [(s.base, j) for j, s in enumerate(sl)]
        if bridge is not None and bridge.self_window:
            entries.append((bridge.base, -1))
        entries.sort()
        weights = [1 if j < 0 else _weight(items[j]) for _, j in entries]
        prev_end, gaps = None, 0
        for base, j in entries:
            win = bridge.self_window if j < 0 else sl[j].window
            if prev_end is not None and base > prev_end:
                gaps += 1
            prev_end = max(prev_end or 0, base + win)
        unit = (r.width() - gaps * self.GAP) / max(1, sum(weights))
        small = theme.mono_font()
        small.setPixelSize(10)
        name_font = self.font()
        name_font.setBold(True)
        x, prev_end = r.left(), None
        for (base, j), wgt in zip(entries, weights):
            win = bridge.self_window if j < 0 else sl[j].window
            if prev_end is not None and base > prev_end:
                p.setPen(QColor(t["muted"]))
                p.setFont(name_font)
                p.drawText(QRectF(x, r.top(), self.GAP, r.height()), Qt.AlignmentFlag.AlignCenter, "⋯")
                x += self.GAP
            prev_end = max(prev_end or 0, base + win)
            box = QRectF(x, r.top(), unit * wgt, r.height())
            x += unit * wgt
            inner = box.adjusted(2, 0, -2, 0)
            if depth == 0:
                p.setFont(small)
                p.setPen(QColor(t["muted"]))
                p.drawText(QRectF(inner.left(), 0, inner.width(), 16), Qt.AlignmentFlag.AlignLeft,
                           p.fontMetrics().elidedText(hex32(base), Qt.TextElideMode.ElideRight, int(inner.width())))
            if j < 0:                                   # the bridge's own window
                bf, bb, bt = theme.block_colors("bridge", t)
                p.setBrush(QBrush(QColor(bb), Qt.BrushStyle.BDiagPattern))
                p.setPen(QPen(QColor(bb), 1))
                p.drawRoundedRect(inner, 4, 4)
                p.setFont(small)
                text = p.fontMetrics().elidedText(tr("own · {0}").format(size_text(win)), Qt.TextElideMode.ElideRight,
                                                  int(inner.width() - 12))
                tw = p.fontMetrics().horizontalAdvance(text) + 10
                label = QRectF(inner.center().x() - tw / 2, inner.center().y() - 9, tw, 18)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t["surface"]))
                p.drawRoundedRect(label, 4, 4)
                p.setPen(QColor(t["muted"]))
                p.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
                continue
            inst, s = items[j], sl[j]
            self._boxes.append((inner, inst.name))
            sel = inst.name == self.selected
            if inst.bridge:
                bf, bb, bt = theme.block_colors("bridge", t)
                p.setBrush(QColor(bf))
                p.setPen(QPen(QColor(t["danger"] if j in bad else t["text"] if sel else bb),
                              2.5 if sel or j in bad else 1.4))
                p.drawRoundedRect(inner, 6, 6)
                p.setPen(QColor(bt))
                p.setFont(name_font)
                title = QRectF(inner.left() + 6, inner.top() + 2, inner.width() - 12, self.TITLE)
                p.drawText(title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(f"{inst.name} · {size_text(s.window)}",
                                                      Qt.TextElideMode.ElideRight, int(title.width())))
                body = inner.adjusted(4, self.TITLE + 4, -4, -4)
                if inst.children or inst.self_window:
                    self._level(p, t, inst, body, depth + 1)
                else:
                    p.setPen(QColor(t["muted"]))
                    p.setFont(small)
                    p.drawText(body, Qt.AlignmentFlag.AlignCenter, tr("empty"))
                continue
            bf, bb, bt = theme.block_colors("ext" if s.external else "ip", t)
            p.setBrush(QColor(bf))
            p.setPen(QPen(QColor(t["danger"] if j in bad else t["text"] if sel else bb),
                          2.5 if sel or j in bad else 1.2))
            p.drawRoundedRect(inner, 5, 5)
            p.setPen(QColor(bt))
            p.setFont(name_font)
            fm = p.fontMetrics()
            two = inner.height() >= 34
            p.drawText(inner.adjusted(4, 2, -4, -inner.height() / 2 if two else -2), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(s.name, Qt.TextElideMode.ElideRight, int(inner.width() - 8)))
            if two:
                p.setFont(small)
                p.drawText(inner.adjusted(4, inner.height() / 2, -4, -2), Qt.AlignmentFlag.AlignCenter,
                           p.fontMetrics().elidedText(size_text(s.window), Qt.TextElideMode.ElideRight,
                                                      int(inner.width() - 8)))

    def _slave_at(self, pos):
        """The innermost block under the mouse."""
        hit = None
        for box, name in self._boxes:
            if box.contains(pos) and (hit is None or hit[0].contains(box)):
                hit = (box, name)
        return hit[1] if hit else None

    def mouseMoveEvent(self, ev):
        name = self._slave_at(ev.position())
        inst = self.project.get_instance(name) if name and self.project else None
        if inst is None:
            QToolTip.hideText()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        w = A.window_of(self.project, inst)
        kind = BRIDGE if inst.bridge else tr("external slave") if inst.external else inst.ip
        QToolTip.showText(ev.globalPosition().toPoint(),
                          tr("{0} · {1}\n{2} – {3}\nwindow {4}, used {5:#x}").format(inst.name, kind, hex32(inst.base), hex32(inst.base + w - 1), size_text(w), A.span_of(self.project, inst)), self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        name = self._slave_at(ev.position())
        if name:
            self.slaveClicked.emit(name)

    def mouseDoubleClickEvent(self, ev):
        name = self._slave_at(ev.position())
        if name:
            self.slaveActivated.emit(name)


# ============================================================================ address tree
class LockHeader(QHeaderView):
    """Tree header that draws the lock-all icons right-aligned in the lock columns, exactly above
    the rows' lock buttons (a header icon would sit on the left of its section)."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.locks = {}                           # column -> all locked
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def paintSection(self, painter, rect, index):
        painter.save()
        super().paintSection(painter, rect, index)
        painter.restore()
        if index in self.locks:
            on = self.locks[index]
            pm = icons.pixmap("lock" if on else "unlock", 14,
                              theme.manager().tokens["accent" if on else "muted"])
            cx = rect.x() + rect.width() - LOCK_RIGHT - 2 - LOCK_BTN / 2   # the cell widget sits 2 px in
            painter.drawPixmap(int(cx - 7), int(rect.center().y() - 7), pm)



class AddressTree(QTreeWidget):
    """The address map as a tree; rows are dragged within a bus or onto / into another bus.
    moved(name, parent bridge name or "" for the top bus, index or -1 for the end)."""

    moved = Signal(str, str, int)

    def __init__(self):
        super().__init__()
        self.setColumnCount(7)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDropIndicatorShown(True)

    def dropEvent(self, ev):
        src = self.currentItem()
        ev.setDropAction(Qt.DropAction.IgnoreAction)
        ev.accept()
        if src is None or not src.data(0, ROLE):
            return
        target = self.itemAt(ev.position().toPoint())
        pos = self.dropIndicatorPosition()
        P = QAbstractItemView.DropIndicatorPosition
        if target is None or pos == P.OnViewport:
            return self.moved.emit(src.data(0, ROLE), "", -1)
        if target is src:
            return
        if not target.data(0, ROLE):                 # a bridge's own-window row: to the start of that bus
            par = target.parent()
            return self.moved.emit(src.data(0, ROLE), par.data(0, ROLE) if par else "", 0)
        if pos == P.OnItem and target.data(0, Qt.ItemDataRole.UserRole + 1):       # onto a bridge: into it
            return self.moved.emit(src.data(0, ROLE), target.data(0, ROLE), -1)
        par = target.parent()
        idx = target.data(0, Qt.ItemDataRole.UserRole + 2)
        if pos == P.BelowItem or pos == P.OnItem:
            idx += 1
        self.moved.emit(src.data(0, ROLE), par.data(0, ROLE) if par else "", idx)


# ============================================================================ page
class AddressMapPage(ToolPage):
    tool_id = "addrmap"
    title = "Address Map Editor"
    short_title = "Address Map Editor"
    subtitle = tr("Place IPs in the address map: base address, window size, port order and Sub-bus Bridges.")
    description = (tr("Places the project's IPs in the address map (an IP can be used several times), builds a bus hierarchy with Sub-bus Bridges, gives automatic or fixed addresses and checks alignment and overlaps."))
    icon_name = "addrmap"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._loading = False
        self._collapsed = set()

        bar = self.header       # the tool's commands sit in the top bar
        self.regs_btn = bar.add(tr("Edit register map"), "regmap", self.edit_regs,
                                tr("Open the selected IP's register map in the Register Map Editor"))
        bar.add(tr("Export to Excel…"), "file", self.export_xlsx, tr("The whole address map and its registers (.xlsx)"),
                regmap_xlsx.available())
        bar.sep()
        bar.add("Interconnect Configurator", "interconnect", lambda: self.open_tool("interconnect"),
                tr("Configure and generate the crossbars of this address map"))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: start address + default window
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.section(tr("START ADDRESS"), tr("Automatic slaves are placed from this address, in list order, aligned to their windows (inside a Sub-bus Bridge: after its own window). A typed address is locked; the lock next to an address locks it or makes it automatic again.")))
        self.start_ed = QLineEdit()
        self.start_ed.setProperty("mono", "true")
        self.start_ed.setToolTip(tr("Automatic slaves on the top bus are placed from this address in list order"))
        self.start_ed.editingFinished.connect(self._start_changed)
        left.lay.addWidget(self.start_ed)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.section(tr("DEFAULT WINDOW"), tr("Every slave without a hand-set size gets this window (larger when its IP needs more). A size chosen in the Size column, or its lock, keeps that slave's size.")))
        self.window_cb = QComboBox()
        for w in window_choices(0x100):
            self.window_cb.addItem(f"{size_text(w)}  ({w:#x})", w)
        self.window_cb.setToolTip(tr("Window of every slave whose size is not set by hand"))
        self.window_cb.currentIndexChanged.connect(self._default_window_changed)
        left.lay.addWidget(self.window_cb)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.section(BRIDGE.upper(), tr("Takes one window in the address map; its start can be kept for the bridge itself, the rest is forwarded unchanged to the interconnect behind it. Add IPs, external slaves and more Sub-bus Bridges into it; move rows between buses by drag and drop or right-click → Move (a fixed address becomes automatic on another bus).")))
        left.lay.addWidget(ui.label(tr("A sub-bus with its own interconnect, inside one window of this bus."), "muted", wrap=True))
        left.lay.addStretch(1)
        self.issues = ui.IssueList(tr("No errors"))     # shown in the window's status bar
        left.setMinimumWidth(300)
        split.addWidget(left)

        # ---- right: memory strip + address tree
        right = ui.Card(margins=16, spacing=10)
        self.map = MemoryMap()
        self.map.slaveClicked.connect(self.select_instance)
        self.map.slaveActivated.connect(lambda n: self.edit_slave(self.proj.get_instance(n)))
        right.lay.addWidget(self.map)
        self.tree = AddressTree()
        self.tree.setHeader(LockHeader(self.tree))
        self.tree.setHeaderLabels(["Port", tr("Name"), tr("Type / IP"), "", tr("Address"), "", tr("Size"), ""])
        hdr = self.tree.header()
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._header_clicked)
        hdr.setMinimumSectionSize(LOCK_W)         # the lock columns stay narrow: lock right next to its value
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                  QAbstractItemView.EditTrigger.EditKeyPressed)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemClicked.connect(self._item_clicked)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemCollapsed.connect(lambda it: self._collapsed.add(it.data(0, ROLE)))
        self.tree.itemExpanded.connect(lambda it: self._collapsed.discard(it.data(0, ROLE)))
        self.tree.moved.connect(self._moved)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        right.lay.addWidget(self.tree, 1)
        sbar = QHBoxLayout()
        sbar.setSpacing(4)
        sbar.addWidget(ui.button(tr("Add slave from IP Library…"), "plus", "ghost", self.add_ip,
                                 tr("Pick an IP in the IP Library and add it to the selected bus (an IP can be added several times: <IP>_0, <IP>_1 …)")))
        sbar.addWidget(ui.button(tr("Add external slave…"), "plus", "ghost", self.add_external,
                                 tr("Add a slave that has no register map in the project (e.g. a memory, a ready-made IP)")))
        sbar.addWidget(ui.button(tr("Add {0}…").format(BRIDGE), "plus", "ghost", self.add_bridge,
                                 tr("Add a sub-bus with its own interconnect behind it")))
        sbar.addStretch(1)
        sbar.addWidget(ui.button(tr("Sort by address"), None, "ghost", self.sort_slaves,
                                 tr("Order every bus (port numbers) by address")))
        sbar.addWidget(ui.button("↑", None, "ghost", lambda: self.move(-1), tr("Move up in the bus")))
        sbar.addWidget(ui.button("↓", None, "ghost", lambda: self.move(1), tr("Move down in the bus")))
        right.lay.addLayout(sbar)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 950])
        self.body.addWidget(split, 1)
        self._refresh()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Address Map")
        win = self.window()
        for text, slot in ((tr("Add slave from IP Library…"), self.add_ip), (tr("Add external slave…"), self.add_external),
                           (tr("Add {0}…").format(BRIDGE), self.add_bridge), (None, None),
                           (tr("Sort by address"), self.sort_slaves),
                           (tr("Lock / unlock every address"), self.toggle_all_addr_locks),
                           (tr("Lock / unlock every size"), self.toggle_all_size_locks), (None, None),
                           (tr("Edit register map"), self.edit_regs),
                           (tr("Export to Excel…"), self.export_xlsx), (None, None),
                           ("Interconnect Configurator", lambda: self.open_tool("interconnect"))):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            m.addAction(a)

    # ================================================================ data
    @property
    def proj(self):
        return self.ctx.project

    def _changed(self, select=None):
        """Commit an edit: re-address, tell the other tools, redraw."""
        self.ctx.mark_modified(self.tool_id)
        self._refresh(select)

    def _guarded(self, edit, select=None):
        """Run an edit; undo it when it makes a hand-sized Sub-bus Bridge (more) too small."""
        keep = copy.deepcopy(self.proj.instances)
        before = A._fill(self.proj)
        result = edit()
        if result is False:
            return False
        try:
            A.readdress(self.proj)
        except ValueError:
            pass
        err = A.check_fits(self.proj, before)
        if err:
            self.proj.instances[:] = keep
            try:
                A.readdress(self.proj)
            except ValueError:
                pass
            self._refresh()
            QMessageBox.warning(self, self.title, tr("Not possible: {0}.\nMake the window larger or automatic.").format(err))
            return False
        self._changed(select() if callable(select) else select)
        return result

    def on_project_modified(self):
        self._refresh()

    def on_project_replaced(self):
        self._refresh(None)

    def select_instance(self, name):
        it = self._items.get(name)
        if it is not None:
            self.tree.setCurrentItem(it)

    # ================================================================ view
    def _refresh(self, select=None):
        sel = (select.name if isinstance(select, IPInstance) else select) if select is not None \
            else (self._sel_inst().name if self._sel_inst() else None)
        self._loading = True
        self.start_ed.setText(hex32(A.start_of(self.proj)))
        self.window_cb.setCurrentIndex(max(0, self.window_cb.findData(A.default_window(self.proj))))
        self.tree.clear()
        self._items = {}
        self._fill_level(None, self.tree.invisibleRootItem())
        for c in (C_PORT, C_IP, C_ADDR):
            self.tree.resizeColumnToContents(c)
        self.tree.setColumnWidth(C_PORT, self.tree.columnWidth(C_PORT) + 24)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(C_SIZE, 140)
        for c in (C_ALOCK, C_SLOCK):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(c, LOCK_W)
        ui.actions_column(hdr, C_ACT)
        insts = self.proj.all_instances()
        all_a = bool(insts) and all(i.fixed for i in insts)
        all_s = bool(insts) and all(i.window for i in insts)
        head = self.tree.headerItem()
        for col, on, what, more in ((C_ALOCK, all_a, tr("addresses"), tr("a locked address stays, an unlocked one is automatic")),
                                    (C_SLOCK, all_s, tr("sizes"), tr("a locked size stays, an unlocked one follows the default or grows with its contents"))):
            self.tree.header().locks[col] = on
            head.setToolTip(col, (tr("Click: unlock all {0}").format(what) if on else tr("Click: lock all {0}").format(what))
                            + f" ({more})")
        if sel in self._items:
            self.tree.setCurrentItem(self._items[sel])
        self._loading = False
        cur = self._sel_inst()
        self.map.set_project(self.proj, cur.name if cur else None)
        errs = A.validate(self.proj) if self.proj.instances else [
            tr("Address map is empty: add a slave from the IP Library, an external slave or a Sub-bus Bridge.")]
        if not errs:
            sl = A.xbar_slaves(self.proj)
            lo = min(s.base for s in sl)
            hi = max(s.base + s.window for s in sl) - 1
            nb = len(self.proj.bridges())
            self.issues.ok_text = (tr("No errors · {0} slaves").format(len(self.proj.leaf_instances()))
                                   + (f" · {nb} {BRIDGE}" if nb else "") + f" · {hex32(lo)} – {hex32(hi)}")
        self.issues.set_issues(errs)
        self._sync_buttons()

    def _fill_level(self, bridge, parent_item):
        t = theme.manager().tokens
        mono = theme.mono_font()
        muted = QColor(t["muted"])
        items = self.proj.level(bridge)
        sl = A.xbar_slaves(self.proj, bridge)
        bad = A.overlapping(self.proj, bridge) | (A.outside(self.proj, bridge) if bridge is not None else set())
        if bridge is not None and bridge.self_window:
            own = QTreeWidgetItem(["", tr("own window"), BRIDGE, "", hex32(bridge.base), "",
                                   size_text(bridge.self_window), ""])
            own.setData(0, ROLE, "")
            own.setFlags(Qt.ItemFlag.ItemIsEnabled)
            own.setFont(C_ADDR, mono)
            for c in range(C_ACT):
                own.setForeground(c, muted)
            own.setToolTip(C_NAME, tr("Space kept for the bridge {0} itself (not forwarded): {1} – {2}").format(bridge.name, hex32(bridge.base), hex32(bridge.base + bridge.self_window - 1)))
            parent_item.addChild(own)
        ro = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        for j, (inst, s) in enumerate(zip(items, sl)):
            ip = self.proj.get_ip(inst.ip) if inst.ip else None
            if inst.bridge:
                kind = BRIDGE
            elif inst.external:
                kind = tr("external")
            elif ip is None:
                kind = tr("{0} (not in the project)").format(inst.ip)
            else:
                kind = ip.name + (f" {ip.version}" if ip.version else "")
            it = QTreeWidgetItem([f"SLAVE{j}", inst.name, kind, "", hex32(s.base), "", "", ""])
            it.setData(0, ROLE, inst.name)
            it.setData(0, Qt.ItemDataRole.UserRole + 1, inst.bridge)
            it.setData(0, Qt.ItemDataRole.UserRole + 2, j)
            it.setFlags(ro | Qt.ItemFlag.ItemIsEditable | (Qt.ItemFlag.ItemIsDropEnabled if inst.bridge
                                                           else Qt.ItemFlag.NoItemFlags))
            for c in (C_PORT, C_ADDR):
                it.setFont(c, mono)
            tip = (tr("{0} – {1}\nMASK {2}\nused {3:#x} bytes").format(hex32(s.base), hex32(s.base + s.window - 1), hex32(s.mask), s.span))
            for c in (C_PORT, C_ADDR, C_SIZE):
                it.setToolTip(c, tip)
            it.setToolTip(C_NAME, tr("Double-click: rename (the Debug Bridge and the interconnect use this name)"))
            if inst.bridge:
                f = it.font(C_NAME)
                f.setBold(True)
                it.setFont(C_NAME, f)
                it.setForeground(C_IP, QColor(theme.block_colors("bridge", t)[2]))
                it.setToolTip(C_IP, tr("{0} slaves · interconnect {1}\nA row dropped on it moves to this bus").format(len(inst.children), inst.xbar_module))
            else:
                it.setForeground(C_IP, muted if inst.external else QColor(t["danger"]) if ip is None
                                 else QColor(t["text"]))
                if ip is not None:
                    n = len(self.proj.instances_of(ip.name))
                    it.setToolTip(C_IP, tr("{0}: {1} registers").format(ip.name, len(ip.registers))
                                        + (tr(" · {0}× in the map").format(n) if n > 1 else "")
                                        + tr("\nRight-click: edit register map"))
            it.setToolTip(C_ADDR, tip + tr("\nDouble-click: type an address (a typed address is locked)"))
            if j in bad:
                it.setForeground(C_ADDR, QColor(t["danger"]))
            parent_item.addChild(it)
            self._items[inst.name] = it
            self.tree.setItemWidget(it, C_SIZE, self._size_combo(inst, s))
            self.tree.setItemWidget(it, C_ALOCK, self._lock_button(
                inst.fixed, lambda *_, i=inst: self._toggle_lock(i),
                tr("Address locked (fixed): click to make it automatic") if inst.fixed else
                tr("Address automatic: click to lock it here")))
            self.tree.setItemWidget(it, C_SLOCK, self._lock_button(
                bool(inst.window), lambda *_, i=inst: self._toggle_size_lock(i),
                tr("Size locked: click to") + (tr("grow with its contents") if inst.bridge else tr("follow the default"))
                if inst.window else tr("Size automatic: click to lock this size")))
            ui.tree_row_actions(self.tree, it, C_ACT, lambda *_, i=inst: self.edit_slave(i),
                                lambda *_, i=inst: self.delete_slave(i),
                                edit_tip=tr("Name, address and size"),
                                delete_tip=tr("Remove from the address map") + (
                                    tr(" (with its contents)") if inst.bridge else tr(" (the IP definition stays in the project)")))
            if inst.bridge:
                self._fill_level(inst, it)
                it.setExpanded(inst.name not in self._collapsed)

    def _lock_button(self, locked, slot, tip):
        b = QToolButton()
        b.setAutoRaise(True)
        b.setObjectName("RowAction")
        b.setFixedSize(LOCK_BTN, 22)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setIcon(icons.icon("lock", "accent", 15) if locked else ui.row_icon("unlock", "text"))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        box = QWidget()
        box.setObjectName("RowActions")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, LOCK_RIGHT, 0)
        lay.addStretch(1)                         # free room on the left keeps it apart from the column before
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)                                  # next to its value
        return box

    def _size_combo(self, inst, s):
        cb = QComboBox()
        if inst.bridge:
            need = A.content_end(self.proj, inst)
            cb.addItem(tr("automatic · {0}").format(size_text(A.auto_window(self.proj, inst))), 0)
            cb.setToolTip(tr("Sub-bus Bridge window: automatic grows with its own window and contents; a size chosen by hand (locked) stays and a slave that does not fit cannot be added."))
        else:
            need = A.need_window(s.span, 4)
            cb.addItem(tr("default · {0}").format(size_text(A.auto_window(self.proj, inst))), 0)
            cb.setToolTip(tr("Address range of the slave (at least what its IP uses). A size chosen here stays with this slave (locked); \"default\" follows the Default window setting."))
        for w in window_choices(A.need_window(need, 4)):
            cb.addItem(size_text(w), w)
        cb.setCurrentIndex(max(0, cb.findData(inst.window if inst.window else 0)))
        cb.currentIndexChanged.connect(lambda _i, inst=inst, cb=cb: self._size_changed(inst, cb.currentData()))
        return cb

    def _inst(self, item):
        name = item.data(0, ROLE) if item is not None else None
        return self.proj.get_instance(name) if name else None

    def _sel_inst(self):
        return self._inst(self.tree.currentItem()) if hasattr(self, "_items") else None

    def _target_bus(self):
        """Where "add" puts a new entry: into the selected bridge, next to a selected entry inside a
        bridge, else on the top bus."""
        cur = self._sel_inst()
        if cur is None:
            it = self.tree.currentItem()
            return self._inst(it.parent()) if it is not None and it.parent() is not None else None
        return cur if cur.bridge else self.proj.parent_of(cur)

    def _selection_changed(self):
        cur = self._sel_inst()
        self.map.set_project(self.proj, cur.name if cur else None)
        self._sync_buttons()

    def _sync_buttons(self):
        inst = self._sel_inst()
        self.regs_btn.setEnabled(bool(inst and not inst.external and not inst.bridge and self.proj.get_ip(inst.ip)))

    def on_theme(self):
        self.issues.retint()
        self._refresh()

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        inst = self._inst(it)
        m = QMenu(self)
        if inst is not None:
            self.tree.setCurrentItem(it)
            m.addAction(tr("Edit…"), lambda: self.edit_slave(inst))
            if not inst.external and not inst.bridge:
                m.addAction(tr("Edit register map"), self.edit_regs)
            m.addAction(tr("Make the address automatic") if inst.fixed else tr("Lock the address"), lambda: self._toggle_lock(inst))
            mv = m.addMenu(tr("Move to"))
            here = self.proj.parent_of(inst)
            for b in [None] + self.proj.bridges():
                if b is here or (inst.bridge and A.is_inside(self.proj, inst, b)):
                    continue
                label = tr("Top bus") if b is None else "  " * (len(A.path_of(self.proj, b)) - 1) + b.name
                mv.addAction(label, lambda *_, b=b: self._move_to(inst, b, None))
            mv.setEnabled(bool(mv.actions()))
            m.addSeparator()
            m.addAction(tr("Remove from the address map"), lambda: self.delete_slave(inst))
            m.addSeparator()
        target = self._target_bus()
        where = tr(" (into {0})").format(target.name) if target else ""
        m.addAction(tr("Add IP…{0}").format(where), self.add_ip)
        m.addAction(tr("Add external slave…{0}").format(where), self.add_external)
        m.addAction(tr("Add {0}…{1}").format(BRIDGE, where), self.add_bridge)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ================================================================ edits
    def _start_changed(self):
        if self._loading:
            return
        try:
            v = parse_int(self.start_ed.text()) & 0xFFFFFFFF
        except ValueError:
            self.start_ed.setStyleSheet(f"border-color: {theme.manager().tokens['danger']};")
            return
        self.start_ed.setStyleSheet("")
        if v != A.start_of(self.proj):
            A.set_start(self.proj, v)
            self._changed()

    def _default_window_changed(self, *_):
        if self._loading:
            return
        w = self.window_cb.currentData()
        if w and w != A.default_window(self.proj):
            old = A.default_window(self.proj)
            if self._guarded(lambda: A.set_default_window(self.proj, w)) is False:
                A.set_default_window(self.proj, old)
                return self._refresh()
            self.status(tr("Default window {0}; slaves with a locked size did not change").format(size_text(w)))

    def _name_ok(self, inst, name):
        if not X.IDENT.match(name):
            QMessageBox.warning(self, self.title, tr("The name must start with a letter and contain only letters, digits and _."))
            return False
        if name != inst.name and self.proj.get_instance(name):
            QMessageBox.warning(self, self.title, tr("The address map already has a slave named “{0}”.").format(name))
            return False
        return True

    def _rename(self, inst, name):
        if name == inst.name:
            return True
        if not self._name_ok(inst, name):
            return False
        if inst.name in self._collapsed:
            self._collapsed.discard(inst.name)
            self._collapsed.add(name)
        inst.name = name
        return True

    def _item_changed(self, item, col):
        if self._loading:
            return
        inst = self._inst(item)
        if inst is None:
            return
        if col == C_NAME:
            name = item.text(C_NAME).strip()
            if name and name != inst.name and self._rename(inst, name):
                return self._changed(inst)
            return self._refresh(inst)
        if col == C_ADDR:
            w = A.window_of(self.proj, inst)
            try:
                v = parse_int(item.text(C_ADDR).strip()) & 0xFFFFFFFF
            except ValueError:
                self.status(tr("The address must be a number (e.g. 0x80000000)"))
                return self._refresh(inst)
            if v & (w - 1):
                QMessageBox.warning(self, self.title, tr("{0} is not aligned to the {2} window of {1}.").format(hex32(v), inst.name, size_text(w)))
                return self._refresh(inst)

            def set_base():
                A.set_base(self.proj, inst, v)           # a typed address is kept; a bridge's contents move along
            self._guarded(set_base, inst)
            return
        self._refresh(inst)

    def _item_clicked(self, item, col):
        pass

    def _toggle_lock(self, inst):
        self._guarded(lambda: setattr(inst, "fixed", not inst.fixed), inst)

    def _size_changed(self, inst, w):
        if self._loading:
            return

        def apply():
            inst.window = w                   # 0: follow the default window / grow with the contents
            ww = A.window_of(self.proj, inst)
            if inst.fixed and inst.base & (ww - 1):
                A.set_base(self.proj, inst, inst.base & ~(ww - 1))
                self.status(tr("{0}'s address was aligned to its new size: {1}").format(inst.name, hex32(inst.base)))
        if self._guarded(apply, inst) is False:
            self._refresh(inst)

    def _move_to(self, inst, bridge, index):
        try:
            note = A.move(self.proj, inst, bridge, index)
        except ValueError as exc:
            self._refresh(inst)
            QMessageBox.warning(self, self.title, tr("Could not move: {0}").format(exc))
            return
        self._changed(inst)
        self.status(note or f"{inst.name} → {A.level_name(self.proj, bridge)}")

    def _moved(self, name, parent_name, index):
        inst = self.proj.get_instance(name)
        bridge = self.proj.get_instance(parent_name) if parent_name else None
        if inst is not None:
            self._move_to(inst, bridge, None if index < 0 else index)

    def move(self, delta):
        inst = self._sel_inst()
        if inst is None:
            return
        level = self.proj.level(self.proj.parent_of(inst))
        j = level.index(inst)
        if 0 <= j + delta < len(level):
            self._guarded(lambda: level.insert(j + delta, level.pop(j)), inst)

    def sort_slaves(self):
        def sort(items):
            items.sort(key=lambda i: i.base)
            for i in items:
                if i.bridge:
                    sort(i.children)
        if self._guarded(lambda: sort(self.proj.instances)) is not False:
            self.status(tr("Every bus is sorted by address"))

    def _toggle_size_lock(self, inst):
        if inst.window:
            self._guarded(lambda: setattr(inst, "window", 0), inst)
        else:
            w = A.window_of(self.proj, inst)
            self._guarded(lambda: setattr(inst, "window", w), inst)

    def _header_clicked(self, col):
        if col == C_ALOCK:
            self.toggle_all_addr_locks()
        elif col == C_SLOCK:
            self.toggle_all_size_locks()

    def toggle_all_addr_locks(self):
        insts = self.proj.all_instances()
        lock = not (insts and all(i.fixed for i in insts))

        def apply():
            for inst in insts:
                inst.fixed = lock
        if self._guarded(apply) is False:
            return
        self.status(tr("Every address is locked (fixed)") if lock else tr("Every address is unlocked (automatic)"))

    def toggle_all_size_locks(self):
        insts = self.proj.all_instances()
        lock = not (insts and all(i.window for i in insts))
        sizes = {id(i): A.window_of(self.proj, i) for i in insts}

        def apply():
            for inst in insts:
                inst.window = sizes[id(inst)] if lock else 0
        if self._guarded(apply) is False:
            return
        self.status(tr("Every window size is locked") if lock else
                    tr("Every size is unlocked (default window / grows with its contents)"))

    def unlock_all(self):
        insts = self.proj.all_instances()

        def apply():
            for inst in insts:
                inst.fixed = False
        self._guarded(apply)

    def edit_slave(self, inst=None):
        inst = inst if isinstance(inst, IPInstance) else self._sel_inst()
        if inst is None:
            return
        parent = self.proj.parent_of(inst)
        j = self.proj.level(parent).index(inst)
        if inst.bridge:
            new = BridgeDialog.ask(self, f"{BRIDGE}: {inst.name}", inst.name, inst.base, inst.self_window,
                                   inst.window, inst.fixed, A.content_end(self.proj, inst))
            if new is None:
                return
            name, base, own, window, fixed = new
            if name != inst.name and not self._name_ok(inst, name):
                return

            def apply():
                self._rename(inst, name)
                inst.self_window, inst.window = own, window
                if fixed:
                    A.set_base(self.proj, inst, base)    # its contents move along
                inst.fixed = fixed
            self._guarded(apply, lambda: name)
            return
        s = A.xbar_slaves(self.proj, parent)[j]
        new = SlaveDialog.ask(self, s, f"SLAVE{j}: {inst.name}", 4, False)
        if new is None:
            return
        if new.name != inst.name and not self._name_ok(inst, new.name):
            return

        def apply():
            self._rename(inst, new.name)
            if new.window != s.window:            # a size chosen here is kept for this slave
                inst.window = new.window
            inst.base, inst.fixed = new.base, new.fixed
            if inst.external:
                inst.span = new.span
        self._guarded(apply, lambda: new.name)

    def _add(self, inst):
        """Append a new entry to the bus chosen by _target_bus (refused when it does not fit)."""
        bus = self._target_bus()
        self.proj.level(bus).append(inst)
        return inst

    def add_ip(self):
        name = self.pick_ip(tr("Add IP to the address map"), tr("Pick an IP of the project or of an open library. An IP can be added several times; each instance gets its own name and address."))
        if not name:
            return
        inst = IPInstance(A.unique_name(self.proj, name), name)
        if self._guarded(lambda: self._add(inst), inst) is not False:
            self.status(tr("{0} added ({1})").format(inst.name, hex32(inst.base)))

    def add_external(self):
        name = A.unique_name(self.proj, "EXT")                # EXT_0, EXT_1 …
        proto = X.XbarSlave(name, 0, 0xFFFFF000, 0x1000, True, False)    # external, automatic address
        proto.mask = (~(A.default_window(self.proj) - 1)) & 0xFFFFFFFF
        new = SlaveDialog.ask(self, proto, tr("External slave"), 4, False)
        if new is None:
            return
        if self.proj.get_instance(new.name) or not X.IDENT.match(new.name):
            QMessageBox.warning(self, self.title, tr("“{0}” cannot be used: the name must be unique, start with a letter and contain only letters, digits and _.").format(new.name))
            return
        window = 0 if new.window == A.default_window(self.proj) else new.window   # 0: follows the default
        inst = IPInstance(new.name, "", new.base, window, new.fixed, new.span)
        self._guarded(lambda: self._add(inst), inst)

    def add_bridge(self):
        name = A.unique_name(self.proj, A.BRIDGE_STEM)
        new = BridgeDialog.ask(self, tr("Add {0}").format(BRIDGE), name, 0, 0, 0, False, 0)
        if new is None:
            return
        name, base, own, window, fixed = new
        if self.proj.get_instance(name) or not X.IDENT.match(name):
            QMessageBox.warning(self, self.title, tr("“{0}” cannot be used: the name must be unique, start with a letter and contain only letters, digits and _.").format(name))
            return
        inst = IPInstance(name, "", base, window, fixed, bridge=True, self_window=own,
                          xbar={"module": "", "masters": []})
        if self._guarded(lambda: self._add(inst), inst) is not False:
            self.status(tr("{0} added ({1}); to add IPs into it, keep it selected and use 'Add slave from IP Library…'").format(inst.name, hex32(inst.base)))

    def delete_slave(self, inst=None):
        inst = inst if isinstance(inst, IPInstance) else self._sel_inst()
        if inst is None:
            return
        if inst.bridge:
            n = len([i for i, _ in self.proj.walk(inst.children)])
            text = f"{inst.name} ({BRIDGE}) " + (tr("and the {0} entries inside it").format(n) if n else "") + tr("will be removed from the address map.")
        elif inst.external:
            text = tr("{0} will be removed from the address map.").format(inst.name)
        else:
            text = tr("{0} will be removed from the address map; the IP definition {1} stays in the project.").format(inst.name, inst.ip)
        if not ui.confirm(self, tr("Remove from the address map"), text + tr(" The port numbers of the following slaves go down by one."),
                          ok=tr("Remove")):
            return
        parent = self.proj.parent_of(inst)
        level = self.proj.level(parent)
        j = level.index(inst)
        level.remove(inst)
        nxt = level[min(j, len(level) - 1)] if level else parent
        self._changed(nxt)

    def keyPressEvent(self, ev):
        if self.tree.hasFocus() and ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) \
                and self.tree.state() != QAbstractItemView.State.EditingState:
            self.delete_slave()
        else:
            super().keyPressEvent(ev)

    # ================================================================ other tools / output
    def edit_regs(self):
        inst = self._sel_inst()
        if inst and not inst.external and not inst.bridge and self.proj.get_ip(inst.ip):
            self.edit_register_map(inst.ip)

    def export_xlsx(self):
        ips = self.ctx.address_ips()
        if not ips:
            QMessageBox.information(self, self.title, tr("No IP with a register map in the address map."))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Address and register map (Excel)"),
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{self.proj.name}_regmap.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        try:
            regmap_xlsx.export_xlsx(ips, path, tr("{0} address map").format(self.proj.name),
                                    bus_of={i.name: A.level_name(self.proj, p) for i, p in self.proj.walk()})
        except (OSError, RuntimeError) as exc:
            return self.error(tr("Could not export"), exc)
        self.status(tr("Exported to Excel: {0}").format(path))
