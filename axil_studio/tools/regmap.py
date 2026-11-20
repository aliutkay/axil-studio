"""Register Map Editor: the registers of one IP, their bit fields, data types and value meanings.

The register map has no address: an IP is placed (any number of times) in the Address Map
Editor. Maps are kept as IP definitions in the project (with their full register spec so the
Slave Interface Wrapper can generate HDL from them), in .regspec.json files or in IP libraries.
Every place in the studio that edits a register map opens this tool.
"""

import copy
import os
import re

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import numfmt as N
from axil_studio.core import portparse as PP
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import KIND_SHORT, FieldDialog, RegDialog, ValueDialog
from axil_studio.tools.base import ToolPage
from axil_studio.core.model import KIND_READS, KIND_WRITES, EnumValue, single_line

SPEC_FILTER = "Register spesifikasyonu (*.regspec.json);;JSON (*.json);;Tüm dosyalar (*)"
ROLE = Qt.ItemDataRole.UserRole
UNPLACED_MIME = "application/x-axil-unplaced-fields"     # drag data: indexes into spec.unplaced
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
    unplacedDropped = Signal(list, int)     # unplaced field indexes, lsb of the first one

    TOP = 18      # bit-number row
    BOTTOM = 16   # legend row

    def __init__(self):
        super().__init__()
        self.reg = None
        self.selected = None
        self.drop = None                    # (lsb, width, fits) while unplaced fields are dragged over
        self.widths_of = lambda indexes: []
        self.setMinimumHeight(76)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

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
        if self.drop is not None:            # where the dragged fields would land
            lsb, width, fits = self.drop
            x0 = r.left() + (31 - min(31, lsb + width - 1)) * cell
            box = QRectF(x0 + 1, r.top() - 1, min(width, 32) * cell - 2, r.height() + 2)
            c = QColor(t["success"] if fits else t["danger"])
            p.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
            c.setAlpha(60)
            p.setBrush(c)
            p.drawRoundedRect(box, 4, 4)
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

    # ---- dropping unplaced bit fields
    def _dragged(self, ev):
        md = ev.mimeData()
        if self.reg is None or not md.hasFormat(UNPLACED_MIME):
            return None
        return [int(x) for x in bytes(md.data(UNPLACED_MIME)).decode().split(",") if x]

    def _landing(self, ev, indexes):
        """(lsb, total width, fits): the dragged fields side by side, centred on the cursor bit."""
        total = sum(self.widths_of(indexes))
        bit = self._bit_at(ev.position().x())
        bit = 0 if bit is None else bit
        lsb = max(0, min(bit - (total - 1) // 2, 32 - total)) if total <= 32 else 0
        used = 0
        for f in self.reg.fields:
            used |= f.mask
        fits = 0 < total <= 32 and not used & (((1 << total) - 1) << lsb)
        return lsb, total, fits

    def dragEnterEvent(self, ev):
        if self._dragged(ev) is not None:
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        idx = self._dragged(ev)
        if idx is None:
            return
        self.drop = self._landing(ev, idx)
        self.update()
        ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self.drop = None
        self.update()

    def dropEvent(self, ev):
        idx = self._dragged(ev)
        self.drop = None
        self.update()
        if idx is None:
            return
        lsb, _total, _fits = self._landing(ev, idx)
        ev.acceptProposedAction()
        self.unplacedDropped.emit(idx, lsb)


class RegTree(QTreeWidget):
    """Register list that also takes unplaced bit fields: dropped on a register they go into it,
    dropped below the registers they make a new register."""

    unplacedDropped = Signal(list, object)  # unplaced field indexes, register name or None

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    @staticmethod
    def _indexes(ev):
        md = ev.mimeData()
        if not md.hasFormat(UNPLACED_MIME):
            return None
        return [int(x) for x in bytes(md.data(UNPLACED_MIME)).decode().split(",") if x]

    def dragEnterEvent(self, ev):
        if self._indexes(ev) is not None:
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if self._indexes(ev) is not None:
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        idx = self._indexes(ev)
        if idx is None:
            return
        it = self.itemAt(ev.position().toPoint())
        ev.acceptProposedAction()
        self.unplacedDropped.emit(idx, it.data(0, ROLE) if it else None)


class UnplacedTree(QTreeWidget):
    """Bit fields that are not in a register yet; drag them onto the bit map or a register."""

    deleteRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["Bit alanı", "Bit", "Tip", "Kaynak"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        h = self.header()
        h.setStretchLastSection(True)
        for c in (0, 1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.setToolTip("Bit haritasına ya da bir registera sürükle (birden çok seçilebilir); "
                        "register listesinin boş alanına bırakmak yeni register açar. Delete: sil")

    def mimeTypes(self):
        return [UNPLACED_MIME]

    def mimeData(self, items):
        md = QMimeData()
        idx = sorted(it.data(0, ROLE) for it in items)
        md.setData(UNPLACED_MIME, ",".join(str(i) for i in idx).encode())
        return md

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
        else:
            super().keyPressEvent(ev)


# ============================================================================ port import
DIR_TEXT = {"in": "giriş", "out": "çıkış", "inout": "çift yönlü"}
KIND_OF_DIR = {"in": "RW", "out": "RO", "inout": "RW"}     # seen from the IP core behind the registers


class PortImportDialog(QDialog):
    """Paste the port list of the IP core behind the register file (Verilog / SystemVerilog or
    VHDL); every chosen port becomes an unplaced bit field, signals wider than 32 bits are cut into
    32-bit pieces. Inputs of the core become RW fields (driven by the registers), its outputs RO
    fields (read through the registers)."""

    C_ON, C_PORT, C_DIR, C_TYPE, C_WIDTH, C_KIND, C_FIELDS = range(7)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Port içe aktar (Verilog / VHDL)")
        self.resize(1040, 760)
        self.ports, self.block, self.over = [], "", {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(ui.label("Register haritasının arkasındaki IP çekirdeğinin portlarını yapıştır: modülün / "
                               "entity'nin tamamı, yalnızca port listesi ya da port satırları. Seçilen her port "
                               "yerleştirilmemiş bir bit alanı olur. Çekirdeğin girişleri RW (registerdan sürülür), "
                               "çıkışları RO (registerdan okunur) olur; 32 bitten geniş sinyaller 32 bitlik "
                               "parçalara bölünür. Saat ve reset portları baştan seçili gelmez.", "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(ui.label("Dil", "muted"))
        self.lang = QComboBox()
        for text, key in (("Otomatik", None), ("Verilog / SystemVerilog", "verilog"), ("VHDL", "vhdl")):
            self.lang.addItem(text, key)
        row.addWidget(self.lang)
        row.addSpacing(10)
        row.addWidget(ui.label("Blok", "muted"))
        self.block_ed = QLineEdit()
        self.block_ed.setPlaceholderText("modül / entity adı")
        self.block_ed.setToolTip("Bit alanı açıklamasına kaynak olarak yazılır (blok.port[bitler])")
        row.addWidget(self.block_ed)
        row.addSpacing(10)
        row.addWidget(ui.label("Alan adı öneki", "muted"))
        self.prefix_ed = QLineEdit()
        self.prefix_ed.setPlaceholderText("isteğe bağlı")
        self.prefix_ed.setToolTip("Birden çok bloğun aynı adlı portları için, ör. RX → RX_DATA")
        self.prefix_ed.setMaximumWidth(140)
        row.addWidget(self.prefix_ed)
        self.strip_cb = QCheckBox("i_ / o_ öneklerini at")
        self.strip_cb.setChecked(True)
        self.strip_cb.setToolTip("i_threshold → THRESHOLD (wrapper portu o_<reg>_threshold olur)")
        row.addWidget(self.strip_cb)
        row.addStretch(1)
        lay.addLayout(row)

        split = QSplitter(Qt.Orientation.Vertical)
        self.text = QPlainTextEdit()
        self.text.setProperty("mono", "true")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setPlaceholderText("module my_core #(parameter W = 12) (\n"
                                     "    input  wire         clk,\n"
                                     "    input  wire [W-1:0] threshold,\n"
                                     "    output wire [63:0]  timestamp\n"
                                     ");\n\nya da\n\n"
                                     "entity my_core is\n"
                                     "  port ( clk       : in  std_logic;\n"
                                     "         threshold : in  std_logic_vector(11 downto 0);\n"
                                     "         timestamp : out unsigned(63 downto 0) );\n"
                                     "end entity;")
        split.addWidget(self.text)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Port", "Yön", "Tip", "Genişlik", "Alan tipi", "Bit alanları"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.SelectedClicked)
        h = self.table.horizontalHeader()
        for c in range(6):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.C_FIELDS, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._item_changed)
        split.addWidget(self.table)
        split.setSizes([300, 360])
        lay.addWidget(split, 1)
        brow = QHBoxLayout()
        self.summary = ui.label("", "muted")
        brow.addWidget(self.summary, 1)
        brow.addWidget(ui.button("Tümünü seç", None, "ghost", lambda: self._check_all(True)))
        brow.addWidget(ui.button("Hiçbirini seçme", None, "ghost", lambda: self._check_all(False)))
        lay.addLayout(brow)
        box = QDialogButtonBox()
        self.ok = box.addButton("Yerleştirilmemişlere ekle", QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton("Vazgeç", QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._parse)
        self.text.textChanged.connect(self._timer.start)
        self.lang.currentIndexChanged.connect(self._parse)
        for w in (self.prefix_ed, self.block_ed):
            w.textChanged.connect(lambda *_: self._fill())
        self.strip_cb.toggled.connect(lambda *_: self._fill())
        self._loading = False
        self._fill()

    # ---- model
    def _width(self, p):
        return self.over.get(p.name, {}).get("width", p.width)

    def _checked(self, p):
        default = self._width(p) is not None and not PP.is_clock_or_reset(p.name)
        return self.over.get(p.name, {}).get("on", default)

    def _kind(self, p):
        return self.over.get(p.name, {}).get("kind", KIND_OF_DIR[p.direction])

    def _pieces(self, p):
        """[(field name, width, description)] of one port."""
        width = self._width(p)
        if not width:
            return []
        base = p.name
        if self.strip_cb.isChecked() and re.match(r"^[io]_.", base, re.I):
            base = base[2:]
        prefix = self.prefix_ed.text().strip()
        name = PP.field_name(f"{prefix}_{base}" if prefix else base)
        block = self.block_ed.text().strip()
        src = f"{block}.{p.name}" if block else p.name
        pieces = PP.split_width(width, p.lsb)
        out = []
        for k, lo, w in pieces:
            bits = f"[{lo + w - 1}:{lo}]" if width > 1 else ""
            out.append((f"{name}_{k}" if len(pieces) > 1 else name, w, f"{src}{bits}"))
        return out

    def fields(self):
        out = []
        for p in self.ports:
            if self._checked(p):
                for name, w, desc in self._pieces(p):
                    out.append(G.FieldSpec(name, 0, w, self._kind(p), 0, desc))
        return out

    # ---- view
    def _parse(self):
        lang = self.lang.currentData()
        self.block, self.ports = PP.parse_ports(self.text.toPlainText(), lang)
        if self.block and not self.block_ed.isModified():
            self.block_ed.blockSignals(True)
            self.block_ed.setText(self.block)
            self.block_ed.blockSignals(False)
        self._fill()

    def _fill(self):
        t = theme.manager().tokens
        mono = theme.mono_font()
        self._loading = True
        self.table.setRowCount(len(self.ports))
        for row, p in enumerate(self.ports):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            on.setCheckState(Qt.CheckState.Checked if self._checked(p) else Qt.CheckState.Unchecked)
            self.table.setItem(row, self.C_ON, on)
            for col, text in ((self.C_PORT, p.name), (self.C_DIR, DIR_TEXT[p.direction]), (self.C_TYPE, p.type_text)):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if col == self.C_TYPE:
                    it.setFont(mono)
                    it.setForeground(QColor(t["muted"]))
                if p.error:
                    it.setToolTip(p.error)
                self.table.setItem(row, col, it)
            width = self._width(p)
            wi = QTableWidgetItem("?" if width is None else str(width))
            wi.setFont(mono)
            wi.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable)
            wi.setToolTip((p.error + "\n" if p.error else "") + "Çift tık: genişliği değiştir")
            if width is None:
                wi.setForeground(QColor(t["danger"]))
            self.table.setItem(row, self.C_WIDTH, wi)
            cb = QComboBox()
            for k in G.KINDS:
                cb.addItem(k, k)
            cb.setCurrentIndex(max(0, cb.findData(self._kind(p))))
            cb.setToolTip(f"Çekirdeğin {DIR_TEXT[p.direction]}i → varsayılan {KIND_OF_DIR[p.direction]}")
            cb.currentIndexChanged.connect(lambda _i, n=p.name, cb=cb: self._set(n, "kind", cb.currentData()))
            self.table.setCellWidget(row, self.C_KIND, cb)
            pieces = self._pieces(p)
            fi = QTableWidgetItem(", ".join(f"{n} ({w} bit)" for n, w, _ in pieces) if pieces else "—")
            fi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            fi.setToolTip("\n".join(f"{n}: {d}" for n, _, d in pieces))
            if len(pieces) > 1:
                fi.setForeground(QColor(t["accent"]))
            self.table.setItem(row, self.C_FIELDS, fi)
        self._loading = False
        fields = self.fields()
        bad = sum(1 for p in self.ports if self._width(p) is None)
        self.summary.setText(f"{len(self.ports)} port bulundu · {sum(1 for p in self.ports if self._checked(p))} "
                             f"seçili → {len(fields)} bit alanı"
                             + (f" · {bad} portun genişliği çözülemedi (Genişlik hücresine yaz)" if bad else "")
                             if self.ports else "Port bulunamadı: Verilog ya da VHDL port tanımlarını yapıştır.")
        self.ok.setEnabled(bool(fields))

    def _set(self, name, key, value):
        self.over.setdefault(name, {})[key] = value
        if key != "kind":
            self._fill()

    def _item_changed(self, item):
        if self._loading:
            return
        p = self.ports[item.row()]
        if item.column() == self.C_ON:
            self._set(p.name, "on", item.checkState() == Qt.CheckState.Checked)
        elif item.column() == self.C_WIDTH:
            try:
                w = int(item.text().strip(), 0)
                if w < 1:
                    raise ValueError
            except ValueError:
                return self._fill()
            self.over.setdefault(p.name, {})["on"] = True
            self._set(p.name, "width", w)

    def _check_all(self, on):
        for p in self.ports:
            self.over.setdefault(p.name, {})["on"] = on and self._width(p) is not None
        self._fill()

    @classmethod
    def ask(cls, parent):
        d = cls(parent)
        return d.fields() if d.exec() == QDialog.DialogCode.Accepted else None


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
    version = "0.2.0"

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
        bar.add("Port içe aktar…", "plus", self.import_ports,
                "IP çekirdeğinin Verilog / VHDL port listesinden yerleştirilmemiş bit alanları oluştur")
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

        lsplit = QSplitter(Qt.Orientation.Vertical)
        lsplit.setChildrenCollapsible(False)
        regs_box = QWidget()
        rl = QVBoxLayout(regs_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rhead = QHBoxLayout()
        rhead.addWidget(ui.label("REGISTERLAR", "section"))
        rhead.addStretch(1)
        rhead.addWidget(small_button("Register", self.add_reg, "Yeni register (Ctrl+R)", None, "plus"))
        rl.addLayout(rhead)
        self.reg_tree = RegTree()
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
        self.reg_tree.unplacedDropped.connect(self._drop_on_register)
        rl.addWidget(self.reg_tree, 1)
        rbar = QHBoxLayout()
        rbar.setSpacing(4)
        rbar.addWidget(small_button("Düzenle", self.edit_reg, "Seçili registerı düzenle (Enter)"))
        rbar.addWidget(small_button("Sil", self.del_reg, "Seçili registerı sil (Delete)"))
        rbar.addStretch(1)
        rbar.addWidget(small_button("↑", lambda: self.move_reg(-1), "Yukarı taşı (offset'leri değiştirir)"))
        rbar.addWidget(small_button("↓", lambda: self.move_reg(1), "Aşağı taşı (offset'leri değiştirir)"))
        rbar.addWidget(small_button("Sırala", self.repack, "Offset'leri 0x00'dan itibaren 4'er artır"))
        rl.addLayout(rbar)
        lsplit.addWidget(regs_box)
        unp_box = QWidget()
        ul = QVBoxLayout(unp_box)
        ul.setContentsMargins(0, 8, 0, 0)
        ul.setSpacing(8)
        uhead = QHBoxLayout()
        self.unp_title = ui.label("YERLEŞTİRİLMEMİŞ BİT ALANLARI", "section")
        uhead.addWidget(self.unp_title)
        uhead.addStretch(1)
        uhead.addWidget(small_button("Port içe aktar", self.import_ports,
                                     "IP çekirdeğinin Verilog / VHDL port listesinden (Ctrl+I)", None, "plus"))
        ul.addLayout(uhead)
        self.unplaced = UnplacedTree()
        self.unplaced.deleteRequested.connect(self.delete_unplaced)
        self.unplaced.itemDoubleClicked.connect(lambda *_: self.place_selected())
        self.unplaced.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.unplaced.customContextMenuRequested.connect(self._unplaced_menu)
        ul.addWidget(self.unplaced, 1)
        ubar = QHBoxLayout()
        ubar.setSpacing(4)
        ubar.addWidget(small_button("Yerleştir", self.place_selected,
                                    "Seçilenleri seçili registerın ilk boş bitlerine koy (çift tık da olur)"))
        ubar.addWidget(small_button("Yeni registera", lambda: self.place_selected(new_register=True),
                                    "Seçilenlerle yeni bir register aç"))
        ubar.addStretch(1)
        ubar.addWidget(small_button("Sil", self.delete_unplaced, "Seçilenleri sil (Delete)"))
        ubar.addWidget(small_button("Tümünü sil", self.clear_unplaced))
        ul.addLayout(ubar)
        lsplit.addWidget(unp_box)
        lsplit.setSizes([320, 320])
        left.lay.addWidget(lsplit, 1)
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
        self.bitmap.unplacedDropped.connect(self._drop_on_bitmap)
        self.bitmap.widths_of = lambda idx: [self.spec.unplaced[i].width for i in idx if i < len(self.spec.unplaced)]
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
        act("Port içe aktar (Verilog / VHDL)…", self.import_ports, "Ctrl+I")
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
        self._refresh_unplaced()
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
        n_unp = len(self.spec.unplaced)
        if n_unp:
            self.issues.ok_text += f" · {n_unp} yerleştirilmemiş bit alanı (HDL'e girmez)"
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
            m.addAction("Yerleştirilmemişlere geri al", self.unplace_field)
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


    # ================================================================ unplaced bit fields
    def _refresh_unplaced(self, select=()):
        t = theme.manager().tokens
        mono = theme.mono_font()
        self.unplaced.clear()
        for i, f in enumerate(self.spec.unplaced):
            it = QTreeWidgetItem([f.name, str(f.width), f.kind, f.desc])
            it.setData(0, ROLE, i)
            it.setFont(1, mono)
            it.setForeground(2, QColor(theme.kind_color(f.kind)))
            it.setForeground(3, QColor(t["muted"]))
            it.setToolTip(0, f"{f.name}: {f.width} bit {f.kind}\n{f.desc}\nSürükle: bit haritasına ya da bir registera")
            self.unplaced.addTopLevelItem(it)
            if i in select:
                it.setSelected(True)
        n = len(self.spec.unplaced)
        self.unp_title.setText(f"YERLEŞTİRİLMEMİŞ BİT ALANLARI ({n})" if n else "YERLEŞTİRİLMEMİŞ BİT ALANLARI")

    def _sel_unplaced(self):
        return sorted(it.data(0, ROLE) for it in self.unplaced.selectedItems())

    def _unplaced_menu(self, pos):
        m = QMenu(self)
        if self._sel_unplaced():
            r = self._sel_reg()
            if r:
                m.addAction(f"{r.name} registerına yerleştir", self.place_selected)
            m.addAction("Yeni registera yerleştir", lambda: self.place_selected(new_register=True))
            m.addAction("Tipini değiştir…", self.retype_unplaced)
            m.addSeparator()
            m.addAction("Sil", self.delete_unplaced)
        m.addAction("Port içe aktar…", self.import_ports)
        m.exec(self.unplaced.viewport().mapToGlobal(pos))

    def import_ports(self):
        new = PortImportDialog.ask(self)
        if not new:
            return
        start = len(self.spec.unplaced)
        self.spec.unplaced.extend(new)
        self._refresh_unplaced(set(range(start, len(self.spec.unplaced))))
        self._mark()
        self.status(f"{len(new)} bit alanı yerleştirilmemişlere eklendi; bit haritasına ya da registerlara sürükle")

    def delete_unplaced(self):
        idx = self._sel_unplaced()
        if not idx:
            return
        drop = set(idx)
        self.spec.unplaced = [f for i, f in enumerate(self.spec.unplaced) if i not in drop]
        self._refresh_unplaced()
        self._mark()

    def clear_unplaced(self):
        if self.spec.unplaced and ui.confirm(self, "Yerleştirilmemişleri sil",
                                             f"{len(self.spec.unplaced)} yerleştirilmemiş bit alanı silinecek."):
            self.spec.unplaced = []
            self._refresh_unplaced()
            self._mark()

    def retype_unplaced(self):
        idx = self._sel_unplaced()
        if not idx:
            return
        kind, ok = QInputDialog.getItem(self, "Bit alanı tipi", "Tip:", list(G.KINDS),
                                        G.KINDS.index(self.spec.unplaced[idx[0]].kind), False)
        if ok:
            for i in idx:
                self.spec.unplaced[i].kind = kind
            self._refresh_unplaced(set(idx))
            self._mark()

    def place_selected(self, new_register=False):
        idx = self._sel_unplaced()
        if not idx:
            return
        r = None if new_register else self._sel_reg()
        self._place(idx, r)

    def _drop_on_bitmap(self, idx, lsb):
        r = self._sel_reg()
        if r is not None:
            self._place(idx, r, lsb)

    def _drop_on_register(self, idx, reg_name):
        r = next((x for x in self.spec.registers if x.name == reg_name), None) if reg_name else None
        self._place(idx, r)

    def _new_register(self, stem=None):
        """A register at the next free offset, named `stem` (or REG<n> after its word address)."""
        names = {r.name.upper() for r in self.spec.registers}
        off = max((r.offset for r in self.spec.registers), default=-4) + 4
        stem = stem or f"REG{off // 4}"
        if stem.lower() in G.VERILOG_KEYWORDS or stem.lower() in G.VHDL_KEYWORDS:
            stem = f"{stem}_REG"
        name, k = stem, 2
        while name.upper() in names:
            name, k = f"{stem}_{k}", k + 1
        reg = G.RegSpec(name, off)
        self.spec.registers.append(reg)
        return reg

    def _place(self, indexes, reg, lsb=None):
        """Move unplaced fields into `reg` (None: a new register), side by side from bit `lsb`
        when it is given and free, otherwise at the first free bits. Names are kept unique in the
        register (NAME_2 …)."""
        fields = [self.spec.unplaced[i] for i in sorted(set(indexes)) if 0 <= i < len(self.spec.unplaced)]
        if not fields:
            return
        created = reg is None
        if created:
            reg = self._new_register(fields[0].name if len(fields) == 1 else None)
        used = 0
        for f in reg.fields:
            used |= f.mask
        span = lambda lo, w: ((1 << w) - 1) << lo
        placed, failed, pos, renamed = [], [], lsb, []
        for f in fields:
            w = f.width
            start = None
            if pos is not None and pos + w <= 32 and not used & span(pos, w):
                start = pos
            if start is None and lsb is None:     # a drop on the bit map goes exactly where shown
                start = next((b for b in range(33 - w) if not used & span(b, w)), None)
            if start is None:
                failed.append(f)
                continue
            new = copy.deepcopy(f)
            new.lsb = start
            names = {x.name.upper() for x in reg.fields}
            base, k = new.name, 2
            while new.name.upper() in names:
                new.name, k = f"{base}_{k}", k + 1
            if new.name != base:
                renamed.append(f"{base} → {new.name}")
            reg.fields.append(new)
            used |= span(start, w)
            placed.append(f)
            if pos is not None:
                pos = start + w
        if created and not reg.fields:
            self.spec.registers.remove(reg)
        self.spec.unplaced = [f for f in self.spec.unplaced if not any(f is p for p in placed)]
        self._refresh_regs(reg.name if reg in self.spec.registers else None)
        if placed:
            self._refresh_fields(placed[-1].name if not renamed else None)
        self._refresh_unplaced()
        self._mark()
        msg = f"{len(placed)} bit alanı {reg.name} registerına yerleştirildi" if placed else ""
        if renamed:
            msg += " · yeniden adlandırıldı: " + ", ".join(renamed)
        if msg:
            self.status(msg)
        if failed:
            QMessageBox.information(self, self.title, f"{reg.name} registerında "
                                    + ("bırakılan bitler dolu: " if lsb is not None else "yer yok: ")
                                    + ", ".join(f"{f.name} ({f.width} bit)" for f in failed)
                                    + ". Yerleştirilmemişlerde kaldı.")

    def unplace_field(self):
        r, f = self._sel_field()
        if not f:
            return
        r.fields.remove(f)
        moved = copy.deepcopy(f)
        moved.lsb = 0
        self.spec.unplaced.append(moved)
        self._refresh_regs(r.name)
        self._refresh_unplaced({len(self.spec.unplaced) - 1})
        self._mark()
        self.status(f"{r.name}.{f.name} yerleştirilmemişlere alındı")

    # ================================================================ theme
    def _paint_legend(self):
        for dot in self.legend:
            k = dot.property("kind")
            dot.setText(f"<span style='color:{theme.kind_color(k)}'>■</span> {k}")

    def on_theme(self):
        self.issues.retint()
        self._paint_legend()
        self._refresh_fields(self._selected_field_name())
        self._refresh_unplaced()

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
