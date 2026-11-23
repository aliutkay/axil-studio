#!/usr/bin/env python3
"""Command sequencer interface (cmd_seq): write and assemble programs, load them into I-mem,
run / step / stop, follow the PC in the source, registers, flags, errors and D-mem.

An AXIL Studio IP interface: AXIL Studio starts it from the UART-to-AXI Debug Bridge for a
command sequencer subsystem (register map "cmd_seq", see axil-interface.json). Every access
goes through the Debug Bridge's connection over the local service (axil_client.py).
"""

import concurrent.futures
import os
import sys

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QHeaderView, QMessageBox, QPlainTextEdit, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "sdk"))      # axil_client when run from the repository

import look  # noqa: E402
import seq_core as S  # noqa: E402
import seqasm as A  # noqa: E402
from axil_client import AxilClient, AxilError  # noqa: E402
from look import tr  # noqa: E402

TITLE = "Command sequencer"
MAX_DMEM_ROWS = 4096
REFRESH_MS = 250
RUN_TO_MAX_STEPS = 2000
NEW_PROGRAM = """; cmd_seq program
; Address names of the project: <INSTANCE>_<REGISTER> (e.g. UART0_PHY_CTRL),
; or .include "<project>_map.inc" (Address Map Editor: Assembler include).

        LDI   R1, 0
loop:   ADD   R1, 1
        STD   D[0], R1
        CMP   R1, 10
        BR    LO, loop
        END
"""


class Worker(QObject):
    """One worker thread for the requests to AXIL Studio; results come back on the GUI thread."""
    _done = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._closed = False
        self.pending = 0
        self._done.connect(self._deliver)

    def _emit_done(self, fut, ok, err):
        if self._closed:
            return
        try:
            self._done.emit(fut, ok, err)
        except RuntimeError:
            pass

    def run(self, fn, ok=None, err=None):
        if self._closed:
            return
        self.pending += 1
        fut = self._pool.submit(fn)
        fut.add_done_callback(lambda f: self._emit_done(f, ok, err))

    def _deliver(self, fut, ok, err):
        self.pending -= 1
        if self._closed or fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            if err:
                err(exc)
        elif ok:
            ok(fut.result())

    def shutdown(self):
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)


class SeqWindow(QWidget):
    _event = Signal(dict)

    def __init__(self, client: AxilClient):
        super().__init__()
        self.client = client
        self.setObjectName("Page")
        self.resize(1180, 800)
        self.bus = Worker(self)
        self.imem_aw, self.dmem_aw = 9, 8         # from CAPS once read
        self.syms = {}                            # address map names (client.address_map)
        self.prog, self.entry, self.pc_line = None, 0, {}
        self.card = None                          # (IMEM_CNT, IMEM_SUM) read from the card
        self.ctrl_bits = S.ERR_HALT
        self.last = None                          # last refresh: dict
        self.path = client.settings.get("program") or ""
        self._busy_op = False
        self._closing = False
        self._caps_text = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)
        bar = look.Card(margins=12)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(look.label(tr(TITLE), "title"))
        self.ip_lbl = look.label("", "muted")
        row.addWidget(self.ip_lbl)
        row.addSpacing(12)
        self.conn_pill = look.Pill("")
        row.addWidget(self.conn_pill)
        self.state_pill = look.Pill("")
        row.addWidget(self.state_pill)
        self.card_pill = look.Pill("")
        row.addWidget(self.card_pill)
        row.addStretch(1)
        row.addWidget(look.button(tr("Show in Debug Bridge"), None, lambda: self.show_in_bridge(),
                                  tr("Select this sequencer's registers in the UART-to-AXI Debug Bridge")))
        bar.lay.addLayout(row)
        outer.addWidget(bar)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        card = look.Card(margins=0)
        card.lay.addWidget(self.tabs)
        outer.addWidget(card, 1)
        self.tabs.addTab(self._build_program_tab(), tr("Program"))
        self.tabs.addTab(self._build_run_tab(), tr("Run"))
        self.tabs.addTab(self._build_dmem_tab(), tr("D-mem"))
        self.tabs.currentChanged.connect(lambda i: i == 2 and self.refresh(quiet=True))
        self.msg = look.label("", "muted", wrap=True)
        outer.addWidget(self.msg)

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self._tick)

        self._event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        client.on_event = self._event.emit
        self._load_text()
        self._update_title()
        self._on_connection()
        self._fetch_map()

    # ================================================================ AXIL Studio
    @property
    def ip(self):
        return self.client.ip

    @property
    def ip_name(self):
        return self.ip.get("name", "")

    @property
    def base(self):
        return int(self.ip.get("base", 0))

    @property
    def connected(self):
        return self.client.connected

    def _on_event(self, ev):
        kind = ev.get("event")
        if kind == "connection":
            self._on_connection()
        elif kind == "ip":
            self._update_title()
            self._fetch_map()
        elif kind == "ip_removed":
            QMessageBox.warning(self, tr(TITLE), tr("{0} is no longer in the address map").format(self.ip_name))
            self.close()
        elif kind == "theme":
            look.apply(QApplication.instance(), ev.get("theme"))
            self._highlight()
        elif kind == "raise":
            self.showNormal()
            self.raise_()
            self.activateWindow()
        elif kind == "closing":
            self._closing = True
            self.close()

    def _update_title(self):
        self.ip_lbl.setText(f"{self.ip_name} · 0x{self.base:08X}" + (f" · {self._caps_text}" if self._caps_text else ""))
        self.setWindowTitle(f"{self.ip_name} — {tr(TITLE)}")

    def _on_connection(self):
        if self.connected:
            self.conn_pill.set("● " + tr("Debug Bridge connected"), "ok")
            self.read_caps()
        else:
            self.conn_pill.set("● " + tr("Debug Bridge not connected"), "")
            self.auto.setChecked(False)
        self._update_controls()

    def _update_controls(self):
        on = self.connected and not self._busy_op
        for b in self._io:
            b.setEnabled(on)

    def _fetch_map(self):
        """Address map names for the assembler (AXIL Studio 0.1.28+)."""
        def ok(entries):
            self.syms = S.symbols(entries)
            self.sym_lbl.setText(tr("{0} address names from the project").format(len(self.syms)))

        def fail(exc):
            self.syms = {}
            self.sym_lbl.setText(tr("Address names: not available ({0})").format(exc))
        self.bus.run(self.client.address_map, ok, fail)

    def _call(self, fn, ok=None, what=None):
        if not self.connected:
            self.say(tr("Not connected"))
            return

        def fail(exc):
            self._busy_op = False
            self._update_controls()
            if isinstance(exc, AxilError) and exc.code == "closed":
                return
            if self.auto.isChecked():
                self.auto.setChecked(False)
            QMessageBox.critical(self, what or tr(TITLE), str(exc))
        self.bus.run(fn, ok, fail)

    def say(self, text):
        self.msg.setText(text)

    def show_in_bridge(self, reg=None):
        self.bus.run(lambda: self.client.show_register(reg))

    def closeEvent(self, ev):
        self.timer.stop()
        if self.editor.document().isModified() and not self._closing:
            if not look.confirm(self, tr(TITLE), tr("The program has unsaved changes. Close anyway?"), tr("Close")):
                ev.ignore()
                return
        self.client.on_event = lambda ev: None
        super().closeEvent(ev)

    # ================================================================ program tab
    def _build_program_tab(self):
        page = QWidget()
        page.setObjectName("Page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        self._io = []
        row = look.hline(
            look.button(tr("New"), "ghost", self.new_program),
            look.button(tr("Open…"), None, self.open_program),
            look.button(tr("Save"), None, self.save_program),
            look.button(tr("Save as…"), "ghost", self.save_program_as), 12,
            look.button(tr("Assemble"), None, self.assemble, tr("Assemble the program (F5)")),
            stretch=False)
        self.load_btn = look.button(tr("Load to I-mem"), "primary", self.load,
                                    tr("Stop the sequencer, write the program into I-mem and check IMEM_CNT / IMEM_SUM"))
        self.load_run_btn = look.button(tr("Load and run"), None, lambda: self.load(run=True))
        self._io += [self.load_btn, self.load_run_btn]
        row.addWidget(self.load_btn)
        row.addWidget(self.load_run_btn)
        row.addStretch(1)
        self.file_lbl = look.label("", "muted")
        row.addWidget(self.file_lbl)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.editor = QPlainTextEdit()
        self.editor.setFont(look.mono_font())
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.textChanged.connect(self._text_changed)
        split.addWidget(self.editor)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.listing = QTableWidget(0, 4)
        self.listing.setHorizontalHeaderLabels([tr("PC"), tr("Word"), tr("Instruction"), tr("Source")])
        self.listing.verticalHeader().hide()
        self.listing.setFont(look.mono_font(-1))
        self.listing.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.listing.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.listing.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.listing.cellDoubleClicked.connect(self._goto_listing)
        rl.addWidget(self.listing, 1)
        self.asm_lbl = look.label("", "muted", wrap=True)
        rl.addWidget(self.asm_lbl)
        self.sym_lbl = look.label("", "muted")
        rl.addWidget(self.sym_lbl)
        split.addWidget(right)
        split.setSizes([620, 520])
        lay.addWidget(split, 1)
        QShortcut(QKeySequence("F5"), self, self.assemble)
        QShortcut(QKeySequence.StandardKey.Save, self, self.save_program)
        return page

    def _load_text(self):
        text = NEW_PROGRAM
        if self.path:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                self.say(tr("Cannot read {0}: {1}").format(self.path, exc.strerror))
                self.path = ""
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._file_text()

    def _file_text(self):
        name = os.path.basename(self.path) if self.path else tr("(not saved)")
        self.file_lbl.setText(name + (" *" if self.editor.document().isModified() else ""))

    def _text_changed(self):
        self._file_text()
        if self.prog is not None:
            self.prog = None
            self.asm_lbl.setText(tr("Changed: assemble again"))
            self._update_card_pill()

    def new_program(self):
        if self.editor.document().isModified() and not look.confirm(
                self, tr(TITLE), tr("Discard the unsaved changes?"), tr("Discard")):
            return
        self.path = ""
        self.editor.setPlainText(NEW_PROGRAM)
        self.editor.document().setModified(False)
        self._file_text()

    def open_program(self):
        if self.editor.document().isModified() and not look.confirm(
                self, tr(TITLE), tr("Discard the unsaved changes?"), tr("Discard")):
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Open program"), os.path.dirname(self.path or ""),
                                              tr("Sequencer program (*.s *.asm);;All files (*)"))
        if path:
            self.path = path
            self._load_text()
            self._remember()

    def save_program(self):
        if not self.path:
            return self.save_program_as()
        try:
            with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.editor.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, tr(TITLE), str(exc))
            return False
        self.editor.document().setModified(False)
        self._file_text()
        self._remember()
        return True

    def save_program_as(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save program"), self.path or f"{self.ip_name.lower()}.s",
                                              tr("Sequencer program (*.s *.asm)"))
        if not path:
            return False
        self.path = path
        return self.save_program()

    def _remember(self, **extra):
        """The program file and the last loaded program are kept in the project (per sequencer)."""
        vals = {"program": self.path or None}
        vals.update(extra)
        if any(self.client.settings.get(k) != v for k, v in vals.items()):
            self.bus.run(lambda: self.client.set_settings(**vals))

    def assemble(self):
        base_dir = os.path.dirname(self.path) if self.path else os.getcwd()
        try:
            self.prog, self.entry, self.pc_line = S.assemble(self.editor.toPlainText(), self.imem_aw,
                                                             self.dmem_aw, self.syms, base_dir)
        except A.AsmError as exc:
            self.prog = None
            self.listing.setRowCount(0)
            self.asm_lbl.setText("✖ " + str(exc))
            self._mark_error(str(exc))
            self._update_card_pill()
            return False
        self.listing.setRowCount(len(self.prog))
        for i, (pc, w, raw) in enumerate(self.prog):
            for c, text in enumerate((str(pc), f"{w:016X}", A.disasm(w), raw)):
                self.listing.setItem(i, c, QTableWidgetItem(text))
        self.listing.resizeColumnsToContents()
        self.asm_lbl.setText(tr("{0} instructions · IMEM_SUM 0x{1:08X} · ENTRY {2} · I-mem {3}").format(
            len(self.prog), A.imem_sum(self.prog), self.entry, 1 << self.imem_aw))
        self.entry_spin.setValue(self.entry)
        self._mark_error(None)
        self._update_card_pill()
        self._highlight()
        return True

    def _mark_error(self, text):
        self._err_line = None
        if text:
            import re
            m = re.match(r"line (\d+)", text)
            if m:
                self._err_line = int(m.group(1))
        self._highlight()

    def _goto_listing(self, row, _col):
        if self.prog and 0 <= row < len(self.prog):
            self._goto_line(self.pc_line.get(self.prog[row][0]))

    def _goto_line(self, no):
        if not no:
            return
        block = self.editor.document().findBlockByNumber(no - 1)
        cur = QTextCursor(block)
        self.editor.setTextCursor(cur)
        self.editor.centerCursor()

    def _highlight(self):
        """PC line (from the last refresh) and the assembler error line."""
        sels = []
        t = look.tokens()

        def add(no, color):
            if not no:
                return
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(color))
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = QTextCursor(self.editor.document().findBlockByNumber(no - 1))
            sels.append(sel)
        if self.last and self.prog is not None and self.last["status"].state in ("PAUSED", "RUNNING", "ERROR", "DONE"):
            pc = self.last["status"].pc
            add(self.pc_line.get(pc), t.get("selection", "#CCD6E7"))
            for r in range(self.listing.rowCount()):
                it = self.listing.item(r, 0)
                if it is not None:
                    hit = self.prog[r][0] == pc
                    for c in range(4):
                        cell = self.listing.item(r, c)
                        if cell is not None:
                            cell.setBackground(QColor(t.get("selection", "#CCD6E7")) if hit else QColor(0, 0, 0, 0))
        add(getattr(self, "_err_line", None), "#F6C6C6")
        self.editor.setExtraSelections(sels)

    def _update_card_pill(self):
        if self.card is None:
            self.card_pill.set("", "")
            return
        cnt, total = self.card
        if self.prog is None:
            self.card_pill.set(tr("I-mem: {0} instructions").format(cnt), "")
        elif cnt == len(self.prog) and total == A.imem_sum(self.prog):
            self.card_pill.set(tr("I-mem = this program"), "ok")
        else:
            self.card_pill.set(tr("I-mem ≠ this program"), "warn")

    # ---------------------------------------------------------------- loading
    def load(self, run=False):
        if self.prog is None and not self.assemble():
            return
        if self.editor.document().isModified() and self.path:
            self.save_program()
        prog, entry = self.prog, self.entry_spin.value()
        batches = S.load_batches(prog, self.ctrl_bits)
        self._busy_op = True
        self._update_controls()
        self.auto.setChecked(False)
        self.say(tr("Loading {0} instructions…").format(len(prog)))

        def job():
            self.client.lock()
            try:
                res = None
                for b in batches:
                    res = self.client.batch(b, quiet=True)
                cnt, total, st = res
                errs = S.verify_load(prog, cnt, total, st)
                if not errs:
                    self.client.batch([("write", S.ENTRY, entry)] +
                                      ([("write", S.CTRL, self.ctrl_bits | S.RUN)] if run else []))
                return cnt, total, errs
            finally:
                self.client.unlock()

        def ok(r):
            cnt, total, errs = r
            self._busy_op = False
            self._update_controls()
            self.card = (cnt, total)
            self._update_card_pill()
            if errs:
                QMessageBox.warning(self, tr("Load"), tr("The program was not loaded correctly:\n{0}\n\nLoad it again.").format("\n".join(errs)))
                self.say(tr("Load failed"))
                return
            self._remember(loaded_sum=total, loaded_cnt=cnt)
            self.say(tr("Loaded: {0} instructions, IMEM_SUM 0x{1:08X} checked").format(cnt, total) +
                     (tr(" · running") if run else ""))
            self.refresh()
            if run:
                self.auto.setChecked(True)
        self._call(job, ok, tr("Load"))

    # ================================================================ run tab
    def _build_run_tab(self):
        page = QWidget()
        page.setObjectName("Page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        ctl = look.Card(tr("Control"))
        b_run = look.button(tr("Run"), "primary", lambda: self.pulse(S.RUN), tr("RUN: start at ENTRY, or continue when paused"))
        b_stop = look.button(tr("Stop"), None, lambda: self.pulse(S.STOP), tr("STOP: pause at the next instruction"))
        b_step = look.button(tr("Step"), None, lambda: self.pulse(S.STEP), tr("STEP: run one instruction"))
        b_reset = look.button(tr("Reset"), None, lambda: self.pulse(S.RESET), tr("RESET: core to IDLE, R0–R15 cleared"))
        b_to = look.button(tr("Run to line"), "ghost", self.run_to_line,
                           tr("Step until the PC reaches the instruction of the editor's cursor line"))
        self._io += [b_run, b_stop, b_step, b_reset, b_to]
        self.entry_spin = QSpinBox()
        self.entry_spin.setRange(0, (1 << 16) - 1)
        row = look.hline(b_run, b_stop, b_step, b_reset, b_to, 16, look.label(tr("ENTRY"), "muted"), self.entry_spin)
        ctl.lay.addLayout(row)
        self.bit_boxes = {}
        brow = QHBoxLayout()
        for bit, name, tip in ((S.ERR_HALT, "ERR_HALT", tr("Stop on a bus error")),
                               (S.SNAP_EN, "SNAP_EN", tr("Two D-mem banks, swapped by SNAP")),
                               (S.PT_WR_RUN, "PT_WR_RUN", tr("Allow pass-through writes while the program runs")),
                               (S.IRQ_EN, "IRQ_EN", tr("Interrupt output enable"))):
            cb = QCheckBox(name)
            cb.setToolTip(tip)
            cb.setChecked(bool(self.ctrl_bits & bit))
            cb.toggled.connect(self._bits_changed)
            self.bit_boxes[bit] = cb
            brow.addWidget(cb)
            self._io.append(cb)
        brow.addStretch(1)
        self.auto = QCheckBox(tr("Follow (refresh {0} ms)").format(REFRESH_MS))
        self.auto.toggled.connect(lambda on: self.timer.start() if on else self.timer.stop())
        brow.addWidget(self.auto)
        b_ref = look.button(tr("Refresh"), None, self.refresh)
        self._io.append(b_ref)
        brow.addWidget(b_ref)
        ctl.lay.addLayout(brow)
        lay.addWidget(ctl)

        mid = QHBoxLayout()
        st = look.Card(tr("State"))
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        self.st_vals = {}
        for i, (k, label) in enumerate((("state", tr("State")), ("pc", "PC"), ("line", tr("Source")),
                                        ("flags", tr("Flags")), ("err", tr("Error")), ("err_pc", "ERR_PC"),
                                        ("err_addr", "ERR_ADDR"), ("irq", "IRQ_STAT"), ("snap", "SNAP_CNT"),
                                        ("timer", "TIMER"), ("drain", tr("Lower bus")))):
            g.addWidget(look.label(label, "muted"), i, 0)
            v = look.label("—", "mono")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(v, i, 1)
            self.st_vals[k] = v
        st.lay.addLayout(g)
        b_irq = look.button(tr("Clear IRQ_STAT"), "ghost", lambda: self._write([(S.IRQ_STAT, 7)], self.refresh))
        self._io.append(b_irq)
        st.lay.addWidget(b_irq)
        st.lay.addStretch(1)
        mid.addWidget(st, 1)
        rc = look.Card(tr("Registers"))
        self.regs = QTableWidget(16, 3)
        self.regs.setHorizontalHeaderLabels([tr("Register"), tr("Hex"), tr("Decimal")])
        self.regs.verticalHeader().hide()
        self.regs.setFont(look.mono_font())
        self.regs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for i in range(16):
            self.regs.setItem(i, 0, QTableWidgetItem(f"R{i}"))
            self.regs.setItem(i, 1, QTableWidgetItem("—"))
            self.regs.setItem(i, 2, QTableWidgetItem(""))
        self.regs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        rc.lay.addWidget(self.regs)
        mid.addWidget(rc, 1)
        lay.addLayout(mid, 1)
        return page

    def _write(self, writes, ok=None):
        self._call(lambda: self.client.batch([("write", o, v) for o, v in writes]), lambda _: ok and ok(),
                   tr("Write error"))

    def _bits_changed(self):
        self.ctrl_bits = sum(bit for bit, cb in self.bit_boxes.items() if cb.isChecked())
        self._write([(S.CTRL, self.ctrl_bits)])

    def pulse(self, bit):
        ops = [(S.CTRL, self.ctrl_bits | bit)]
        if bit == S.RUN and self.last and self.last["status"].state in ("IDLE", "DONE", "ERROR"):
            ops.insert(0, (S.ENTRY, self.entry_spin.value()))
        self._write(ops, self.refresh)
        if bit == S.RUN:
            self.auto.setChecked(True)

    def run_to_line(self):
        """Software breakpoint: STEP until the PC is the instruction of the cursor line."""
        if self.prog is None:
            self.say(tr("Assemble the program first"))
            return
        no = self.editor.textCursor().blockNumber() + 1
        target = next((pc for pc, line in sorted(self.pc_line.items()) if line == no), None)
        if target is None:
            self.say(tr("No instruction on line {0}").format(no))
            return
        self._busy_op = True
        self._update_controls()
        bits = self.ctrl_bits

        def job():
            st = S.Status(self.client.read(S.STATUS, quiet=True))
            if st.state == "RUNNING":
                self.client.write(S.CTRL, bits | S.STOP)
            for n in range(RUN_TO_MAX_STEPS):
                st = S.Status(self.client.read(S.STATUS, quiet=True))
                if st.state in ("DONE", "ERROR") or (st.state == "PAUSED" and st.pc == target):
                    return st, n
                self.client.write(S.CTRL, bits | S.STEP, quiet=True)
            return st, RUN_TO_MAX_STEPS

        def ok(r):
            st, n = r
            self._busy_op = False
            self._update_controls()
            if st.pc == target and st.state == "PAUSED":
                self.say(tr("Stopped at line {0} (PC {1}) after {2} steps").format(no, target, n))
            else:
                self.say(tr("Line {0} not reached: {1} after {2} steps").format(no, st.state, n))
            self.refresh()
        self._call(job, ok, tr("Run to line"))

    # ---------------------------------------------------------------- refresh
    def read_caps(self):
        def ok(v):
            ident, cap = v
            if ident >> 16 != S.ID_SIGNATURE >> 16:
                self.say(tr("ID 0x{0:08X}: this is not a cmd_seq block").format(ident))
                return
            self.imem_aw, self.dmem_aw, _, sid = S.caps(cap)
            self.entry_spin.setMaximum((1 << self.imem_aw) - 1)
            self._caps_text = tr("I-mem {0} · D-mem {1} words · v{2}.{3}").format(
                1 << self.imem_aw, 1 << self.dmem_aw, (ident >> 8) & 0xFF, ident & 0xFF)
            self._update_title()
            self.dmem.setRowCount(min(1 << self.dmem_aw, MAX_DMEM_ROWS))
            for i in range(self.dmem.rowCount()):
                self.dmem.setItem(i, 0, self._ro(str(i)))
                self.dmem.setItem(i, 1, self._ro(f"0x{(self.base + S.DMEM_BASE + 4 * i) & 0xFFFFFFFF:08X}"))
            self.refresh()
        self._call(lambda: self.client.batch([("read", S.ID), ("read", S.CAPS)], quiet=True), ok)

    REFRESH_OPS = [S.STATUS, S.ERR_CODE, S.ERR_PC, S.ERR_ADDR, S.IRQ_STAT, S.SNAP_CNT, S.TIMER, S.CTRL,
                   S.IMEM_CNT, S.IMEM_SUM] + [S.REG_BASE + 4 * i for i in range(16)]

    def _tick(self):
        if self.bus.pending == 0 and not self._busy_op:
            self.refresh(quiet=True)

    def refresh(self, quiet=False):
        if not self.connected:
            return
        ops = [("read", o) for o in self.REFRESH_OPS]
        dm = self.tabs.currentIndex() == 2
        if dm:
            ops += [("read", S.DMEM_BASE + 4 * i) for i in range(self.dmem.rowCount())]
        self._call(lambda: self.client.batch(ops, quiet=True), lambda v: self._show(v, dm))

    def _show(self, v, with_dmem):
        st = S.Status(v[0])
        err_code, err_pc, err_addr, irq, snap, tim, ctrl, cnt, total = v[1:10]
        regs = v[10:26]
        self.last = {"status": st, "regs": regs}
        self.card = (cnt, total)
        self._update_card_pill()
        state_kind = {"RUNNING": "accent", "PAUSED": "warn", "DONE": "ok", "ERROR": "err"}.get(st.state, "")
        self.state_pill.set(tr(st.state.capitalize()), state_kind)
        no = self.pc_line.get(st.pc) if self.prog is not None else None
        src = ""
        if no:
            block = self.editor.document().findBlockByNumber(no - 1)
            src = f"{no}: {block.text().strip()}"
        vals = {"state": tr(st.state.capitalize()), "pc": str(st.pc), "line": src or "—",
                "flags": st.flags_text(),
                "err": (S.err_text(err_code) if st.err else "—"), "err_pc": str(err_pc),
                "err_addr": f"0x{err_addr:08X}",
                "irq": " ".join(n for b, n in ((1, "DONE"), (2, "ERR"), (4, "IRQ")) if irq & b) or "—",
                "snap": str(snap), "timer": str(tim),
                "drain": tr("abandoned transfer in flight") if (st.wr_drain or st.rd_drain) else tr("idle")}
        for k, text in vals.items():
            self.st_vals[k].setText(text)
        for i, r in enumerate(regs):
            self.regs.item(i, 1).setText(f"0x{r:08X}")
            self.regs.item(i, 2).setText(str(r if r < 0x80000000 else r - (1 << 32)))
        bits = ctrl & S.SETTING_BITS
        if bits != self.ctrl_bits:
            self.ctrl_bits = bits
            for bit, cb in self.bit_boxes.items():
                cb.blockSignals(True)
                cb.setChecked(bool(bits & bit))
                cb.blockSignals(False)
        if with_dmem:
            for i, d in enumerate(v[26:]):
                self._set_dmem(i, d)
        if self.auto.isChecked() and st.state in ("DONE", "ERROR", "IDLE"):
            self.auto.setChecked(False)
        self._highlight()

    # ================================================================ D-mem tab
    def _build_dmem_tab(self):
        page = QWidget()
        page.setObjectName("Page")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        b = look.button(tr("Read"), None, self.refresh)
        self._io.append(b)
        lay.addLayout(look.hline(b, look.label(tr("Double-click a value to write it. With SNAP_EN the published bank is shown."), "muted")))
        self.dmem = QTableWidget(0, 4)
        self.dmem.setHorizontalHeaderLabels([tr("Index"), tr("Address"), tr("Hex"), tr("Decimal")])
        self.dmem.verticalHeader().hide()
        self.dmem.setFont(look.mono_font())
        self.dmem.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.dmem.itemChanged.connect(self._dmem_edited)
        lay.addWidget(self.dmem, 1)
        return page

    @staticmethod
    def _ro(text):
        it = QTableWidgetItem(text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return it

    def _set_dmem(self, i, v):
        self.dmem.blockSignals(True)
        self.dmem.setItem(i, 2, QTableWidgetItem(f"0x{v:08X}"))
        self.dmem.setItem(i, 3, self._ro(str(v)))
        self.dmem.blockSignals(False)

    def _dmem_edited(self, item):
        if item.column() != 2:
            return
        try:
            v = int(item.text(), 0) & 0xFFFFFFFF
        except ValueError:
            self.say(tr("Not a number: {0}").format(item.text()))
            return
        i = item.row()
        self._write([(S.DMEM_BASE + 4 * i, v)], lambda: self._set_dmem(i, v))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("cmd_seq interface")
    try:
        client = AxilClient.from_env("cmd_seq interface")
    except (AxilError, OSError) as exc:
        look.use_language(os.environ.get("AXIL_STUDIO_LANG", "en"))
        QMessageBox.critical(None, tr(TITLE), tr("This window is opened from AXIL Studio's UART-to-AXI Debug "
                                                 "Bridge (IP interface of cmd_seq).") + f"\n\n{exc}")
        return 2
    look.use_language(client.lang)
    look.apply(app, client.theme)
    w = SeqWindow(client)
    w.show()
    code = app.exec()
    w.bus.shutdown()
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
