"""Address Map & Interconnect Configurator: project address map -> axilxbar wrapper.

Every slave in the list is a used port; the list order is the port number (SLAVE0, SLAVE1 …).
Each slave's address is either automatic (consecutive, aligned, in list order from the start
address) or fixed (typed by hand, marked with a lock). Masters, slaves, order, sizes and the
port style are stored in the project file ("interconnect").
"""

import os

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
                               QPlainTextEdit, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolTip, QVBoxLayout, QWidget)

import xbargen as X
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import SlaveDialog, size_text, window_choices
from axil_studio.tools.base import ToolPage
from axil_studio.tools.xbar_diagram import BlockDiagram
from model import hex32, parse_int

XBAR_FILTER = "Interconnect ayarları (*.xbar.json);;JSON (*.json);;Tüm dosyalar (*)"
MIN_WINDOW = 0x1000          # default slave size: 4 KB, larger when the IP needs it
STYLE_SHORT = {"packed": "HDL Designer (paketli)", "signals": "Ayrık AXI sinyalleri"}
# slave table columns
C_PORT, C_NAME, C_SRC, C_ADDR, C_SIZE, C_LOCK = range(6)


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
class InterconnectPage(ToolPage):
    tool_id = "interconnect"
    title = "Address Map & Interconnect Configurator"
    short_title = "Address Map & Interconnect"
    subtitle = "Adres haritasını oluştur, doğrula ve axilxbar interconnect wrapper'ını üret."
    description = ("Projedeki IP'lerin adres haritasını oluşturur, hizalama ve çakışma kontrolü yapar ve "
                   "ZipCPU axilxbar için isimli portlu wrapper üretir.")
    icon_name = "interconnect"
    legacy_script = None

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.spec = X.XbarSpec(slaves=[])
        self.excluded = []          # project IPs deliberately not connected to this interconnect
        self.start = 0x40000000     # automatic addresses begin here
        self._loading = False

        self.gen_btn = self.header.add(ui.button("Üret…", "play", "primary", self.generate,
                                                 "axilxbar wrapper'ını ve dokümanını üret"))
        bar = ui.ActionBar()
        bar.add("Projeyle eşitle", "refresh", self.sync_project, "Slave listesini projedeki IP'lere göre güncelle")
        bar.add("Harici slave ekle…", "plus", self.add_external,
                "Projede register tanımı olmayan bir slave (ör. bellek, hazır IP) ekle")
        bar.add("IP bağla…", "interconnect", self.connect_ip, "Listeden çıkarılmış bir proje IP'sini geri ekle")
        bar.sep()
        bar.add("Ayar dosyası aç…", "folder", self.import_xbar, ".xbar.json dosyasından ayarları al")
        bar.add("Ayar dosyası kaydet…", "save", self.export_xbar, "Ayarları .xbar.json olarak kaydet")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: crossbar, masters, validation
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("CROSSBAR", "section"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.module_ed = QLineEdit()
        self.style_cb = QComboBox()
        for key, text in X.STYLES.items():
            if key in STYLE_SHORT:
                self.style_cb.addItem(STYLE_SHORT[key], key)
                self.style_cb.setItemData(self.style_cb.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        form.addRow("Modül adı", self.module_ed)
        form.addRow("Port stili", self.style_cb)
        left.lay.addLayout(form)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("MASTER'LAR", "section"))
        self.masters = QTableWidget(0, 2)
        self.masters.setHorizontalHeaderLabels(["Port", "Ad"])
        self.masters.verticalHeader().setVisible(False)
        self.masters.setShowGrid(False)
        self.masters.setAlternatingRowColors(True)
        self.masters.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.masters.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.masters.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                     QAbstractItemView.EditTrigger.EditKeyPressed)
        mh = self.masters.horizontalHeader()
        mh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        mh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.masters.setMaximumHeight(160)
        self.masters.setToolTip("Ada çift tıklayarak değiştir")
        self.masters.itemChanged.connect(self._master_changed)
        self.masters.itemSelectionChanged.connect(self._update_diagram)
        left.lay.addWidget(self.masters)
        mbar = QHBoxLayout()
        mbar.setSpacing(4)
        mbar.addWidget(ui.button("Master", "plus", "ghost", self.add_master, "Yeni master"))
        mbar.addWidget(ui.button("Sil", None, "ghost", self.delete_master, "Seçili master'ı sil"))
        mbar.addStretch(1)
        left.lay.addLayout(mbar)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("DOĞRULAMA", "section"))
        self.issues = ui.IssueList("Hata yok")
        self.issues.setMaximumHeight(16777215)
        left.lay.addWidget(self.issues, 1)
        left.setMinimumWidth(320)
        split.addWidget(left)

        # ---- right: tabs
        right = ui.Card(margins=0)
        self.tabs = ui.DetachableTabs(self.title)
        right.lay.addWidget(self.tabs)

        map_tab = self.map_tab = QWidget()
        mt = QVBoxLayout(map_tab)
        mt.setContentsMargins(16, 12, 16, 12)
        mt.setSpacing(10)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        srow.addWidget(ui.label("Başlangıç adresi", "muted"))
        self.start_ed = QLineEdit()
        self.start_ed.setProperty("mono", "true")
        self.start_ed.setMaximumWidth(140)
        self.start_ed.setToolTip("Otomatik adresli slave'ler bu adresten itibaren liste sırasıyla yerleşir")
        srow.addWidget(self.start_ed)
        srow.addStretch(1)
        srow.addWidget(ui.label("Adres hücresine yazılan değer sabitlenir (🔒); kilide tıkla: otomatiğe dön",
                                "muted"))
        mt.addLayout(srow)
        self.map = MemoryMap()
        self.map.slaveClicked.connect(self._select_slave)
        self.map.slaveActivated.connect(self.edit_slave)
        mt.addWidget(self.map)
        self.slaves = SlaveTable()
        self.slaves.setHorizontalHeaderLabels(["Port", "Ad", "Kaynak", "Adres", "Boyut", "Adresleme"])
        self.slaves.verticalHeader().setVisible(False)
        self.slaves.setShowGrid(False)
        self.slaves.setAlternatingRowColors(True)
        self.slaves.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.slaves.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.slaves.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                    QAbstractItemView.EditTrigger.EditKeyPressed)
        self.slaves.itemChanged.connect(self._slave_item_changed)
        self.slaves.cellClicked.connect(self._slave_clicked)
        self.slaves.itemSelectionChanged.connect(self._selection_changed)
        self.slaves.rowMoved.connect(self._row_moved)
        mt.addWidget(self.slaves, 1)
        sbar = QHBoxLayout()
        sbar.setSpacing(4)
        sbar.addWidget(ui.button("Düzenle", None, "ghost", lambda: self.edit_slave(self._sel_row()),
                                 "Ad, adres ve boyut"))
        sbar.addWidget(ui.button("Sil", None, "ghost", self.delete_slave, "Seçili slave'i listeden çıkar (Delete)"))
        sbar.addStretch(1)
        sbar.addWidget(ui.button("Adrese göre sırala", None, "ghost", self.sort_slaves,
                                 "Listeyi (port numaralarını) adres sırasına diz"))
        sbar.addWidget(ui.button("↑", None, "ghost", lambda: self.move_slave(-1), "Yukarı taşı"))
        sbar.addWidget(ui.button("↓", None, "ghost", lambda: self.move_slave(1), "Aşağı taşı"))
        mt.addLayout(sbar)
        self.tabs.addTab(map_tab, "Adres haritası")

        dia_tab = self.dia_tab = QWidget()
        dt = QVBoxLayout(dia_tab)
        dt.setContentsMargins(16, 12, 16, 12)
        dt.setSpacing(8)
        drow = QHBoxLayout()
        drow.setSpacing(4)
        drow.addWidget(ui.button("−", None, "ghost", lambda: self.diagram.zoom_by(1 / 1.2), "Uzaklaş"))
        self.zoom_lbl = ui.label("100%", "muted")
        self.zoom_lbl.setMinimumWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drow.addWidget(self.zoom_lbl)
        drow.addWidget(ui.button("+", None, "ghost", lambda: self.diagram.zoom_by(1.2), "Yakınlaş"))
        drow.addWidget(ui.button("Sığdır", None, "ghost", lambda: self.diagram.set_zoom(None), "Genişliğe sığdır"))
        drow.addWidget(ui.button("100%", None, "ghost", lambda: self.diagram.set_zoom(1.0), "Gerçek boyut"))
        drow.addSpacing(12)
        drow.addWidget(ui.label("Fare tekerleği: yakınlaş / uzaklaş · sürükle: kaydır", "muted"))
        drow.addStretch(1)
        self.all_sig_cb = QCheckBox("Tüm sinyalleri göster")
        self.all_sig_cb.setToolTip("Ayrık sinyal stilinde her AXI sinyalini ayrı pin olarak çizer")
        self.all_sig_cb.toggled.connect(lambda on: self.diagram.set_all_signals(on))
        drow.addWidget(self.all_sig_cb)
        dt.addLayout(drow)
        self.diagram = BlockDiagram()
        self.diagram.slaveClicked.connect(self._select_slave)
        self.diagram.slaveActivated.connect(self.edit_slave)
        self.diagram.masterClicked.connect(lambda i: self.masters.selectRow(i))
        self.diagram.masterActivated.connect(self.rename_master)
        self.diagram.zoomChanged.connect(lambda z: self.zoom_lbl.setText(f"{round(z * 100)}%"))
        scroll = QScrollArea()
        scroll.setWidget(self.diagram)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.diagram.scroll = scroll
        dt.addWidget(scroll, 1)
        self.tabs.addTab(dia_tab, "Blok diyagramı")

        pv_tab = self.pv_tab = QWidget()
        pv = QVBoxLayout(pv_tab)
        pv.setContentsMargins(12, 12, 12, 12)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setProperty("mono", "true")
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        pv.addWidget(self.preview)
        self.tabs.addTab(pv_tab, "Verilog önizleme")
        self.tabs.currentChanged.connect(lambda *_: self._refresh_preview())

        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 930])
        self.body.addWidget(split, 1)

        self.module_ed.editingFinished.connect(self._settings_changed)
        self.style_cb.currentIndexChanged.connect(self._settings_changed)
        self.start_ed.editingFinished.connect(self._start_changed)
        self.load_from_project()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Interconnect")
        win = self.window()
        for text, slot in (("Projeyle eşitle", self.sync_project), ("Harici slave ekle…", self.add_external),
                           ("IP bağla…", self.connect_ip), ("Master ekle", self.add_master), (None, None),
                           ("Adrese göre sırala", self.sort_slaves),
                           ("Tüm adresleri otomatiğe çevir", self.unlock_all), (None, None),
                           ("Blok diyagramı", lambda: self.tabs.show_page(self.dia_tab)),
                           ("Verilog önizleme", lambda: self.tabs.show_page(self.pv_tab)), (None, None),
                           ("Ayar dosyası aç…", self.import_xbar), ("Ayar dosyası kaydet…", self.export_xbar),
                           (None, None), ("Üret…", self.generate)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        m.addAction("Klasik arayüzü aç", lambda: ui.launch_legacy("wrapgen_gui.py"))

    # ================================================================ project storage
    def _normalize(self):
        """Every port is used and enabled; at least one master."""
        self.spec.slaves = [s for s in self.spec.slaves if s.enabled]
        for m in self.spec.masters:
            m.enabled = True
        if not self.spec.masters:
            self.spec.masters.append(X.XbarMaster("hexbus"))

    def load_from_project(self):
        d = self.ctx.project.interconnect
        self.spec = X.XbarSpec.from_dict(d) if d else X.XbarSpec(slaves=[])
        self.excluded = list(d.get("excluded", [])) if d else []
        auto = (d or {}).get("auto", {})
        bases = [ip.base for ip in self.ctx.project.ips]
        default_start = (min(bases) & ~0xFFF) if bases else 0x40000000
        self.start = parse_int(auto["start"]) if "start" in auto else default_start
        self._normalize()
        X.reconcile(self.spec, self.ctx.project, MIN_WINDOW, excluded=self.excluded, compact=True)
        self._readdress()
        moved = self._push_bases()
        self._load_widgets()
        self._commit(mark=moved)
        self._refresh()

    def _commit(self, mark=True):
        d = self.spec.to_dict()
        d["auto"] = {"start": f"0x{self.start:08X}"}
        if self.excluded:
            d["excluded"] = list(self.excluded)
        if d != self.ctx.project.interconnect:
            self.ctx.project.interconnect = d
            if mark:
                self.ctx.mark_modified(self.tool_id)

    def _load_widgets(self):
        self._loading = True
        self.module_ed.setText(self.spec.module)
        self.style_cb.setCurrentIndex(max(0, self.style_cb.findData(self.spec.style)))
        self.start_ed.setText(f"0x{self.start:08X}")
        self._loading = False

    def on_project_replaced(self):
        self.load_from_project()

    def on_project_modified(self):
        X.reconcile(self.spec, self.ctx.project, MIN_WINDOW, excluded=self.excluded, compact=True)
        self._after_change()

    def sync_project(self):
        self.on_project_modified()
        self.status("Slave listesi projeyle eşitlendi")

    # ================================================================ addressing
    def _readdress(self):
        """Automatic slaves get consecutive aligned addresses in list order; fixed ones stay."""
        try:
            X.auto_address(self.spec, self.start)
        except ValueError as exc:
            self.status(str(exc))

    def _push_bases(self):
        changed = False
        for s in self.spec.slaves:
            ip = None if s.external else self.ctx.project.get_ip(s.name)
            if ip is not None and ip.base != s.base:
                ip.base = s.base
                changed = True
        return changed

    def _after_change(self, select=None):
        self._readdress()
        self._push_bases()
        self._commit()
        self._refresh(select)

    # ================================================================ view
    def _refresh(self, select=None):
        t = theme.manager().tokens
        mono = theme.mono_font()
        muted = QColor(t["muted"])
        sel = self._sel_row() if select is None else select
        self._loading = True

        self.masters.setRowCount(self.spec.nm)
        for i, m in enumerate(self.spec.masters):
            port = QTableWidgetItem(f"MASTER{i}")
            port.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            port.setFont(mono)
            self.masters.setItem(i, 0, port)
            self.masters.setItem(i, 1, QTableWidgetItem(m.name))

        bad = overlapping(self.spec.slaves)
        self.slaves.setRowCount(self.spec.ns)
        for j, s in enumerate(self.spec.slaves):
            tip = (f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}\nMASK {hex32(s.mask)}\n"
                   f"kullanılan {s.span:#x} byte")
            ro = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
            cells = [
                (f"SLAVE{j}", ro, True),
                (s.name, ro | (Qt.ItemFlag.ItemIsEditable if s.external else Qt.ItemFlag.NoItemFlags), False),
                ("harici" if s.external else "proje", ro, False),
                (("🔒 " if s.fixed else "") + hex32(s.base), ro | Qt.ItemFlag.ItemIsEditable, True),
                (size_text(s.window), ro, False),
                ("sabit" if s.fixed else "otomatik", ro, False),
            ]
            for col, (text, flags, is_mono) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setFlags(flags)
                it.setToolTip(tip)
                if is_mono:
                    it.setFont(mono)
                if col == C_LOCK:
                    it.setForeground(QColor(t["accent"]) if s.fixed else muted)
                    it.setToolTip("Tıkla: " + ("otomatik adrese dön" if s.fixed else "bu adresi sabitle"))
                if col == C_ADDR and j in bad:
                    it.setForeground(QColor(t["danger"]))
                self.slaves.setItem(j, col, it)
            self.slaves.setCellWidget(j, C_SIZE, self._size_combo(j, s))
        self.slaves.resizeColumnsToContents()
        hdr = self.slaves.horizontalHeader()
        hdr.setSectionResizeMode(C_NAME, QHeaderView.ResizeMode.Stretch)
        self.slaves.setColumnWidth(C_SIZE, max(self.slaves.columnWidth(C_SIZE), 110))
        if sel is not None and 0 <= sel < self.spec.ns:
            self.slaves.selectRow(sel)
        self._loading = False

        self.map.set_slaves(self.spec.slaves, self._sel_row())
        if not self.spec.slaves:
            errs = ["Henüz slave yok: Wrapper'dan projeye IP ekle ya da Harici slave ekle."]
        else:
            errs = self.spec.validate()
        if not errs:
            self.issues.ok_text = f"Hata yok · {self.spec.nm} master · {self.spec.ns} slave"
        self.issues.set_issues(errs)
        self.gen_btn.setEnabled(not errs)
        self._update_diagram()

    def _size_combo(self, j, s):
        cb = QComboBox()
        need = 4
        while need < max(s.span, MIN_WINDOW):
            need <<= 1
        for w in window_choices(need):
            cb.addItem(size_text(w), w)
        cb.setCurrentIndex(max(0, cb.findData(s.window)))
        cb.setToolTip("Slave'e ayrılan adres aralığı (en az IP'nin kullandığı kadar)")
        cb.currentIndexChanged.connect(lambda _i, j=j, cb=cb: self._size_changed(j, cb.currentData()))
        return cb

    def _sel_row(self):
        rows = self.slaves.selectionModel().selectedRows() if self.slaves.selectionModel() else []
        return rows[0].row() if rows else None

    def _select_slave(self, j):
        self.slaves.selectRow(j)

    def _selection_changed(self):
        self.map.set_slaves(self.spec.slaves, self._sel_row())
        self._update_diagram()

    def _update_diagram(self):
        rows = self.masters.selectionModel().selectedRows() if self.masters.selectionModel() else []
        sel = ("s", self._sel_row()) if self._sel_row() is not None else (("m", rows[0].row()) if rows else None)
        self.diagram.set_spec(self.spec, sel, overlapping(self.spec.slaves))
        self._refresh_preview()

    def _refresh_preview(self):
        if not self.tabs.is_shown(self.pv_tab):
            return
        errs = self.spec.validate() if self.spec.slaves else ["Henüz slave yok"]
        if errs:
            text = "// Ayarlarda hata var, Verilog üretilemiyor:\n// " + "\n// ".join(errs)
        else:
            text = X.gen_xbar(self.spec)
        bar = self.preview.verticalScrollBar().value()
        self.preview.setPlainText(text)
        self.preview.verticalScrollBar().setValue(bar)

    def on_theme(self):
        self.issues.retint()
        self._refresh()

    # ================================================================ edits
    def _settings_changed(self, *_):
        if self._loading:
            return
        self.spec.module = self.module_ed.text().strip()
        self.spec.style = self.style_cb.currentData()
        self._commit()
        self._refresh()

    def _start_changed(self):
        if self._loading:
            return
        try:
            v = parse_int(self.start_ed.text()) & 0xFFFFFFFF
        except ValueError:
            self.start_ed.setStyleSheet(f"border-color: {theme.manager().tokens['danger']};")
            return
        self.start_ed.setStyleSheet("")
        self.start = v
        self.start_ed.setText(f"0x{v:08X}")
        self._after_change()

    def _master_changed(self, item):
        if self._loading or item.column() != 1:
            return
        m = self.spec.masters[item.row()]
        m.name = item.text().strip() or m.name
        self._commit()
        self._refresh()

    def _slave_item_changed(self, item):
        if self._loading:
            return
        j, col = item.row(), item.column()
        s = self.spec.slaves[j]
        if col == C_NAME and s.external:
            name = item.text().strip()
            if name and name not in {x.name for x in self.spec.slaves}:
                s.name = name
            self._after_change(j)
        elif col == C_ADDR:
            try:
                v = parse_int(item.text().replace("🔒", "").strip()) & 0xFFFFFFFF
            except ValueError:
                self.status("Adres bir sayı olmalı (ör. 0x80000000)")
                return self._refresh(j)
            if v & (s.window - 1):
                QMessageBox.warning(self, self.title, f"{hex32(v)}, {s.name} için {size_text(s.window)} "
                                                      "sınırına hizalı değil.")
                return self._refresh(j)
            s.base, s.fixed = v, True          # a typed address is kept
            self._after_change(j)

    def _slave_clicked(self, row, col):
        if col == C_LOCK and 0 <= row < self.spec.ns:
            s = self.spec.slaves[row]
            s.fixed = not s.fixed
            self._after_change(row)

    def _size_changed(self, j, w):
        if self._loading or not 0 <= j < self.spec.ns:
            return
        s = self.spec.slaves[j]
        s.mask = (~(w - 1)) & 0xFFFFFFFF
        if s.fixed and s.base & (w - 1):
            s.base &= ~(w - 1)
            self.status(f"{s.name} adresi yeni boyuta hizalandı: {hex32(s.base)}")
        self._after_change(j)

    def _row_moved(self, src, dst):
        sl = self.spec.slaves
        sl.insert(dst, sl.pop(src))
        self._after_change(dst)

    def move_slave(self, delta):
        row = self._sel_row()
        if row is not None and 0 <= row + delta < self.spec.ns:
            self._row_moved(row, row + delta)

    def sort_slaves(self):
        self.spec.slaves.sort(key=lambda s: s.base)
        self._after_change()
        self.status("Liste adres sırasına dizildi")

    def unlock_all(self):
        for s in self.spec.slaves:
            s.fixed = False
        self._after_change()

    def edit_slave(self, row):
        if row is None or not 0 <= row < self.spec.ns:
            return
        s = self.spec.slaves[row]
        new = SlaveDialog.ask(self, s, f"SLAVE{row}: {s.name}", MIN_WINDOW, False)
        if new is None:
            return
        new.enabled = True
        if new.name != s.name and new.name in {x.name for x in self.spec.slaves}:
            QMessageBox.warning(self, self.title, f"“{new.name}” adında bir slave zaten var.")
            return
        self.spec.slaves[row] = new
        self._after_change(row)

    def add_external(self):
        names = {s.name for s in self.spec.slaves}
        k = 0
        while f"EXT{k}" in names:
            k += 1
        proto = X.XbarSlave(f"EXT{k}", 0, 0xFFFFF000, 0x1000, True, True, False)
        new = SlaveDialog.ask(self, proto, "Harici slave", MIN_WINDOW, False)
        if new is None:
            return
        if new.name in names:
            QMessageBox.warning(self, self.title, f"“{new.name}” adında bir slave zaten var.")
            return
        new.external, new.enabled = True, True
        self.spec.slaves.append(new)
        self._after_change(self.spec.ns - 1)

    def delete_slave(self):
        row = self._sel_row()
        if row is None:
            return
        s = self.spec.slaves[row]
        if s.external:
            text = f"{s.name} listeden çıkarılacak."
        else:
            text = (f"{s.name} bu interconnect'ten çıkarılacak; IP projede kalır ve IP bağla ile geri "
                    "eklenebilir.")
        if not ui.confirm(self, "Slave'i çıkar", text + " Sonraki slave'lerin port numarası bir azalır.",
                          ok="Çıkar"):
            return
        if not s.external and s.name not in self.excluded:
            self.excluded.append(s.name)
        del self.spec.slaves[row]
        self._after_change(min(row, self.spec.ns - 1) if self.spec.ns else None)

    def connect_ip(self):
        names = [ip.name for ip in self.ctx.project.ips if ip.name in self.excluded]
        if not names:
            QMessageBox.information(self, self.title, "Projedeki tüm IP'ler bu interconnect'e bağlı.")
            return
        name, ok = QInputDialog.getItem(self, "IP bağla", "Eklenecek IP:", names, 0, False)
        if not ok:
            return
        self.excluded.remove(name)
        X.reconcile(self.spec, self.ctx.project, MIN_WINDOW, excluded=self.excluded, compact=True)
        self._after_change([s.name for s in self.spec.slaves].index(name))

    def add_master(self):
        if self.spec.nm >= 16:
            return
        self.spec.masters.append(X.XbarMaster(f"master{self.spec.nm}", True))
        self._after_change()
        self.masters.selectRow(self.spec.nm - 1)

    def delete_master(self):
        rows = self.masters.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        if self.spec.nm == 1:
            QMessageBox.warning(self, self.title, "Interconnect'te en az bir master olmalı.")
            return
        m = self.spec.masters[i]
        if not ui.confirm(self, "Master'ı sil", f"MASTER{i} ({m.name}) silinecek. Sonraki master'ların "
                                                 "port numarası bir azalır."):
            return
        del self.spec.masters[i]
        self._after_change()

    def rename_master(self, i):
        m = self.spec.masters[i]
        name, ok = QInputDialog.getText(self, f"MASTER{i}", "Ad:", text=m.name)
        if ok and name.strip():
            m.name = name.strip()
            self._after_change()

    def keyPressEvent(self, ev):
        if self.slaves.hasFocus() and ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) \
                and self.slaves.state() != QAbstractItemView.State.EditingState:
            self.delete_slave()
        else:
            super().keyPressEvent(ev)

    # ================================================================ files / output
    def import_xbar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Interconnect ayar dosyası", os.path.dirname(self.ctx.path or ROOT),
                                              XBAR_FILTER)
        if not path:
            return
        try:
            spec = X.XbarSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error("Açılamadı", exc)
        ips = {ip.name for ip in self.ctx.project.ips}
        for s in spec.slaves:
            if s.enabled and s.name not in ips:
                s.external = True
            s.fixed = True                  # addresses from the file are kept
        self.spec = spec
        self._normalize()
        X.reconcile(self.spec, self.ctx.project, MIN_WINDOW, excluded=self.excluded, compact=True)
        self._load_widgets()
        self._after_change()
        self.status(f"Ayarlar alındı: {path}")

    def export_xbar(self):
        path, _ = QFileDialog.getSaveFileName(self, "Interconnect ayar dosyası",
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{self.spec.module}.xbar.json"), XBAR_FILTER)
        if not path:
            return
        try:
            self.spec.save(path)
        except OSError as exc:
            return self.error("Kaydedilemedi", exc)
        self.status(f"Kaydedildi: {path}")

    def generate(self):
        errs = self.spec.validate() if self.spec.slaves else ["Henüz slave yok"]
        if errs:
            QMessageBox.warning(self, self.title, "Adres haritasında hata var. Soldaki listede gösterilen hataları "
                                                  "düzeltip tekrar dene.")
            return
        outdir = QFileDialog.getExistingDirectory(self, "Çıktı klasörü", os.path.dirname(self.ctx.path or ROOT))
        if not outdir:
            return
        try:
            paths = X.write_xbar(self.spec, outdir)
        except Exception as exc:  # noqa: BLE001
            return self.error("Üretim başarısız", exc)
        hint = {
            "packed": "Bağlantı: master MASTER_AXI_REQ/RSP → MASTER<i>_AXI_REQ/RSP, "
                      "IP'nin _pk bloğu SLAVE_AXI_REQ/RSP → SLAVE<j>_AXI_REQ/RSP.",
            "signals": "Bağlantı: MASTER<i>_AXI_* ve SLAVE<j>_AXI_* portları IP'lerin M_AXI_* / S_AXI_* "
                       "portlarına bağlanır.",
        }.get(self.spec.style, "")
        QMessageBox.information(self, "Üretim tamamlandı",
                                f"{outdir}\n\n" + "\n".join(os.path.basename(p) for p in paths)
                                + f"\n\n{hint}\n\nHDL projesine hdl/axilxbar.v ile wb2axip'ten addrdecode.v ve "
                                  "skidbuffer.v dosyaları da eklenmelidir.")
