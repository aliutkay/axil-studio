"""Edit dialogs shared by the tools: register, bit field and value meanings."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import numfmt as N
from axil_studio import theme, ui
from axil_studio.core.model import KIND_READS, KIND_WRITES, EnumValue, parse_int
from axil_studio.i18n import tr

KIND_SHORT = {"RW": tr("control (o_)"), "RO": tr("status (i_)"), "PULSE": tr("pulse (o_)"),
              "W1C": tr("event flag (i_)"), "CONST": tr("constant")}


def text_box(text="", lines=3):
    w = QPlainTextEdit(text or "")
    w.setTabChangesFocus(True)
    fm = w.fontMetrics()
    w.setFixedHeight(int(fm.lineSpacing() * lines + 18))
    return w


def mono_edit(text=""):
    e = QLineEdit(text)
    e.setProperty("mono", "true")
    return e


class FormatPicker(QWidget):
    """Data type combo + fraction bits (for fixed point). value() -> numfmt string."""

    def __init__(self, fmt="", width_getter=lambda: 32, allow_auto=True, auto_text=None):
        super().__init__()
        self._width = width_getter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.combo = QComboBox()
        if allow_auto:
            self.combo.addItem(auto_text or tr("Automatic"), "")
        for key, text in N.BASE_FORMATS:
            self.combo.addItem(text, key)
        self.frac_lbl = ui.label(tr("Fraction bits"), "muted")
        self.frac = QSpinBox()
        self.frac.setRange(0, 31)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.frac_lbl)
        lay.addWidget(self.frac)
        self.set(fmt)
        self.combo.currentIndexChanged.connect(self._sync)

    def set(self, fmt):
        base, frac = N.split(fmt, self._width()) if fmt else ("", 0)
        i = self.combo.findData(base)
        self.combo.setCurrentIndex(max(0, i))
        self.frac.setValue(frac)
        self._sync()

    def _sync(self):
        fix = self.combo.currentData() in ("ufix", "sfix")
        self.frac_lbl.setVisible(fix)
        self.frac.setVisible(fix)

    def value(self):
        base = self.combo.currentData()
        return N.join(base, self.frac.value()) if base else ""


class FormDialog(QDialog):
    """Modal dialog with a form; subclasses implement build() and apply() -> result.
    apply() raises ValueError with a user message when the input is invalid."""

    def __init__(self, parent, title, width=480):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.result_value = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setProperty("role", "cardTitle")
        lay.addWidget(head)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(10)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(self.form)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(8)
        lay.addLayout(self.extra, 1)
        self.build()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText(tr("OK"))
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def row(self, label, widget, hint=None):
        if hint:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            h.addWidget(widget, 1)
            hl = ui.label(hint, "muted")
            h.addWidget(hl)
            widget.setProperty("hintLabel", hl)
            self.form.addRow(label, box)
        else:
            self.form.addRow(label, widget)
        return widget

    def _ok(self):
        try:
            self.result_value = self.apply()
        except ValueError as exc:
            QMessageBox.warning(self, tr("Invalid value"), str(exc))
            return
        self.accept()

    @classmethod
    def ask(cls, parent, *args, **kw):
        d = cls(parent, *args, **kw)
        return d.result_value if d.exec() == QDialog.DialogCode.Accepted else None


# ============================================================================ register
class RegDialog(FormDialog):
    def __init__(self, parent, reg: G.RegSpec, title):
        self.reg = reg
        super().__init__(parent, title)

    def build(self):
        r = self.reg
        self.name = self.row(tr("Name"), QLineEdit(r.name))
        self.off = self.row("Offset", mono_edit(f"0x{r.offset:02X}"), tr("multiple of 4"))
        self.desc = self.row(tr("Description"), text_box(r.desc, 3))
        self.wr = QCheckBox(tr("Write strobe  (o_<reg>_wr, e.g. FIFO push)"))
        self.wr.setChecked(r.wr_strobe)
        self.rd = QCheckBox(tr("Read strobe  (o_<reg>_rd, e.g. FIFO pop)"))
        self.rd.setChecked(r.rd_strobe)
        self.extra.addWidget(self.wr)
        self.extra.addWidget(self.rd)
        self.name.selectAll()
        self.name.setFocus()

    def apply(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError(tr("Name cannot be empty"))
        try:
            off = parse_int(self.off.text())
        except ValueError:
            raise ValueError(tr("Offset must be a number")) from None
        if off % 4 or off < 0:
            raise ValueError(tr("Offset must be a multiple of 4"))
        return G.RegSpec(name, off, self.desc.toPlainText().strip(), self.wr.isChecked(), self.rd.isChecked(),
                         self.reg.fields)


# ============================================================================ value meanings
def fmt_hint(width, fmt):
    base, _ = N.split(fmt, width)
    return tr("{0}, {1} bits").format(N.label(fmt, width), width)


class ValueDialog(FormDialog):
    def __init__(self, parent, e: EnumValue, width, reads, writes, fmt=""):
        self.e, self.nbits, self.reads, self.writes = e, width, reads, writes
        self.fmt = "hex" if N.split(fmt, width)[0] == "enum" else fmt
        super().__init__(parent, tr("Value meaning"), 460)

    def build(self):
        self.val = self.row(tr("Value"), mono_edit(N.format_value(self.e.value, self.nbits, self.fmt)),
                            fmt_hint(self.nbits, self.fmt))
        self.read = self.row(tr("Meaning when read"), text_box(self.e.read, 2))
        self.write = self.row(tr("Effect when written"), text_box(self.e.write, 2))
        self.read.setEnabled(self.reads)
        self.write.setEnabled(self.writes)
        self.val.selectAll()
        self.val.setFocus()

    def apply(self):
        v = N.parse_value(self.val.text(), self.nbits, self.fmt)
        return EnumValue(v, self.read.toPlainText().strip(), self.write.toPlainText().strip())


class ValuesEditor(QWidget):
    """Table of field values with their read meaning and write effect."""

    def __init__(self, values, kind="", width_getter=lambda: 32, fmt_getter=lambda: ""):
        super().__init__()
        self._values = [EnumValue(e.value, e.read, e.write) for e in values]
        self._kind = kind
        self._width = width_getter
        self._fmt = fmt_getter
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(ui.label(tr("VALUE MEANINGS (OPTIONAL)"), "section"))
        head.addStretch(1)
        self.hint = ui.label("", "muted")
        head.addWidget(self.hint)
        lay.addLayout(head)
        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        for c in (0, 1, 2):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for c in (3, 4):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        ui.actions_column(hdr, 5)
        self.table.setMinimumHeight(120)
        self.table.doubleClicked.connect(lambda *_: self._edit())
        lay.addWidget(self.table, 1)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(ui.button(tr("Add"), "plus", None, self._add))
        bar.addStretch(1)
        lay.addLayout(bar)
        self.set_kind(kind)

    @property
    def reads(self):
        return KIND_READS.get(self._kind, True)

    @property
    def writes(self):
        return KIND_WRITES.get(self._kind, True)

    def set_kind(self, kind):
        self._kind = kind
        self.hint.setText(tr("Only the read meaning is used for this type") if not self.writes else
                          tr("Only the write effect is used for this type") if not self.reads else "")
        self.refresh()

    def _vfmt(self):
        f = self._fmt()
        return "hex" if N.split(f, self._width())[0] == "enum" else f

    def refresh(self):
        """Columns: Hex | Binary | <data type> | Okunursa | Yazılırsa (type column hidden when it
        would repeat hex/binary)."""
        self._values.sort(key=lambda e: e.value)
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        w, fmt = self._width(), self._fmt()
        extra = N.typed_extra(fmt, w)
        self.table.setHorizontalHeaderLabels(["Hex", "Binary", N.label(fmt, w).replace(tr(" (auto)"), "") if extra else "",
                                              tr("Meaning when read"), tr("Effect when written"), ""])
        self.table.setColumnHidden(2, not extra)
        self.table.setRowCount(len(self._values))
        for row, e in enumerate(self._values):
            vals = N.triple(e.value, w, fmt) + ([""] if not extra else [])
            cells = (*vals, e.read.replace("\n", " ") if self.reads else "—",
                     e.write.replace("\n", " ") if self.writes else "—")
            for col, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if col < 3:
                    it.setFont(mono)
                if text == "—":
                    it.setForeground(muted)
                self.table.setItem(row, col, it)
            ui.table_row_actions(self.table, row, 5, self._edit, self._delete,
                                 edit_tip=tr("Edit value meaning"), delete_tip=tr("Delete value meaning"))

    def _selected(self):
        row = self.table.currentRow()
        return self._values[row] if 0 <= row < len(self._values) else None

    def _store(self, new, old=None):
        if any(e.value == new.value and e is not old for e in self._values):
            QMessageBox.warning(self, tr("Value meaning"), tr("{0} is already defined").format(N.format_value(new.value, self._width(), self._vfmt())))
            return
        if old is None:
            self._values.append(new)
        else:
            self._values[self._values.index(old)] = new
        self.refresh()

    def _add(self):
        used = {e.value for e in self._values}
        nxt = next((v for v in range(1 << min(self._width(), 16)) if v not in used), 0)
        new = ValueDialog.ask(self, EnumValue(nxt), self._width(), self.reads, self.writes, self._fmt())
        if new:
            self._store(new)

    def _edit(self):
        e = self._selected()
        if e:
            new = ValueDialog.ask(self, e, self._width(), self.reads, self.writes, self._fmt())
            if new:
                self._store(new, e)

    def _delete(self):
        e = self._selected()
        if e:
            self._values.remove(e)
            self.refresh()

    def get(self):
        """Values for the current kind (meanings that do not apply are dropped)."""
        out = []
        for e in self._values:
            read = e.read if self.reads else ""
            write = e.write if self.writes else ""
            if read or write:
                out.append(EnumValue(e.value, read, write))
        return out


# ============================================================================ field
class FieldDialog(FormDialog):
    def __init__(self, parent, f: G.FieldSpec, title):
        self.f = f
        super().__init__(parent, title, 600)

    def build(self):
        f = self.f
        self.name = self.row(tr("Name"), QLineEdit(f.name))
        self.name.setToolTip(tr("If the bit field has the register's name, its port is o_<register>."))
        bits = QWidget()
        h = QHBoxLayout(bits)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.lsb = mono_edit(str(f.lsb))
        self.width_ed = mono_edit(str(f.width))
        h.addWidget(ui.label("LSB", "muted"))
        h.addWidget(self.lsb)
        h.addWidget(ui.label(tr("Width"), "muted"))
        h.addWidget(self.width_ed)
        self.bits_lbl = ui.label("", "muted")
        h.addWidget(self.bits_lbl)
        self.form.addRow(tr("Bits"), bits)
        self.kind = QComboBox()
        for k in G.KINDS:
            self.kind.addItem(f"{k}  —  {KIND_SHORT[k]}", k)
        self.kind.setCurrentIndex(max(0, G.KINDS.index(f.kind) if f.kind in G.KINDS else 0))
        self.form.addRow(tr("Type"), self.kind)
        self.kind_help = ui.label("", "muted", wrap=True)
        self.form.addRow("", self.kind_help)
        self.fmt = FormatPicker(f.fmt, self._cur_width, auto_text=tr("Automatic (1 bit: bool, else: hex)"))
        self.form.addRow(tr("Data type"), self.fmt)
        self.form.addRow("", ui.label(tr("How values are shown and typed; also the default in the UART-to-AXI Debug Bridge."), "muted", wrap=True))
        self._shown_fmt = f.fmt
        self.reset = self.row(tr("Reset / constant"), mono_edit(N.format_value(f.reset, f.width, f.fmt)), "")
        self.desc = self.row(tr("Description"), text_box(f.desc, 3))
        opts = QHBoxLayout()
        self.irq = QCheckBox(tr("Interrupt (drives o_irq)"))
        self.irq.setChecked(f.irq)
        self.sync = QCheckBox(tr("2-FF synchronizer (asynchronous input)"))
        self.sync.setChecked(f.sync)
        opts.addWidget(self.irq)
        opts.addWidget(self.sync)
        opts.addStretch(1)
        self.form.addRow("", opts)
        self.values = ValuesEditor(f.values, f.kind, self._cur_width, self.fmt.value)
        self.extra.addWidget(self.values, 1)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.lsb.textChanged.connect(self._bits_changed)
        self.width_ed.textChanged.connect(self._bits_changed)
        self.fmt.combo.currentIndexChanged.connect(self._fmt_changed)
        self.fmt.frac.valueChanged.connect(self._fmt_changed)
        self._kind_changed()
        self._bits_changed()
        self.name.selectAll()
        self.name.setFocus()

    def _cur_width(self):
        try:
            return max(1, min(32, parse_int(self.width_ed.text())))
        except ValueError:
            return 32

    def _bits_changed(self):
        try:
            lsb, w = parse_int(self.lsb.text()), parse_int(self.width_ed.text())
            self.bits_lbl.setText(f"[{lsb}]" if w == 1 else f"[{lsb + w - 1}:{lsb}]")
        except ValueError:
            self.bits_lbl.setText("")
        self._update_reset_hint()
        self.values.refresh()

    def _update_reset_hint(self):
        hl = self.reset.property("hintLabel")
        if hl:
            hl.setText(N.label(self.fmt.value(), self._cur_width()))

    def _fmt_changed(self, *_):
        """Re-show the reset value in the newly chosen type."""
        new = self.fmt.value()
        w = self._cur_width()
        try:
            v = N.parse_value(self.reset.text(), w, self._shown_fmt)
            self.reset.setText(N.format_value(v, w, new))
        except ValueError:
            pass
        self._shown_fmt = new
        self._update_reset_hint()
        self.values.refresh()

    def _kind_changed(self):
        k = self.kind.currentData()
        self.kind_help.setText(G.KIND_HELP.get(k, ""))
        self.reset.setEnabled(k in ("RW", "CONST"))
        self.irq.setEnabled(k == "W1C")
        self.sync.setEnabled(k == "RO")
        if k not in ("RW", "CONST"):
            self.reset.setText(N.format_value(0, self._cur_width(), self.fmt.value()))
        if k != "W1C":
            self.irq.setChecked(False)
        if k != "RO":
            self.sync.setChecked(False)
        self.values.set_kind(k)

    def apply(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError(tr("Name cannot be empty"))
        try:
            lsb, width = parse_int(self.lsb.text()), parse_int(self.width_ed.text())
        except ValueError:
            raise ValueError(tr("LSB and width must be numbers")) from None
        if not (0 <= lsb <= 31 and width >= 1 and lsb + width <= 32):
            raise ValueError(tr("The bit field must stay within 32 bits"))
        fmt = self.fmt.value()
        base, frac = N.split(fmt, width)
        if base in ("ufix", "sfix") and frac >= width:
            raise ValueError(tr("Fraction bits ({0}) must be fewer than the field width ({1})").format(frac, width))
        try:
            reset = N.parse_value(self.reset.text(), width, fmt)
        except ValueError as exc:
            raise ValueError(tr("Reset value: {0}").format(exc)) from None
        values = self.values.get()
        bad = [f"0x{e.value:X}" for e in values if e.value >> width]
        if bad:
            raise ValueError(tr("Value(s) {0} in the value table do not fit in {1} bits").format(', '.join(bad), width))
        return G.FieldSpec(name, lsb, width, self.kind.currentData(), reset, self.desc.toPlainText().strip(),
                           self.irq.isChecked(), self.sync.isChecked(), values, fmt)


# ============================================================================ slave base address
class BaseDialog(FormDialog):
    """Asks for the slave base address of an IP (project placement or Excel export).
    The base must be aligned to the IP's address window."""

    def __init__(self, parent, title, base, window, note=""):
        self.base, self.window, self.note = base, window, note
        super().__init__(parent, title, 460)

    def build(self):
        self.ed = self.row(tr("Base address"), mono_edit(f"0x{self.base:08X}"), tr("{0:#x} aligned").format(self.window))
        if self.note:
            self.extra.addWidget(ui.label(self.note, "muted", wrap=True))
        self.ed.selectAll()
        self.ed.setFocus()

    def apply(self):
        try:
            v = parse_int(self.ed.text())
        except ValueError:
            raise ValueError(tr("Base address must be a number (e.g. 0x80002000)")) from None
        if not 0 <= v <= 0xFFFFFFFF:
            raise ValueError(tr("Base address must fit in 32 bits"))
        if v & (self.window - 1):
            raise ValueError(tr("Base address must be aligned to the IP's window ({0:#x})").format(self.window))
        return v


# ============================================================================ interconnect slave port
def window_choices(need):
    """Power-of-two window sizes from `need` up to 256 MB."""
    out, w = [], max(4, need)
    while w <= 0x10000000:
        out.append(w)
        w <<= 1
    return out


def size_text(n):
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div and n % div == 0:
            return f"{n // div} {unit}"
    return f"{n} B"


class SlaveDialog(FormDialog):
    """One address-map entry (Address Map Editor): name, base and window; external slaves also
    the used size (an IP's comes from its register map)."""

    def __init__(self, parent, slave, title, min_window=0x1000, auto=False):
        self.s, self.min_window, self.auto = slave, min_window, auto
        super().__init__(parent, title, 460)

    def build(self):
        s = self.s
        self.name = QLineEdit(s.name)
        self.form.addRow(tr("Name"), self.name)
        self.base = self.row(tr("Base address"), mono_edit(f"0x{s.base:08X}"))
        self.span = mono_edit(f"0x{s.span:X}" if s.span else "0x1000")
        self.span.setEnabled(s.external)
        self.form.addRow(tr("Used space"), self.span)
        self.window = QComboBox()
        self.form.addRow(tr("Window"), self.window)
        self.fixed = QCheckBox(tr("Fixed address (otherwise given automatically in port order)"))
        self.fixed.setChecked(s.fixed)
        self.form.addRow("", self.fixed)
        self.fixed.toggled.connect(self._sync_base)
        self._sync_base()
        if not s.external:
            self.extra.addWidget(ui.label(tr("The used space comes from the IP's register map."), "muted"))
        self.span.textChanged.connect(self._fill_windows)
        self._fill_windows()
        self.name.setFocus()

    def _sync_base(self, *_):
        """With auto addressing on, only fixed slaves take a hand-entered base."""
        self.base.setEnabled(not self.auto or self.fixed.isChecked())
        self.base.setToolTip("" if self.base.isEnabled() else
                             tr("Automatic addressing: the address follows the port order. Check Fixed address to type one."))

    def _need(self):
        try:
            span = parse_int(self.span.text())
        except ValueError:
            span = 4
        w = max(4, self.min_window if not self.s.external else 4)
        while w < span:
            w <<= 1
        return w

    def _fill_windows(self, *_):
        cur = self.window.currentData() or self.s.window
        self.window.clear()
        for w in window_choices(self._need()):
            self.window.addItem(f"{size_text(w)}  ({w:#x})", w)
        i = self.window.findData(cur)
        self.window.setCurrentIndex(max(0, i))

    def apply(self):
        from axil_studio.core import xbargen as X
        name = self.name.text().strip()
        if not name:
            raise ValueError(tr("Name cannot be empty"))
        try:
            base = parse_int(self.base.text())
            span = parse_int(self.span.text())
        except ValueError:
            raise ValueError(tr("Base address and used space must be numbers")) from None
        w = self.window.currentData()
        if not self.base.isEnabled():
            base = self.s.base & ~(w - 1)     # re-addressed by the page anyway
        fixed = self.fixed.isChecked() or (self.base.isEnabled() and base != self.s.base and not self.auto)
        if base & (w - 1):
            raise ValueError(tr("Base address must be aligned to the window ({0:#x})").format(w))
        if not 0 <= base <= 0xFFFFFFFF:
            raise ValueError(tr("Base address must fit in 32 bits"))
        return X.XbarSlave(name, base, (~(w - 1)) & 0xFFFFFFFF, span, self.s.external,
                           fixed)


class BridgeDialog(FormDialog):
    """A Sub-bus Bridge (Address Map Editor): name, its own window at the start, the window size
    (grows with the contents unless chosen) and the address (automatic or fixed)."""

    def __init__(self, parent, title, name, base, self_window, window, fixed, need):
        self.v = (name, base, self_window, window, fixed)
        self.need = need
        super().__init__(parent, title, 480)

    def build(self):
        name, base, self_window, window, fixed = self.v
        self.name = QLineEdit(name)
        self.form.addRow(tr("Name"), self.name)
        self.own = QComboBox()
        self.own.addItem(tr("none"), 0)
        for w in window_choices(0x100):
            self.own.addItem(f"{size_text(w)}  ({w:#x})", w)
        self.own.setCurrentIndex(max(0, self.own.findData(self_window)))
        self.own.setToolTip(tr("Space at the start of the window for the bridge itself; the slaves behind it are placed after it"))
        self.form.addRow(tr("Own window"), self.own)
        self.window = QComboBox()
        self.form.addRow(tr("Window"), self.window)
        self.base = self.row(tr("Base address"), mono_edit(f"0x{base:08X}"))
        self.fixed = QCheckBox(tr("Fixed address (otherwise given automatically in port order)"))
        self.fixed.setChecked(fixed)
        self.form.addRow("", self.fixed)
        self.fixed.toggled.connect(lambda on: self.base.setEnabled(on))
        self.base.setEnabled(fixed)
        self.extra.addWidget(ui.note(tr("The rest of the window is forwarded to the interconnect behind the bridge."),
                                     tr("Addresses in the window (outside its own part) are forwarded unchanged to the interconnect behind the bridge. An automatic window grows with its contents; a slave that does not fit in a fixed window cannot be added.")))
        self.own.currentIndexChanged.connect(self._fill)
        self._fill()
        self.name.setFocus()

    def _fill(self, *_):
        cur = self.window.currentData() if self.window.count() else self.v[3]
        rest = max(0, self.need - self.v[2])            # the children's part keeps its size
        need = self.own.currentData() + rest
        self.window.clear()
        self.window.addItem(tr("automatic (grows with its contents)"), 0)
        first = 4
        while first < need:
            first <<= 1                         # windows are powers of two
        for w in window_choices(first):
            self.window.addItem(tr("{0}  ({1:#x}) · locked").format(size_text(w), w), w)
        self.window.setCurrentIndex(max(0, self.window.findData(cur or 0)))

    def apply(self):
        name = self.name.text().strip()
        if not name:
            raise ValueError(tr("Name cannot be empty"))
        try:
            base = parse_int(self.base.text()) & 0xFFFFFFFF
        except ValueError:
            raise ValueError(tr("Base address must be a number")) from None
        w = self.window.currentData() or 0
        if self.fixed.isChecked() and w and base & (w - 1):
            raise ValueError(tr("Base address must be aligned to the window ({0:#x})").format(w))
        return name, base, self.own.currentData(), w, self.fixed.isChecked()


# ============================================================================ debug: custom value / map edits
class CustomValueDialog(FormDialog):
    """Write any value to one bit field in any data type; optionally keep it as a new value
    description in the register map."""

    def __init__(self, parent, f, value, readable=True, writable=True, command=False, can_keep=True):
        self.f, self.v0, self.readable, self.writable, self.is_command = f, value, readable, writable, command
        self.can_keep = can_keep    # project IPs: value descriptions are edited in the Register Map Editor
        super().__init__(parent, tr("Custom value: {0} {1}").format(f.name, f.bits), 500)

    def build(self):
        f = self.f
        self.fmt = FormatPicker(f.fmt if N.split(f.fmt, f.width)[0] != "enum" else "hex", lambda: f.width,
                                allow_auto=False)
        self.form.addRow(tr("Data type"), self.fmt)
        self._shown = self.fmt.value()
        self.val = self.row(tr("Value"), mono_edit(N.format_value(self.v0, f.width, self._shown)), "")
        self.preview = ui.label("", "muted")
        self.form.addRow("", self.preview)
        self.now = QCheckBox(tr("Write now (register read-modify-write)"))
        self.now.setChecked(self.is_command)
        self.now.setEnabled(not self.is_command)
        self.form.addRow("", self.now)
        self.keep = QCheckBox(tr("Add this value to the register map as a value meaning"))
        self.form.addRow("", self.keep)
        self.read = self.row(tr("Meaning when read"), QLineEdit())
        self.write = self.row(tr("Effect when written"), QLineEdit())
        for w in (self.read, self.write):
            w.setEnabled(False)
        self.keep.toggled.connect(lambda on: (self.read.setEnabled(on and self.readable),
                                              self.write.setEnabled(on and self.writable)))
        if not self.can_keep:
            for w in (self.keep, self.read, self.write):
                w.setVisible(False)
                hint = self.form.labelForField(w)
                if hint is not None:
                    hint.setVisible(False)
        self.fmt.combo.currentIndexChanged.connect(self._fmt_changed)
        self.fmt.frac.valueChanged.connect(self._fmt_changed)
        self.val.textEdited.connect(self._update)
        self._update()
        self.val.selectAll()
        self.val.setFocus()

    def _fmt_changed(self, *_):
        new = self.fmt.value()
        try:
            v = N.parse_value(self.val.text(), self.f.width, self._shown)
            self.val.setText(N.format_value(v, self.f.width, new))
        except ValueError:
            pass
        self._shown = new
        self._update()

    def _update(self, *_):
        hl = self.val.property("hintLabel")
        if hl:
            hl.setText(tr("{0}, {1} bits").format(N.label(self.fmt.value(), self.f.width), self.f.width))
        try:
            v = N.parse_value(self.val.text(), self.f.width, self.fmt.value())
            self.preview.setText("  ·  ".join(N.triple(v, self.f.width, "udec")))
            self.val.setStyleSheet("")
        except ValueError as exc:
            self.preview.setText(str(exc))
            self.val.setStyleSheet(f"border-color: {theme.manager().tokens['danger']};")

    def apply(self):
        v = N.parse_value(self.val.text(), self.f.width, self.fmt.value())
        meaning = None
        if self.keep.isChecked():
            meaning = EnumValue(v, self.read.text().strip(), self.write.text().strip())
            if not (meaning.read or meaning.write):
                raise ValueError(tr("Type the meaning when read or the effect when written"))
        return {"value": v, "now": self.now.isChecked(), "meaning": meaning}


class FieldMapDialog(FormDialog):
    """Data type and value descriptions of one bit field (edited from the debugger)."""

    def __init__(self, parent, f, kind):
        self.f, self.kind = f, kind
        super().__init__(parent, tr("Register map: {0} {1}").format(f.name, f.bits), 620)

    def build(self):
        f = self.f
        self.fmt = FormatPicker(f.fmt, lambda: f.width, auto_text=tr("Automatic (1 bit: bool, else: hex)"))
        self.form.addRow(tr("Data type"), self.fmt)
        self.values = ValuesEditor(f.values, self.kind, lambda: f.width, self.fmt.value)
        self.fmt.combo.currentIndexChanged.connect(lambda *_: self.values.refresh())
        self.extra.addWidget(self.values, 1)

    def apply(self):
        return {"fmt": self.fmt.value(), "values": self.values.get()}
