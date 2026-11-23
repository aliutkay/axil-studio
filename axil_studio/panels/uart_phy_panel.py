"""UART interface of axiluart_phy: line settings, terminal and hardware validation.

Opened from the UART-to-AXI Debug Bridge for an IP whose register map names the "axiluart_phy"
interface. Every register access goes through the Debug Bridge's connection and request queue.
"""

import json
import os
import threading
import time

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPlainTextEdit,
                               QRadioButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QButtonGroup)

from axil_studio import ROOT, theme, ui
from axil_studio.core import hexbus
from axil_studio.core import uart_phy as U
from axil_studio.core import uart_validate as V
from axil_studio.core.model import hex32, load_config, save_config
from axil_studio.i18n import tr
from axil_studio.panels import PanelInfo, register

BAUDS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600",
         "1000000", "2000000"]
PARITY_LABELS = [("N", tr("None")), ("E", tr("Even")), ("O", tr("Odd")), ("M", tr("Mark (1)")), ("S", tr("Space (0)"))]
DE_LABELS = [("low", tr("DE low (driver off)")), ("high", tr("DE high (driver always on)")),
             ("tx", tr("DE while transmitting"))]
MAX_RX_PER_POLL = 256
MAX_TX_CHUNK = 64
TERM_BLOCKS = 5000


def _hline(*widgets, stretch=True):
    row = QHBoxLayout()
    row.setSpacing(8)
    for w in widgets:
        if isinstance(w, int):
            row.addSpacing(w)
        else:
            row.addWidget(w)
    if stretch:
        row.addStretch(1)
    return row


class UartPhyPanel(QWidget):
    """Window of one axiluart_phy instance."""

    _row = Signal(dict)            # validation result (worker thread -> GUI)
    _vlog = Signal(str)

    def __init__(self, host, group, ip_name):
        super().__init__(host, Qt.WindowType.Window)       # closes with the Debug Bridge
        self.host, self.group, self.ip_name = host, group, ip_name
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1080, 760)
        self._store = QSettings()
        self.validating = False
        self.cancel = threading.Event()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        # ---- header: IP, address, clock, connection, back to the Debug Bridge
        self.header = ui.ToolHeader(tr("UART interface"), "debug", "", tr("axiluart_phy: line settings, terminal and validation"))
        self.ip_lbl = ui.label("", "muted")
        self.header.add_right(self.ip_lbl)
        outer.addWidget(self.header)

        bar = ui.Card(margins=12)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(ui.label(tr("ACLK"), "muted"))
        self.clk = QDoubleSpinBox()
        self.clk.setRange(0.1, 1000.0)
        self.clk.setDecimals(3)
        self.clk.setSuffix(" MHz")
        self.clk.setToolTip(tr("Clock of the UART IP (S_AXI_ACLK): baud dividers and DE timing are computed from it"))
        self.clk.setValue(float(self._settings().get("clk_mhz", 50.0)))
        self.clk.valueChanged.connect(self._clk_changed)
        row.addWidget(self.clk)
        row.addSpacing(16)
        self.conn_pill = ui.Pill("")
        row.addWidget(self.conn_pill)
        row.addStretch(1)
        row.addWidget(ui.button(tr("Show in Debug Bridge"), "arrow", "ghost", self.show_in_bridge,
                                tr("Select this IP's registers in the UART-to-AXI Debug Bridge")))
        bar.lay.addLayout(row)
        outer.addWidget(bar)

        self.tabs = ui.DetachableTabs(tr("UART interface"))
        card = ui.Card(margins=0)
        card.lay.addWidget(self.tabs)
        outer.addWidget(card, 1)
        self.tabs.addTab(self._build_line_tab(), tr("Line settings"))
        self.tabs.addTab(self._build_terminal_tab(), tr("Terminal"))
        self.tabs.addTab(self._build_validation_tab(), tr("Validation"))

        self._row.connect(self._add_result)
        self._vlog.connect(self._append_vlog)
        host.connection_changed.connect(self._on_connection)
        host.ctx.modified.connect(self._on_project)
        theme.manager().changed.connect(self._on_theme)
        self._update_title()
        self._form_changed()
        self._line_changed()
        self._on_connection(host.link.connected)
        self._on_theme()

    # ================================================================ common
    @property
    def ip(self):
        return self.host.find_ip(self.group, self.ip_name)

    @property
    def connected(self):
        return self.host.link.connected

    def _addr(self, off):
        ip = self.ip
        if ip is None:
            raise hexbus.HexbusError(tr("{0} is no longer in the address map").format(self.ip_name))
        return ip.base + off

    def _settings(self):
        d = (self.host.ctx.project.debug or {}).get("panels", {})
        return dict(d.get(self.ip_name, {}))

    def _save_settings(self, **kw):
        """Board facts (ACLK, lead / tail) are kept in the project, under the Debug Bridge settings."""
        cur = self._settings()
        if all(cur.get(k) == v for k, v in kw.items()):
            return
        cur.update(kw)
        dbg = dict(self.host.ctx.project.debug or {})
        panels = dict(dbg.get("panels", {}))
        panels[self.ip_name] = cur
        dbg["panels"] = panels
        self.host.ctx.project.debug = dbg
        self.host.ctx.mark_modified("uart_panel")

    @property
    def clk_hz(self):
        return self.clk.value() * 1e6

    def _clk_changed(self):
        self._form_changed()
        self._line_changed()

    def _update_title(self):
        ip = self.ip
        base = hex32(ip.base) if ip else "—"
        self.ip_lbl.setText(f"{self.ip_name} · {base}")
        self.setWindowTitle(f"{self.ip_name} — {tr('UART interface')}")

    def _on_project(self, _source=None):
        if self.ip is None:
            self.close()
            return
        self._update_title()

    def _on_connection(self, on):
        if on:
            self.conn_pill.set("● " + tr("Debug Bridge connected"), "ok")
        else:
            self.conn_pill.set("● " + tr("Debug Bridge not connected"), "off")
            self.stop_rx()
            self.tx_queue.clear()
        self._update_controls()

    def _update_controls(self):
        on = self.connected and not self.validating
        for b in self._io_buttons:
            b.setEnabled(on)
        self.rx_btn.setEnabled(on or self.rx_running)
        self.send_btn.setEnabled(on)
        self.file_btn.setEnabled(on)
        self.run_btn.setEnabled(self.connected and not self.validating)
        self.stop_btn.setEnabled(self.validating)
        for w in self._val_inputs:
            w.setEnabled(not self.validating)
        self._update_stats()

    def _run(self, fn, ok=None, what=tr("UART interface")):
        """One request on the Debug Bridge's queue."""
        if not self.connected:
            self.host.status(tr("Not connected"))
            return
        self.host.bus.run(fn, ok, lambda e: QMessageBox.critical(self, what, str(e)))

    def _read(self, off, ok, src=tr("UART interface")):
        addr = self._addr(off)

        def done(v):
            self.host.record(addr, v, src)
            ok(v)
        self._run(lambda: self.host.link.read(addr), done, tr("Read error"))

    def _write(self, writes, ok=None):
        """writes: [(offset, value)] in order, one request."""
        pairs = [(self._addr(off), v & 0xFFFFFFFF) for off, v in writes]

        def job():
            for a, v in pairs:
                self.host.link.write(a, v)
        self._run(job, ok, tr("Write error"))

    def show_in_bridge(self, reg=None):
        ip = self.ip
        if ip is not None:
            self.host.show_register(self.group, self.ip_name, reg or (ip.registers[0].name if ip.registers else None))

    def closeEvent(self, ev):
        if self.validating:
            if not ui.confirm(self, tr("Validation is running"), tr("Stop the validation and close the window?"),
                              tr("Stop and close")):
                ev.ignore()
                return
            self.cancel.set()
        self.stop_rx()
        try:
            self.host.connection_changed.disconnect(self._on_connection)
            self.host.ctx.modified.disconnect(self._on_project)
            theme.manager().changed.disconnect(self._on_theme)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(ev)

    def _on_theme(self):
        ui.refresh_icons(self)
        m = theme.manager()
        self.fmt_rx = self._cf(m.color("text"))
        self.fmt_tx = self._cf(m.color("accent"))
        self.fmt_ctl = self._cf(m.color("warning"))
        self.fmt_time = self._cf(m.color("muted"))
        self.fmt_err = self._cf(m.color("danger"), bold=True)
        self.fmt_bad = self._cf(QColor("#FFFFFF"), m.color("danger"))
        self.term.setFont(theme.mono_font())
        self.vlog.setFont(theme.mono_font(-1))
        self._color_results()

    @staticmethod
    def _cf(fg, bg=None, bold=False):
        f = QTextCharFormat()
        f.setForeground(fg)
        if bg is not None:
            f.setBackground(bg)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    # ================================================================ line settings
    def _build_line_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        self._io_buttons = []
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # -- character format
        fmt = ui.Card(tr("Character format"), "SETUP · " + tr("baud divider, data bits, parity, stop bits"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText("115200")
        self.data_bits = QComboBox()
        self.data_bits.addItems(["8", "7", "6", "5"])
        self.parity = QComboBox()
        for key, text in PARITY_LABELS:
            self.parity.addItem(text, key)
        self.stop_bits = QComboBox()
        self.stop_bits.addItems(["1", "2"])
        self.flow = QCheckBox(tr("RTS/CTS flow control"))
        self.flow.setToolTip(tr("Only when the IP has RTS/CTS pins (PHY_STATUS HAS_FLOW)"))
        form.addRow(tr("Baud"), self.baud)
        form.addRow(tr("Data bits"), self.data_bits)
        form.addRow(tr("Parity"), self.parity)
        form.addRow(tr("Stop bits"), self.stop_bits)
        form.addRow("", self.flow)
        fmt.lay.addLayout(form)
        self.setup_info = ui.label("", "mono", wrap=True)
        fmt.lay.addWidget(self.setup_info)
        fmt.lay.addStretch(1)
        b1 = ui.button(tr("Read value"), "read", None, self.read_setup, tr("Read SETUP from the board into the form"))
        b2 = ui.button(tr("Write"), "write", "primary", self.write_setup, tr("Write SETUP (resets the receiver and the transmitter)"))
        self._io_buttons += [b1, b2]
        fmt.lay.addLayout(_hline(b1, b2))
        for w in (self.data_bits, self.parity, self.stop_bits):
            w.currentIndexChanged.connect(self._form_changed)
        self.baud.currentTextChanged.connect(self._form_changed)
        self.flow.toggled.connect(self._form_changed)
        cols.addWidget(fmt, 1)

        # -- line interface
        line = ui.Card(tr("Line interface"), "PHY_CTRL · PHY_TIMING · " + tr("transceiver control"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.line_type = QComboBox()
        for key, lt in U.LINE_TYPES.items():
            self.line_type.addItem(lt["label"], key)
            self.line_type.setItemData(self.line_type.count() - 1, lt["desc"], Qt.ItemDataRole.ToolTipRole)
        self.line_type.addItem(tr("Custom"), "")
        self.de_mode = QComboBox()
        for key, text in DE_LABELS:
            self.de_mode.addItem(text, key)
        form.addRow(tr("Line type"), self.line_type)
        form.addRow(tr("DE mode"), self.de_mode)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.half = QCheckBox(tr("Half duplex"))
        self.half.setToolTip(tr("Receiver off while the driver is on (2-wire bus)"))
        self.echo = QCheckBox(tr("Echo"))
        self.echo.setToolTip(tr("Half duplex only: keep receiving while driving (own bytes come back)"))
        self.tx_inv = QCheckBox(tr("Invert TX"))
        self.rx_inv = QCheckBox(tr("Invert RX"))
        self.tx_en = QCheckBox(tr("TX enabled"))
        self.rx_en = QCheckBox(tr("RX enabled"))
        self.tx_en.setChecked(True)
        self.rx_en.setChecked(True)
        for i, w in enumerate((self.half, self.echo, self.tx_inv, self.rx_inv, self.tx_en, self.rx_en)):
            grid.addWidget(w, i // 2, i % 2)
        form.addRow(tr("Options"), grid)
        self.lead = QDoubleSpinBox()
        self.lead.setRange(0, 100000)
        self.lead.setDecimals(2)
        self.lead.setSuffix(" µs")
        self.lead.setToolTip(tr("DE on → start bit; at least twice the transceiver's enable time (tZH)"))
        self.tail = QDoubleSpinBox()
        self.tail.setRange(0, 100000)
        self.tail.setDecimals(2)
        self.tail.setSuffix(" µs")
        self.tail.setToolTip(tr("End of the stop bit → DE off"))
        self.tail_auto = QCheckBox(tr("1 bit time"))
        self.tail_auto.setToolTip(tr("Tail = one bit time at the selected baud rate"))
        st = self._settings()
        self.lead.setValue(float(st.get("lead_us", 10.0)))
        self.tail.setValue(float(st.get("tail_us", 10.0)))
        self.tail_auto.setChecked(bool(st.get("tail_auto", True)))
        form.addRow(tr("DE lead"), self.lead)
        tail_row = QHBoxLayout()
        tail_row.addWidget(self.tail, 1)
        tail_row.addWidget(self.tail_auto)
        form.addRow(tr("DE tail"), tail_row)
        self.apply_now = QCheckBox(tr("Apply now (also while TX is busy)"))
        self.apply_now.setToolTip(tr("PHY_CTRL APPLY_NOW; otherwise the setting waits until the transmitter is idle"))
        form.addRow("", self.apply_now)
        line.lay.addLayout(form)
        self.line_info = ui.label("", "mono", wrap=True)
        line.lay.addWidget(self.line_info)
        line.lay.addStretch(1)
        b1 = ui.button(tr("Read value"), "read", None, self.read_line, tr("Read PHY_CTRL and PHY_TIMING into the form"))
        b2 = ui.button(tr("Write"), "write", "primary", self.write_line, tr("Write PHY_TIMING and PHY_CTRL"))
        self._io_buttons += [b1, b2]
        line.lay.addLayout(_hline(b1, b2))
        self.line_type.currentIndexChanged.connect(self._line_type_picked)
        for w in (self.de_mode,):
            w.currentIndexChanged.connect(self._line_changed)
        for w in (self.half, self.echo, self.tx_inv, self.rx_inv, self.tx_en, self.rx_en, self.tail_auto):
            w.toggled.connect(self._line_changed)
        for w in (self.lead, self.tail):
            w.valueChanged.connect(self._line_changed)
        self._loading_line = False
        self._line_type_picked()
        cols.addWidget(line, 1)

        # -- status
        stat = ui.Card(tr("Status"), "PHY_STATUS · FIFO")
        self.status_grid = QGridLayout()
        self.status_grid.setHorizontalSpacing(12)
        self.status_vals = {}
        for i, (key, text) in enumerate((("caps", tr("Pins")), ("state", tr("Line state")), ("cfg", tr("Setting error")),
                                         ("rx", tr("RX FIFO")), ("tx", tr("TX FIFO")))):
            self.status_grid.addWidget(ui.label(text, "muted"), i, 0)
            v = ui.label("—")
            v.setWordWrap(True)
            self.status_vals[key] = v
            self.status_grid.addWidget(v, i, 1)
        self.status_grid.setColumnStretch(1, 1)
        stat.lay.addLayout(self.status_grid)
        stat.lay.addStretch(1)
        b1 = ui.button(tr("Read value"), "read", None, self.read_status, tr("Read PHY_STATUS and FIFO"))
        b2 = ui.button(tr("Clear CFG_ERR"), None, "ghost", lambda: self._write([(U.PHY_STATUS, U.ST_CFG_ERR)], lambda _: self.read_status()))
        b3 = ui.button(tr("RX reset"), None, "ghost", self.rx_reset, tr("Reset the receiver and the RX FIFO (clears OVFL)"))
        b4 = ui.button(tr("TX reset"), None, "ghost", lambda: self._write([(U.TXREG, U.TX_RESET)], lambda _: self.read_status()),
                       tr("Reset the transmitter and the TX FIFO"))
        self._io_buttons += [b1, b2, b3, b4]
        stat.lay.addLayout(_hline(b1))
        stat.lay.addLayout(_hline(b2, b3, b4))
        cols.addWidget(stat, 1)
        lay.addLayout(cols, 1)

        # -- presets
        pre = ui.Card(margins=12)
        b_all = ui.button(tr("Read all"), "read", None, self.read_all, tr("Read SETUP, PHY_CTRL, PHY_TIMING and the status"))
        b_wall = ui.button(tr("Write all"), "write", None, self.write_all, tr("Write PHY_TIMING, PHY_CTRL and SETUP"))
        self._io_buttons += [b_all, b_wall]
        pre.lay.addLayout(_hline(
            b_all, b_wall, 16,
            ui.button(tr("Open preset…"), "folder", "ghost", self.open_preset, tr("Load a line preset (.axcfg.json) into the form")),
            ui.button(tr("Save preset…"), "save", "ghost", self.save_preset, tr("Save the form as a line preset (.axcfg.json)")),
            ui.button(tr("Add to config list"), "plus", "ghost", self.add_to_config,
                      tr("Add PHY_TIMING, PHY_CTRL and SETUP to the Debug Bridge config list"))))
        pre.lay.addWidget(ui.note(tr("Presets are Debug Bridge config files: they can also be sent from its Config tab.")))
        lay.addWidget(pre)
        return page

    # ---------------------------------------------------------------- form <-> values
    def _fmt(self):
        par = self.parity.currentData()
        return U.Fmt(f"{self.data_bits.currentText()}{par}{self.stop_bits.currentText()}")

    def _baud(self):
        try:
            b = int(float(self.baud.currentText()))
            return b if b > 0 else None
        except ValueError:
            return None

    def _setup_value(self):
        baud = self._baud()
        if baud is None:
            raise ValueError(tr("Invalid baud rate"))
        return U.setup_value(self.clk_hz, baud, self._fmt(), no_flow=not self.flow.isChecked())

    def _form_changed(self, *_):
        try:
            v = self._setup_value()
            baud = self._baud()
            div = v & U.SETUP_BAUD_MASK
            err = U.baud_error(self.clk_hz, baud)
            warn = "  ⚠" if abs(err) > 2 else ""
            self.setup_info.setText(tr("SETUP = {0} · divider {1} · real baud {2:.1f} ({3:+.2f} %){4}").format(
                hex32(v), div, self.clk_hz / div, err, warn))
        except ValueError as exc:
            self.setup_info.setText(str(exc))
        if hasattr(self, "tail_auto"):
            self._line_changed()

    def _ctrl_value(self):
        return U.ctrl_value(self.de_mode.currentData(), self.half.isChecked(), self.echo.isChecked(),
                            self.tx_inv.isChecked(), self.rx_inv.isChecked(), self.tx_en.isChecked(),
                            self.rx_en.isChecked())

    def _tail_us(self):
        if self.tail_auto.isChecked():
            baud = self._baud() or 115200
            return 1e6 / baud
        return self.tail.value()

    def _timing_value(self):
        return U.timing_value(self.clk_hz, self.lead.value(), self._tail_us())

    def _line_type_picked(self):
        key = self.line_type.currentData()
        if not key or self._loading_line:
            self._line_changed()
            return
        lt = U.LINE_TYPES[key]
        self._loading_line = True
        self.de_mode.setCurrentIndex(self.de_mode.findData(lt["de"]))
        self.half.setChecked(lt["half"])
        self.echo.setChecked(lt["echo"])
        self._loading_line = False
        self._line_changed()

    def _line_changed(self, *_):
        if not hasattr(self, "line_info"):
            return
        self.echo.setEnabled(self.half.isChecked())
        self.tail.setEnabled(not self.tail_auto.isChecked())
        de_tx = self.de_mode.currentData() == "tx"
        self.lead.setEnabled(de_tx)
        if not self._loading_line:
            lt = U.line_type_of(self._ctrl_value())
            i = self.line_type.findData(lt or "")
            if i != self.line_type.currentIndex():
                self.line_type.blockSignals(True)
                self.line_type.setCurrentIndex(i)
                self.line_type.blockSignals(False)
        ctrl, timing = self._ctrl_value(), self._timing_value()
        t = U.decode_timing(timing, self.clk_hz)
        txt = tr("PHY_CTRL = {0} · PHY_TIMING = {1}").format(hex32(ctrl), hex32(timing))
        if de_tx:
            txt += "\n" + tr("lead {0} clk ({1:.2f} µs) · tail {2} clk ({3:.2f} µs)").format(
                t["lead"], t["lead_us"], t["tail"], t["tail_us"])
        self.line_info.setText(txt)

    def _set_setup_form(self, v):
        d = U.decode_setup(v, self.clk_hz)
        f = d["fmt"]
        if d["div"]:
            # the divider is rounded: show the standard rate it was made for when it is that close
            near = min((int(b) for b in BAUDS), key=lambda b: abs(b - d["baud"]))
            self.baud.setCurrentText(str(near if abs(near - d["baud"]) / near < 0.02 else round(d["baud"])))
        self.data_bits.setCurrentText(str(f.data))
        self.parity.setCurrentIndex(self.parity.findData(f.parity))
        self.stop_bits.setCurrentText(str(f.stop))
        self.flow.setChecked(not d["no_flow"])
        self._form_changed()

    def _set_line_form(self, ctrl, timing):
        c = U.decode_ctrl(ctrl)
        self._loading_line = True
        if c["de"] in U.DE_CODES:
            self.de_mode.setCurrentIndex(self.de_mode.findData(c["de"]))
        for w, k in ((self.half, "half"), (self.echo, "echo"), (self.tx_inv, "tx_inv"), (self.rx_inv, "rx_inv"),
                     (self.tx_en, "tx_en"), (self.rx_en, "rx_en")):
            w.setChecked(c[k])
        if timing is not None:
            t = U.decode_timing(timing, self.clk_hz)
            self.lead.setValue(t["lead_us"])
            self.tail_auto.setChecked(False)
            self.tail.setValue(t["tail_us"])
        self._loading_line = False
        self._line_changed()

    # ---------------------------------------------------------------- board access
    def read_setup(self):
        self._read(U.SETUP, self._set_setup_form)

    def write_setup(self):
        try:
            v = self._setup_value()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        self._save_settings(clk_mhz=self.clk.value())
        self._write([(U.SETUP, v)], lambda _: self.host.status(tr("SETUP written: {0}").format(hex32(v))))

    def read_line(self):
        addr_c, addr_t = self._addr(U.PHY_CTRL), self._addr(U.PHY_TIMING)

        def job():
            return self.host.link.read(addr_c), self.host.link.read(addr_t)

        def ok(vals):
            self.host.record(addr_c, vals[0], tr("UART interface"))
            self.host.record(addr_t, vals[1], tr("UART interface"))
            self._set_line_form(*vals)
        self._run(job, ok, tr("Read error"))

    def write_line(self):
        ctrl = self._ctrl_value() | (U.CTRL_APPLY_NOW if self.apply_now.isChecked() else 0)
        timing = self._timing_value()
        self._save_settings(clk_mhz=self.clk.value(), lead_us=self.lead.value(), tail_us=self.tail.value(),
                            tail_auto=self.tail_auto.isChecked())
        self._write([(U.PHY_TIMING, timing), (U.PHY_CTRL, ctrl)], lambda _: self.read_status())

    def read_status(self):
        addr_s, addr_f = self._addr(U.PHY_STATUS), self._addr(U.FIFO)

        def job():
            return self.host.link.read(addr_s), self.host.link.read(addr_f)

        def ok(vals):
            st, fifo = vals
            self.host.record(addr_s, st, tr("UART interface"))
            self.host.record(addr_f, fifo, tr("UART interface"))
            self._show_status(st, fifo)
        self._run(job, ok, tr("Read error"))

    def _show_status(self, st, fifo):
        names = U.status_names(st)
        caps = [n[4:].replace("RE", "/RE").replace("FLOW", "RTS/CTS") for n in names if n.startswith("HAS_")]
        self.status_vals["caps"].setText(", ".join(caps) or "—")
        state = [n.replace("RE_N", "/RE high") for n in names if not n.startswith("HAS_") and n != "CFG_ERR"]
        self.status_vals["state"].setText(", ".join(state) or tr("idle"))
        err = bool(st & U.ST_CFG_ERR)
        self.status_vals["cfg"].setText(tr("CFG_ERR: an unsupported setting was rejected") if err else tr("none"))
        self.status_vals["cfg"].setStyleSheet(f"color: {theme.manager().color('danger').name()}" if err else "")
        f = U.decode_fifo(fifo)
        self.status_vals["rx"].setText(tr("{0} / {1} bytes waiting").format(f["rx_fill"], 1 << f["rx_lg"]))
        self.status_vals["tx"].setText(tr("{0} / {1} free").format(f["tx_free"], 1 << f["tx_lg"]))
        self.flow.setEnabled(bool(st & U.ST_HAS_FLOW) or self.flow.isChecked())

    def read_all(self):
        offs = (U.SETUP, U.PHY_CTRL, U.PHY_TIMING, U.PHY_STATUS, U.FIFO)
        addrs = [self._addr(o) for o in offs]

        def job():
            return [self.host.link.read(a) for a in addrs]

        def ok(vals):
            for a, v in zip(addrs, vals):
                self.host.record(a, v, tr("UART interface"))
            self._set_setup_form(vals[0])
            self._set_line_form(vals[1], vals[2])
            self._show_status(vals[3], vals[4])
        self._run(job, ok, tr("Read error"))

    def write_all(self):
        try:
            setup = self._setup_value()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        ctrl = self._ctrl_value() | (U.CTRL_APPLY_NOW if self.apply_now.isChecked() else 0)
        self._save_settings(clk_mhz=self.clk.value(), lead_us=self.lead.value(), tail_us=self.tail.value(),
                            tail_auto=self.tail_auto.isChecked())
        self._write([(U.PHY_TIMING, self._timing_value()), (U.PHY_CTRL, ctrl), (U.SETUP, setup)],
                    lambda _: self.read_status())

    def rx_reset(self):
        self._last_ovfl = False
        self._write([(U.RXREG, U.RX_OVFL)], lambda _: self.read_status())

    # ---------------------------------------------------------------- presets
    def _preset_writes(self, with_setup=True):
        writes = [(self.ip_name, "PHY_TIMING", self._timing_value()), (self.ip_name, "PHY_CTRL", self._ctrl_value())]
        if with_setup:
            writes.append((self.ip_name, "SETUP", self._setup_value()))
        return writes

    def _preset_dir(self):
        return self._store.value("uart_panel/preset_dir", "") or os.path.join(ROOT, "examples", "uart_phy")

    def save_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save preset"),
                                              os.path.join(self._preset_dir(), "line.axcfg.json"), "Config (*.axcfg.json)")
        if not path:
            return
        try:
            save_config(path, self._preset_writes(), os.path.basename(path).split(".")[0])
        except (OSError, ValueError) as exc:
            return QMessageBox.critical(self, tr("Could not save"), str(exc))
        self._store.setValue("uart_panel/preset_dir", os.path.dirname(path))
        self.host.status(tr("Preset saved: {0}").format(path))

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open preset"), self._preset_dir(),
                                              "Config (*.axcfg.json);;JSON (*.json)")
        if not path:
            return
        try:
            writes = load_config(path)
        except Exception as exc:  # noqa: BLE001
            return QMessageBox.critical(self, tr("Could not open"), str(exc))
        self._store.setValue("uart_panel/preset_dir", os.path.dirname(path))
        # a preset names its own UART instance; the values are taken for this one
        names = {ip for ip, _, _ in writes}
        own = [w for w in writes if w[0] == self.ip_name] or (writes if len(names) == 1 else [])
        vals = {reg: v for _, reg, v in own}
        if not vals:
            return QMessageBox.warning(self, tr("Open preset"), tr("The file has no settings for {0}").format(self.ip_name))
        if "SETUP" in vals:
            self._set_setup_form(vals["SETUP"])
        if "PHY_CTRL" in vals:
            self._set_line_form(vals["PHY_CTRL"], vals.get("PHY_TIMING"))
        self.host.status(tr("Preset loaded into the form; Write sends it to the board"))

    def add_to_config(self):
        try:
            writes = self._preset_writes()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        self.host.add_config_rows([(self.group, ip, reg, v) for ip, reg, v in writes])
        self.host.status(tr("Added to the Debug Bridge config list"))

    # ================================================================ terminal
    def _build_terminal_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.rx_running = self.rx_busy = self.tx_busy = False
        self.tx_queue = bytearray()
        self.rx_count = self.tx_count = self.err_count = 0
        self.history, self.hist_pos = [], 0
        self._last_nl = True
        self._last_ovfl = False
        self.split_rx, self.split_tx = U.LineSplitter(), U.LineSplitter()

        self.rx_btn = ui.button(tr("Start receiving"), "play", "primary", self.toggle_rx,
                                tr("Read RXREG periodically and show the received bytes"))
        self.poll_ms = QSpinBox()
        self.poll_ms.setRange(10, 2000)
        self.poll_ms.setSingleStep(10)
        self.poll_ms.setValue(50)
        self.poll_ms.setSuffix(" ms")
        self.poll_ms.setToolTip(tr("Read period"))
        self.log_poll = QCheckBox(tr("Log in traffic"))
        self.log_poll.setToolTip(tr("Show the RXREG reads in the Debug Bridge traffic log"))
        b_flags = ui.button(tr("Clear flags"), None, "ghost", self.clear_flags, tr("Clear the sticky PERR / FERR flags"))
        b_rst = ui.button(tr("RX reset"), None, "ghost", self.rx_reset, tr("Reset the receiver and the RX FIFO (clears OVFL)"))
        self._io_buttons += [b_flags, b_rst]
        lay.addLayout(_hline(self.rx_btn, ui.label(tr("Period"), "muted"), self.poll_ms, self.log_poll,
                             stretch=False))
        lay.itemAt(lay.count() - 1).layout().addStretch(1)
        lay.itemAt(lay.count() - 1).layout().addWidget(b_flags)
        lay.itemAt(lay.count() - 1).layout().addWidget(b_rst)

        view = QHBoxLayout()
        view.setSpacing(8)
        view.addWidget(ui.label(tr("Display"), "muted"))
        self.view_group = QButtonGroup(self)
        for i, m in enumerate(("ASCII", "HEX", "ASCII+HEX")):
            rb = QRadioButton(m)
            rb.setChecked(i == 0)
            self.view_group.addButton(rb, i)
            view.addWidget(rb)
        view.addSpacing(12)
        self.show_time = QCheckBox(tr("Time"))
        self.show_tx = QCheckBox(tr("Show sent"))
        self.show_tx.setChecked(True)
        self.autoscroll = QCheckBox(tr("Follow"))
        self.autoscroll.setChecked(True)
        view.addWidget(self.show_time)
        view.addWidget(self.show_tx)
        view.addWidget(self.autoscroll)
        view.addSpacing(12)
        view.addWidget(ui.label(tr("RX line end"), "muted"))
        self.rx_nl = QComboBox()
        for key, text in (("auto", tr("Auto (CR, LF, CR+LF)")), ("LF", "LF"), ("CR", "CR"), ("CR+LF", "CR+LF")):
            self.rx_nl.addItem(text, key)
        self.rx_nl.currentIndexChanged.connect(self._nl_changed)
        view.addWidget(self.rx_nl)
        view.addStretch(1)
        view.addWidget(ui.button(tr("Clear"), None, "ghost", self.clear_term))
        view.addWidget(ui.button(tr("Save…"), "save", "ghost", self.save_term))
        lay.addLayout(view)

        self.term = QPlainTextEdit()
        self.term.setReadOnly(True)
        self.term.setMaximumBlockCount(TERM_BLOCKS)
        self.term.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.term.setProperty("mono", "true")
        lay.addWidget(self.term, 1)

        send = QHBoxLayout()
        send.setSpacing(6)
        self.send_mode = QComboBox()
        self.send_mode.addItems(["ASCII", "HEX"])
        send.addWidget(self.send_mode)
        self.entry = QLineEdit()
        self.entry.setProperty("mono", "true")
        self.entry.setPlaceholderText(tr("Text to send (Enter); ↑ ↓ history"))
        self.entry.returnPressed.connect(self.send)
        self.entry.installEventFilter(self)
        send.addWidget(self.entry, 1)
        self.add_eol = QCheckBox(tr("Append"))
        self.add_eol.setChecked(True)
        send.addWidget(self.add_eol)
        self.eol = QComboBox()
        self.eol.addItems(list(U.TX_ENDINGS))
        send.addWidget(self.eol)
        self.send_btn = ui.button(tr("Send"), "write", "primary", self.send)
        send.addWidget(self.send_btn)
        lay.addLayout(send)

        opts = QHBoxLayout()
        self.escapes = QCheckBox(tr("Escapes (\\r \\n \\t \\xNN)"))
        self.escapes.setChecked(True)
        opts.addWidget(self.escapes)
        opts.addSpacing(12)
        self.stats = ui.label("", "muted")
        opts.addWidget(self.stats)
        opts.addStretch(1)
        self.file_btn = ui.button(tr("Send file…"), "file", "ghost", self.send_file)
        opts.addWidget(self.file_btn)
        lay.addLayout(opts)
        self.send_mode.currentIndexChanged.connect(
            lambda *_: (self.add_eol.setEnabled(self.send_mode.currentText() == "ASCII"),
                        self.escapes.setEnabled(self.send_mode.currentText() == "ASCII")))
        return page

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.entry and ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._history(-1 if ev.key() == Qt.Key.Key_Up else 1)
            return True
        return super().eventFilter(obj, ev)

    def _history(self, step):
        if not self.history:
            return
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.entry.setText(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")

    def _nl_changed(self):
        self.split_rx.mode = self.split_tx.mode = self.rx_nl.currentData()

    def _update_stats(self):
        if hasattr(self, "stats"):
            self.stats.setText(tr("RX {0} · TX {1} · errors {2} · queue {3}").format(
                self.rx_count, self.tx_count, self.err_count, len(self.tx_queue)))

    # ---------------------------------------------------------------- rendering
    def _ins(self, text, fmt):
        cur = self.term.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(text, fmt)

    def _stamp(self):
        self._ins(time.strftime("[%H:%M:%S] "), self.fmt_time)

    def _render(self, data: bytes, tx, bad=frozenset()):
        """Show bytes; indices in `bad` were received with a parity / frame error."""
        mode = self.view_group.checkedId()
        tag = self.fmt_tx if tx else self.fmt_rx
        split = self.split_tx if tx else self.split_rx
        if self.show_time.isChecked() and (self._last_nl or mode != 0):
            self._stamp()
        if mode == 1:                                  # HEX
            for i, b in enumerate(data):
                self._ins(f"{b:02X}", self.fmt_bad if i in bad else tag)
                self._ins(" ", tag)
            if tx:
                self._ins("\n", tag)
            self._last_nl = tx
        elif mode == 2:                                # ASCII+HEX, 16 bytes a line
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16]
                self._ins(f"{'TX' if tx else 'RX'}  ", tag)
                for j, b in enumerate(chunk):
                    self._ins(f"{b:02X}", self.fmt_bad if i + j in bad else tag)
                    self._ins(" ", tag)
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                self._ins("   " * (16 - len(chunk)) + f" |{asc}|\n", tag)
            self._last_nl = True
        else:                                          # ASCII
            for i, b in enumerate(data):
                if i in bad:
                    self._ins(chr(b) if 32 <= b < 127 else f"<{b:02X}>", self.fmt_bad)
                    split.prev_cr = False
                    self._last_nl = False
                    continue
                if split.mode == "CR+LF" and split.prev_cr and b != 10:
                    self._ins("<0D>", self.fmt_ctl)       # lone CR in CR+LF mode
                act = split.action(b)
                if act == "nl":
                    self._ins("\n", tag)
                    self._last_nl = True
                    if self.show_time.isChecked() and i + 1 < len(data):
                        self._stamp()
                    continue
                if act == "skip":
                    continue
                if 32 <= b < 127 or b >= 160:
                    self._ins(chr(b), tag)
                elif b == 9:
                    self._ins("\t", tag)
                else:
                    self._ins(f"<{b:02X}>", self.fmt_ctl)
                self._last_nl = False
        if self.autoscroll.isChecked():
            self.term.moveCursor(QTextCursor.MoveOperation.End)
            self.term.ensureCursorVisible()

    def _mark(self, text):
        if not self._last_nl:
            self._ins("\n", self.fmt_rx)
        self._ins(f"[{text}]\n", self.fmt_err)
        self._last_nl = True
        if self.autoscroll.isChecked():
            self.term.moveCursor(QTextCursor.MoveOperation.End)

    def clear_term(self):
        self.term.clear()
        self.rx_count = self.tx_count = self.err_count = 0
        self._last_nl = True
        self._update_stats()

    def save_term(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save terminal"), f"{self.ip_name}_terminal.txt",
                                              tr("Text (*.txt);;All files (*)"))
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.term.toPlainText())
            except OSError as exc:
                QMessageBox.critical(self, tr("Could not save"), str(exc))

    # ---------------------------------------------------------------- TX
    def send(self):
        raw = self.entry.text()
        try:
            if self.send_mode.currentText() == "HEX":
                data = U.parse_hex_bytes(raw)
            else:
                data = U.parse_ascii(raw, self.escapes.isChecked())
                if self.add_eol.isChecked():
                    data += U.TX_ENDINGS[self.eol.currentText()]
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Could not send"), str(exc))
        if not data:
            return
        if raw and (not self.history or self.history[-1] != raw):
            self.history.append(raw)
        self.hist_pos = len(self.history)
        self.entry.clear()
        self.queue_tx(data)

    def send_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Send file"))
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            return QMessageBox.critical(self, tr("Could not read the file"), str(exc))
        if len(data) > 64 * 1024 and not ui.confirm(self, tr("Large file"),
                                                    tr("{0} bytes will be sent; this can take long. Continue?").format(len(data)),
                                                    tr("Send")):
            return
        self.queue_tx(data)

    def queue_tx(self, data):
        if not self.connected or self.validating:
            self.host.status(tr("Not connected"))
            return
        self.tx_queue += data
        if self.show_tx.isChecked():
            self._render(data, True)
        self._update_stats()
        self._pump_tx()

    def _pump_tx(self):
        if self.tx_busy or not self.tx_queue or not self.connected:
            return
        try:
            fifo_a, tx_a = self._addr(U.FIFO), self._addr(U.TXREG)
        except hexbus.HexbusError as exc:
            self.tx_queue.clear()
            return self._mark(str(exc))
        chunk = bytes(self.tx_queue[:MAX_TX_CHUNK])
        link = self.host.link

        def job():
            free = (link.read(fifo_a) >> U.TX_FREE_SHIFT) & U.TX_FREE_MASK
            for b in chunk[:free]:
                link.write(tx_a, b)
            return min(free, len(chunk))

        def done(sent):
            self.tx_busy = False
            del self.tx_queue[:sent]
            self.tx_count += sent
            if self.tx_queue:
                QTimer.singleShot(1 if sent else 20, self._pump_tx)
            self._update_stats()

        def fail(exc):
            self.tx_busy = False
            self.tx_queue.clear()
            self.err_count += 1
            self._mark(tr("TX error: {0}").format(exc))
            self._update_stats()
        self.tx_busy = True
        self.host.bus.run(job, done, fail)

    # ---------------------------------------------------------------- RX
    def toggle_rx(self):
        if self.rx_running:
            self.stop_rx()
            return
        if not self.connected:
            self.host.status(tr("Not connected"))
            return
        self.rx_running = True
        self.rx_btn.setText(tr("Stop receiving"))
        self.rx_btn.setProperty("variant", None)
        theme.repolish(self.rx_btn)
        self._poll()

    def stop_rx(self):
        if not getattr(self, "rx_running", False):
            return
        self.rx_running = False
        self.rx_btn.setText(tr("Start receiving"))
        self.rx_btn.setProperty("variant", "primary")
        theme.repolish(self.rx_btn)
        ui.refresh_icons(self.rx_btn.parentWidget())

    def _poll(self):
        if not self.rx_running or self.rx_busy:
            return
        if not self.connected or self.validating:
            return self.stop_rx()
        try:
            rx_a = self._addr(U.RXREG)
        except hexbus.HexbusError as exc:
            self._mark(str(exc))
            return self.stop_rx()
        link = self.host.link
        quiet = not self.log_poll.isChecked()

        def job():
            data, bad, flags = bytearray(), set(), 0
            link.quiet = quiet
            try:
                for _ in range(MAX_RX_PER_POLL):
                    v = link.read(rx_a)
                    flags |= v & (U.RX_PERR | U.RX_FERR | U.RX_BREAK | U.RX_OVFL)
                    if v & U.RX_EMPTY:
                        break
                    if v & (U.RX_BYTE_PERR | U.RX_BYTE_FERR):
                        bad.add(len(data))
                        flags |= v & (U.RX_BYTE_PERR | U.RX_BYTE_FERR)
                    data.append(v & 0xFF)
            finally:
                link.quiet = False
            return bytes(data), bad, flags

        def done(result):
            self.rx_busy = False
            data, bad, flags = result
            if data:
                self.rx_count += len(data)
                self._render(data, False, bad)
            self._handle_flags(flags, rx_a)
            self._update_stats()
            if self.rx_running:
                QTimer.singleShot(1 if len(data) >= MAX_RX_PER_POLL else self.poll_ms.value(), self._poll)

        def fail(exc):
            self.rx_busy = False
            self.err_count += 1
            self._mark(tr("RX error: {0}").format(exc))
            self.stop_rx()
            self._update_stats()
        self.rx_busy = True
        self.host.bus.run(job, done, fail)

    def _handle_flags(self, flags, rx_a):
        if flags & (U.RX_BYTE_PERR | U.RX_BYTE_FERR):
            # the bytes are already highlighted; one summary line
            kinds = [n for bit, n in ((U.RX_BYTE_PERR, tr("parity")), (U.RX_BYTE_FERR, tr("frame"))) if flags & bit]
            names = [tr("bytes with errors: {0}").format(", ".join(kinds))]
        else:      # RTL without per-byte flags: only the sticky summary
            names = [n for bit, n in ((U.RX_PERR, tr("parity error")), (U.RX_FERR, tr("frame error"))) if flags & bit]
        if flags & U.RX_BREAK:
            names.append("BREAK")
        ovfl = bool(flags & U.RX_OVFL)
        if ovfl and not self._last_ovfl:
            names.append(tr("OVERFLOW: data lost, RX reset needed"))
        self._last_ovfl = ovfl
        if names:
            self.err_count += 1
            self._mark("RX " + ", ".join(names))
        if flags & (U.RX_PERR | U.RX_FERR):
            self.host.bus.run(lambda: self.host.link.write(rx_a, U.RX_PERR | U.RX_FERR))

    def clear_flags(self):
        self._write([(U.RXREG, U.RX_PERR | U.RX_FERR)])

    # ================================================================ validation
    def _build_validation_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        st = self._settings().get("validation", {})
        self._val_inputs = []
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(10)

        conn = ui.Card(tr("Connections"), tr("The register access uses the Debug Bridge connection"))
        form = QFormLayout()
        self.v_line = QComboBox()
        self.v_line.setEditable(True)
        self.v_line.setToolTip(tr("Serial adapter (USB-RS485 / RS-232) on the UART line: the PC is the far end"))
        self.v_refresh = ui.button("", "refresh", "ghost", self._fill_line_ports, tr("Refresh ports"))
        r = QHBoxLayout()
        r.addWidget(self.v_line, 1)
        r.addWidget(self.v_refresh)
        form.addRow(tr("Line adapter"), r)
        self.v_loop = QCheckBox(tr("TX wired to RX (loopback, no adapter)"))
        self.v_loop.setChecked(bool(st.get("loopback", False)))
        form.addRow("", self.v_loop)
        conn.lay.addLayout(form)
        ll.addWidget(conn)

        lt = ui.Card(tr("Line under test"), tr("Written to PHY_CTRL / PHY_TIMING before the tests"))
        form = QFormLayout()
        self.v_type = QComboBox()
        for key, d in U.LINE_TYPES.items():
            self.v_type.addItem(d["label"], key)
        self.v_type.setCurrentIndex(max(0, self.v_type.findData(st.get("line_type", "rs485-4w"))))
        self.v_de = QComboBox()
        self.v_de.addItem(tr("From the line type"), "")
        for key, text in DE_LABELS:
            self.v_de.addItem(text, key)
        self.v_de.setCurrentIndex(max(0, self.v_de.findData(st.get("de", ""))))
        self.v_lead = QDoubleSpinBox()
        self.v_lead.setRange(0, 100000)
        self.v_lead.setDecimals(2)
        self.v_lead.setSuffix(" µs")
        self.v_lead.setValue(float(st.get("lead_us", 10.0)))
        self.v_tail = QDoubleSpinBox()
        self.v_tail.setRange(0, 100000)
        self.v_tail.setDecimals(2)
        self.v_tail.setSuffix(" µs")
        self.v_tail_auto = QCheckBox(tr("1 bit time"))
        tail = st.get("tail_us", "auto")
        self.v_tail_auto.setChecked(tail == "auto")
        self.v_tail.setValue(10.0 if tail == "auto" else float(tail))
        self.v_tail_auto.toggled.connect(lambda on: self.v_tail.setEnabled(not on))
        self.v_tail.setEnabled(not self.v_tail_auto.isChecked())
        form.addRow(tr("Line type"), self.v_type)
        form.addRow(tr("DE mode"), self.v_de)
        form.addRow(tr("DE lead"), self.v_lead)
        r = QHBoxLayout()
        r.addWidget(self.v_tail, 1)
        r.addWidget(self.v_tail_auto)
        form.addRow(tr("DE tail"), r)
        lt.lay.addLayout(form)
        ll.addWidget(lt)

        mx = ui.Card(tr("Test matrix"))
        form = QFormLayout()
        self.v_mode = QComboBox()
        self.v_mode.addItem(tr("All tests"), "")
        self.v_mode.addItem(tr("DE lead sweep"), "lead")
        self.v_mode.addItem(tr("DE tail sweep"), "tail")
        self.v_mode.setToolTip(tr("Sweep: the smallest lead / tail that works with DE switched per transmission, "
                                  "at the last baud rate and the first format (needs the line adapter)"))
        self.v_mode.setCurrentIndex(max(0, self.v_mode.findData(st.get("sweep") or "")))
        self.v_bauds = QLineEdit(", ".join(str(b) for b in st.get("bauds", [9600, 19200, 38400, 57600, 115200])))
        self.v_bauds.setProperty("mono", "true")
        self.v_formats = QLineEdit(", ".join(st.get("formats", ["8N1", "8E1", "8O1", "8N2", "7E1", "7O1", "8M1", "8S1"])))
        self.v_formats.setProperty("mono", "true")
        self.v_formats.setToolTip(tr("Data bits 5-8, parity N/E/O/M/S, stop bits 1-2; e.g. 8N1, 7E1, 5O2"))
        self.v_count = QSpinBox()
        self.v_count.setRange(1, 4096)
        self.v_count.setValue(int(st.get("count", 64)))
        self.v_count.setSuffix(tr(" bytes"))
        self.v_quick = QCheckBox(tr("Skip parity / frame error injection"))
        self.v_quick.setChecked(bool(st.get("quick", False)))
        self.v_burst = QCheckBox(tr("Burst test in every format"))
        self.v_burst.setChecked(bool(st.get("burst_all", False)))
        self.v_trials = QSpinBox()
        self.v_trials.setRange(1, 100)
        self.v_trials.setValue(int(st.get("sweep_trials", 3)))
        self.v_res = QDoubleSpinBox()
        self.v_res.setRange(0.5, 50)
        self.v_res.setSuffix(" %")
        self.v_res.setValue(float(st.get("sweep_resolution", 5.0)))
        self.v_smode = QComboBox()
        self.v_smode.addItem(tr("Smart (doubling, bisection, verify)"), "smart")
        self.v_smode.addItem(tr("Linear (fixed list)"), "linear")
        self.v_smode.setCurrentIndex(max(0, self.v_smode.findData(st.get("sweep_mode", "smart"))))
        self.v_traffic = QCheckBox(tr("Show the register accesses in the traffic log"))
        form.addRow(tr("Run"), self.v_mode)
        form.addRow(tr("Baud rates"), self.v_bauds)
        form.addRow(tr("Formats"), self.v_formats)
        form.addRow(tr("Bytes per test"), self.v_count)
        form.addRow("", self.v_quick)
        form.addRow("", self.v_burst)
        self.sweep_rows = [(tr("Sweep method"), self.v_smode), (tr("Resolution"), self.v_res),
                           (tr("Error-free runs"), self.v_trials)]
        for text, w in self.sweep_rows:
            form.addRow(text, w)
        form.addRow("", self.v_traffic)
        self._val_form = form
        mx.lay.addLayout(form)
        ll.addWidget(mx)
        r = QHBoxLayout()
        r.addWidget(ui.button(tr("Open settings…"), "folder", "ghost", self.open_val_settings,
                              tr("Load validation settings (JSON, also used by the command line)")))
        r.addWidget(ui.button(tr("Save settings…"), "save", "ghost", self.save_val_settings,
                              tr("Save the validation settings (JSON, for the command line too)")))
        r.addStretch(1)
        ll.addLayout(r)
        ll.addStretch(1)
        self.v_mode.currentIndexChanged.connect(self._mode_changed)
        self._val_inputs = [self.v_line, self.v_refresh, self.v_loop, self.v_type, self.v_de, self.v_lead, self.v_tail,
                            self.v_tail_auto, self.v_mode, self.v_bauds, self.v_formats, self.v_count, self.v_quick,
                            self.v_burst, self.v_trials, self.v_res, self.v_smode, self.v_traffic]
        scroll_left = left
        split.addWidget(scroll_left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(8)
        self.run_btn = ui.button(tr("Start"), "play", "primary", self.start_validation,
                                 tr("Run the tests on the board; the registers are restored at the end"))
        self.stop_btn = ui.button(tr("Stop"), None, None, self.stop_validation)
        self.v_summary = ui.Pill("")
        self.v_save = ui.button(tr("Save report…"), "save", "ghost", self.save_report)
        self.v_save.setEnabled(False)
        rl.addLayout(_hline(self.run_btn, self.stop_btn, 8, self.v_summary, stretch=False))
        rl.itemAt(rl.count() - 1).layout().addStretch(1)
        rl.itemAt(rl.count() - 1).layout().addWidget(self.v_save)
        self.v_status = ui.label(tr("Connect the Debug Bridge, choose the line and start."), "muted", wrap=True)
        rl.addWidget(self.v_status)
        vs = QSplitter(Qt.Orientation.Vertical)
        self.results = QTableWidget(0, 3)
        self.results.setHorizontalHeaderLabels([tr("Result"), tr("Test"), tr("Detail")])
        self.results.verticalHeader().setVisible(False)
        self.results.setShowGrid(False)
        self.results.setAlternatingRowColors(True)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setWordWrap(False)
        h = self.results.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.results.setColumnWidth(1, 300)
        vs.addWidget(self.results)
        self.vlog = QPlainTextEdit()
        self.vlog.setReadOnly(True)
        self.vlog.setMaximumBlockCount(20000)
        self.vlog.setProperty("mono", "true")
        vs.addWidget(self.vlog)
        vs.setSizes([420, 160])
        rl.addWidget(vs, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 680])
        lay.addWidget(split, 1)
        self.report = None
        self._fill_line_ports()
        if st.get("line"):
            self.v_line.setCurrentText(st["line"])
        self._mode_changed()
        return page

    def _fill_line_ports(self):
        cur = self.v_line.currentText()
        self.v_line.clear()
        self.v_line.addItem(tr("None"), "")
        ports = [p for p in hexbus.list_ports() if p != hexbus.SIM_PORT]
        for p in ports:
            self.v_line.addItem(p, p)
        if cur:
            self.v_line.setCurrentText(cur)

    def _line_port(self):
        t = self.v_line.currentText().strip()
        return "" if t in ("", tr("None")) else t

    def _mode_changed(self):
        sweep = bool(self.v_mode.currentData())
        for _, w in self.sweep_rows:
            w.setVisible(sweep)
            lbl = self._val_form.labelForField(w)
            if lbl:
                lbl.setVisible(sweep)
        self.v_quick.setEnabled(not sweep and not self.validating)
        self.v_burst.setEnabled(not sweep and not self.validating)

    def _val_settings(self):
        ip = self.ip
        if ip is None:
            raise ValueError(tr("{0} is no longer in the address map").format(self.ip_name))
        return V.Settings(base=ip.base, clk=self.clk_hz, line=self._line_port(), loopback=self.v_loop.isChecked(),
                          line_type=self.v_type.currentData(), de=self.v_de.currentData(), lead_us=self.v_lead.value(),
                          tail_us="auto" if self.v_tail_auto.isChecked() else self.v_tail.value(),
                          bauds=self.v_bauds.text(), formats=self.v_formats.text(), count=self.v_count.value(),
                          quick=self.v_quick.isChecked(), burst_all=self.v_burst.isChecked(),
                          sweep=self.v_mode.currentData() or None, sweep_mode=self.v_smode.currentData(),
                          sweep_trials=self.v_trials.value(), sweep_resolution=self.v_res.value())

    def _set_val_form(self, s: V.Settings):
        self.v_line.setCurrentText(s.line or tr("None"))
        self.v_loop.setChecked(s.loopback)
        self.v_type.setCurrentIndex(max(0, self.v_type.findData(s.line_type)))
        self.v_de.setCurrentIndex(max(0, self.v_de.findData(s.de or "")))
        self.v_lead.setValue(s.lead_us)
        self.v_tail_auto.setChecked(s.tail_us == "auto")
        if s.tail_us != "auto":
            self.v_tail.setValue(s.tail_us)
        self.v_bauds.setText(", ".join(str(b) for b in s.bauds))
        self.v_formats.setText(", ".join(str(f) for f in s.formats))
        self.v_count.setValue(s.count)
        self.v_quick.setChecked(s.quick)
        self.v_burst.setChecked(s.burst_all)
        self.v_mode.setCurrentIndex(max(0, self.v_mode.findData(s.sweep or "")))
        self.v_smode.setCurrentIndex(max(0, self.v_smode.findData(s.sweep_mode)))
        self.v_trials.setValue(s.sweep_trials)
        self.v_res.setValue(s.sweep_resolution)

    def save_val_settings(self):
        try:
            s = self._val_settings()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Validation"), str(exc))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save settings"),
                                              os.path.join(self._preset_dir(), f"{self.ip_name}_validation.json"), "JSON (*.json)")
        if not path:
            return
        d = s.to_dict()
        d["hexbus"] = self.host.port_cb.currentText() if hasattr(self.host, "port_cb") else ""
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, tr("Could not save"), str(exc))

    def open_val_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open settings"), self._preset_dir(), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            d = {k: v for k, v in d.items() if k not in ("hexbus", "hexbus_baud", "report", "base", "clk") or k == "clk"}
            s = V.Settings.from_dict(d)
        except (OSError, ValueError, TypeError) as exc:
            return QMessageBox.critical(self, tr("Could not open"), str(exc))
        if "clk" in d:
            self.clk.setValue(float(d["clk"]) / 1e6)
        self._set_val_form(s)

    # ---------------------------------------------------------------- run
    def start_validation(self):
        if self.validating:
            return
        if not self.connected:
            return QMessageBox.information(self, tr("Validation"), tr("Connect the Debug Bridge first."))
        try:
            s = self._val_settings()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Validation"), str(exc))
        if s.sweep and not s.line:
            return QMessageBox.warning(self, tr("Validation"), tr("The DE sweep needs the line adapter."))
        if s.line and V.serial is None:
            return QMessageBox.critical(self, tr("Validation"), "pyserial is not installed (pip install pyserial)")
        if s.line and s.line == getattr(self.host, "port_cb", None).currentText():
            return QMessageBox.warning(self, tr("Validation"),
                                       tr("The line adapter cannot be the Debug Bridge's own port."))
        self._save_settings(clk_mhz=self.clk.value(), validation=s.to_dict())
        self.stop_rx()
        self.results.setRowCount(0)
        self.vlog.clear()
        self.v_save.setEnabled(False)
        self.cancel.clear()
        self.report = V.Report(on_row=self._row.emit, on_log=self._vlog.emit, cancel=self.cancel)
        self._settings_run = s
        self.validating = True
        self.host.set_exclusive(self)
        self._update_controls()
        self._mode_changed()
        self.v_summary.set(tr("Running…"), "accent")
        self.v_status.setText(tr("Running on {0} @ {1}…").format(self.ip_name, hex32(s.base)))
        self._t0 = time.monotonic()
        link, report, quiet = self.host.link, self.report, not self.v_traffic.isChecked()

        def job():
            port = None
            link.quiet = quiet
            try:
                if s.line:
                    port = V.open_line(s.line)
                return V.run_validation(link, s, report, port)
            finally:
                link.quiet = False
                if port is not None:
                    port.close()

        def fail(exc):
            report.add("FAIL", tr("Line adapter"), str(exc))
            self._finished("error")
        self.host.bus.run(job, self._finished, fail)

    def stop_validation(self):
        if self.validating:
            self.cancel.set()
            self.v_summary.set(tr("Stopping…"), "warn")
            self.stop_btn.setEnabled(False)

    def _finished(self, result):
        self.validating = False
        self.host.set_exclusive(None)
        self._update_controls()
        self._mode_changed()
        c = self.report.counts()
        dt = time.monotonic() - self._t0
        text = tr("{0} passed · {1} failed · {2} skipped").format(c["PASS"], c["FAIL"], c["SKIP"])
        if result == "stopped":
            self.v_summary.set(tr("Stopped"), "warn")
        elif c["FAIL"] or result == "error":
            self.v_summary.set(tr("Failed"), "err")
        else:
            self.v_summary.set(tr("Passed"), "ok")
        self.v_status.setText(f"{text} · {dt:.1f} s")
        self.v_save.setEnabled(True)
        self._stopped = result == "stopped"

    def _add_result(self, row):
        r = self.results.rowCount()
        self.results.insertRow(r)
        names = {"PASS": tr("Passed"), "FAIL": tr("Failed"), "SKIP": tr("Skipped"), "INFO": tr("Info")}
        st = QTableWidgetItem(names.get(row["status"], row["status"]))
        st.setData(Qt.ItemDataRole.UserRole, row["status"])
        self.results.setItem(r, 0, st)
        t = QTableWidgetItem(row["test"])
        t.setToolTip(row.get("section", ""))
        self.results.setItem(r, 1, t)
        d = QTableWidgetItem(row["detail"])
        d.setToolTip(row["detail"])
        self.results.setItem(r, 2, d)
        self._color_row(r)
        self.results.scrollToBottom()
        c = self.report.counts() if self.report else {}
        if self.validating:
            self.v_status.setText(tr("Running · {0}: {1} passed, {2} failed").format(
                row.get("section", ""), c.get("PASS", 0), c.get("FAIL", 0)))

    def _color_row(self, r):
        m = theme.manager()
        colors = {"PASS": m.color("success"), "FAIL": m.color("danger"), "SKIP": m.color("warning"),
                  "INFO": m.color("muted")}
        it = self.results.item(r, 0)
        if it is not None:
            it.setForeground(colors.get(it.data(Qt.ItemDataRole.UserRole), m.color("text")))
            f = it.font()
            f.setBold(True)
            it.setFont(f)

    def _color_results(self):
        if hasattr(self, "results"):
            for r in range(self.results.rowCount()):
                self._color_row(r)

    def _append_vlog(self, text):
        self.vlog.appendPlainText(text)

    def save_report(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Save report"),
                                              os.path.join(self._preset_dir(),
                                                           f"{self.ip_name}_validation_{time.strftime('%Y%m%d_%H%M')}.json"),
                                              "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.report.to_dict(self._settings_run, getattr(self, "_stopped", False)), fh, indent=2)
        except OSError as exc:
            QMessageBox.critical(self, tr("Could not save"), str(exc))


register(PanelInfo(U.PANEL_ID, tr("UART interface"),
                   tr("axiluart_phy: line settings (baud, format, RS-232 / RS-422 / RS-485), terminal and hardware validation"),
                   UartPhyPanel))
