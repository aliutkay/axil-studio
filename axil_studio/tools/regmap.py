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
from PySide6.QtGui import QAction, QBrush, QColor, QDrag, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import numfmt as N
from axil_studio.core import portparse as PP
from axil_studio.core import hdlspec as H
from axil_studio.core import regmap_xlsx
from axil_studio.history import History
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import KIND_SHORT, FieldDialog, RegDialog, ValueDialog
from axil_studio.tools.base import ToolPage
from axil_studio.core.model import KIND_READS, KIND_WRITES, EnumValue, single_line
from axil_studio.i18n import tr

SPEC_FILTER = tr("Register spec (*.regspec.json);;JSON (*.json);;All files (*)")
ROLE = Qt.ItemDataRole.UserRole
UNPLACED_MIME = "application/x-axil-unplaced-fields"     # drag data: indexes into spec.unplaced
PLACED_MIME = "application/x-axil-placed-field"          # drag data: "<register>\n<field>"
# field tree columns
C_NAME, C_BITS, C_KIND, C_TYPE, C_RESET, C_VALUES, C_PORT, C_DESC, C_ACT = range(9)


# ============================================================================ read-only register map tree
def regmap_tree():
    """Register / bit field overview (IP Library, Slave Interface Wrapper)."""
    tree = QTreeWidget()
    tree.setHeaderLabels([tr("Register / bit field"), tr("Offset / bits"), tr("Type"), tr("Data type"), tr("Description")])
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
                                  single_line(f.desc) + (tr("  ({0} value meanings)").format(len(f.values)) if f.values else "")])
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
    fieldMoved = Signal(str, int)           # a placed field dragged to a new lsb

    TOP = 18      # bit-number row
    BOTTOM = 16   # legend row

    def __init__(self):
        super().__init__()
        self.reg = None
        self.selected = None
        self.drop = None                    # (lsb, width, fits) while fields are dragged over
        self._press = None                  # (position, field name, grab offset) of a possible drag
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
                   tr("{0} bits unused").format(free))

    def mouseMoveEvent(self, ev):
        if self._press and ev.buttons() & Qt.MouseButton.LeftButton:
            pos, name, offset = self._press
            if (ev.position() - pos).manhattanLength() >= QApplication.startDragDistance():
                self._press = None
                self._drag_field(name, offset)
                return
        bit = self._bit_at(ev.position().x())
        f = self._field_at(bit)
        if bit is None:
            QToolTip.hideText()
            return
        tip = (tr("{0} {1} · {2} · {3}\n{4}\nDrag: move to other bits or drop on the unplaced list").format(f.name, f.bits, f.kind, N.label(f.fmt, f.width), single_line(f.desc)) if f
               else tr("Bit {0} unused\nDouble-click: new bit field at this bit").format(bit))
        QToolTip.showText(ev.globalPosition().toPoint(), tip.strip(), self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        bit = self._bit_at(ev.position().x())
        f = self._field_at(bit)
        self._press = None
        if f:
            self.fieldClicked.emit(f.name)
            if ev.button() == Qt.MouseButton.LeftButton:
                self._press = (ev.position(), f.name, bit - f.lsb)

    def mouseReleaseEvent(self, ev):
        self._press = None

    def _drag_field(self, name, offset):
        """Drag a placed field: onto another bit position here, or onto the unplaced list."""
        md = QMimeData()
        md.setData(PLACED_MIME, f"{self.reg.name}\n{name}".encode())
        self._grab = offset
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.DropAction.MoveAction)
        self._grab = 0

    def mouseDoubleClickEvent(self, ev):
        bit = self._bit_at(ev.position().x())
        f = self._field_at(bit)
        if f:
            self.fieldActivated.emit(f.name)
        elif bit is not None and self.reg is not None:
            self.freeBitActivated.emit(bit)

    # ---- dropping unplaced fields / moving placed ones
    def _dragged(self, ev):
        """("unplaced", [indexes]) | ("placed", field) | None"""
        md = ev.mimeData()
        if self.reg is None:
            return None
        if md.hasFormat(UNPLACED_MIME):
            return "unplaced", [int(x) for x in bytes(md.data(UNPLACED_MIME)).decode().split(",") if x]
        if md.hasFormat(PLACED_MIME):
            reg, name = bytes(md.data(PLACED_MIME)).decode().split("\n", 1)
            f = next((x for x in self.reg.fields if x.name == name), None) if reg == self.reg.name else None
            return ("placed", f) if f else None
        return None

    def _landing(self, ev, what):
        """(lsb, width, fits). Unplaced fields: side by side, centred on the cursor bit. A placed
        field keeps the bit it was grabbed by under the cursor; its own bits count as free."""
        kind, data = what
        bit = self._bit_at(ev.position().x())
        bit = 0 if bit is None else bit
        if kind == "placed":
            total, start = data.width, bit - getattr(self, "_grab", 0)
            others = [f for f in self.reg.fields if f is not data]
        else:
            total = sum(self.widths_of(data))
            start, others = bit - (total - 1) // 2, self.reg.fields
        lsb = max(0, min(start, 32 - total)) if total <= 32 else 0
        used = 0
        for f in others:
            used |= f.mask
        fits = 0 < total <= 32 and not used & (((1 << total) - 1) << lsb)
        return lsb, total, fits

    def dragEnterEvent(self, ev):
        if self._dragged(ev) is not None:
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        what = self._dragged(ev)
        if what is None:
            return
        self.drop = self._landing(ev, what)
        self.update()
        ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self.drop = None
        self.update()

    def dropEvent(self, ev):
        what = self._dragged(ev)
        self.drop = None
        self.update()
        if what is None:
            return
        lsb, _total, fits = self._landing(ev, what)
        ev.acceptProposedAction()
        if what[0] == "placed":
            if fits and lsb != what[1].lsb:
                self.fieldMoved.emit(what[1].name, lsb)
        else:
            self.unplacedDropped.emit(what[1], lsb)


TAIL_ROLE = Qt.ItemDataRole.UserRole + 7
TAIL_SPACE = 56     # empty, inert row at the end of the lists: room to drop below the last entry


def add_tail_space(tree, height=TAIL_SPACE):
    """An empty row at the end of a list (no data, not selectable): however full the list is,
    scrolling down leaves free room to drop fields. It is not painted (see TailRowTree)."""
    it = QTreeWidgetItem([""])
    it.setFlags(Qt.ItemFlag.NoItemFlags)
    it.setSizeHint(0, QSize(10, height))
    it.setData(0, TAIL_ROLE, True)
    tree.addTopLevelItem(it)
    it.setFirstColumnSpanned(True)
    return it


class TailRowTree(QTreeWidget):
    """Tree that leaves its tail space row unpainted, like the empty area below the rows."""

    def drawRow(self, painter, option, index):
        if index.data(TAIL_ROLE):
            return
        super().drawRow(painter, option, index)


def real_items(tree):
    """Top-level items that stand for something (the tail space row has no data)."""
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).data(0, ROLE) is not None]


class FieldsTree(TailRowTree):
    """Bit fields of the selected register. A field row can be dragged onto the bit map (new bits),
    the unplaced list or another register; unplaced fields dropped here go to its first free bits."""

    unplacedDropped = Signal(list)
    dragStarted = Signal(str)               # field name (lets the bit map centre it under the cursor)

    def __init__(self):
        super().__init__()
        self.reg_name = ""                  # register shown (set by the editor)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def mimeTypes(self):
        return [PLACED_MIME]

    def mimeData(self, items):
        key = next((it.data(0, ROLE) for it in items if it.data(0, ROLE) and it.data(0, ROLE)[0] == "field"), None)
        if key is None:
            return None
        self.dragStarted.emit(key[1])
        md = QMimeData()
        md.setData(PLACED_MIME, f"{self.reg_name}\n{key[1]}".encode())
        return md

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(UNPLACED_MIME):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(UNPLACED_MIME):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        md = ev.mimeData()
        if not md.hasFormat(UNPLACED_MIME):
            return ev.ignore()
        ev.acceptProposedAction()
        self.unplacedDropped.emit([int(x) for x in bytes(md.data(UNPLACED_MIME)).decode().split(",") if x])


class RegTree(TailRowTree):
    """Register list that also takes bit fields: unplaced ones dropped on a register go into it,
    below the registers they make new registers; a placed field dropped on another register moves
    there (below the registers: into a new register)."""

    unplacedDropped = Signal(list, object)  # unplaced field indexes, register name or None
    placedDropped = Signal(str, str, object)  # from register, field, to register name or None

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    @staticmethod
    def _what(ev):
        md = ev.mimeData()
        if md.hasFormat(UNPLACED_MIME):
            return "unplaced", [int(x) for x in bytes(md.data(UNPLACED_MIME)).decode().split(",") if x]
        if md.hasFormat(PLACED_MIME):
            return "placed", bytes(md.data(PLACED_MIME)).decode().split("\n", 1)
        return None

    def dragEnterEvent(self, ev):
        if self._what(ev) is not None:
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        if self._what(ev) is not None:
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        what = self._what(ev)
        if what is None:
            return
        it = self.itemAt(ev.position().toPoint())
        target = it.data(0, ROLE) if it else None
        ev.acceptProposedAction()
        if what[0] == "unplaced":
            self.unplacedDropped.emit(what[1], target)
        elif target != what[1][0]:
            self.placedDropped.emit(what[1][0], what[1][1], target)


class UnplacedTree(TailRowTree):
    """Bit fields that are not in a register yet; drag them onto the bit map or a register. A
    placed field dragged here from the bit map is taken out of its register."""

    deleteRequested = Signal()
    placedDropped = Signal(str, str)        # register, field

    def __init__(self):
        super().__init__()
        self.setHeaderLabels([tr("Bit field"), "Bit", tr("Type"), tr("Source"), ""])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        h = self.header()
        for c in (0, 1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        ui.actions_column(h, 4)
        self.setToolTip(tr("Drag onto the bit map or a register (several can be selected); dropping on the empty part of the register list makes a new register. Delete: delete"))

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

    # only placed fields are dropped here (the list itself is not reordered)
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(PLACED_MIME):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(PLACED_MIME):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        if not ev.mimeData().hasFormat(PLACED_MIME):
            return ev.ignore()
        reg, name = bytes(ev.mimeData().data(PLACED_MIME)).decode().split("\n", 1)
        ev.acceptProposedAction()
        self.placedDropped.emit(reg, name)


# ============================================================================ port import
DIR_TEXT = {"in": tr("input"), "out": tr("output"), "inout": tr("bidirectional")}
KIND_OF_DIR = {"in": "RW", "out": "RO", "inout": "RW"}     # seen from the IP core behind the registers


class PortImportDialog(QDialog):
    """Paste the port list of the IP core behind the register file (Verilog / SystemVerilog or
    VHDL); every chosen port becomes an unplaced bit field, signals wider than 32 bits are cut into
    32-bit pieces. Inputs of the core become RW fields (driven by the registers), its outputs RO
    fields (read through the registers)."""

    C_ON, C_PORT, C_DIR, C_TYPE, C_WIDTH, C_KIND, C_FIELDS = range(7)

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("Import ports (Verilog / VHDL)"))
        self.resize(1040, 760)
        self.ports, self.block, self.over = [], "", {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(ui.note(tr("Paste the IP core's ports; each selected port becomes an unplaced bit field."),
                              tr("Paste the ports of the IP core behind the register map: the whole module / entity, only its port list, or port lines. Every selected port becomes an unplaced bit field: the core's inputs become RW (driven from a register), its outputs RO (read through a register); signals wider than 32 bits are split into 32-bit parts. Clock and reset ports start unselected.")))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(ui.label(tr("Language"), "muted"))
        self.lang = QComboBox()
        for text, key in ((tr("Automatic"), None), ("Verilog / SystemVerilog", "verilog"), ("VHDL", "vhdl")):
            self.lang.addItem(text, key)
        row.addWidget(self.lang)
        row.addSpacing(10)
        row.addWidget(ui.label(tr("Block"), "muted"))
        self.block_ed = QLineEdit()
        self.block_ed.setPlaceholderText(tr("module / entity name"))
        self.block_ed.setToolTip(tr("Written into the bit field description as its source (block.port[bits])"))
        row.addWidget(self.block_ed)
        row.addSpacing(10)
        row.addWidget(ui.label(tr("Field name prefix"), "muted"))
        self.prefix_ed = QLineEdit()
        self.prefix_ed.setPlaceholderText(tr("optional"))
        self.prefix_ed.setToolTip(tr("For ports with the same name in several blocks, e.g. RX → RX_DATA"))
        self.prefix_ed.setMaximumWidth(140)
        row.addWidget(self.prefix_ed)
        self.strip_cb = QCheckBox(tr("Drop the i_ / o_ prefixes"))
        self.strip_cb.setChecked(True)
        self.strip_cb.setToolTip(tr("i_threshold → THRESHOLD (the wrapper port becomes o_<reg>_threshold)"))
        row.addWidget(self.strip_cb)
        row.addStretch(1)
        lay.addLayout(row)

        split = QSplitter(Qt.Orientation.Vertical)
        self.text = QPlainTextEdit()
        self.text.setProperty("mono", "true")
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setPlaceholderText(tr("module my_core #(parameter W = 12) (\n    input  wire         clk,\n    input  wire [W-1:0] threshold,\n    output wire [63:0]  timestamp\n);\n\nor\n\nentity my_core is\n  port ( clk       : in  std_logic;\n         threshold : in  std_logic_vector(11 downto 0);\n         timestamp : out unsigned(63 downto 0) );\nend entity;"))
        split.addWidget(self.text)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["", "Port", tr("Direction"), tr("Type"), tr("Width"), tr("Field type"), tr("Bit fields")])
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
        brow.addWidget(ui.button(tr("Select all"), None, "ghost", lambda: self._check_all(True)))
        brow.addWidget(ui.button(tr("Select none"), None, "ghost", lambda: self._check_all(False)))
        lay.addLayout(brow)
        box = QDialogButtonBox()
        self.ok = box.addButton(tr("Add to unplaced"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
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
            wi.setToolTip((p.error + "\n" if p.error else "") + tr("Double-click: change the width"))
            if width is None:
                wi.setForeground(QColor(t["danger"]))
            self.table.setItem(row, self.C_WIDTH, wi)
            cb = QComboBox()
            for k in G.KINDS:
                cb.addItem(k, k)
            cb.setCurrentIndex(max(0, cb.findData(self._kind(p))))
            cb.setToolTip(tr("Core {0} → default {1}").format(DIR_TEXT[p.direction], KIND_OF_DIR[p.direction]))
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
        self.summary.setText(tr("{0} ports found · {1} selected → {2} bit fields").format(len(self.ports), sum(1 for p in self.ports if self._checked(p)), len(fields))
                             + (tr(" · width of {0} ports could not be resolved (type it in the Width cell)").format(bad) if bad else "")
                             if self.ports else tr("No ports found: paste Verilog or VHDL port declarations."))
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
        cols = ["Hex", "Binary"] + ([N.label(f.fmt, f.width).replace(tr(" (auto)"), "")] if extra else [])
        self.n_val_cols = len(cols)
        if self.reads:
            cols.append(tr("Meaning when read"))
        if self.writes:
            cols.append(tr("Effect when written"))
        cols.append("")                         # row icons
        self.act_col = len(cols) - 1
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
        for c in range(self.n_val_cols, self.act_col):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        ui.actions_column(hdr, self.act_col)
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
                it.setToolTip(tr("Double-click: edit · Delete: delete"))
                self.setItem(row, col, it)
            ui.table_row_actions(self, row, self.act_col, lambda *_, v=e.value: self.editRequested.emit(v),
                                 lambda *_, v=e.value: self.deleteRequested.emit(v),
                                 edit_tip=tr("Edit value meaning"), delete_tip=tr("Delete value meaning"))
        add = QTableWidgetItem(tr("+ add value meaning"))
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
            m.addAction(tr("Edit"), lambda: self.editRequested.emit(v))
            m.addAction(tr("Delete"), lambda: self.deleteRequested.emit(v))
            m.addSeparator()
        m.addAction(tr("Add value meaning"), self.addRequested.emit)
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
    subtitle = tr("Define the registers, bit fields, access types and value encodings of an AXI4-Lite IP.")
    description = tr("Register map design for AXI4-Lite IPs: registers, bit fields, access types, reset values and value encodings, with port import from Verilog and VHDL and Excel export.")
    icon_name = "regmap"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.spec = G.ModuleSpec()
        self._spec_win = None           # read-only Spec window (show_spec)
        self.history = History()        # undo / redo of the register map being edited
        # where "Kaydet" writes: ("project", ip name) | ("library", library name) | ("file", path);
        # ("other", label) / None: not saved anywhere yet, "Kaydet" asks
        self.origin = None
        self.source_lib = ""        # library the map came from (the IP's source when put in the project)
        self.outdir = None
        self.dirty = False
        self._loading = False

        # ---- header: where the map lives + main action
        self.origin_pill = self.header.add_right(ui.Pill(""))
        self.header.add_right(ui.button(tr("Save as…"), "saveas", "ghost", lambda: self.save_as(),
                                        tr("Choose where to save: project, IP library or .regspec.json file")))
        self.save_btn = self.header.add_right(ui.button(tr("Save"), "save", "primary", self.save,
                                                        tr("Save to where it was last saved (project / library / file); asks the first time (Ctrl+Shift+S)")))

        # ---- action bar: every command visible
        bar = self.header       # the tool's commands sit in the top bar
        bar.add(tr("New IP"), "file", self.new_spec, tr("Empty register map"))
        bar.add(tr("Open IP…"), "library", self.choose_ip, tr("Open an IP of the project or of a library (IP Library)"))
        bar.add(tr("Import…"), "folder", self.open_spec, tr("Open a .regspec.json file"))
        bar.sep()
        bar.add(tr("Open in Wrapper"), "wrapper", self.open_in_wrapper, tr("Generate the HDL in the Slave Interface Wrapper"))
        bar.add("Spec", "file", self.show_spec, tr("Show the HDL spec of this register map (read-only); follows the edits"))
        bar.sep()
        bar.add(tr("Export to Excel…"), "file", self.export_xlsx, tr("Register map (.xlsx)"), regmap_xlsx.available())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: IP card + registers card
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(14)
        ipcard = ui.Card(margins=16, spacing=10)
        ipcard.lay.addWidget(ui.label("IP", "section"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.name_ed = QLineEdit()
        self.name_ed.setToolTip(tr("IP (module) name: used in the project and in the generated HDL"))
        self.version_ed = QLineEdit()
        self.version_ed.setProperty("mono", "true")
        self.version_ed.setPlaceholderText("1.0.0")
        self.version_ed.setToolTip(tr("IP version: written into the generated files and the IP library (e.g. 1.0.0, 2.1.0-beta)"))
        form.addRow(tr("Name"), self.name_ed)
        form.addRow(tr("Version"), self.version_ed)
        drow = QHBoxLayout()
        drow.setSpacing(6)
        self.desc_lbl = ui.label("", "muted")
        self.desc_lbl.setMinimumWidth(60)
        drow.addWidget(self.desc_lbl, 1)
        drow.addWidget(small_button(tr("Edit…"), self.edit_desc, tr("Edit the IP description")))
        form.addRow(tr("Description"), drow)
        ipcard.lay.addLayout(form)
        left_lay.addWidget(ipcard)

        regs_box = QWidget()
        rl = QVBoxLayout(regs_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rhead = QHBoxLayout()
        rhead.addWidget(ui.label(tr("REGISTERS"), "section"))
        rhead.addStretch(1)
        rhead.addWidget(small_button("Register", self.add_reg, tr("New register (Ctrl+R)"), None, "plus"))
        rl.addLayout(rhead)
        self.search_ed = QLineEdit()
        self.search_ed.setPlaceholderText(tr("Search: register or bit field name / description"))
        self.search_ed.setClearButtonEnabled(True)
        self.search_ed.setToolTip(tr("Matches in bit fields are shown on the register row and highlighted in the field list (Ctrl+K)"))
        self.search_ed.textChanged.connect(lambda *_: self._refresh_regs())
        rl.addWidget(self.search_ed)
        self.reg_tree = RegTree()
        self.reg_tree.setHeaderLabels([tr("Name"), "Offset", tr("Access"), "Strobe", ""])
        self.reg_tree.setRootIsDecorated(False)
        self.reg_tree.setUniformRowHeights(True)
        self.reg_tree.setAlternatingRowColors(True)
        hdr = self.reg_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        ui.actions_column(hdr, 4)
        self.reg_tree.currentItemChanged.connect(lambda *_: self._reg_changed())
        self.reg_tree.itemDoubleClicked.connect(lambda *_: self.edit_reg())
        self.reg_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.reg_tree.customContextMenuRequested.connect(self._reg_menu)
        self.reg_tree.unplacedDropped.connect(self._drop_on_register)
        self.reg_tree.placedDropped.connect(self._move_to_register)
        rl.addWidget(self.reg_tree, 1)
        rbar = QHBoxLayout()
        rbar.setSpacing(4)
        rbar.addWidget(small_button("↑", lambda: self.move_reg(-1), tr("Move up (changes the offsets)")))
        rbar.addWidget(small_button("↓", lambda: self.move_reg(1), tr("Move down (changes the offsets)")))
        rbar.addWidget(small_button(tr("Renumber"), self.repack, tr("Set the offsets from 0x00 in steps of 4")))
        rbar.addStretch(1)
        rl.addLayout(rbar)
        regcard = ui.Card(margins=16, spacing=10)
        regcard.lay.addWidget(regs_box, 1)
        left_lay.addWidget(regcard, 1)
        left.setMinimumWidth(400)
        split.addWidget(left)

        # ---- right: the selected register's bit fields (card) above the unplaced bit fields (own card)
        ftab = ui.Card(margins=16, spacing=10)
        fl = ftab.lay
        th = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.reg_title = ui.label("", "cardTitle")
        self.reg_sub = ui.label("", "muted", wrap=True)
        col.addWidget(self.reg_title)
        col.addWidget(self.reg_sub)
        th.addLayout(col, 1)
        self.add_field_btn = small_button(tr("Bit field"), lambda: self.add_field(), tr("New bit field (Ctrl+F)"), None, "plus")
        th.addWidget(self.add_field_btn, 0, Qt.AlignmentFlag.AlignTop)
        fl.addLayout(th)
        self.bitmap = BitMap()
        self.bitmap.fieldClicked.connect(self._select_field)
        self.bitmap.fieldActivated.connect(self._activate_field)
        self.bitmap.freeBitActivated.connect(self.add_field)
        self.bitmap.unplacedDropped.connect(self._drop_on_bitmap)
        self.bitmap.fieldMoved.connect(self._move_field)
        self.bitmap.widths_of = lambda idx: [self.spec.unplaced[i].width for i in idx if i < len(self.spec.unplaced)]
        fl.addWidget(self.bitmap)

        self.fields = FieldsTree()
        self.fields.unplacedDropped.connect(lambda idx: self._place(idx, self._sel_reg()))
        self.fields.dragStarted.connect(self._list_drag_started)
        self.fields.setHeaderLabels([tr("Bit field"), tr("Bits"), tr("Type"), tr("Data type"), "Reset", tr("Value meanings"), "Port",
                                     tr("Description"), ""])
        self.fields.setAlternatingRowColors(True)
        self.fields.setUniformRowHeights(False)
        self.fields.setIndentation(18)
        self.fields.setExpandsOnDoubleClick(False)
        fh = self.fields.header()
        fh.setSectionResizeMode(C_DESC, QHeaderView.ResizeMode.Stretch)
        ui.actions_column(fh, C_ACT)
        self.fields.itemClicked.connect(self._item_clicked)
        self.fields.itemDoubleClicked.connect(self._item_double_clicked)
        self.fields.currentItemChanged.connect(lambda *_: self._field_selection_changed())
        self.fields.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fields.customContextMenuRequested.connect(self._field_menu)
        fl.addWidget(self.fields, 1)
        # every list bar: actions on the left, Sil on the far right
        fbar = QHBoxLayout()
        fbar.setSpacing(4)
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
        unp_box = ui.Card(margins=14, spacing=8)
        ul = unp_box.lay
        uhead = QHBoxLayout()
        self.unp_title = ui.label(tr("UNPLACED BIT FIELDS"), "section")
        self.unp_title.setToolTip(tr("Bit fields not in any register yet (not part of the HDL).\nDrag onto the bit map or a register; dragging a placed field here takes it back."))
        uhead.addWidget(self.unp_title)
        uhead.addStretch(1)
        hint = ui.label(tr("drag: onto the bit map / a register"), "muted")
        hint.setToolTip(self.unp_title.toolTip())
        uhead.addWidget(hint)
        uhead.addSpacing(8)
        uhead.addWidget(small_button(tr("Import ports…"), self.import_ports,
                                     tr("Make bit fields from the IP core's Verilog / VHDL port list (Ctrl+I)"),
                                     None, "plus"))
        ul.addLayout(uhead)
        self.unplaced = UnplacedTree()
        self.unplaced.deleteRequested.connect(self.delete_unplaced)
        self.unplaced.placedDropped.connect(self.unplace_field)
        self.unplaced.itemDoubleClicked.connect(lambda *_: self.place_selected())
        self.unplaced.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.unplaced.customContextMenuRequested.connect(self._unplaced_menu)
        ul.addWidget(self.unplaced, 1)
        ubar = QHBoxLayout()
        ubar.setSpacing(4)
        self.place_btn = small_button(tr("Place in bit field"), self.place_selected,
                                      tr("Put the selected ones on the free bits of the selected register (double-click works too)"))
        self.place_new_btn = small_button(tr("Place in new register"), lambda: self.place_selected(new_register=True),
                                          tr("Put the selected ones in new registers; more are added for those that do not fit"))
        ubar.addWidget(self.place_btn)
        ubar.addWidget(self.place_new_btn)
        ubar.addStretch(1)
        ubar.addWidget(small_button(tr("Delete all"), self.clear_unplaced))
        ul.addLayout(ubar)
        self.unplaced.itemSelectionChanged.connect(self._update_place_buttons)
        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(ftab)
        right.addWidget(unp_box)
        right.setSizes([520, 240])
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 820])
        self.body.addWidget(split, 1)

        # ---- validation strip
        self.issues = ui.IssueList()     # shown in the window's status bar

        self.name_ed.textEdited.connect(self._form_changed)
        self.version_ed.textEdited.connect(self._form_changed)
        QShortcut(QKeySequence("Ctrl+R"), self, self.add_reg)
        QShortcut(QKeySequence("Ctrl+K"), self, lambda: (self.search_ed.setFocus(), self.search_ed.selectAll()))
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
        act(tr("New IP"), self.new_spec)
        act(tr("Open IP (IP Library)…"), self.choose_ip, "Ctrl+Shift+O")
        m.addSeparator()
        act(tr("Save"), self.save, "Ctrl+Shift+S")
        act(tr("Save as (project / library / file)…"), lambda: self.save_as())
        act(tr("Import (.regspec.json)…"), self.open_spec)
        m.addSeparator()
        act(tr("New register"), self.add_reg)
        act(tr("New bit field"), lambda: self.add_field())
        act(tr("Import ports (Verilog / VHDL)…"), self.import_ports, "Ctrl+I")
        m.addSeparator()
        act(tr("Export to Excel…"), self.export_xlsx)
        act(tr("Open in Slave Interface Wrapper"), self.open_in_wrapper)

    # ================================================================ state
    def _start(self):
        ips = self.ctx.project.ips
        if ips:
            self._open_ip(ips[0].name)
        else:
            self._load_spec()

    def _mark(self):
        self.history.push(self.spec.to_dict())
        self.dirty = not self.history.is_saved()
        self._show_origin()
        self._validate()

    def _set_clean(self):
        self.dirty = False
        self.history.mark_saved()
        self._show_origin()

    # ------------------------------------------------------------ undo / redo (Edit menu)
    def history_target(self):
        return self

    def can_undo(self):
        return self.history.can_undo()

    def can_redo(self):
        return self.history.can_redo()

    def undo(self):
        return self._apply_snapshot(self.history.undo())

    def redo(self):
        return self._apply_snapshot(self.history.redo())

    def _apply_snapshot(self, state):
        if state is None:
            return False
        reg, fld = self._sel_reg(), self._selected_field_name()
        self.spec = G.ModuleSpec.from_dict(state)
        self.dirty = not self.history.is_saved()
        self._show_spec_widgets()
        names = {r.name for r in self.spec.registers}
        if reg and reg.name in names:
            self._refresh_regs(reg.name)
            if fld:
                self._select_field(fld)
        return True

    def _show_origin(self):
        """One pill: where "Kaydet" writes, with a dot (and the warning colour) while there are
        unsaved changes."""
        o = self.origin
        if o is None or o[0] == "other":
            text, state = (tr("no save location yet") if o is None else o[1]), ""
            tip = tr("Not saved anywhere yet: Save asks where")
        elif o[0] == "project":
            n = len(self.ctx.project.instances_of(o[1]))
            text, state = tr("in project: {0}").format(o[1]), "ok"
            tip = tr("Project IP definition · {0}× in the address map").format(n)
        elif o[0] == "file":
            text, state = tr("file: {0}").format(os.path.basename(o[1])), ""
            tip = tr("{0}\nSave writes to this file").format(o[1])
        else:
            text, state = tr("library: {0}").format(o[1]), "ok"
            tip = tr("Save writes it to the library {0} as version {1}").format(o[1], self.spec.version)
        if self.dirty:
            text, state, tip = f"● {text}", "warn", tip + tr("\nThere are unsaved changes")
        self.origin_pill.set(text, state)
        self.origin_pill.setToolTip(tip)

    def _load_spec(self):
        """Show a register map that was just opened (its undo history starts here)."""
        self.history.reset(self.spec.to_dict(), saved=not self.dirty)
        self._show_spec_widgets()

    def _show_spec_widgets(self):
        self._loading = True
        self.search_ed.blockSignals(True)       # another register map: start without a filter
        self.search_ed.clear()
        self.search_ed.blockSignals(False)
        self.name_ed.setText(self.spec.module)
        self.version_ed.setText(self.spec.version)
        self._show_desc()
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

    def _show_desc(self):
        first = single_line(self.spec.desc)
        self.desc_lbl.setText(first or "—")
        self.desc_lbl.setToolTip(self.spec.desc or tr("No description"))

    def edit_desc(self):
        """IP description in its own window: the first line goes into the HDL / C headers."""
        d = QDialog(self)
        d.setWindowTitle(tr("IP description: {0}").format(self.spec.module))
        d.resize(620, 420)
        lay = QVBoxLayout(d)
        lay.addWidget(ui.label(tr("The first line is the header description in the generated HDL and C files."),
                               "muted", wrap=True))
        ed = QPlainTextEdit(self.spec.desc)
        ed.setPlaceholderText(tr("What the IP does, the order of use, things to watch out for…"))
        lay.addWidget(ed, 1)
        box = QDialogButtonBox()
        box.addButton(tr("OK"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(d.accept)
        box.rejected.connect(d.reject)
        lay.addWidget(box)
        ed.setFocus()
        if d.exec() == QDialog.DialogCode.Accepted:
            text = ed.toPlainText().strip()
            if text != self.spec.desc:
                self.spec.desc = text
                self._show_desc()
                self._mark()

    def _validate(self):
        errs = self.spec.validate()
        if not errs:
            n = sum(len(r.fields) for r in self.spec.registers)
            self.issues.ok_text = (tr("No errors · {0} registers, {1} bit fields · address bits [{2}:2] · the IP uses 0x{3:X} bytes").format(len(self.spec.registers), n, self.spec.reg_aw + 1, self.spec.span)
                                   + (tr(" · has an interrupt output (o_irq)") if self.spec.has_irq() else ""))
        n_unp = len(self.spec.unplaced)
        if n_unp:
            self.issues.ok_text += tr(" · {0} unplaced bit fields (not part of the HDL)").format(n_unp)
        self.issues.set_issues(errs)
        self._refresh_spec_window()
        return errs

    # ================================================================ spec view
    def spec_data(self):
        """The HDL spec of the register map being edited (core/hdlspec.py)."""
        return H.ip_hdlspec(self.spec)

    def show_spec(self):
        if self._spec_win is None:
            w = self._spec_win = QWidget(self, Qt.WindowType.Window)
            w.setWindowTitle(f"{self.title}: Spec")
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            w.view = ui.SpecView(tr("HDL spec (read-only): the data that goes from this register map to the Verilog, only what changes the code (registers, bit fields, their types, strobes, reset values). Follows the edits."))
            lay.addWidget(w.view)
            w.resize(640, 720)
        self._spec_win.show()
        self._spec_win.raise_()
        self._spec_win.activateWindow()
        self._refresh_spec_window()

    def _refresh_spec_window(self):
        w = self._spec_win
        if w is not None and w.isVisible():
            w.view.set_data(self.spec_data())

    def _name_taken(self, names, name, old=None):
        if name != old and name in names:
            QMessageBox.warning(self, self.title, tr("The name “{0}” is already used in this register.").format(name))
            return True
        return False

    # ================================================================ registers
    def _sel_reg(self):
        it = self.reg_tree.currentItem()
        name = it.data(0, ROLE) if it else None
        return next((r for r in self.spec.registers if r.name == name), None)

    def _search(self):
        return self.search_ed.text().strip().lower() if hasattr(self, "search_ed") else ""

    def _field_hit(self, f, q):
        return bool(q) and (q in f.name.lower() or q in f.desc.lower())

    def _refresh_regs(self, select=None):
        cur = self._sel_reg()
        keep = select or (cur.name if cur else None)
        mono = theme.mono_font()
        q = self._search()
        accent = theme.manager().color("accent")
        self.reg_tree.blockSignals(True)
        self.reg_tree.clear()
        target = None
        for r in self.spec.regs:
            hits = [f.name for f in sorted(r.fields, key=lambda f: -f.lsb) if self._field_hit(f, q)]
            if q and not hits and q not in r.name.lower() and q not in r.desc.lower():
                continue                       # filtered out by the search
            strobe = "/".join(s for s, on in (("wr", r.wr_strobe), ("rd", r.rd_strobe)) if on)
            label = r.name + (f"   · {', '.join(hits)}" if hits else "")
            it = QTreeWidgetItem([label, f"0x{r.offset:02X}", r.access, strobe])
            it.setData(0, ROLE, r.name)
            it.setFont(1, mono)
            it.setToolTip(0, single_line(r.desc) + (tr("\nBit fields matching the search: {0}").format(', '.join(hits)) if hits else ""))
            if hits:
                it.setForeground(0, accent)
            self.reg_tree.addTopLevelItem(it)
            ui.tree_row_actions(self.reg_tree, it, 4, self.edit_reg, self.del_reg,
                                edit_tip=tr("Edit register"), delete_tip=tr("Delete register"))
            if r.name == keep:
                target = it
        regs = real_items(self.reg_tree)
        if target is None and regs:
            target = regs[0]
        add_tail_space(self.reg_tree)
        self.reg_tree.setCurrentItem(target)
        self.reg_tree.blockSignals(False)
        self._reg_changed()

    def _reg_changed(self):
        """Another register: show its fields; while searching, select the first field found."""
        self._refresh_fields()
        if hasattr(self, "place_btn"):
            self._update_place_buttons()
        r, q = self._sel_reg(), self._search()
        if r and q and not self._selected_field_name():
            hit = next((f.name for f in sorted(r.fields, key=lambda f: -f.lsb) if self._field_hit(f, q)), None)
            if hit:
                self._select_field(hit)

    def _reg_menu(self, pos):
        m = QMenu(self)
        m.addAction(tr("New register"), self.add_reg)
        if self._sel_reg():
            m.addAction(tr("Edit"), self.edit_reg)
            m.addAction(tr("Duplicate"), self.dup_reg)
            m.addAction(tr("New bit field"), lambda: self.add_field())
            m.addSeparator()
            m.addAction(tr("Delete"), self.del_reg)
        m.exec(self.reg_tree.viewport().mapToGlobal(pos))

    def add_reg(self):
        off = max((r.offset for r in self.spec.registers), default=-4) + 4
        new = RegDialog.ask(self, G.RegSpec(f"REG{off // 4}", off), tr("New register"))
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
        if r and ui.confirm(self, tr("Delete register"), tr("Register {0} and its {1} bit fields will be deleted.").format(r.name, len(r.fields))):
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
        return next((it for it in real_items(self.fields) if self._key(it)[1] == name), None)

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
        return {self._key(it)[1] for it in real_items(self.fields) if it.isExpanded()}

    def _refresh_fields(self, select=None, expanded=None):
        r = self._sel_reg()
        self.fields.reg_name = r.name if r else ""
        if expanded is None:
            expanded = self._expanded_names()
        self.add_field_btn.setEnabled(r is not None)
        self.fields.blockSignals(True)
        self.fields.clear()
        if not r:
            self.fields.blockSignals(False)
            self.reg_title.setText(tr("No register"))
            self.reg_sub.setText(tr("Add a register on the left (Ctrl+R)."))
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
            nval = tr("{0} defined  ▾").format(len(f.values)) if f.values else tr("add  ▸")
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
            it.setToolTip(C_VALUES, tr("Click: show / hide the value meanings"))
            if self._field_hit(f, self._search()):           # found by the register search
                for c in (C_NAME, C_DESC):
                    it.setBackground(c, theme.manager().color("selection"))
            it.setToolTip(C_DESC, f.desc)
            if f.kind in ("RW", "CONST"):
                it.setToolTip(C_RESET, "\n".join(f"{a}: {b}" for a, b in N.all_views(f.reset, f.width)))
            self.fields.addTopLevelItem(it)
            ui.tree_row_actions(self.fields, it, C_ACT, self.edit_field, self.del_field,
                                edit_tip=tr("Edit bit field"), delete_tip=tr("Delete bit field"))
            if f.name == select:
                target = it
        # size the columns from the field rows only, then hang the value rows under them
        for c in range(C_DESC):
            self.fields.resizeColumnToContents(c)
            self.fields.setColumnWidth(c, self.fields.columnWidth(c) + 12)
        for it in real_items(self.fields):
            f = next(x for x in r.fields if x.name == self._key(it)[1])
            self._add_value_rows(it, f)
            it.setExpanded(f.name in expanded)
        add_tail_space(self.fields)
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
        table.setFixedHeight(table.fitted_height())     # the row takes the table's height, no blank below
        ch.setSizeHint(0, QSize(10, table.fitted_height() + 8))
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 2, 12, 6)
        hl.addWidget(table)
        self.fields.setItemWidget(ch, 0, holder)

    def _item_clicked(self, item, col):
        """Value descriptions open only from their own column ("N tanım ▾" / "ekle ▸")."""
        if col == C_VALUES and self._key(item) and self._key(item)[0] == "field":
            item.setExpanded(not item.isExpanded())

    def _item_double_clicked(self, item, col=0):
        k = self._key(item)
        if not k:
            return
        if k[0] == "field" and col != C_VALUES:
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
            m.addAction(tr("Add value meaning"), lambda: self.add_value(k[1]))
            m.addAction(tr("Edit bit field"), self.edit_field)
            m.addAction(tr("Delete bit field"), self.del_field)
            m.addAction(tr("Move back to unplaced"), self.unplace_field)
            m.addSeparator()
        m.addAction(tr("New bit field"), lambda: self.add_field())
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
        new = FieldDialog.ask(self, proto, tr("{0}: new bit field").format(r.name))
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
        if f and ui.confirm(self, tr("Delete bit field"), tr("Bit field {0}.{1} will be deleted.").format(r.name, f.name)):
            r.fields.remove(f)
            self._refresh_regs(r.name)
            self._mark()

    def _store_value(self, f, new, old=None):
        if any(e.value == new.value and e is not old for e in f.values):
            QMessageBox.warning(self, self.title, tr("{0} is already defined").format(N.format_value(new.value, f.width, value_fmt(f))))
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
            it.setToolTip(0, tr("{0}: {1} bits {2}\n{3}\nDrag: onto the bit map or a register").format(f.name, f.width, f.kind, f.desc))
            self.unplaced.addTopLevelItem(it)
            ui.tree_row_actions(self.unplaced, it, 4, self.edit_unplaced, self.delete_unplaced,
                                edit_tip=tr("Edit bit field"), delete_tip=tr("Delete bit field"))
            if i in select:
                it.setSelected(True)
        add_tail_space(self.unplaced)
        self._update_place_buttons()
        n = len(self.spec.unplaced)
        self.unp_title.setText(tr("UNPLACED BIT FIELDS ({0})").format(n) if n else tr("UNPLACED BIT FIELDS"))

    def _sel_unplaced(self):
        return sorted(it.data(0, ROLE) for it in self.unplaced.selectedItems())

    def _unplaced_menu(self, pos):
        m = QMenu(self)
        if self._sel_unplaced():
            r = self._sel_reg()
            if r:
                m.addAction(tr("Place in register {0}").format(r.name), self.place_selected)
            m.addAction(tr("Place in new register"), lambda: self.place_selected(new_register=True))
            if len(self._sel_unplaced()) == 1:
                m.addAction(tr("Edit…"), self.edit_unplaced)
            m.addAction(tr("Change type…"), self.retype_unplaced)
            m.addSeparator()
            m.addAction(tr("Delete"), self.delete_unplaced)
        m.addAction(tr("Import ports…"), self.import_ports)
        m.exec(self.unplaced.viewport().mapToGlobal(pos))

    def import_ports(self):
        new = PortImportDialog.ask(self)
        if not new:
            return
        start = len(self.spec.unplaced)
        self.spec.unplaced.extend(new)
        self._refresh_unplaced(set(range(start, len(self.spec.unplaced))))
        self._mark()
        self.status(tr("{0} bit fields added to unplaced; drag them onto the bit map or the registers").format(len(new)))

    def delete_unplaced(self):
        idx = self._sel_unplaced()
        if not idx:
            return
        drop = set(idx)
        self.spec.unplaced = [f for i, f in enumerate(self.spec.unplaced) if i not in drop]
        self._refresh_unplaced()
        self._mark()

    def edit_unplaced(self):
        idx = self._sel_unplaced()
        if len(idx) != 1:
            return
        i = idx[0]
        f = self.spec.unplaced[i]
        new = FieldDialog.ask(self, f, tr("Unplaced: {0}").format(f.name))
        if not new:
            return
        if new.name != f.name and any(x.name == new.name for j, x in enumerate(self.spec.unplaced) if j != i):
            QMessageBox.warning(self, self.title, tr("The name “{0}” is already in the unplaced list.").format(new.name))
            return
        new.lsb = 0
        self.spec.unplaced[i] = new
        self._refresh_unplaced({i})
        self._mark()

    def clear_unplaced(self):
        if self.spec.unplaced and ui.confirm(self, tr("Delete unplaced"),
                                             tr("{0} unplaced bit fields will be deleted.").format(len(self.spec.unplaced))):
            self.spec.unplaced = []
            self._refresh_unplaced()
            self._mark()

    def retype_unplaced(self):
        idx = self._sel_unplaced()
        if not idx:
            return
        kind, ok = QInputDialog.getItem(self, tr("Bit field type"), tr("Type:"), list(G.KINDS),
                                        G.KINDS.index(self.spec.unplaced[idx[0]].kind), False)
        if ok:
            for i in idx:
                self.spec.unplaced[i].kind = kind
            self._refresh_unplaced(set(idx))
            self._mark()

    def _update_place_buttons(self):
        """The place buttons say what they will do: how many fields, into which register."""
        n, r = len(self._sel_unplaced()), self._sel_reg()
        if n:
            self.place_btn.setText(tr("Place {0} bit fields on the free bits of register {1}").format(n, r.name) if r
                                   else tr("Place in bit field (select a register)"))
            self.place_new_btn.setText(tr("Place {0} bit fields in a new register").format(n))
        else:
            self.place_btn.setText(tr("Place in bit field"))
            self.place_new_btn.setText(tr("Place in new register"))
        self.place_btn.setEnabled(bool(n and r))
        self.place_new_btn.setEnabled(bool(n))

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

    def _place_new(self, fields):
        """Fields into new registers, in order: each register takes fields while they fit, then the
        next one is opened. A register that ends up with a single field is named after it (a 32-bit
        field gives its own register); the others are REG<n>."""
        span = lambda lo, w: ((1 << w) - 1) << lo
        made, cur, used = [], None, 0
        for f in fields:
            start = None if cur is None else next((b for b in range(33 - f.width) if not used & span(b, f.width)),
                                                  None)
            if start is None:
                cur, used, start = self._new_register(), 0, 0
                made.append(cur)
            new = copy.deepcopy(f)
            new.lsb = start
            cur.fields.append(new)
            used |= span(start, f.width)
        for reg in made:                       # single-field registers take the field's name
            if len(reg.fields) == 1:
                stem = reg.fields[0].name
                if stem.lower() in G.VERILOG_KEYWORDS or stem.lower() in G.VHDL_KEYWORDS:
                    stem = f"{stem}_REG"
                names = {r.name.upper() for r in self.spec.registers if r is not reg}
                name, k = stem, 2
                while name.upper() in names:
                    name, k = f"{stem}_{k}", k + 1
                reg.name = name
        self.spec.unplaced = [f for f in self.spec.unplaced if not any(f is p for p in fields)]
        self._refresh_regs(made[-1].name if made else None)
        self._refresh_unplaced()
        self._mark()
        self.status(tr("{0} bit fields placed in {1} new registers:").format(len(fields), len(made))
                    + ", ".join(r.name for r in made))

    def _place(self, indexes, reg, lsb=None):
        """Move unplaced fields into `reg` (None: new registers, see _place_new), side by side from
        bit `lsb` when it is given and free, otherwise at the first free bits. Names are kept unique
        in the register (NAME_2 …)."""
        fields = [self.spec.unplaced[i] for i in sorted(set(indexes)) if 0 <= i < len(self.spec.unplaced)]
        if not fields:
            return
        if reg is None:
            return self._place_new(fields)
        created = False
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
        msg = tr("{0} bit fields placed in register {1}").format(len(placed), reg.name) if placed else ""
        if renamed:
            msg += tr(" · renamed:") + ", ".join(renamed)
        if msg:
            self.status(msg)
        if failed:
            QMessageBox.information(self, self.title, tr("in register {0}").format(reg.name)
                                    + (tr("the bits dropped on are in use:") if lsb is not None else tr("no room:"))
                                    + ", ".join(f"{f.name} ({f.width} bit)" for f in failed)
                                    + tr(". They stay in the unplaced list."))

    def unplace_field(self, reg_name=None, field_name=None):
        """Take a placed field (the selected one, or reg_name.field_name) back to the unplaced list."""
        if field_name is None:
            r, f = self._sel_field()
        else:
            r = next((x for x in self.spec.registers if x.name == reg_name), None)
            f = next((x for x in (r.fields if r else []) if x.name == field_name), None)
        if not f:
            return
        r.fields.remove(f)
        moved = copy.deepcopy(f)
        moved.lsb = 0
        self.spec.unplaced.append(moved)
        self._refresh_regs(r.name)
        self._refresh_unplaced({len(self.spec.unplaced) - 1})
        self._mark()
        self.status(tr("{0}.{1} moved to unplaced").format(r.name, f.name))

    def _list_drag_started(self, name):
        """A field dragged from the list lands centred on the cursor on the bit map."""
        _r, f = self._field(name)
        self.bitmap._grab = (f.width - 1) // 2 if f else 0

    def _move_to_register(self, from_reg, name, to_reg):
        """A placed field dropped on another register (None: below the registers -> a new one)."""
        src = next((r for r in self.spec.registers if r.name == from_reg), None)
        f = next((x for x in (src.fields if src else []) if x.name == name), None)
        if f is None:
            return
        dst = next((r for r in self.spec.registers if r.name == to_reg), None) if to_reg else None
        if dst is not None:                    # no room there: the field stays where it is
            used = 0
            for x in dst.fields:
                used |= x.mask
            if not any(not used & (((1 << f.width) - 1) << b) for b in range(33 - f.width)):
                QMessageBox.information(self, self.title, tr("No room for {1} ({2} bits) in register {0}; the field stays in register {3}.").format(dst.name, f.name, f.width, src.name))
                return
        src.fields.remove(f)
        moved = copy.deepcopy(f)
        moved.lsb = 0
        self.spec.unplaced.append(moved)
        self._place([len(self.spec.unplaced) - 1], dst)

    def _move_field(self, name, lsb):
        """A placed field dragged on the bit map to a free position."""
        r, f = self._field(name)
        if not f:
            return
        old = f.bits
        f.lsb = lsb
        self._refresh_regs(r.name)
        self._refresh_fields(f.name)
        self._mark()
        self.status(f"{r.name}.{f.name}: {old} → {f.bits}")

    # ================================================================ theme
    def _paint_legend(self):
        for dot in self.legend:
            k = dot.property("kind")
            dot.setText(f"<span style='color:{theme.kind_color(k)}'>■</span> {k}")

    def on_theme(self):
        self.issues.retint()
        self._paint_legend()
        self._refresh_regs()                   # list colours (the tail space row) follow the theme
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
        self._refresh_spec_window()             # the base follows the address map

    # ================================================================ opening
    def can_close(self):
        return self._confirm_discard()

    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = ui.ask_save(self, self.title, tr("The register map “{0}” has unsaved changes.").format(self.spec.module))
        if r == "save":
            return self.save()
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
                QMessageBox.warning(self, self.title, tr("“{0}” is not an IP definition of the project.").format(name))
                return False
        if reg:
            if self._search():
                self.search_ed.clear()          # the register asked for must not be filtered out
            self._refresh_regs(reg)
            if field:
                self._select_field(field)
        self._raise()
        self.status(tr("{0} opened").format(name))
        return True

    def load_spec(self, spec, origin="", library=""):
        """Open a register map handed over by another tool; `origin`: a label for the user,
        `library`: the IP library it comes from (becomes the IP's source when saved to the project)."""
        if not self._confirm_discard():
            return False
        self.spec = spec
        # from a library: "Kaydet" writes back into that library
        self.origin = ("library", library) if library else (("other", origin) if origin else None)
        self.source_lib = library
        self._set_clean()
        self._load_spec()
        self._raise()
        self.status(tr("{0} opened").format(spec.module) + (f" ({origin})" if origin else ""))
        return True

    def choose_ip(self):
        if not self._confirm_discard():
            return
        name = self.pick_ip(tr("IP whose register map to open"), tr("Select an IP of the project or of an open library. A library IP is brought into the project first."))
        if name:
            self._open_ip(name)
            self.status(tr("{0} opened").format(name))

    def new_spec(self):
        if self._confirm_discard():
            self.spec, self.origin = G.ModuleSpec(), None
            self._set_clean()
            self._load_spec()

    def open_spec(self):
        if not self._confirm_discard():
            return
        start = self.origin[1] if self.origin and self.origin[0] == "file" else (self.outdir or ROOT)
        path, _ = QFileDialog.getOpenFileName(self, tr("Open register spec"), os.path.dirname(start)
                                              if os.path.isfile(start) else start, SPEC_FILTER)
        if not path:
            return
        try:
            self.spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error(tr("Could not open"), exc)
        self.origin, self.outdir = ("file", path), os.path.dirname(path)
        self._set_clean()
        self._load_spec()
        self.status(tr("Opened: {0}").format(path))

    # ================================================================ saving
    # The register map remembers where it was saved (self.origin): the project, an IP library or a
    # .regspec.json file. "Kaydet" writes there; "Farklı kaydet" picks another place and remembers it.
    def save(self):
        o = self.origin
        if o and o[0] == "project":
            return self._save_project(o[1])
        if o and o[0] == "library":
            lib = next((x for x in self.ctx.libraries if x.name == o[1]), None)
            if lib is not None:
                return self._save_library(lib, self.spec.version)
        if o and o[0] == "file":
            return self._save_file(o[1])
        return self.save_as()

    def save_as(self, select=None):
        from axil_studio.tools.ip_library import SaveAsDialog
        o = self.origin or ("", "")
        current = o[1] if o[0] == "project" and self.ctx.project.get_ip(o[1]) else None
        prev = self.ctx.project.get_ip(current) if current else None
        path = o[1] if o[0] == "file" else os.path.join(self.outdir or ROOT, f"{self.spec.module}.regspec.json")
        if select not in ("project", "library", "file"):      # also the `checked` a button / action passes
            select = o[0] if o[0] in ("project", "library", "file") else "project"
        res = SaveAsDialog.ask(self, self.ctx, self.spec.module or "my_ip_axil", self.spec.version, current,
                               prev.source if prev else self.source_lib, path, select)
        if not res:
            return False
        if res[0] == "project":
            return self._save_project(res[1], ask=False)
        if res[0] == "library":
            return self._save_library(res[1], res[2])
        return self._save_file(res[1])

    def _errors_ok(self, where):
        errs = self._validate()
        return not errs or ui.confirm(self, self.title, tr("The register map has {0} errors (status bar). Save it {1} anyway? HDL can only be generated once the errors are fixed.").format(len(errs), where), ok=tr("Save"))

    def _set_name(self, name):
        """The name chosen in a save dialog is the IP's name."""
        if name != self.spec.module:
            self.spec.module = name
            self._loading = True
            self.name_ed.setText(name)
            self._loading = False

    def _save_project(self, name, ask=False):
        """Store the register map as a project IP definition (instances in the address map follow it)."""
        if not self._errors_ok(tr("to the project")):
            return False
        old = self.origin[1] if self.origin and self.origin[0] == "project" else None
        if old and not self.ctx.project.get_ip(old):
            old = None
        if ask:
            from axil_studio.tools.ip_library import ProjectSaveDialog
            name = ProjectSaveDialog.ask(self, self.ctx, self.spec.module or "my_ip_axil", old)
            if not name:
                return False
        self._set_name(name)
        prev = self.ctx.project.get_ip(old or name)
        ip = G.to_ipblock(copy.deepcopy(self.spec), 0, with_spec=True)
        ip.source = prev.source if prev else self.source_lib
        replaced = self.ctx.put_ip(ip, self.tool_id, old_name=old)
        self.origin = ("project", name)
        self._set_clean()
        self._show_origin()
        self.status(tr("{0} {1} in the project").format(name, tr("updated") if replaced else tr("added"))
                    + (f" ({old} → {name})" if old and old != name else ""))
        return True

    def save_to_project(self):
        """Into the project, asking for the name (used where a project IP is required)."""
        return self._save_project(self.spec.module, ask=True)

    def _save_library(self, lib, version):
        if not self._errors_ok(tr("to the library")):
            return False
        spec = copy.deepcopy(self.spec)
        spec.version = version                    # the version chosen there is the IP's version
        try:
            replaced = self.ctx.store_in_library(lib, G.to_ipblock(copy.deepcopy(spec), 0), spec.to_dict())
        except OSError as exc:
            self.error(tr("Could not store in the library"), exc)
            return False
        if version != self.spec.version:
            self.spec.version = version
            self._loading = True
            self.version_ed.setText(version)
            self._loading = False
        self.origin, self.source_lib = ("library", lib.name), lib.name
        self._set_clean()
        self._show_origin()
        self.status(tr("{0} {1}: {3} in the library {2}").format(self.spec.module, version, lib.name, tr("updated") if replaced else 'kaydedildi'))
        return True

    def _save_file(self, path):
        try:
            self.spec.save(path)
        except OSError as exc:
            self.error(tr("Could not save"), exc)
            return False
        self.outdir = os.path.dirname(path)
        self.origin = ("file", path)
        self._set_clean()
        self._show_origin()
        self.status(tr("Saved: {0}").format(path))
        return True

    # ================================================================ other tools
    def export_xlsx(self):
        if not regmap_xlsx.available():
            return self.error(tr("Could not export"), tr("openpyxl is not installed"))
        errs = self.spec.validate()
        if errs:
            QMessageBox.warning(self, self.title, tr("The register map has errors. Fix the errors shown in the status bar and try again."))
            return
        folder = os.path.dirname(self.ctx.path or "") or ROOT
        path, _ = QFileDialog.getSaveFileName(self, tr("Register map (Excel)"),
                                              os.path.join(folder, f"{self.spec.module}_regmap.xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        base = 0
        if self.origin and self.origin[0] == "project" and self.ctx.project.get_ip(self.origin[1]) is not None:
            base = H.ip_base(self.ctx.project, self.origin[1])
        try:
            G.export_xlsx(self.spec, path, base)
        except (OSError, RuntimeError) as exc:
            return self.error(tr("Could not export"), exc)
        self.status(tr("Exported to Excel: {0} (base {1}; can be changed in the yellow cell of the Summary sheet)").format(path, f"0x{base:08X}"))

    def open_in_wrapper(self):
        w = self.open_tool("wrapper")
        if self.origin and self.origin[0] == "project" and not self.dirty:
            w.page.load_project_ip(self.origin[1])
        else:
            w.page.load_spec(copy.deepcopy(self.spec), tr("From the Register Map Editor, not saved to the project"))
