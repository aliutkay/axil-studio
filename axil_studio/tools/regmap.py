"""Register Map Editor: the registers of one IP, their bit fields, data types and value meanings.

The register map has no address: an IP is placed (any number of times) in the Address Map
Editor. Maps are kept as IP definitions in the project (with their full register spec so the
Slave Interface Wrapper can generate HDL from them), in .regspec.json files or in IP libraries.
Every place in the studio that edits a register map opens this tool.
"""

import copy
import os

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

import axilgen as G
import numfmt as N
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import KIND_SHORT, FieldDialog, RegDialog, ValueDialog
from axil_studio.tools.base import ToolPage
from model import KIND_READS, KIND_WRITES, EnumValue, single_line

SPEC_FILTER = "Register spesifikasyonu (*.regspec.json);;JSON (*.json);;Tüm dosyalar (*)"
ROLE = Qt.ItemDataRole.UserRole
# field tree columns
C_NAME, C_BITS, C_KIND, C_TYPE, C_RESET, C_VALUES, C_PORT, C_DESC = range(8)


# ============================================================================ read-only register map tree
def regmap_tree():
    """Register / bit field overview (IP Library, Slave Interface Wrapper)."""
    tree = QTreeWidget()
    tree.setHeaderLabels(["Register / bit alanı", "Offset / bitler", "Tip", "Veri tipi", "Açıklama"])
    tree.setUniformRowHeights(True)
    tree.header().setStretchLastSection(True)
    return tree


def fill_regmap_tree(tree, ip):
    tree.clear()
    if ip is None:
        return
    mono = theme.mono_font()
    muted = theme.manager().color("muted")
    for reg in ip.registers:
        it = QTreeWidgetItem([reg.name, f"0x{reg.offset:02X}", reg.access, "", single_line(reg.desc)])
        it.setFont(1, mono)
        bf = it.font(0)
        bf.setBold(True)
        it.setFont(0, bf)
        for f in sorted(reg.fields, key=lambda f: -f.lsb):
            ch = QTreeWidgetItem([f.name, f.bits, f.kind or reg.access, N.label(f.fmt, f.width),
                                  single_line(f.desc) + (f"  ({len(f.values)} değer açıklaması)" if f.values else "")])
            ch.setFont(1, mono)
            ch.setForeground(3, muted)
            it.addChild(ch)
        tree.addTopLevelItem(it)
    for c in range(4):
        tree.resizeColumnToContents(c)


# ============================================================================ 32-bit field map
class BitMap(QWidget):
    """The register's 32 bits, MSB on the left: every bit numbered, fields coloured by kind,
    unused bits hatched. Click selects a field, double-click edits it; double-click on a free
    bit adds a field there."""

    fieldClicked = Signal(str)
    fieldActivated = Signal(str)
    freeBitActivated = Signal(int)

    TOP = 18      # bit-number row
    BOTTOM = 16   # legend row

    def __init__(self):
        super().__init__()
        self.reg = None
        self.selected = None
        self.setMinimumHeight(76)
        self.setMouseTracking(True)

    def set_register(self, reg, selected=None):
        self.reg, self.selected = reg, selected
        self.update()

    def _geom(self):
        r = QRectF(self.rect()).adjusted(1, self.TOP, -1, -self.BOTTOM)
        return r, r.width() / 32

    def _bit_at(self, x):
        r, cell = self._geom()
        i = int((x - r.left()) // cell)
        return 31 - i if 0 <= i < 32 else None

    def _field_at(self, bit):
        if self.reg is None or bit is None:
            return None
        return next((f for f in self.reg.fields if f.lsb <= bit <= f.msb), None)

    def paintEvent(self, _):
        t = theme.manager().tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, cell = self._geom()
        num_font = theme.mono_font()
        num_font.setPixelSize(max(8, min(11, int(cell * 0.55))))
        p.setFont(num_font)
        used = set()
        for f in (self.reg.fields if self.reg else []):
            used.update(range(max(0, f.lsb), min(31, f.msb) + 1))
        hatch = QColor(t["muted"])
        hatch.setAlpha(110)
        for i in range(32):
            b = 31 - i
            x = r.left() + i * cell
            # number above every bit; byte boundaries stronger
            p.setPen(QColor(t["text"] if b % 8 == 7 or b == 0 else t["muted"]))
            p.drawText(QRectF(x, 0, cell, self.TOP - 2), Qt.AlignmentFlag.AlignCenter, str(b))
            if b in used:
                continue
            box = QRectF(x + 1.5, r.top(), cell - 3, r.height())
            p.setPen(QPen(QColor(t["border"]), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(t["surface"]))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(hatch, Qt.BrushStyle.BDiagPattern))
            p.drawRoundedRect(box, 3, 3)
        # byte separators
        p.setPen(QPen(QColor(t["border"]), 1))
        for k in (1, 2, 3):
            x = r.left() + k * 8 * cell
            p.drawLine(int(x), int(r.top() - 2), int(x), int(r.bottom() + 2))
        if self.reg is None:
            return
        name_font = self.font()
        name_font.setPixelSize(11)
        name_font.setBold(True)
        for f in self.reg.fields:
            if not (0 <= f.lsb <= 31 and f.msb <= 31):
                continue
            x0 = r.left() + (31 - f.msb) * cell
            box = QRectF(x0 + 1.5, r.top(), f.width * cell - 3, r.height())
            fill = theme.kind_color(f.kind)
            p.setBrush(QColor(fill))
            sel = f.name == self.selected
            p.setPen(QPen(QColor(t["text"]), 2.5) if sel else Qt.PenStyle.NoPen)
            p.drawRoundedRect(box, 4, 4)
            if box.width() > 16:
                p.setPen(QColor(theme.ink_on(fill)))
                p.setFont(name_font)
                text = p.fontMetrics().elidedText(f.name, Qt.TextElideMode.ElideRight, int(box.width() - 6))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        small = theme.mono_font()
        small.setPixelSize(10)
        p.setFont(small)
        p.setPen(QColor(t["muted"]))
        free = 32 - len(used)
        p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), self.BOTTOM - 2), Qt.AlignmentFlag.AlignRight,
                   f"{free} bit kullanılmıyor")

    def mouseMoveEvent(self, ev):
        bit = self._bit_at(ev.position().x())
        f = self._field_at(bit)
        if bit is None:
            QToolTip.hideText()
            return
        tip = (f"{f.name} {f.bits} · {f.kind} · {N.label(f.fmt, f.width)}\n{single_line(f.desc)}" if f
               else f"Bit {bit} kullanılmıyor\nÇift tık: bu bitte yeni bit alanı")
        QToolTip.showText(ev.globalPosition().toPoint(), tip.strip(), self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        f = self._field_at(self._bit_at(ev.position().x()))
        if f:
            self.fieldClicked.emit(f.name)

    def mouseDoubleClickEvent(self, ev):
        bit = self._bit_at(ev.position().x())
        f = self._field_at(bit)
        if f:
            self.fieldActivated.emit(f.name)
        elif bit is not None and self.reg is not None:
            self.freeBitActivated.emit(bit)


class ValueTable(QTableWidget):
    """Value descriptions of one bit field: Hex | Binary | <data type> | Okunursa | Yazılırsa,
    plus an "add" row. Double-click edits, Delete removes, the last row adds."""

    editRequested = Signal(int)
    deleteRequested = Signal(int)
    addRequested = Signal()

    def __init__(self, f):
        super().__init__()
        self.f = f
        self.reads = KIND_READS.get(f.kind, True)
        self.writes = KIND_WRITES.get(f.kind, True)
        extra = N.typed_extra(f.fmt, f.width)
        cols = ["Hex", "Binary"] + ([N.label(f.fmt, f.width).replace(" (oto)", "")] if extra else [])
        self.n_val_cols = len(cols)
        if self.reads:
            cols.append("Okunursa anlamı")
        if self.writes:
            cols.append("Yazılırsa etkisi")
        self.setColumnCount(len(cols))
        self.setHorizontalHeaderLabels(cols)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrap(False)
        hdr = self.horizontalHeader()
        for c in range(self.n_val_cols):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(self.n_val_cols, len(cols)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        self.values = sorted(f.values, key=lambda e: e.value)
        self.setRowCount(len(self.values) + 1)
        for row, e in enumerate(self.values):
            texts = N.triple(e.value, f.width, f.fmt)
            if self.reads:
                texts.append(e.read.replace("\n", " ") or "—")
            if self.writes:
                texts.append(e.write.replace("\n", " ") or "—")
            for col, text in enumerate(texts):
                it = QTableWidgetItem(text)
                if col < self.n_val_cols:
                    it.setFont(mono)
                if text == "—":
                    it.setForeground(muted)
                it.setToolTip("Çift tık: düzenle · Delete: sil")
                self.setItem(row, col, it)
        add = QTableWidgetItem("+ değer açıklaması ekle")
        add.setForeground(theme.manager().color("accent"))
        add.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.setItem(len(self.values), 0, add)
        self.setSpan(len(self.values), 0, 1, len(cols))
        self.cellClicked.connect(self._clicked)
        self.cellDoubleClicked.connect(self._double)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.resizeRowsToContents()

    def fitted_height(self):
        h = self.horizontalHeader().sizeHint().height()
        h += sum(self.rowHeight(r) for r in range(self.rowCount()))
        return h + 2 * self.frameWidth() + 2

    def _value_at(self, row):
        return self.values[row].value if 0 <= row < len(self.values) else None

    def _clicked(self, row, _col):
        if row == len(self.values):
            self.addRequested.emit()

    def _double(self, row, _col):
        v = self._value_at(row)
        if v is not None:
            self.editRequested.emit(v)

    def keyPressEvent(self, ev):
        v = self._value_at(self.currentRow())
        if v is not None and ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit(v)
        elif v is not None and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.editRequested.emit(v)
        else:
            super().keyPressEvent(ev)

    def _menu(self, pos):
        row = self.rowAt(pos.y())
        v = self._value_at(row)
        m = QMenu(self)
        if v is not None:
            m.addAction("Düzenle", lambda: self.editRequested.emit(v))
            m.addAction("Sil", lambda: self.deleteRequested.emit(v))
            m.addSeparator()
        m.addAction("Değer açıklaması ekle", self.addRequested.emit)
        m.exec(self.viewport().mapToGlobal(pos))

    def wheelEvent(self, ev):
        ev.ignore()   # let the field tree scroll


def small_button(text, slot, tip=None, variant="ghost", icon=None):
    return ui.button(text, icon, variant, slot, tip)


def value_fmt(f):
    """Format used for listing a field's value meanings (enum would just repeat the meaning)."""
    return "hex" if N.split(f.fmt, f.width)[0] == "enum" else f.fmt


class RegMapEditorPage(ToolPage):
    tool_id = "regmap"
    title = "Register Map Editor"
    short_title = "Register Map Editor"
    subtitle = "Bir IP'nin registerlarını, bit alanlarını, veri tiplerini ve değer anlamlarını tasarla."
    description = ("IP'nin register haritasını tasarlar: registerlar, bit alanları, veri tipleri ve değer "
                   "açıklamaları. Adres içermez; IP, Address Map Editor'da yerleştirilir.")
    icon_name = "regmap"
    version = "0.1.0"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.spec = G.ModuleSpec()
        self.origin = None          # ("project", ip name) | ("file", path) | ("library", label) | None
        self.outdir = None
        self.dirty = False
        self._loading = False

        # ---- header: where the map lives + main action
        self.origin_pill = self.header.add(ui.Pill(""))
        self.dirty_pill = self.header.add(ui.Pill("kaydedilmedi", "warn"))
        self.dirty_pill.hide()
        self.header.add(ui.button("Projeye kaydet", "save", "primary", self.save_to_project,
                                  "Register haritasını projedeki IP tanımı olarak kaydet (Ctrl+Shift+S)"))

        # ---- action bar: every command visible
        bar = ui.ActionBar()
        bar.add("Yeni IP", "file", self.new_spec, "Boş register haritası")
        bar.add("IP aç…", "library", self.choose_ip, "IP Library'den projedeki ya da bir kütüphanedeki IP'yi aç")
        bar.add("Örnek", "play", self.load_example, "Örnek register haritasını yükle")
        bar.sep()
        bar.add("Dosyadan aç…", "folder", self.open_spec, ".regspec.json dosyası")
        bar.add("Dosyaya kaydet…", None, self.save_file, ".regspec.json olarak kaydet")
        bar.sep()
        bar.add("Kütüphaneye kaydet…", "library", self.store_in_library,
                "Register haritasını bir IP kütüphanesine kaydet (başka projelerde kullanmak için)")
        bar.add("Adres haritasına ekle", "addrmap", self.add_to_address_map,
                "IP'yi projeye kaydet ve Address Map Editor'da adres haritasına yerleştir")
        bar.add("Wrapper'da aç", "wrapper", self.open_in_wrapper, "HDL'i Slave Interface Wrapper'da üret")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: IP + registers
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("IP", "section"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.name_ed = QLineEdit()
        self.name_ed.setToolTip("IP (modül) adı: projede ve üretilen HDL'de bu adla geçer")
        self.version_ed = QLineEdit()
        self.version_ed.setProperty("mono", "true")
        self.version_ed.setPlaceholderText("1.0.0")
        self.version_ed.setToolTip("IP sürümü: üretilen dosyalara ve IP kütüphanesine yazılır (ör. 1.0.0, 2.1.0-beta)")
        form.addRow("Ad", self.name_ed)
        form.addRow("Sürüm", self.version_ed)
        left.lay.addLayout(form)
        left.lay.addWidget(ui.Divider())

        rhead = QHBoxLayout()
        rhead.addWidget(ui.label("REGISTERLAR", "section"))
        rhead.addStretch(1)
        rhead.addWidget(small_button("Register", self.add_reg, "Yeni register (Ctrl+R)", None, "plus"))
        left.lay.addLayout(rhead)
        self.reg_tree = QTreeWidget()
        self.reg_tree.setHeaderLabels(["Ad", "Offset", "Erişim", "Strobe"])
        self.reg_tree.setRootIsDecorated(False)
        self.reg_tree.setUniformRowHeights(True)
        self.reg_tree.setAlternatingRowColors(True)
        hdr = self.reg_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.reg_tree.currentItemChanged.connect(lambda *_: self._refresh_fields())
        self.reg_tree.itemDoubleClicked.connect(lambda *_: self.edit_reg())
        self.reg_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.reg_tree.customContextMenuRequested.connect(self._reg_menu)
        left.lay.addWidget(self.reg_tree, 1)
        rbar = QHBoxLayout()
        rbar.setSpacing(4)
        rbar.addWidget(small_button("Düzenle", self.edit_reg, "Seçili registerı düzenle (Enter)"))
        rbar.addWidget(small_button("Sil", self.del_reg, "Seçili registerı sil (Delete)"))
        rbar.addStretch(1)
        rbar.addWidget(small_button("↑", lambda: self.move_reg(-1), "Yukarı taşı (offset'leri değiştirir)"))
        rbar.addWidget(small_button("↓", lambda: self.move_reg(1), "Aşağı taşı (offset'leri değiştirir)"))
        rbar.addWidget(small_button("Sırala", self.repack, "Offset'leri 0x00'dan itibaren 4'er artır"))
        left.lay.addLayout(rbar)
        left.setMinimumWidth(360)
        split.addWidget(left)

        # ---- right: tabs
        self.tabs = ui.DetachableTabs(self.title)
        ftab = QWidget()
        fl = QVBoxLayout(ftab)
        fl.setContentsMargins(16, 14, 16, 14)
        fl.setSpacing(10)
        th = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.reg_title = ui.label("", "cardTitle")
        self.reg_sub = ui.label("", "muted", wrap=True)
        col.addWidget(self.reg_title)
        col.addWidget(self.reg_sub)
        th.addLayout(col, 1)
        self.add_field_btn = small_button("Bit alanı", lambda: self.add_field(), "Yeni bit alanı (Ctrl+F)", None, "plus")
        th.addWidget(self.add_field_btn, 0, Qt.AlignmentFlag.AlignTop)
        fl.addLayout(th)
        self.bitmap = BitMap()
        self.bitmap.fieldClicked.connect(self._select_field)
        self.bitmap.fieldActivated.connect(self._activate_field)
        self.bitmap.freeBitActivated.connect(self.add_field)
        fl.addWidget(self.bitmap)

        self.fields = QTreeWidget()
        self.fields.setHeaderLabels(["Bit alanı", "Bitler", "Tip", "Veri tipi", "Reset", "Değer açıklamaları", "Port",
                                     "Açıklama"])
        self.fields.setAlternatingRowColors(False)
        self.fields.setUniformRowHeights(False)
        self.fields.setIndentation(18)
        self.fields.setExpandsOnDoubleClick(False)
        fh = self.fields.header()
        fh.setStretchLastSection(True)
        self.fields.itemClicked.connect(self._item_clicked)
        self.fields.itemDoubleClicked.connect(self._item_double_clicked)
        self.fields.currentItemChanged.connect(lambda *_: self._field_selection_changed())
        self.fields.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fields.customContextMenuRequested.connect(self._field_menu)
        fl.addWidget(self.fields, 1)
        fbar = QHBoxLayout()
        fbar.setSpacing(4)
        fbar.addWidget(small_button("Düzenle", self.edit_field, "Seçili bit alanını düzenle (Enter)"))
        fbar.addWidget(small_button("Sil", self.del_field, "Seçili bit alanını sil (Delete)"))
        fbar.addStretch(1)
        self.legend = []
        for k in G.KINDS:
            dot = ui.label("", "muted")
            dot.setToolTip(f"{KIND_SHORT[k]}: {G.KIND_HELP[k]}")
            dot.setProperty("kind", k)
            fbar.addWidget(dot)
            fbar.addSpacing(6)
            self.legend.append(dot)
        fl.addLayout(fbar)
        self.tabs.addTab(ftab, "Bit alanları")

        dtab = QWidget()
        dl = QVBoxLayout(dtab)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.addWidget(ui.label("İlk satır, üretilen HDL ve C dosyalarında başlık açıklaması olarak kullanılır.",
                              "muted", wrap=True))
        self.desc_ed = QPlainTextEdit()
        self.desc_ed.setPlaceholderText("IP'nin işlevi, kullanım sırası, dikkat edilmesi gerekenler…")
        dl.addWidget(self.desc_ed, 1)
        self.tabs.addTab(dtab, "IP açıklaması")

        right = ui.Card(margins=0)
        right.lay.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([370, 870])
        self.body.addWidget(split, 1)

        # ---- validation strip
        self.issues = ui.IssueList(compact=True)
        self.body.addWidget(self.issues)

        self.name_ed.textEdited.connect(self._form_changed)
        self.version_ed.textEdited.connect(self._form_changed)
        self.desc_ed.textChanged.connect(self._desc_changed)
        QShortcut(QKeySequence("Ctrl+R"), self, self.add_reg)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.add_field())
        for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            QShortcut(QKeySequence(key), self.reg_tree, self.del_reg, context=Qt.ShortcutContext.WidgetShortcut)
            QShortcut(QKeySequence(key), self.fields, self._delete_current,
                      context=Qt.ShortcutContext.WidgetShortcut)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QShortcut(QKeySequence(key), self.reg_tree, self.edit_reg, context=Qt.ShortcutContext.WidgetShortcut)
            QShortcut(QKeySequence(key), self.fields, lambda: self._item_double_clicked(self.fields.currentItem()),
                      context=Qt.ShortcutContext.WidgetShortcut)
        self._paint_legend()
        self._start()

    # ================================================================ window menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Register Map")
        win = self.window()

        def act(text, slot, shortcut=None):
            a = QAction(text, win)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            m.addAction(a)
            return a
        act("Yeni IP", self.new_spec)
        act("IP aç (IP Library)…", self.choose_ip, "Ctrl+Shift+O")
        act("Örnek register haritası", self.load_example)
        m.addSeparator()
        act("Projeye kaydet", self.save_to_project, "Ctrl+Shift+S")
        act("Dosyadan aç…", self.open_spec)
        act("Dosyaya kaydet…", self.save_file)
        act("Kütüphaneye kaydet…", self.store_in_library)
        m.addSeparator()
        act("Yeni register", self.add_reg)
        act("Yeni bit alanı", lambda: self.add_field())
        m.addSeparator()
        act("Adres haritasına ekle", self.add_to_address_map)
        act("Slave Interface Wrapper'da aç", self.open_in_wrapper)

    # ================================================================ state
    def _start(self):
        ips = self.ctx.project.ips
        if ips:
            self._open_ip(ips[0].name)
        else:
            self._load_spec()

    def _mark(self):
        self.dirty = True
        self.dirty_pill.show()
        self._validate()

    def _set_clean(self):
        self.dirty = False
        self.dirty_pill.hide()

    def _show_origin(self):
        o = self.origin
        if o is None:
            self.origin_pill.set("projede değil", "")
            self.origin_pill.setToolTip("Bu register haritası henüz projeye kaydedilmedi")
        elif o[0] == "project":
            self.origin_pill.set(f"projede: {o[1]}", "ok")
            n = len(self.ctx.project.instances_of(o[1]))
            self.origin_pill.setToolTip(f"Projedeki IP tanımı · adres haritasında {n} kez")
        elif o[0] == "file":
            self.origin_pill.set(f"dosya: {os.path.basename(o[1])}", "")
            self.origin_pill.setToolTip(o[1])
        else:
            self.origin_pill.set(o[1], "")
            self.origin_pill.setToolTip(o[1])

    def _load_spec(self):
        self._loading = True
        self.name_ed.setText(self.spec.module)
        self.version_ed.setText(self.spec.version)
        self.desc_ed.setPlainText(self.spec.desc)
        self._loading = False
        self._show_origin()
        self._refresh_regs()
        self._validate()

    def _form_changed(self, *_):
        if self._loading:
            return
        self.spec.module = self.name_ed.text().strip()
        self.spec.version = self.version_ed.text().strip()
        self._refresh_fields(self._selected_field_name())
        self._mark()

    def _desc_changed(self):
        if self._loading:
            return
        text = self.desc_ed.toPlainText().strip()
        if text != self.spec.desc:
            self.spec.desc = text
            self._mark()

    def _validate(self):
        errs = self.spec.validate()
        if not errs:
            n = sum(len(r.fields) for r in self.spec.registers)
            self.issues.ok_text = (f"Hata yok · {len(self.spec.registers)} register, {n} bit alanı · adres bitleri "
                                   f"[{self.spec.reg_aw + 1}:2] · IP'nin kullandığı alan 0x{self.spec.span:X} byte"
                                   + (" · kesme çıkışı (o_irq) var" if self.spec.has_irq() else ""))
        self.issues.set_issues(errs)
        return errs

    def _name_taken(self, names, name, old=None):
        if name != old and name in names:
            QMessageBox.warning(self, self.title, f"“{name}” adı bu registerda zaten kullanılıyor.")
            return True
        return False

    # ================================================================ registers
    def _sel_reg(self):
        it = self.reg_tree.currentItem()
        name = it.data(0, ROLE) if it else None
        return next((r for r in self.spec.registers if r.name == name), None)

    def _refresh_regs(self, select=None):
        cur = self._sel_reg()
        keep = select or (cur.name if cur else None)
        mono = theme.mono_font()
        self.reg_tree.blockSignals(True)
        self.reg_tree.clear()
        target = None
        for r in self.spec.regs:
            strobe = "/".join(s for s, on in (("wr", r.wr_strobe), ("rd", r.rd_strobe)) if on)
            it = QTreeWidgetItem([r.name, f"0x{r.offset:02X}", r.access, strobe])
            it.setData(0, ROLE, r.name)
            it.setFont(1, mono)
            it.setToolTip(0, single_line(r.desc))
            self.reg_tree.addTopLevelItem(it)
            if r.name == keep:
                target = it
        if target is None and self.reg_tree.topLevelItemCount():
            target = self.reg_tree.topLevelItem(0)
        self.reg_tree.setCurrentItem(target)
        self.reg_tree.blockSignals(False)
        self._refresh_fields()

    def _reg_menu(self, pos):
        m = QMenu(self)
        m.addAction("Yeni register", self.add_reg)
        if self._sel_reg():
            m.addAction("Düzenle", self.edit_reg)
            m.addAction("Çoğalt", self.dup_reg)
            m.addAction("Yeni bit alanı", lambda: self.add_field())
            m.addSeparator()
            m.addAction("Sil", self.del_reg)
        m.exec(self.reg_tree.viewport().mapToGlobal(pos))

    def add_reg(self):
        off = max((r.offset for r in self.spec.registers), default=-4) + 4
        new = RegDialog.ask(self, G.RegSpec(f"REG{off // 4}", off), "Yeni register")
        if not new or self._name_taken({r.name for r in self.spec.registers}, new.name):
            return
        new.fields = []
        self.spec.registers.append(new)
        self._refresh_regs(new.name)
        self._mark()
        self.add_field()

    def edit_reg(self):
        r = self._sel_reg()
        if not r:
            return
        new = RegDialog.ask(self, r, f"Register: {r.name}")
        if not new or self._name_taken({x.name for x in self.spec.registers}, new.name, r.name):
            return
        self.spec.registers[self.spec.registers.index(r)] = new
        self._refresh_regs(new.name)
        self._mark()

    def dup_reg(self):
        r = self._sel_reg()
        if not r:
            return
        new = copy.deepcopy(r)
        names = {x.name for x in self.spec.registers}
        i = 2
        while f"{r.name}{i}" in names:
            i += 1
        new.name = f"{r.name}{i}"
        new.offset = max(x.offset for x in self.spec.registers) + 4
        self.spec.registers.append(new)
        self._refresh_regs(new.name)
        self._mark()

    def del_reg(self):
        r = self._sel_reg()
        if r and ui.confirm(self, "Register sil", f"{r.name} registerı ve {len(r.fields)} bit alanı silinecek."):
            self.spec.registers.remove(r)
            self._refresh_regs()
            self._mark()

    def move_reg(self, delta):
        r = self._sel_reg()
        regs = self.spec.regs
        if not r:
            return
        i = regs.index(r)
        j = i + delta
        if 0 <= j < len(regs):
            regs[i].offset, regs[j].offset = regs[j].offset, regs[i].offset
            self._refresh_regs(r.name)
            self._mark()

    def repack(self):
        for i, r in enumerate(self.spec.regs):
            r.offset = 4 * i
        self._refresh_regs()
        self._mark()

    # ================================================================ field tree
    @staticmethod
    def _key(item):
        """("field", name) | ("values", field) | None"""
        return item.data(0, ROLE) if item else None

    def _field_item(self, item):
        while item is not None and item.parent() is not None:
            item = item.parent()
        return item

    def _selected_field_name(self):
        it = self._field_item(self.fields.currentItem())
        k = self._key(it)
        return k[1] if k else None

    def _sel_field(self):
        r = self._sel_reg()
        name = self._selected_field_name()
        return r, next((f for f in (r.fields if r else []) if f.name == name), None)

    def _find_field_item(self, name):
        for i in range(self.fields.topLevelItemCount()):
            it = self.fields.topLevelItem(i)
            if self._key(it)[1] == name:
                return it
        return None

    def _select_field(self, name):
        it = self._find_field_item(name)
        if it:
            self.fields.setCurrentItem(it)

    def _activate_field(self, name):
        self._select_field(name)
        self.edit_field()

    def _field_selection_changed(self):
        self.bitmap.set_register(self._sel_reg(), self._selected_field_name())

    def _expanded_names(self):
        return {self._key(self.fields.topLevelItem(i))[1] for i in range(self.fields.topLevelItemCount())
                if self.fields.topLevelItem(i).isExpanded()}

    def _refresh_fields(self, select=None, expanded=None):
        r = self._sel_reg()
        if expanded is None:
            expanded = self._expanded_names()
        self.add_field_btn.setEnabled(r is not None)
        self.fields.blockSignals(True)
        self.fields.clear()
        if not r:
            self.fields.blockSignals(False)
            self.reg_title.setText("Register yok")
            self.reg_sub.setText("Soldan bir register ekle (Ctrl+R).")
            self.bitmap.set_register(None)
            return
        self.reg_title.setText(f"{r.name}   ·   0x{r.offset:02X}   ·   {r.access}")
        self.reg_sub.setText(f"Offset 0x{r.offset:02X}  ·  reset 0x{r.reset_value:08X}" + (f"  —  {single_line(r.desc)}" if r.desc else ""))
        t = theme.manager().tokens
        mono = theme.mono_font()
        target = None
        for f in sorted(r.fields, key=lambda f: -f.lsb):
            kind = f.kind + "".join(f" · {s}" for s, on in (("irq", f.irq), ("sync", f.sync)) if on)
            reset = N.format_value(f.reset, f.width, f.fmt) if f.kind in ("RW", "CONST") else ""
            ports = ", ".join(G.port_names(r, f)) or "—"
            nval = f"{len(f.values)} tanım  ▾" if f.values else "ekle  ▸"
            it = QTreeWidgetItem([f.name, f.bits, kind, N.label(f.fmt, f.width), reset, nval, ports,
                                  single_line(f.desc)])
            it.setData(0, ROLE, ("field", f.name))
            for c in (C_BITS, C_RESET, C_PORT):
                it.setFont(c, mono)
            bold = it.font(C_NAME)
            bold.setBold(True)
            it.setFont(C_NAME, bold)
            it.setForeground(C_KIND, QColor(theme.kind_color(f.kind)))
            it.setFont(C_KIND, bold)
            it.setForeground(C_TYPE, QColor(t["muted"]))
            it.setForeground(C_VALUES, QColor(t["muted"] if not f.values else t["accent"]))
            it.setToolTip(C_KIND, G.KIND_HELP.get(f.kind, ""))
            it.setToolTip(C_DESC, f.desc)
            if f.kind in ("RW", "CONST"):
                it.setToolTip(C_RESET, "\n".join(f"{a}: {b}" for a, b in N.all_views(f.reset, f.width)))
            self.fields.addTopLevelItem(it)
            if f.name == select:
                target = it
        # size the columns from the field rows only, then hang the value rows under them
        for c in range(C_DESC):
            self.fields.resizeColumnToContents(c)
            self.fields.setColumnWidth(c, self.fields.columnWidth(c) + 12)
        for i in range(self.fields.topLevelItemCount()):
            it = self.fields.topLevelItem(i)
            f = next(x for x in r.fields if x.name == self._key(it)[1])
            self._add_value_rows(it, f)
            it.setExpanded(f.name in expanded)
        if target is not None:
            self.fields.setCurrentItem(target)
        self.fields.blockSignals(False)
        self.bitmap.set_register(r, select)

    def _add_value_rows(self, parent, f):
        """One child row under the field holding an aligned sub-table of its value descriptions."""
        ch = QTreeWidgetItem([""])
        ch.setData(0, ROLE, ("values", f.name))
        parent.addChild(ch)
        ch.setFirstColumnSpanned(True)
        table = ValueTable(f)
        table.editRequested.connect(lambda v, n=f.name: self.edit_value(n, v))
        table.deleteRequested.connect(lambda v, n=f.name: self.del_value(n, v))
        table.addRequested.connect(lambda n=f.name: self.add_value(n))
        ch.setSizeHint(0, QSize(10, table.fitted_height() + 8))
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 2, 12, 6)
        hl.addWidget(table)
        self.fields.setItemWidget(ch, 0, holder)

    def _item_clicked(self, item, col):
        if self._key(item) and self._key(item)[0] == "field":
            item.setExpanded(not item.isExpanded())

    def _item_double_clicked(self, item, col=0):
        k = self._key(item)
        if not k:
            return
        if k[0] == "field":
            item.setExpanded(not item.isExpanded())   # undo the toggle of the first click
            self.edit_field()

    def _delete_current(self):
        k = self._key(self.fields.currentItem())
        if not k:
            return
        if k[0] == "field":
            self.del_field()

    def _field_menu(self, pos):
        it = self.fields.itemAt(pos)
        k = self._key(it)
        m = QMenu(self)
        if k:
            m.addAction("Değer açıklaması ekle", lambda: self.add_value(k[1]))
            m.addAction("Bit alanını düzenle", self.edit_field)
            m.addAction("Bit alanını sil", self.del_field)
            m.addSeparator()
        m.addAction("Yeni bit alanı", lambda: self.add_field())
        m.exec(self.fields.viewport().mapToGlobal(pos))

    # ================================================================ field edits
    def _field(self, name):
        r = self._sel_reg()
        return r, next((f for f in (r.fields if r else []) if f.name == name), None)

    def add_field(self, lsb=None):
        r = self._sel_reg()
        if not r:
            return
        used = 0
        for f in r.fields:
            used |= f.mask
        if lsb is None:
            lsb = next((b for b in range(32) if not used >> b & 1), 0)
        proto = G.FieldSpec(r.name, 0, 32) if not r.fields else G.FieldSpec(f"BIT{lsb}", lsb)
        new = FieldDialog.ask(self, proto, f"{r.name}: yeni bit alanı")
        if not new or self._name_taken({f.name for f in r.fields}, new.name):
            return
        r.fields.append(new)
        self._refresh_regs(r.name)
        self._refresh_fields(new.name)
        self._mark()

    def edit_field(self):
        r, f = self._sel_field()
        if not f:
            return
        new = FieldDialog.ask(self, f, f"{r.name}.{f.name}")
        if not new or self._name_taken({x.name for x in r.fields}, new.name, f.name):
            return
        expanded = self._expanded_names()
        if f.name in expanded:
            expanded.add(new.name)
        r.fields[r.fields.index(f)] = new
        self._refresh_regs(r.name)
        self._refresh_fields(new.name, expanded)
        self._mark()

    def del_field(self):
        r, f = self._sel_field()
        if f and ui.confirm(self, "Bit alanı sil", f"{r.name}.{f.name} bit alanı silinecek."):
            r.fields.remove(f)
            self._refresh_regs(r.name)
            self._mark()

    def _store_value(self, f, new, old=None):
        if any(e.value == new.value and e is not old for e in f.values):
            QMessageBox.warning(self, self.title, f"{N.format_value(new.value, f.width, value_fmt(f))} zaten tanımlı")
            return False
        if old is None:
            f.values.append(new)
        else:
            f.values[f.values.index(old)] = new
        f.values.sort(key=lambda e: e.value)
        return True

    def _after_value_change(self, f):
        expanded = self._expanded_names() | {f.name}
        self._refresh_fields(f.name, expanded)
        self._mark()

    def add_value(self, field_name):
        _, f = self._field(field_name)
        if not f:
            return
        used = {e.value for e in f.values}
        nxt = next((v for v in range(1 << min(f.width, 16)) if v not in used), 0)
        new = ValueDialog.ask(self, EnumValue(nxt), f.width, KIND_READS.get(f.kind, True),
                              KIND_WRITES.get(f.kind, True), f.fmt)
        if new and (new.read or new.write) and self._store_value(f, new):
            self._after_value_change(f)

    def edit_value(self, field_name, value):
        _, f = self._field(field_name)
        e = next((x for x in (f.values if f else []) if x.value == value), None)
        if not e:
            return
        new = ValueDialog.ask(self, e, f.width, KIND_READS.get(f.kind, True), KIND_WRITES.get(f.kind, True), f.fmt)
        if new and self._store_value(f, new, e):
            self._after_value_change(f)

    def del_value(self, field_name, value):
        _, f = self._field(field_name)
        e = next((x for x in (f.values if f else []) if x.value == value), None)
        if e:
            f.values.remove(e)
            self._after_value_change(f)


    # ================================================================ theme
    def _paint_legend(self):
        for dot in self.legend:
            k = dot.property("kind")
            dot.setText(f"<span style='color:{theme.kind_color(k)}'>■</span> {k}")

    def on_theme(self):
        self.issues.retint()
        self._paint_legend()
        self._refresh_fields(self._selected_field_name())

    # ================================================================ project sync
    def on_project_replaced(self):
        if self.dirty:
            if self.origin and self.origin[0] == "project":
                self.origin = None
            self._show_origin()
            return
        self.origin = None
        self.spec = G.ModuleSpec()
        self._set_clean()
        self._start()

    def on_project_modified(self):
        """Another tool changed the project: follow the IP being edited (rename, library update)."""
        if not self.origin or self.origin[0] != "project":
            return self._show_origin()
        ip = self.ctx.project.get_ip(self.origin[1])
        if ip is None:
            self.origin = None
            self._mark()
        elif not self.dirty:
            reg, fld = self._sel_reg(), self._selected_field_name()
            self.spec = G.spec_from_ipblock(ip)
            self._load_spec()
            if reg:
                self._refresh_regs(reg.name)
                if fld:
                    self._select_field(fld)
        self._show_origin()

    # ================================================================ opening
    def can_close(self):
        return self._confirm_discard()

    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = ui.ask_save(self, self.title, f"“{self.spec.module}” register haritasında kaydedilmemiş değişiklikler var.")
        if r == "save":
            if self.origin and self.origin[0] == "file":
                return self.save_file(False)
            return self.save_to_project()
        return r == "discard"

    def _raise(self):
        win = self.window()
        win.showNormal()
        win.raise_()
        win.activateWindow()

    def _open_ip(self, name):
        ip = self.ctx.project.get_ip(name)
        if ip is None:
            return False
        self.spec = G.spec_from_ipblock(ip)
        self.origin = ("project", name)
        self._set_clean()
        self._load_spec()
        return True

    def edit_project_ip(self, name, reg=None, field=None):
        """Open a project IP definition (called by the other tools); optionally select a register
        and a bit field."""
        if not (self.origin == ("project", name) and self.dirty):
            if not self._confirm_discard():
                return False
            if not self._open_ip(name):
                QMessageBox.warning(self, self.title, f"“{name}” projede bir IP tanımı değil.")
                return False
        if reg:
            self._refresh_regs(reg)
            if field:
                self._select_field(field)
        self._raise()
        self.status(f"{name} açıldı")
        return True

    def load_spec(self, spec, origin=""):
        """Open a register map handed over by another tool (e.g. from an IP library)."""
        if not self._confirm_discard():
            return False
        self.spec = spec
        self.origin = ("library", origin) if origin else None
        self._set_clean()
        self._load_spec()
        self._raise()
        self.status(f"{spec.module} açıldı" + (f" ({origin})" if origin else ""))
        return True

    def choose_ip(self):
        if not self._confirm_discard():
            return
        name = self.pick_ip("Register haritası açılacak IP", "Projedeki ya da açık kütüphanelerdeki bir IP'yi seç. "
                                                             "Kütüphane IP'si önce projeye alınır.")
        if name:
            self._open_ip(name)
            self.status(f"{name} açıldı")

    def new_spec(self):
        if self._confirm_discard():
            self.spec, self.origin = G.ModuleSpec(), None
            self._set_clean()
            self._load_spec()

    def load_example(self):
        if self._confirm_discard():
            self.spec, self.origin = G.example_spec(), None
            self._set_clean()
            self._load_spec()
            self.status("Örnek register haritası yüklendi")

    def open_spec(self):
        if not self._confirm_discard():
            return
        start = self.origin[1] if self.origin and self.origin[0] == "file" else (self.outdir or ROOT)
        path, _ = QFileDialog.getOpenFileName(self, "Register spesifikasyonu aç", os.path.dirname(start)
                                              if os.path.isfile(start) else start, SPEC_FILTER)
        if not path:
            return
        try:
            self.spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error("Açılamadı", exc)
        self.origin, self.outdir = ("file", path), os.path.dirname(path)
        self._set_clean()
        self._load_spec()
        self.status(f"Açıldı: {path}")

    # ================================================================ saving
    def save_file(self, as_new=True):
        path = self.origin[1] if self.origin and self.origin[0] == "file" else None
        if as_new or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Register spesifikasyonunu kaydet",
                                                  path or os.path.join(self.outdir or ROOT,
                                                                       f"{self.spec.module}.regspec.json"),
                                                  SPEC_FILTER)
            if not path:
                return False
        try:
            self.spec.save(path)
        except OSError as exc:
            self.error("Kaydedilemedi", exc)
            return False
        self.outdir = os.path.dirname(path)
        if not self.origin or self.origin[0] != "project":
            self.origin = ("file", path)
            self._set_clean()
        self._show_origin()
        self.status(f"Kaydedildi: {path}")
        return True

    def save_to_project(self):
        """Store the register map as a project IP definition (instances in the address map follow it)."""
        errs = self._validate()
        name = self.spec.module
        if not name:
            QMessageBox.warning(self, self.title, "IP adı boş olamaz.")
            return False
        if errs and not ui.confirm(self, self.title, f"Register haritasında {len(errs)} hata var (alttaki liste). "
                                                     "Yine de projeye kaydedilsin mi? HDL ancak hatalar "
                                                     "düzeltildikten sonra üretilebilir.", ok="Kaydet"):
            return False
        old = self.origin[1] if self.origin and self.origin[0] == "project" else None
        if old and not self.ctx.project.get_ip(old):
            old = None
        other = self.ctx.project.get_ip(name)
        if other is not None and name != old:
            n = len(self.ctx.project.instances_of(name))
            if not ui.confirm(self, self.title, f"Projede “{name}” adlı başka bir IP var"
                                                + (f" (adres haritasında {n} kez)" if n else "")
                                                + ". Register haritası bununla değiştirilsin mi?", ok="Değiştir"):
                return False
        prev = self.ctx.project.get_ip(old or name)
        ip = G.to_ipblock(copy.deepcopy(self.spec), 0, with_spec=True)
        ip.source = prev.source if prev else (self.origin[1].split(" ")[0] if self.origin and
                                              self.origin[0] == "library" else "")
        replaced = self.ctx.put_ip(ip, self.tool_id, old_name=old)
        self.origin = ("project", name)
        self._set_clean()
        self._show_origin()
        self.status(f"{name} projede {'güncellendi' if replaced else 'eklendi'}"
                    + (f" ({old} → {name})" if old and old != name else ""))
        return True

    def _ensure_in_project(self):
        if self.origin and self.origin[0] == "project" and not self.dirty:
            return True
        return self.save_to_project()

    def store_in_library(self):
        libs = self.ctx.libraries
        if not libs:
            QMessageBox.information(self, self.title, "Açık IP kütüphanesi yok. IP Library'den yeni bir kütüphane "
                                                      "oluştur ya da var olanı aç.")
            self.open_tool("library")
            return
        if self._validate() and not ui.confirm(self, self.title, "Register haritasında hata var. Yine de "
                                                                 "kütüphaneye kaydedilsin mi?", ok="Kaydet"):
            return
        if len(libs) == 1:
            lib = libs[0]
        else:
            name, ok = QInputDialog.getItem(self, "Kütüphane seç", "Kütüphane:", [x.name for x in libs], 0, False)
            if not ok:
                return
            lib = next(x for x in libs if x.name == name)
        v = self.spec.version
        latest = lib.get(self.spec.module)
        if lib.get(self.spec.module, v):
            if not ui.confirm(self, self.title, f"{lib.name} kütüphanesinde {self.spec.module} {v} zaten var. "
                                                f"Üzerine yazılsın mı? (Yeni sürüm için Sürüm alanını değiştir, "
                                                f"ör. {G.bump_version(latest.version)})", ok="Üzerine yaz"):
                return
        replaced = self.ctx.store_in_library(lib, G.to_ipblock(copy.deepcopy(self.spec), 0), self.spec.to_dict())
        self.status(f"{self.spec.module} {v} {lib.name} kütüphanesine {'güncellendi' if replaced else 'kaydedildi'}")

    # ================================================================ other tools
    def add_to_address_map(self):
        if not self._ensure_in_project():
            return
        inst = self.ctx.add_instance(self.spec.module, source=self.tool_id)
        self._show_origin()
        self.edit_address_map(inst.name)
        self.status(f"{inst.name} adres haritasına eklendi")

    def open_in_wrapper(self):
        w = self.open_tool("wrapper")
        if self.origin and self.origin[0] == "project" and not self.dirty:
            w.page.load_project_ip(self.origin[1])
        else:
            w.page.load_spec(copy.deepcopy(self.spec), "Register Map Editor'dan, projeye kaydedilmemiş")
