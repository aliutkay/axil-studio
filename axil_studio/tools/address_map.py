"""Address Map Editor: where every IP instance sits on the bus.

The address map is the project's list of IP instances (model.IPInstance) in slave port order.
An IP definition can be placed any number of times, each instance with its own name and base.
Each instance's address is either automatic (consecutive, aligned, in list order from the start
address) or fixed (typed by hand, marked with a lock). External slaves (memories, ready-made
IPs without a register map in the project) are entered with their size. IPs are added through
the IP Library; every address edit in the studio opens this tool.
"""

import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QMenu, QMessageBox, QSplitter, QTableWidget, QTableWidgetItem, QToolTip, QWidget)

import addrmap as A
import regmap_xlsx
import xbargen as X
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import SlaveDialog, size_text, window_choices
from axil_studio.tools.base import ToolPage
from model import IPInstance, hex32, parse_int

# slave table columns
C_PORT, C_NAME, C_IP, C_ADDR, C_SIZE, C_LOCK = range(6)


def overlapping(slaves):
    """Indexes of enabled slaves whose windows overlap another one."""
    bad = set()
    for i, a in enumerate(slaves):
        for j in range(i):
            b = slaves[j]
            if a.enabled and b.enabled and not ((a.base ^ b.base) & a.mask & b.mask):
                bad.update((i, j))
    return bad


# ============================================================================ memory map strip
class MemoryMap(QWidget):
    """Enabled slaves in address order: one block per slave with its base and size, a gap marker
    where the map is not contiguous, overlapping windows in red. Click selects, double-click edits."""

    slaveClicked = Signal(int)
    slaveActivated = Signal(int)

    GAP = 22

    def __init__(self):
        super().__init__()
        self.slaves = []
        self.selected = None
        self.bad = set()
        self._boxes = []
        self.setMinimumHeight(92)
        self.setMouseTracking(True)

    def set_slaves(self, slaves, selected=None):
        self.slaves = slaves
        self.selected = selected
        self.bad = overlapping(slaves)
        self.update()

    def _layout(self):
        items = sorted(((s.base, j) for j, s in enumerate(self.slaves) if s.enabled))
        r = QRectF(self.rect()).adjusted(1, 18, -1, -22)
        gaps = sum(1 for k in range(1, len(items))
                   if self.slaves[items[k][1]].base > self.slaves[items[k - 1][1]].base
                   + self.slaves[items[k - 1][1]].window)
        n = len(items)
        w = (r.width() - gaps * self.GAP) / n if n else 0
        boxes, x, prev_end = [], r.left(), None
        for base, j in items:
            s = self.slaves[j]
            if prev_end is not None and s.base > prev_end:
                boxes.append(("gap", QRectF(x, r.top(), self.GAP, r.height()), None))
                x += self.GAP
            boxes.append(("slave", QRectF(x, r.top(), w, r.height()), j))
            x += w
            prev_end = max(prev_end or 0, s.base + s.window)
        return r, boxes

    def paintEvent(self, _):
        t = theme.manager().tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, boxes = self._layout()
        self._boxes = boxes
        if not any(k == "slave" for k, _, _ in boxes):
            p.setPen(QPen(QColor(t["border"]), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(t["surface2"]))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(t["muted"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Kullanımda slave yok")
            return
        small = theme.mono_font()
        small.setPixelSize(10)
        name_font = self.font()
        name_font.setBold(True)
        for kind, box, j in boxes:
            if kind == "gap":
                p.setPen(QColor(t["muted"]))
                p.setFont(name_font)
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, "⋯")
                continue
            s = self.slaves[j]
            inner = box.adjusted(2, 0, -2, 0)
            fill = QColor(t["danger"]) if j in self.bad else QColor(t["primary"])
            if s.external and j not in self.bad:
                fill = QColor(t["accent_hover"] if not theme.manager().dark else t["primary_hover"])
            p.setBrush(fill)
            p.setPen(QPen(QColor(t["text"]), 2.5) if j == self.selected else Qt.PenStyle.NoPen)
            p.drawRoundedRect(inner, 5, 5)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(name_font)
            fm = p.fontMetrics()
            p.drawText(inner.adjusted(4, 6, -4, -inner.height() / 2), Qt.AlignmentFlag.AlignCenter,
                       fm.elidedText(s.name, Qt.TextElideMode.ElideRight, int(inner.width() - 8)))
            p.setFont(small)
            p.drawText(inner.adjusted(4, inner.height() / 2, -4, -4), Qt.AlignmentFlag.AlignCenter,
                       p.fontMetrics().elidedText(size_text(s.window), Qt.TextElideMode.ElideRight,
                                                  int(inner.width() - 8)))
            p.setPen(QColor(t["muted"]))
            p.drawText(QRectF(inner.left(), 0, inner.width(), 16), Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(hex32(s.base), Qt.TextElideMode.ElideRight, int(inner.width())))
        used = [s for s in self.slaves if s.enabled]
        lo = min(s.base for s in used)
        hi = max(s.base + s.window for s in used) - 1
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16), Qt.AlignmentFlag.AlignRight,
                   f"{len(used)} slave · {hex32(lo)} – {hex32(hi)}"
                   + (" · çakışan pencereler kırmızı" if self.bad else ""))

    def _slave_at(self, pos):
        return next((j for kind, box, j in self._boxes if kind == "slave" and box.contains(pos)), None)

    def mouseMoveEvent(self, ev):
        j = self._slave_at(ev.position())
        if j is None:
            QToolTip.hideText()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        s = self.slaves[j]
        QToolTip.showText(ev.globalPosition().toPoint(),
                          f"SLAVE{j} · {s.name}\n{hex32(s.base)} – {hex32(s.base + s.window - 1)}\n"
                          f"pencere {size_text(s.window)}, kullanılan {s.span:#x}", self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        j = self._slave_at(ev.position())
        if j is not None:
            self.slaveClicked.emit(j)

    def mouseDoubleClickEvent(self, ev):
        j = self._slave_at(ev.position())
        if j is not None:
            self.slaveActivated.emit(j)


# ============================================================================ slave table
class SlaveTable(QTableWidget):
    """Slave list with drag & drop row reordering (rowMoved(src, dst))."""

    rowMoved = Signal(int, int)

    def __init__(self):
        super().__init__(0, 6)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)

    def dropEvent(self, ev):
        src = self.currentRow()
        dst = self.rowAt(int(ev.position().y()))
        if dst < 0:
            dst = self.rowCount() - 1
        ev.setDropAction(Qt.DropAction.IgnoreAction)
        ev.accept()
        if src >= 0 and dst != src:
            self.rowMoved.emit(src, dst)


# ============================================================================ page
class AddressMapPage(ToolPage):
    tool_id = "addrmap"
    title = "Address Map Editor"
    short_title = "Address Map Editor"
    subtitle = "IP'leri adres haritasına yerleştir: base adres, pencere boyutu ve port sırası."
    description = ("Projedeki IP'leri adres haritasına yerleştirir (aynı IP birden çok kez kullanılabilir), "
                   "otomatik ya da sabit adres verir, hizalama ve çakışma kontrolü yapar.")
    icon_name = "addrmap"
    version = "0.1.0"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._loading = False

        self.header.add(ui.button("IP ekle…", "library", "primary", self.add_ip,
                                  "IP Library'den bir IP seç ve adres haritasına ekle (aynı IP birden çok kez "
                                  "eklenebilir)"))
        bar = ui.ActionBar()
        bar.add("Harici slave ekle…", "plus", self.add_external,
                "Projede register haritası olmayan bir slave (ör. bellek, hazır IP) ekle")
        bar.sep()
        self.regs_btn = bar.add("Register haritasını düzenle", "regmap", self.edit_regs,
                                "Seçili IP'nin register haritasını Register Map Editor'da aç")
        bar.add("Excel'e aktar…", "file", self.export_xlsx, "Tüm adres haritası ve registerlar (.xlsx)",
                regmap_xlsx.available())
        bar.sep()
        bar.add("Interconnect Configurator", "interconnect", lambda: self.open_tool("interconnect"),
                "Bu adres haritasıyla crossbar'ı yapılandır ve üret")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: start address + validation
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("BAŞLANGIÇ ADRESİ", "section"))
        self.start_ed = QLineEdit()
        self.start_ed.setProperty("mono", "true")
        self.start_ed.setToolTip("Otomatik adresli slave'ler bu adresten itibaren liste sırasıyla yerleşir")
        self.start_ed.editingFinished.connect(self._start_changed)
        left.lay.addWidget(self.start_ed)
        left.lay.addWidget(ui.label("Otomatik adresli IP'ler bu adresten başlayarak liste sırasıyla, pencere "
                                    "boyutlarına hizalı yerleşir. Adres hücresine yazılan değer sabitlenir (🔒); "
                                    "kilide tıkla: otomatiğe dön.", "muted", wrap=True))
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("DOĞRULAMA", "section"))
        self.issues = ui.IssueList("Hata yok")
        self.issues.setMaximumHeight(16777215)
        left.lay.addWidget(self.issues, 1)
        left.setMinimumWidth(300)
        split.addWidget(left)

        # ---- right: memory strip + instance table
        right = ui.Card(margins=16, spacing=10)
        self.map = MemoryMap()
        self.map.slaveClicked.connect(self._select_row)
        self.map.slaveActivated.connect(self.edit_slave)
        right.lay.addWidget(self.map)
        self.table = SlaveTable()
        self.table.setHorizontalHeaderLabels(["Port", "Ad", "IP", "Adres", "Boyut", "Adresleme"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.rowMoved.connect(self._row_moved)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        right.lay.addWidget(self.table, 1)
        sbar = QHBoxLayout()
        sbar.setSpacing(4)
        sbar.addWidget(ui.button("Düzenle", None, "ghost", lambda: self.edit_slave(self._sel_row()),
                                 "Ad, adres ve boyut"))
        sbar.addWidget(ui.button("Bir kez daha ekle", None, "ghost", self.duplicate,
                                 "Seçili IP'nin yeni bir örneğini ekle (aynı register haritası, ayrı adres)"))
        sbar.addWidget(ui.button("Sil", None, "ghost", self.delete_slave,
                                 "Seçili örneği adres haritasından çıkar (Delete); IP tanımı projede kalır"))
        sbar.addStretch(1)
        sbar.addWidget(ui.button("Adrese göre sırala", None, "ghost", self.sort_slaves,
                                 "Listeyi (port numaralarını) adres sırasına diz"))
        sbar.addWidget(ui.button("Tümü otomatik", None, "ghost", self.unlock_all, "Tüm adresleri otomatiğe çevir"))
        sbar.addWidget(ui.button("↑", None, "ghost", lambda: self.move(-1), "Yukarı taşı"))
        sbar.addWidget(ui.button("↓", None, "ghost", lambda: self.move(1), "Aşağı taşı"))
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
        for text, slot in (("IP ekle (IP Library)…", self.add_ip), ("Harici slave ekle…", self.add_external),
                           ("Seçili IP'yi bir kez daha ekle", self.duplicate), (None, None),
                           ("Adrese göre sırala", self.sort_slaves),
                           ("Tüm adresleri otomatiğe çevir", self.unlock_all), (None, None),
                           ("Register haritasını düzenle", self.edit_regs),
                           ("Excel'e aktar…", self.export_xlsx), (None, None),
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

    def on_project_modified(self):
        self._refresh()

    def on_project_replaced(self):
        self._refresh(None)

    def select_instance(self, name):
        names = [i.name for i in self.proj.instances]
        if name in names:
            self._select_row(names.index(name))

    # ================================================================ view
    def _refresh(self, select=None):
        t = theme.manager().tokens
        mono = theme.mono_font()
        muted = QColor(t["muted"])
        sel = self._sel_row() if select is None else select
        self._loading = True
        self.start_ed.setText(hex32(A.start_of(self.proj)))
        slaves = A.xbar_slaves(self.proj)
        bad = A.overlapping(self.proj)
        self.table.setRowCount(len(slaves))
        for j, (inst, s) in enumerate(zip(self.proj.instances, slaves)):
            ip = self.proj.get_ip(inst.ip) if inst.ip else None
            if inst.external:
                kind = "harici"
            elif ip is None:
                kind = f"{inst.ip} (projede yok)"
            else:
                kind = ip.name + (f" {ip.version}" if ip.version else "")
            tip = (f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}\nMASK {hex32(s.mask)}\n"
                   f"kullanılan {s.span:#x} byte")
            ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
            cells = [
                (f"SLAVE{j}", ro, True),
                (inst.name, ro | Qt.ItemFlag.ItemIsEditable, False),
                (kind, ro, False),
                (("🔒 " if inst.fixed else "") + hex32(s.base), ro | Qt.ItemFlag.ItemIsEditable, True),
                (size_text(s.window), ro, False),
                ("sabit" if inst.fixed else "otomatik", ro, False),
            ]
            for col, (text, flags, is_mono) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setFlags(flags)
                it.setToolTip(tip)
                if is_mono:
                    it.setFont(mono)
                if col == C_NAME:
                    it.setToolTip("Çift tık: örneğin adını değiştir (Debug Bridge ve interconnect bu adı kullanır)")
                if col == C_IP:
                    it.setForeground(muted if inst.external else QColor(t["danger"]) if ip is None
                                     else QColor(t["text"]))
                    if ip is not None:
                        n = len(self.proj.instances_of(ip.name))
                        it.setToolTip(f"{ip.name}: {len(ip.registers)} register"
                                      + (f" · haritada {n} kez" if n > 1 else "")
                                      + "\nSağ tık: register haritasını düzenle")
                if col == C_LOCK:
                    it.setForeground(QColor(t["accent"]) if inst.fixed else muted)
                    it.setToolTip("Tıkla: " + ("otomatik adrese dön" if inst.fixed else "bu adresi sabitle"))
                if col == C_ADDR and j in bad:
                    it.setForeground(QColor(t["danger"]))
                self.table.setItem(j, col, it)
            self.table.setCellWidget(j, C_SIZE, self._size_combo(j, s))
        self.table.resizeColumnsToContents()
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(C_SIZE, max(self.table.columnWidth(C_SIZE), 110))
        if sel is not None and 0 <= sel < len(slaves):
            self.table.selectRow(sel)
        self._loading = False

        self.map.set_slaves(slaves, self._sel_row())
        errs = A.validate(self.proj) if slaves else ["Adres haritası boş: IP ekle ile IP Library'den bir IP seç "
                                                     "ya da Harici slave ekle."]
        if not errs:
            lo = min(s.base for s in slaves)
            hi = max(s.base + s.window for s in slaves) - 1
            self.issues.ok_text = f"Hata yok · {len(slaves)} slave · {hex32(lo)} – {hex32(hi)}"
        self.issues.set_issues(errs)
        self._sync_buttons()

    def _size_combo(self, j, s):
        cb = QComboBox()
        need = A.need_window(s.span)
        for w in window_choices(need):
            cb.addItem(size_text(w), w)
        cb.setCurrentIndex(max(0, cb.findData(s.window)))
        cb.setToolTip("Slave'e ayrılan adres aralığı (en az IP'nin kullandığı kadar)")
        cb.currentIndexChanged.connect(lambda _i, j=j, cb=cb: self._size_changed(j, cb.currentData()))
        return cb

    def _sel_row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else None

    def _sel_inst(self):
        row = self._sel_row()
        return self.proj.instances[row] if row is not None and row < len(self.proj.instances) else None

    def _select_row(self, j):
        self.table.selectRow(j)

    def _selection_changed(self):
        self.map.set_slaves(A.xbar_slaves(self.proj), self._sel_row())
        self._sync_buttons()

    def _sync_buttons(self):
        inst = self._sel_inst()
        self.regs_btn.setEnabled(bool(inst and not inst.external and self.proj.get_ip(inst.ip)))

    def on_theme(self):
        self.issues.retint()
        self._refresh()

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        m = QMenu(self)
        if row >= 0:
            self.table.selectRow(row)
            inst = self.proj.instances[row]
            m.addAction("Düzenle…", lambda: self.edit_slave(row))
            if not inst.external:
                m.addAction("Register haritasını düzenle", self.edit_regs)
                m.addAction("Bir kez daha ekle", self.duplicate)
            m.addAction("Otomatik adrese dön" if inst.fixed else "Adresi sabitle", lambda: self._cell_clicked(row, C_LOCK))
            m.addSeparator()
            m.addAction("Adres haritasından çıkar", self.delete_slave)
            m.addSeparator()
        m.addAction("IP ekle…", self.add_ip)
        m.addAction("Harici slave ekle…", self.add_external)
        m.exec(self.table.viewport().mapToGlobal(pos))

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

    def _rename(self, inst, name):
        if not X.IDENT.match(name):
            QMessageBox.warning(self, self.title, "Ad harfle başlamalı, sadece harf/rakam/_ içermeli.")
            return False
        if name != inst.name and self.proj.get_instance(name):
            QMessageBox.warning(self, self.title, f"Adres haritasında “{name}” adında bir slave zaten var.")
            return False
        inst.name = name
        return True

    def _item_changed(self, item):
        if self._loading:
            return
        j, col = item.row(), item.column()
        inst = self.proj.instances[j]
        if col == C_NAME:
            name = item.text().strip()
            if name and name != inst.name and self._rename(inst, name):
                return self._changed(j)
            return self._refresh(j)
        if col == C_ADDR:
            w = A.window_of(self.proj, inst)
            try:
                v = parse_int(item.text().replace("🔒", "").strip()) & 0xFFFFFFFF
            except ValueError:
                self.status("Adres bir sayı olmalı (ör. 0x80000000)")
                return self._refresh(j)
            if v & (w - 1):
                QMessageBox.warning(self, self.title, f"{hex32(v)}, {inst.name} için {size_text(w)} "
                                                      "sınırına hizalı değil.")
                return self._refresh(j)
            inst.base, inst.fixed = v, True          # a typed address is kept
            self._changed(j)

    def _cell_clicked(self, row, col):
        if col == C_LOCK and 0 <= row < len(self.proj.instances):
            inst = self.proj.instances[row]
            inst.fixed = not inst.fixed
            self._changed(row)

    def _size_changed(self, j, w):
        if self._loading or not 0 <= j < len(self.proj.instances):
            return
        inst = self.proj.instances[j]
        inst.window = w
        if inst.fixed and inst.base & (w - 1):
            inst.base &= ~(w - 1)
            self.status(f"{inst.name} adresi yeni boyuta hizalandı: {hex32(inst.base)}")
        self._changed(j)

    def _row_moved(self, src, dst):
        sl = self.proj.instances
        sl.insert(dst, sl.pop(src))
        self._changed(dst)

    def move(self, delta):
        row = self._sel_row()
        if row is not None and 0 <= row + delta < len(self.proj.instances):
            self._row_moved(row, row + delta)

    def sort_slaves(self):
        self.proj.instances.sort(key=lambda i: i.base)
        self._changed()
        self.status("Liste adres sırasına dizildi")

    def unlock_all(self):
        for inst in self.proj.instances:
            inst.fixed = False
        self._changed()

    def edit_slave(self, row):
        if row is None or not 0 <= row < len(self.proj.instances):
            return
        inst = self.proj.instances[row]
        s = A.xbar_slaves(self.proj)[row]
        new = SlaveDialog.ask(self, s, f"SLAVE{row}: {inst.name}", A.MIN_WINDOW, False)
        if new is None:
            return
        if new.name != inst.name and not self._rename(inst, new.name):
            return
        inst.base, inst.window, inst.fixed = new.base, new.window, new.fixed
        if inst.external:
            inst.span = new.span
        self._changed(row)

    def add_ip(self):
        name = self.pick_ip("Adres haritasına IP ekle", "Projedeki ya da açık kütüphanelerdeki bir IP'yi seç. "
                                                        "Aynı IP birden çok kez eklenebilir; her örnek kendi adını ve "
                                                        "adresini alır.")
        if not name:
            return
        inst = A.add_instance(self.proj, name)
        self._changed(len(self.proj.instances) - 1)
        self.status(f"{inst.name} eklendi ({hex32(inst.base)})")

    def duplicate(self):
        inst = self._sel_inst()
        if inst is None or inst.external:
            return
        new = A.add_instance(self.proj, inst.ip)
        self._changed(len(self.proj.instances) - 1)
        self.status(f"{inst.ip} bir kez daha eklendi: {new.name} ({hex32(new.base)})")

    def add_external(self):
        k = 0
        while self.proj.get_instance(f"EXT{k}"):
            k += 1
        name = f"EXT{k}"
        proto = X.XbarSlave(name, 0, 0xFFFFF000, 0x1000, True, True, False)
        new = SlaveDialog.ask(self, proto, "Harici slave", A.MIN_WINDOW, False)
        if new is None:
            return
        if self.proj.get_instance(new.name) or not X.IDENT.match(new.name):
            QMessageBox.warning(self, self.title, f"“{new.name}” kullanılamaz: ad benzersiz olmalı, harfle başlamalı "
                                                  "ve sadece harf/rakam/_ içermeli.")
            return
        self.proj.instances.append(IPInstance(new.name, "", new.base, new.window, new.fixed, new.span))
        self._changed(len(self.proj.instances) - 1)

    def delete_slave(self):
        row = self._sel_row()
        if row is None:
            return
        inst = self.proj.instances[row]
        text = (f"{inst.name} adres haritasından çıkarılacak." if inst.external else
                f"{inst.name} adres haritasından çıkarılacak; {inst.ip} IP tanımı projede kalır.")
        if not ui.confirm(self, "Adres haritasından çıkar", text + " Sonraki slave'lerin port numarası bir azalır.",
                          ok="Çıkar"):
            return
        del self.proj.instances[row]
        n = len(self.proj.instances)
        self._changed(min(row, n - 1) if n else None)

    def keyPressEvent(self, ev):
        if self.table.hasFocus() and ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) \
                and self.table.state() != QAbstractItemView.State.EditingState:
            self.delete_slave()
        else:
            super().keyPressEvent(ev)

    # ================================================================ other tools / output
    def edit_regs(self):
        inst = self._sel_inst()
        if inst and not inst.external and self.proj.get_ip(inst.ip):
            self.edit_register_map(inst.ip)

    def export_xlsx(self):
        ips = self.ctx.address_ips()
        if not ips:
            QMessageBox.information(self, self.title, "Adres haritasında register haritası olan IP yok.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Adres ve kütük haritası (Excel)",
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{self.proj.name}_regmap.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        try:
            regmap_xlsx.export_xlsx(ips, path, f"{self.proj.name} adres haritası")
        except (OSError, RuntimeError) as exc:
            return self.error("Aktarılamadı", exc)
        self.status(f"Excel'e aktarıldı: {path}")
