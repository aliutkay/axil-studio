#!/usr/bin/env python3
"""UART interface of axiluart_phy: line settings, terminal and hardware validation.

An AXIL Studio IP interface: a separate application that AXIL Studio starts from the UART-to-AXI
Debug Bridge for an IP whose register map names "axiluart_phy" (see axil-interface.json). Every
register access goes through the Debug Bridge's connection over the local service
(axil_client.py); the window does not work on its own.
"""

import concurrent.futures
import json
import math
import os
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QPlainTextEdit, QRadioButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "sdk"))      # axil_client when run from the repository

import look  # noqa: E402
import uart_phy as U  # noqa: E402
import uart_validate as V  # noqa: E402
from axil_client import AxilClient, AxilError  # noqa: E402
from look import tr  # noqa: E402

try:
    import serial.tools.list_ports as _lp
except ImportError:  # pragma: no cover
    _lp = None

BAUDS = ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600",
         "1000000", "2000000"]
MAX_RX_PER_POLL = 256
MAX_TX_CHUNK = 64
TERM_BLOCKS = 5000


def serial_ports():
    return [p.device for p in _lp.comports()] if _lp is not None else []


class Worker(QObject):
    """One worker thread for the requests to AXIL Studio; results come back on the GUI thread."""
    _done = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._closed = False
        self._done.connect(self._deliver)

        self.pending = 0

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


class ValidationLink:
    """The validator's link: absolute addresses through AXIL Studio; failures become LinkError."""

    def __init__(self, client, quiet):
        self.client, self.quiet = client, quiet

    def read(self, addr):
        try:
            return self.client.read(addr=addr, quiet=self.quiet)
        except AxilError as exc:
            raise V.LinkError(str(exc)) from exc

    def write(self, addr, value):
        try:
            self.client.write(addr=addr, value=value, quiet=self.quiet)
        except AxilError as exc:
            raise V.LinkError(str(exc)) from exc


class UartWindow(QWidget):
    _event = Signal(dict)          # AXIL Studio event (reader thread -> GUI)
    _row = Signal(dict)            # validation result (worker thread -> GUI)
    _vlog = Signal(str)
    _vdone = Signal(object)        # validation finished: "done" | "stopped" | "error"
    _vfail = Signal(object)        # validation could not run: the exception

    def __init__(self, client: AxilClient):
        super().__init__()
        self.client = client
        self.setObjectName("Page")
        self.resize(1100, 780)
        self._store = QSettings("AXIL", "axiluart_phy interface")
        self.bus = Worker(self)
        self.validating = False
        self.cancel = threading.Event()
        self.report = None
        self._io_buttons = []
        self.peers_list = []                         # other IPs with this interface (AXIL Studio's "peers")
        self.pair = client.settings.get("pair")      # {"peer", "role": "tx"|"rx", "tx_de": "high"|"tx"} or None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)
        bar = look.Card(margins=12)
        row = QHBoxLayout()
        row.setSpacing(8)
        title = look.label(tr("UART interface"), "title")
        row.addWidget(title)
        self.ip_lbl = look.label("", "muted")
        row.addWidget(self.ip_lbl)
        row.addSpacing(16)
        row.addWidget(look.label("ACLK", "muted"))
        self.clk = QDoubleSpinBox()
        self.clk.setRange(0.1, 1000.0)
        self.clk.setDecimals(3)
        self.clk.setSuffix(" MHz")
        self.clk.setToolTip(tr("Clock of the UART IP (S_AXI_ACLK): baud dividers and DE timing are computed from it"))
        self.clk.setValue(float(self.client.settings.get("clk_mhz", 50.0)))
        self.clk.valueChanged.connect(self._clk_changed)
        row.addWidget(self.clk)
        row.addSpacing(12)
        self.conn_pill = look.Pill("")
        row.addWidget(self.conn_pill)
        row.addStretch(1)
        row.addWidget(look.button(tr("Show in Debug Bridge"), None, lambda: self.show_in_bridge(),
                                  tr("Select this IP's registers in the UART-to-AXI Debug Bridge")))
        bar.lay.addLayout(row)
        outer.addWidget(bar)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        card = look.Card(margins=0)
        card.lay.addWidget(self.tabs)
        outer.addWidget(card, 1)
        self.tabs.addTab(self._build_line_tab(), tr("Line settings"))
        self.tabs.addTab(self._build_terminal_tab(), tr("Terminal"))
        self.tabs.addTab(self._build_validation_tab(), tr("Validation"))

        # queued: events that came while the window was being built are handled once it runs
        self._event.connect(self._on_event, Qt.ConnectionType.QueuedConnection)
        self._row.connect(self._add_result)
        self._vlog.connect(self.vlog.appendPlainText)
        self._vdone.connect(self._finished)
        self._vfail.connect(self._failed)
        client.on_event = self._event.emit
        self._update_title()
        self._form_changed()
        self._line_changed()
        self._on_connection()
        self._restyle()
        self._refresh_peers()

    # ================================================================ AXIL Studio
    @property
    def ip(self):
        return self.client.ip

    @property
    def ip_name(self):
        return self.ip.get("name", "")

    @property
    def connected(self):
        return self.client.connected

    def _on_event(self, ev):
        kind = ev.get("event")
        if kind == "connection":
            self._on_connection()
        elif kind == "ip":
            self._update_title()
            self._refresh_peers()
        elif kind == "ip_removed":
            QMessageBox.warning(self, tr("UART interface"), tr("{0} is no longer in the address map").format(self.ip_name))
            self.close()
        elif kind == "theme":
            look.apply(QApplication.instance(), ev.get("theme"))
            self._restyle()
        elif kind == "raise":
            self.showNormal()
            self.raise_()
            self.activateWindow()
        elif kind == "closing":
            self.cancel.set()
            self._closing = True
            self.close()

    def _update_title(self):
        text = f"{self.ip_name} · 0x{int(self.ip.get('base', 0)):08X}"
        if self.paired:
            tx = self.ip_name if self.pair["role"] == "tx" else self.pair["peer"]
            text = tr("{0} ⇄ {1} · 4-wire ({2} TX)").format(self.ip_name, self.pair["peer"], tx)
        self.ip_lbl.setText(text)
        self.setWindowTitle(f"{self.ip_name} — {tr('UART interface')}")

    def _on_connection(self):
        if self.connected:
            self.conn_pill.set("● " + tr("Debug Bridge connected"), "ok")
        else:
            self.conn_pill.set("● " + tr("Debug Bridge not connected"), "")
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
        if hasattr(self, "swap_btn"):
            self.swap_btn.setEnabled(self.paired and self.connected and not self.validating)
        self._update_stats()

    def _call(self, fn, ok=None, what=None):
        """One request to AXIL Studio on the worker thread."""
        if not self.connected:
            self.status(tr("Not connected"))
            return

        def fail(exc):
            if isinstance(exc, AxilError) and exc.code == "closed":
                return                   # AXIL Studio is closing: the window closes too
            QMessageBox.critical(self, what or tr("UART interface"), str(exc))
        self.bus.run(fn, ok, fail)

    def _read(self, offs, ok):
        """Read registers (offsets) in one request; ok(list of values)."""
        self._call(lambda: self.client.batch([("read", o) for o in offs]), ok, tr("Read error"))

    def _write(self, writes, ok=None):
        """writes: [(offset, value)] in order, one request."""
        self._call(lambda: self.client.batch([("write", o, v) for o, v in writes]), ok, tr("Write error"))

    def status(self, text):
        self.bus.run(lambda: self.client.status(text))

    def _save_settings(self, **kw):
        """Board facts (ACLK, lead / tail, validation) are kept in the project by AXIL Studio."""
        if any(self.client.settings.get(k) != v for k, v in kw.items()):
            self.bus.run(lambda: self.client.set_settings(**kw))

    def show_in_bridge(self, reg=None):
        self.bus.run(lambda: self.client.show_register(reg))

    @property
    def clk_hz(self):
        return self.clk.value() * 1e6

    def _clk_changed(self):
        self._form_changed()
        self._line_changed()

    def closeEvent(self, ev):
        if self.validating and not getattr(self, "_closing", False):
            if not look.confirm(self, tr("Validation is running"), tr("Stop the validation and close the window?"),
                                tr("Stop and close")):
                ev.ignore()
                return
        self.cancel.set()
        self.stop_rx()
        self.client.on_event = lambda ev: None       # no more signals to this window
        super().closeEvent(ev)

    def _restyle(self):
        self.fmt_rx = self._cf(look.color("text"))
        self.fmt_tx = self._cf(look.color("accent"))
        self.fmt_ctl = self._cf(look.color("warning"))
        self.fmt_time = self._cf(look.color("muted"))
        self.fmt_err = self._cf(look.color("danger"), bold=True)
        self.fmt_bad = self._cf(QColor("#FFFFFF"), look.color("danger"))
        for w in (self.term, self.vlog, self.setup_info, self.line_info, self.entry, self.v_bauds, self.v_formats):
            w.setFont(look.mono_font())
        for r in range(self.results.rowCount()):
            self._color_row(r)

    @staticmethod
    def _cf(fg, bg=None, bold=False):
        f = QTextCharFormat()
        f.setForeground(fg)
        if bg is not None:
            f.setBackground(bg)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    # ================================================================ 4-wire pair
    @property
    def paired(self):
        return bool(self.pair) and any(p["name"] == self.pair.get("peer") for p in self.peers_list)

    def _peer(self):
        return next((p for p in self.peers_list if p["name"] == (self.pair or {}).get("peer")), None)

    def _bases(self, pair=None):
        """(tx base, rx base): the same IP alone, the two IPs of a pair by their roles."""
        own = int(self.ip.get("base", 0))
        p = self.pair if pair is None else pair
        if not p:
            return own, own
        peer = next((x for x in self.peers_list if x["name"] == p.get("peer")), None)
        if peer is None:
            return own, own
        other = int(peer["base"])
        return (own, other) if p["role"] == "tx" else (other, own)

    def _names(self):
        """(TX IP name, RX IP name)."""
        if not self.paired:
            return self.ip_name, self.ip_name
        return (self.ip_name, self.pair["peer"]) if self.pair["role"] == "tx" else (self.pair["peer"], self.ip_name)

    def _refresh_peers(self):
        def ok(peers):
            self.peers_list = peers or []
            if self.pair and not self.paired:
                self.status(tr("The paired IP {0} is no longer in the address map: single IP mode").format(
                    self.pair.get("peer")))
            self._fill_peer_box()
            self._pair_changed()
        self.bus.run(self.client.peers, ok, lambda e: None)

    def _fill_peer_box(self):
        cur = (self.pair or {}).get("peer") or self.peer_cb.currentData()
        self.peer_cb.blockSignals(True)
        self.peer_cb.clear()
        for p in self.peers_list:
            other = (p.get("settings") or {}).get("pair") or {}
            note = ""
            if other.get("peer") and other.get("peer") != self.ip_name:
                note = "  " + tr("(paired with {0})").format(other["peer"])
            self.peer_cb.addItem(f"{p['name']} · 0x{int(p['base']):08X}{note}", p["name"])
        i = self.peer_cb.findData(cur)
        self.peer_cb.setCurrentIndex(i if i >= 0 else 0)
        self.peer_cb.blockSignals(False)

    def _build_mode_card(self):
        card = look.Card(tr("Operating mode"), tr("One IP as a 2-wire line, or two IPs as one 4-wire link: one sends, "
                                                   "the other receives, and the directions can be swapped"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.mode_cb = QComboBox()
        self.mode_cb.addItem(tr("Single IP (2-wire)"), "single")
        self.mode_cb.addItem(tr("4-wire pair"), "pair")
        self.peer_cb = QComboBox()
        self.peer_cb.setMinimumWidth(200)
        self.peer_cb.setToolTip(tr("The other IP of the pair: IPs of this project that use this interface"))
        self.role_cb = QComboBox()
        self.role_cb.addItem(tr("This IP sends (TX)"), "tx")
        self.role_cb.addItem(tr("This IP receives (RX)"), "rx")
        self.txde_cb = QComboBox()
        self.txde_cb.addItem(tr("TX DE always on (point-to-point)"), "high")
        self.txde_cb.addItem(tr("TX DE while transmitting (multidrop)"), "tx")
        self.pair_btn = look.button(tr("Pair"), "primary", self.make_pair, tr("Store the pair in the project, for both IPs"))
        self.unpair_btn = look.button(tr("Unpair"), "ghost", self.unpair, tr("Both IPs go back to single IP mode"))
        self.swap_btn = look.button(tr("Swap directions"), None, self.swap_directions,
                                    tr("The TX IP becomes RX and the RX IP TX (the receiver is set first, so two "
                                       "drivers are never on); both FIFOs are reset"))
        for w in (self.mode_cb, self.peer_cb, self.role_cb, self.txde_cb, self.pair_btn, self.unpair_btn, self.swap_btn):
            row.addWidget(w)
        row.addStretch(1)
        card.lay.addLayout(row)
        self.pair_lbl = look.label("", "muted", wrap=True)
        card.lay.addWidget(self.pair_lbl)
        if self.pair:
            self.mode_cb.setCurrentIndex(1)
            self.role_cb.setCurrentIndex(self.role_cb.findData(self.pair.get("role", "tx")))
            self.txde_cb.setCurrentIndex(max(0, self.txde_cb.findData(self.pair.get("tx_de", "high"))))
        self.mode_cb.currentIndexChanged.connect(self._pair_changed)
        return card

    def _pair_changed(self, *_):
        if not hasattr(self, "pair_lbl"):
            return
        want_pair = self.mode_cb.currentData() == "pair"
        for w in (self.peer_cb, self.role_cb, self.txde_cb, self.pair_btn):
            w.setVisible(want_pair)
        self.unpair_btn.setVisible(bool(self.pair))
        self.swap_btn.setVisible(self.paired)
        self.swap_btn.setEnabled(self.paired and self.connected and not self.validating)
        self.pair_btn.setEnabled(bool(self.peers_list))
        if self.paired:
            tx, rx = self._names()
            de = tr("DE always on") if self.pair.get("tx_de") == "high" else tr("DE while transmitting")
            self.pair_lbl.setText(tr("Paired: {0} sends, {1} receives · {2}. Character format and timing are written "
                                     "to both IPs.").format(tx, rx, de))
        elif want_pair and not self.peers_list:
            self.pair_lbl.setText(tr("No other IP in this project uses this interface: place a second axiluart_phy "
                                     "in the address map."))
        elif want_pair:
            self.pair_lbl.setText(tr("Choose the other IP and this IP's role, then Pair."))
        else:
            self.pair_lbl.setText(tr("One IP drives its own transceiver (TTL, RS-232, RS-422, RS-485 2-wire)."))
        # the role decides the line interface of a pair: the form shows the TX IP's view
        if self.paired:
            tx_de = self.pair.get("tx_de", "high")
            self._loading_line = True
            self.line_type.setCurrentIndex(self.line_type.findData("rs485-4w" if tx_de == "tx" else "rs422"))
            self.de_mode.setCurrentIndex(self.de_mode.findData(tx_de))
            self.half.setChecked(False)
            self.echo.setChecked(False)
            self.tx_en.setChecked(True)
            self.rx_en.setChecked(True)
            self._loading_line = False
        for w in (self.line_type, self.de_mode, self.half, self.echo, self.tx_en, self.rx_en):
            w.setEnabled(not self.paired)
        self._line_changed()
        self._update_title()
        if hasattr(self, "v_pc"):
            self._val_mode()

    def make_pair(self):
        peer = self.peer_cb.currentData()
        if not peer:
            return
        role, tx_de = self.role_cb.currentData(), self.txde_cb.currentData()
        info = next(p for p in self.peers_list if p["name"] == peer)
        old_of_peer = ((info.get("settings") or {}).get("pair") or {}).get("peer")
        if old_of_peer and old_of_peer != self.ip_name and not look.confirm(
                self, tr("4-wire pair"), tr("{0} is paired with {1}. Break that pair?").format(peer, old_of_peer), tr("Pair")):
            return
        old_mine = (self.pair or {}).get("peer")
        me = {"peer": peer, "role": role, "tx_de": tx_de}
        other = {"peer": self.ip_name, "role": "rx" if role == "tx" else "tx", "tx_de": tx_de}
        known_peers = {p["name"] for p in self.peers_list}

        def job():
            for name in ({old_of_peer, old_mine} & known_peers) - {self.ip_name, peer}:
                self.client.set_settings(ip=name, pair=None)
            self.client.set_settings(pair=me)
            self.client.set_settings(ip=peer, pair=other)

        def ok(_):
            self.pair = me
            self._pair_changed()
            self._refresh_peers()
        self.bus.run(job, ok, lambda e: QMessageBox.critical(self, tr("4-wire pair"), str(e)))

    def unpair(self):
        peer = (self.pair or {}).get("peer")

        def job():
            self.client.set_settings(pair=None)
            if peer and any(p["name"] == peer for p in self.peers_list):
                self.client.set_settings(ip=peer, pair=None)

        def ok(_):
            self.pair = None
            self.mode_cb.setCurrentIndex(0)
            self._refresh_peers()
        self.bus.run(job, ok, lambda e: QMessageBox.critical(self, tr("4-wire pair"), str(e)))

    def _pair_ctrls(self, pair=None):
        """(TX IP PHY_CTRL, RX IP PHY_CTRL) of the pair."""
        p = self.pair if pair is None else pair
        logical = U.ctrl_value(p.get("tx_de", "high"), False, False, self.tx_inv.isChecked(),
                               self.rx_inv.isChecked(), True, True)
        return V.PairDut.split_ctrl(logical | (U.CTRL_APPLY_NOW if self.apply_now.isChecked() else 0))

    def _pair_writes(self, setup=None, pair=None):
        """Writes for both IPs in a safe order: timing, the receiver's PHY_CTRL, the driver's PHY_CTRL,
        then SETUP (which also resets both FIFOs)."""
        tx_base, rx_base = self._bases(pair)
        tx_ctrl, rx_ctrl = self._pair_ctrls(pair)
        timing = self._timing_value()
        w = [(rx_base + U.PHY_TIMING, timing), (tx_base + U.PHY_TIMING, timing),
             (rx_base + U.PHY_CTRL, rx_ctrl), (tx_base + U.PHY_CTRL, tx_ctrl)]
        if setup is not None:
            w += [(rx_base + U.SETUP, setup), (tx_base + U.SETUP, setup)]
        return w

    def _write_abs(self, writes, ok=None):
        self._call(lambda: self.client.batch([{"op": "write", "addr": a, "value": v} for a, v in writes]), ok,
                   tr("Write error"))

    def swap_directions(self):
        if not self.paired:
            return
        try:
            setup = self._setup_value()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        role = "rx" if self.pair["role"] == "tx" else "tx"
        me = dict(self.pair, role=role)
        other = {"peer": self.ip_name, "role": "rx" if role == "tx" else "tx", "tx_de": me.get("tx_de", "high")}
        peer = self.pair["peer"]
        writes = self._pair_writes(setup, pair=me)

        def job():
            self.client.batch([{"op": "write", "addr": a, "value": v} for a, v in writes])
            self.client.set_settings(pair=me)
            self.client.set_settings(ip=peer, pair=other)

        def ok(_):
            self.pair = me
            self.role_cb.setCurrentIndex(self.role_cb.findData(role))
            self.stop_rx()
            self.tx_queue.clear()
            self._pair_changed()
            self.read_status()
            self.status(tr("Directions swapped: {0} sends, {1} receives").format(*self._names()))
        self._call(job, ok, tr("Swap directions"))

    # ================================================================ line settings
    def _build_line_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        lay.addWidget(self._build_mode_card())
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # -- character format
        fmt = look.Card(tr("Character format"), "SETUP · " + tr("baud divider, data bits, parity, stop bits"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText("115200")
        self.data_bits = QComboBox()
        self.data_bits.addItems(["8", "7", "6", "5"])
        self.parity = QComboBox()
        for key, text in (("N", tr("None")), ("E", tr("Even")), ("O", tr("Odd")), ("M", tr("Mark (1)")),
                          ("S", tr("Space (0)"))):
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
        self.setup_info = look.label("", "mono", wrap=True)
        fmt.lay.addWidget(self.setup_info)
        fmt.lay.addStretch(1)
        b1 = look.button(tr("Read value"), None, self.read_setup, tr("Read SETUP from the board into the form"))
        b2 = look.button(tr("Write"), "primary", self.write_setup, tr("Write SETUP (resets the receiver and the transmitter)"))
        self._io_buttons += [b1, b2]
        fmt.lay.addLayout(look.hline(b1, b2))
        for w in (self.data_bits, self.parity, self.stop_bits):
            w.currentIndexChanged.connect(self._form_changed)
        self.baud.currentTextChanged.connect(self._form_changed)
        self.flow.toggled.connect(self._form_changed)
        cols.addWidget(fmt, 1)

        # -- line interface
        line = look.Card(tr("Line interface"), "PHY_CTRL · PHY_TIMING · " + tr("transceiver control"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        self.line_type = QComboBox()
        for key, lt in U.LINE_TYPES.items():
            self.line_type.addItem(lt["label"], key)
            self.line_type.setItemData(self.line_type.count() - 1, lt["desc"], Qt.ItemDataRole.ToolTipRole)
        self.line_type.addItem(tr("Custom"), "")
        self.de_mode = QComboBox()
        for key, text in self._de_labels():
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
        st = self.client.settings
        self.lead = self._us_box(float(st.get("lead_us", 10.0)))
        self.lead.setToolTip(tr("DE on → start bit; at least twice the transceiver's enable time (tZH)"))
        self.tail = self._us_box(float(st.get("tail_us", 10.0)))
        self.tail.setToolTip(tr("End of the stop bit → DE off"))
        self.tail_auto = QCheckBox(tr("1 bit time"))
        self.tail_auto.setToolTip(tr("Tail = one bit time at the selected baud rate"))
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
        self.line_info = look.label("", "mono", wrap=True)
        line.lay.addWidget(self.line_info)
        line.lay.addStretch(1)
        b1 = look.button(tr("Read value"), None, self.read_line, tr("Read PHY_CTRL and PHY_TIMING into the form"))
        b2 = look.button(tr("Write"), "primary", self.write_line, tr("Write PHY_TIMING and PHY_CTRL"))
        self._io_buttons += [b1, b2]
        line.lay.addLayout(look.hline(b1, b2))
        self._loading_line = False
        self.line_type.currentIndexChanged.connect(self._line_type_picked)
        self.de_mode.currentIndexChanged.connect(self._line_changed)
        for w in (self.half, self.echo, self.tx_inv, self.rx_inv, self.tx_en, self.rx_en, self.tail_auto):
            w.toggled.connect(self._line_changed)
        for w in (self.lead, self.tail):
            w.valueChanged.connect(self._line_changed)
        cols.addWidget(line, 1)

        # -- status
        stat = look.Card(tr("Status"), "PHY_STATUS · FIFO")
        sg = QGridLayout()
        sg.setHorizontalSpacing(12)
        self.status_vals = {}
        for i, (key, text) in enumerate((("caps", tr("Pins")), ("state", tr("Line state")), ("cfg", tr("Setting error")),
                                         ("rx", tr("RX FIFO")), ("tx", tr("TX FIFO")))):
            sg.addWidget(look.label(text, "muted"), i, 0)
            v = look.label("—", wrap=True)
            self.status_vals[key] = v
            sg.addWidget(v, i, 1)
        sg.setColumnStretch(1, 1)
        stat.lay.addLayout(sg)
        stat.lay.addStretch(1)
        b1 = look.button(tr("Read value"), None, self.read_status, tr("Read PHY_STATUS and FIFO"))
        b2 = look.button(tr("Clear CFG_ERR"), "ghost", self.clear_cfg_err)
        b3 = look.button(tr("RX reset"), "ghost", self.rx_reset, tr("Reset the receiver and the RX FIFO (clears OVFL)"))
        b4 = look.button(tr("TX reset"), "ghost", lambda: self._write_abs([(self._bases()[0] + U.TXREG, U.TX_RESET)],
                                                                         lambda _: self.read_status()),
                         tr("Reset the transmitter and the TX FIFO"))
        self._io_buttons += [b1, b2, b3, b4]
        stat.lay.addLayout(look.hline(b1))
        stat.lay.addLayout(look.hline(b2, b3, b4))
        cols.addWidget(stat, 1)
        lay.addLayout(cols, 1)

        # -- presets
        pre = look.Card(margins=12)
        b_all = look.button(tr("Read all"), None, self.read_all, tr("Read SETUP, PHY_CTRL, PHY_TIMING and the status"))
        b_wall = look.button(tr("Write all"), None, self.write_all, tr("Write PHY_TIMING, PHY_CTRL and SETUP"))
        self._io_buttons += [b_all, b_wall]
        pre.lay.addLayout(look.hline(
            b_all, b_wall, 16,
            look.button(tr("Open preset…"), "ghost", self.open_preset, tr("Load a line preset (.axcfg.json) into the form")),
            look.button(tr("Save preset…"), "ghost", self.save_preset, tr("Save the form as a line preset (.axcfg.json)")),
            look.button(tr("Add to config list"), "ghost", self.add_to_config,
                        tr("Add PHY_TIMING, PHY_CTRL and SETUP to the Debug Bridge config list"))))
        pre.lay.addWidget(look.label(tr("Presets are Debug Bridge config files: they can also be sent from its Config tab."),
                                     "muted", wrap=True))
        lay.addWidget(pre)
        self._line_type_picked()
        self.pair_status = look.label("", "muted", wrap=True)
        stat.lay.insertWidget(stat.lay.count() - 2, self.pair_status)
        return page

    @staticmethod
    def _de_labels():
        return [("low", tr("DE low (driver off)")), ("high", tr("DE high (driver always on)")),
                ("tx", tr("DE while transmitting"))]

    @staticmethod
    def _us_box(value):
        w = QDoubleSpinBox()
        w.setRange(0, 100000)
        w.setDecimals(2)
        w.setSuffix(" µs")
        w.setValue(value)
        return w

    # ---------------------------------------------------------------- form <-> values
    def _fmt(self):
        return U.Fmt(f"{self.data_bits.currentText()}{self.parity.currentData()}{self.stop_bits.currentText()}")

    def _baud(self):
        try:
            x = float(self.baud.currentText())
            if not math.isfinite(x):
                return None
            b = int(x)
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
            div = v & U.SETUP_BAUD_MASK
            err = U.baud_error(self.clk_hz, self._baud())
            self.setup_info.setText(tr("SETUP = {0} · divider {1} · real baud {2:.1f} ({3:+.2f} %){4}").format(
                f"0x{v:08X}", div, self.clk_hz / div, err, "  ⚠" if abs(err) > 2 else ""))
        except ValueError as exc:
            self.setup_info.setText(str(exc))
        if hasattr(self, "line_info"):
            self._line_changed()

    def _ctrl_value(self):
        return U.ctrl_value(self.de_mode.currentData(), self.half.isChecked(), self.echo.isChecked(),
                            self.tx_inv.isChecked(), self.rx_inv.isChecked(), self.tx_en.isChecked(), self.rx_en.isChecked())

    def _tail_us(self):
        if self.tail_auto.isChecked():
            return 1e6 / (self._baud() or 115200)
        return self.tail.value()

    def _timing_value(self):
        return U.timing_value(self.clk_hz, self.lead.value(), self._tail_us())

    def _line_type_picked(self):
        key = self.line_type.currentData()
        if key and not self._loading_line:
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
            i = self.line_type.findData(U.line_type_of(self._ctrl_value()) or "")
            if i != self.line_type.currentIndex():
                self.line_type.blockSignals(True)
                self.line_type.setCurrentIndex(i)
                self.line_type.blockSignals(False)
        ctrl, timing = self._ctrl_value(), self._timing_value()
        t = U.decode_timing(timing, self.clk_hz)
        txt = tr("PHY_CTRL = {0} · PHY_TIMING = {1}").format(f"0x{ctrl:08X}", f"0x{timing:08X}")
        if getattr(self, "pair", None) and self.paired:
            tx_ctrl, rx_ctrl = self._pair_ctrls()
            tx, rx = self._names()
            txt = tr("{0} (TX) PHY_CTRL = {1} · {2} (RX) PHY_CTRL = {3} · PHY_TIMING = {4}").format(
                tx, f"0x{tx_ctrl & 0xFF:08X}", rx, f"0x{rx_ctrl & 0xFF:08X}", f"0x{timing:08X}")
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

    def _show_status(self, st, fifo):
        names = U.status_names(st)
        caps = [n[4:].replace("RE", "/RE").replace("FLOW", "RTS/CTS") for n in names if n.startswith("HAS_")]
        self.status_vals["caps"].setText(", ".join(caps) or "—")
        state = [n.replace("RE_N", "/RE high") for n in names if not n.startswith("HAS_") and n != "CFG_ERR"]
        self.status_vals["state"].setText(", ".join(state) or tr("idle"))
        err = bool(st & U.ST_CFG_ERR)
        self.status_vals["cfg"].setText(tr("CFG_ERR: an unsupported setting was rejected") if err else tr("none"))
        self.status_vals["cfg"].setStyleSheet(f"color: {look.color('danger').name()}" if err else "")
        f = U.decode_fifo(fifo)
        self.status_vals["rx"].setText(tr("{0} / {1} bytes waiting").format(f["rx_fill"], 1 << f["rx_lg"]))
        self.status_vals["tx"].setText(tr("{0} / {1} free").format(f["tx_free"], 1 << f["tx_lg"]))

    # ---------------------------------------------------------------- board access
    def read_setup(self):
        self._read([U.SETUP], lambda v: self._set_setup_form(v[0]))

    def write_setup(self):
        try:
            v = self._setup_value()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        self._save_settings(clk_mhz=self.clk.value())
        done = lambda _: self.status(tr("SETUP written: {0}").format(f"0x{v:08X}"))  # noqa: E731
        if self.paired:
            return self._write_abs([(b + U.SETUP, v) for b in sorted(set(self._bases()))], done)
        self._write([(U.SETUP, v)], done)

    def _set_pair_line_form(self, tx_ctrl, rx_ctrl, timing):
        ct, cr = U.decode_ctrl(tx_ctrl), U.decode_ctrl(rx_ctrl)
        self._loading_line = True
        self.tx_inv.setChecked(ct["tx_inv"])
        self.rx_inv.setChecked(cr["rx_inv"])
        if timing is not None:
            t = U.decode_timing(timing, self.clk_hz)
            self.lead.setValue(t["lead_us"])
            self.tail_auto.setChecked(False)
            self.tail.setValue(t["tail_us"])
        self._loading_line = False
        self._line_changed()

    def read_line(self):
        if self.paired:
            tx_base, rx_base = self._bases()
            steps = [{"op": "read", "addr": tx_base + U.PHY_CTRL},
                     {"op": "read", "addr": rx_base + U.PHY_CTRL},
                     {"op": "read", "addr": tx_base + U.PHY_TIMING}]
            return self._call(lambda: self.client.batch(steps),
                              lambda v: self._set_pair_line_form(v[0], v[1], v[2]), tr("Read error"))
        self._read([U.PHY_CTRL, U.PHY_TIMING], lambda v: self._set_line_form(*v))

    def _save_line_settings(self):
        self._save_settings(clk_mhz=self.clk.value(), lead_us=self.lead.value(), tail_us=self.tail.value(),
                            tail_auto=self.tail_auto.isChecked())

    def write_line(self):
        ctrl = self._ctrl_value() | (U.CTRL_APPLY_NOW if self.apply_now.isChecked() else 0)
        self._save_line_settings()
        if self.paired:
            return self._write_abs(self._pair_writes(), lambda _: self.read_status())
        self._write([(U.PHY_TIMING, self._timing_value()), (U.PHY_CTRL, ctrl)], lambda _: self.read_status())

    def read_status(self):
        if not self.paired:
            self.pair_status.setText("")
            return self._read([U.PHY_STATUS, U.FIFO], lambda v: self._show_status(*v))
        tx_base, rx_base = self._bases()
        own = int(self.ip.get("base", 0))
        steps = [{"op": "read", "addr": b + o} for b in (tx_base, rx_base) for o in (U.PHY_STATUS, U.FIFO)]

        def ok(v):
            mine = (v[0], v[1]) if tx_base == own else (v[2], v[3])
            self._show_status(*mine)
            tx, rx = self._names()
            parts = []
            for name, role, (st, fifo) in ((tx, "TX", (v[0], v[1])), (rx, "RX", (v[2], v[3]))):
                f = U.decode_fifo(fifo)
                parts.append(tr("{0} ({1}): DE {2}, /RE {3}, RX {4} B, TX free {5}").format(
                    name, role, int(bool(st & U.ST_DE)), int(bool(st & U.ST_RE_N)), f["rx_fill"], f["tx_free"]))
            self.pair_status.setText(" · ".join(parts))
        self._call(lambda: self.client.batch(steps), ok, tr("Read error"))

    def clear_cfg_err(self):
        if self.paired:
            return self._write_abs([(b + U.PHY_STATUS, U.ST_CFG_ERR) for b in sorted(set(self._bases()))],
                                   lambda _: self.read_status())
        self._write([(U.PHY_STATUS, U.ST_CFG_ERR)], lambda _: self.read_status())

    def read_all(self):
        if self.paired:
            tx_base, rx_base = self._bases()
            steps = [{"op": "read", "addr": tx_base + U.SETUP},
                     {"op": "read", "addr": tx_base + U.PHY_CTRL},
                     {"op": "read", "addr": rx_base + U.PHY_CTRL},
                     {"op": "read", "addr": tx_base + U.PHY_TIMING}]

            def ok(v):
                self._set_setup_form(v[0])
                self._set_pair_line_form(v[1], v[2], v[3])
                self.read_status()
            return self._call(lambda: self.client.batch(steps), ok, tr("Read error"))

        def ok(v):
            self._set_setup_form(v[0])
            self._set_line_form(v[1], v[2])
            self._show_status(v[3], v[4])
        self._read([U.SETUP, U.PHY_CTRL, U.PHY_TIMING, U.PHY_STATUS, U.FIFO], ok)

    def write_all(self):
        try:
            setup = self._setup_value()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        ctrl = self._ctrl_value() | (U.CTRL_APPLY_NOW if self.apply_now.isChecked() else 0)
        self._save_line_settings()
        if self.paired:
            return self._write_abs(self._pair_writes(setup), lambda _: self.read_status())
        self._write([(U.PHY_TIMING, self._timing_value()), (U.PHY_CTRL, ctrl), (U.SETUP, setup)],
                    lambda _: self.read_status())

    def rx_reset(self):
        self._last_ovfl = False
        self._write_abs([(self._bases()[1] + U.RXREG, U.RX_OVFL)], lambda _: self.read_status())

    # ---------------------------------------------------------------- presets (Debug Bridge config files)
    def _preset_writes(self):
        return [("PHY_TIMING", self._timing_value()), ("PHY_CTRL", self._ctrl_value()), ("SETUP", self._setup_value())]

    def _preset_dir(self):
        return self._store.value("preset_dir", "") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "examples",
                                                                  "uart_phy")

    def save_preset(self):
        try:
            writes = self._preset_writes()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save preset"), os.path.join(self._preset_dir(), "line.axcfg.json"),
                                              "Config (*.axcfg.json)")
        if not path:
            return
        data = {"format": "axil-uart-config", "version": 1, "description": os.path.basename(path).split(".")[0],
                "writes": [{"ip": self.ip_name, "register": r, "value": f"0x{v:08X}"} for r, v in writes]}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1)
        except OSError as exc:
            return QMessageBox.critical(self, tr("Could not save"), str(exc))
        self._store.setValue("preset_dir", os.path.dirname(path))

    def open_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open preset"), self._preset_dir(), "Config (*.axcfg.json);;JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("format") != "axil-uart-config":
                raise ValueError(tr("Not a config file"))
            writes = [(w["ip"], w["register"], int(str(w["value"]), 0)) for w in data.get("writes", [])]
        except (OSError, ValueError, KeyError) as exc:
            return QMessageBox.critical(self, tr("Could not open"), str(exc))
        self._store.setValue("preset_dir", os.path.dirname(path))
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

    def add_to_config(self):
        try:
            writes = self._preset_writes()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Character format"), str(exc))
        self.bus.run(lambda: self.client.add_config(writes))

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

        self.rx_btn = look.button(tr("Start receiving"), "primary", self.toggle_rx,
                                  tr("Read RXREG periodically and show the received bytes"))
        self.poll_ms = QSpinBox()
        self.poll_ms.setRange(10, 2000)
        self.poll_ms.setSingleStep(10)
        self.poll_ms.setValue(50)
        self.poll_ms.setSuffix(" ms")
        self.poll_ms.setToolTip(tr("Read period"))
        self.log_poll = QCheckBox(tr("Log in traffic"))
        self.log_poll.setToolTip(tr("Show the RXREG reads in the Debug Bridge traffic log"))
        b_flags = look.button(tr("Clear flags"), "ghost", self.clear_flags, tr("Clear the sticky PERR / FERR flags"))
        b_rst = look.button(tr("RX reset"), "ghost", self.rx_reset, tr("Reset the receiver and the RX FIFO (clears OVFL)"))
        self._io_buttons += [b_flags, b_rst]
        lay.addLayout(look.hline(self.rx_btn, look.label(tr("Period"), "muted"), self.poll_ms, self.log_poll, "stretch",
                                 b_flags, b_rst, stretch=False))

        view = QHBoxLayout()
        view.setSpacing(8)
        view.addWidget(look.label(tr("Display"), "muted"))
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
        for w in (self.show_time, self.show_tx, self.autoscroll):
            view.addWidget(w)
        view.addSpacing(12)
        view.addWidget(look.label(tr("RX line end"), "muted"))
        self.rx_nl = QComboBox()
        for key, text in (("auto", tr("Auto (CR, LF, CR+LF)")), ("LF", "LF"), ("CR", "CR"), ("CR+LF", "CR+LF")):
            self.rx_nl.addItem(text, key)
        self.rx_nl.currentIndexChanged.connect(self._nl_changed)
        view.addWidget(self.rx_nl)
        view.addStretch(1)
        view.addWidget(look.button(tr("Clear"), "ghost", self.clear_term))
        view.addWidget(look.button(tr("Save…"), "ghost", self.save_term))
        lay.addLayout(view)

        self.term = QPlainTextEdit()
        self.term.setReadOnly(True)
        self.term.setMaximumBlockCount(TERM_BLOCKS)
        lay.addWidget(self.term, 1)

        send = QHBoxLayout()
        send.setSpacing(6)
        self.send_mode = QComboBox()
        self.send_mode.addItems(["ASCII", "HEX"])
        send.addWidget(self.send_mode)
        self.entry = QLineEdit()
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
        self.send_btn = look.button(tr("Send"), "primary", self.send)
        send.addWidget(self.send_btn)
        lay.addLayout(send)

        opts = QHBoxLayout()
        self.escapes = QCheckBox(tr("Escapes (\\r \\n \\t \\xNN)"))
        self.escapes.setChecked(True)
        opts.addWidget(self.escapes)
        opts.addSpacing(12)
        self.stats = look.label("", "muted")
        opts.addWidget(self.stats)
        opts.addStretch(1)
        self.file_btn = look.button(tr("Send file…"), "ghost", self.send_file)
        opts.addWidget(self.file_btn)
        lay.addLayout(opts)
        self.send_mode.currentIndexChanged.connect(
            lambda *_: (self.add_eol.setEnabled(self.send_mode.currentText() == "ASCII"),
                        self.escapes.setEnabled(self.send_mode.currentText() == "ASCII")))
        return page

    def eventFilter(self, obj, ev):
        if obj is self.entry and ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._history(-1 if ev.key() == Qt.Key.Key_Up else 1)
            return True
        return super().eventFilter(obj, ev)

    def _history(self, step):
        if self.history:
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

    def _follow(self):
        if self.autoscroll.isChecked():
            self.term.moveCursor(QTextCursor.MoveOperation.End)
            self.term.ensureCursorVisible()

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
        self._follow()

    def _mark(self, text):
        if not self._last_nl:
            self._ins("\n", self.fmt_rx)
        self._ins(f"[{text}]\n", self.fmt_err)
        self._last_nl = True
        self._follow()

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
        if len(data) > 64 * 1024 and not look.confirm(self, tr("Large file"),
                                                      tr("{0} bytes will be sent; this can take long. Continue?").format(len(data)),
                                                      tr("Send")):
            return
        self.queue_tx(data)

    def queue_tx(self, data):
        if not self.connected or self.validating:
            self.status(tr("Not connected"))
            return
        self.tx_queue += data
        if self.show_tx.isChecked():
            self._render(data, True)
        self._update_stats()
        self._pump_tx()

    def _pump_tx(self):
        if self.tx_busy or not self.tx_queue or not self.connected:
            return
        chunk = bytes(self.tx_queue[:MAX_TX_CHUNK])
        c, base = self.client, self._bases()[0]

        def job():
            free = (c.read(addr=base + U.FIFO, quiet=True) >> U.TX_FREE_SHIFT) & U.TX_FREE_MASK
            n = min(free, len(chunk))
            if n:
                c.batch([{"op": "write", "addr": base + U.TXREG, "value": b} for b in chunk[:n]], quiet=True)
            return n

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
        self.bus.run(job, done, fail)

    # ---------------------------------------------------------------- RX
    def toggle_rx(self):
        if self.rx_running:
            return self.stop_rx()
        if not self.connected:
            return self.status(tr("Not connected"))
        self.rx_running = True
        self.rx_btn.setText(tr("Stop receiving"))
        self.rx_btn.setProperty("variant", None)
        look.repolish(self.rx_btn)
        self._poll()

    def stop_rx(self):
        if not getattr(self, "rx_running", False):
            return
        self.rx_running = False
        self.rx_btn.setText(tr("Start receiving"))
        self.rx_btn.setProperty("variant", "primary")
        look.repolish(self.rx_btn)

    def _poll(self):
        if not self.rx_running or self.rx_busy:
            return
        if not self.connected or self.validating:
            return self.stop_rx()
        c, quiet, base = self.client, not self.log_poll.isChecked(), self._bases()[1]

        def job():
            words = c.read_until(0, U.RX_EMPTY, U.RX_EMPTY, MAX_RX_PER_POLL, addr=base + U.RXREG, quiet=quiet)
            data, bad, flags = bytearray(), set(), 0
            for v in words:
                flags |= v & (U.RX_PERR | U.RX_FERR | U.RX_BREAK | U.RX_OVFL)
                if v & U.RX_EMPTY:
                    break
                if v & (U.RX_BYTE_PERR | U.RX_BYTE_FERR):
                    bad.add(len(data))
                    flags |= v & (U.RX_BYTE_PERR | U.RX_BYTE_FERR)
                data.append(v & 0xFF)
            return bytes(data), bad, flags

        def done(result):
            self.rx_busy = False
            data, bad, flags = result
            if data:
                self.rx_count += len(data)
                self._render(data, False, bad)
            self._handle_flags(flags)
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
        self.bus.run(job, done, fail)

    def _handle_flags(self, flags):
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
            a = self._bases()[1] + U.RXREG
            self.bus.run(lambda: self.client.write(addr=a, value=U.RX_PERR | U.RX_FERR, quiet=True))

    def clear_flags(self):
        self._write_abs([(self._bases()[1] + U.RXREG, U.RX_PERR | U.RX_FERR)])

    # ================================================================ validation
    def _build_validation_tab(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        st = self.client.settings.get("validation", {})
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(10)
        conn = look.Card(tr("Connections"), tr("The register access uses the Debug Bridge connection"))
        form = QFormLayout()
        self.v_line = QComboBox()
        self.v_line.setEditable(True)
        self.v_line.setToolTip(tr("Serial adapter (USB-RS485 / RS-232) on the UART line: the PC is the far end"))
        self.v_refresh = look.button("↻", "ghost", self._fill_line_ports, tr("Refresh ports"))
        r = QHBoxLayout()
        r.addWidget(self.v_line, 1)
        r.addWidget(self.v_refresh)
        self.v_pc = QComboBox()
        self.v_pc.addItem(tr("One COM for both directions (4-wire adapter)"), "one")
        self.v_pc.addItem(tr("A COM on each bus (two 2-wire adapters)"), "two")
        self.v_pc.addItem(tr("No PC: the two buses wired to each other"), "none")
        self.v_pc.setToolTip(tr("How the PC is connected to the pair's two RS-485 buses"))
        self.v_pc.setCurrentIndex(max(0, self.v_pc.findData(st.get("pc", "one"))))
        form.addRow(tr("PC connection"), self.v_pc)
        form.addRow(tr("Line adapter"), r)
        self._line_row = r
        self.v_com_a, self.v_com_b = QComboBox(), QComboBox()
        for w in (self.v_com_a, self.v_com_b):
            w.setEditable(True)
        form.addRow("A", self.v_com_a)
        form.addRow("B", self.v_com_b)
        self.v_loop = QCheckBox(tr("TX wired to RX (loopback, no adapter)"))
        self.v_loop.setChecked(bool(st.get("loopback", False)))
        form.addRow("", self.v_loop)
        self.v_swap = QCheckBox(tr("Swap the directions and run again"))
        self.v_swap.setToolTip(tr("Both transceivers are tested as sender and as receiver"))
        self.v_swap.setChecked(bool(st.get("swap", True)))
        form.addRow("", self.v_swap)
        self._conn_form = form
        self.v_pc.currentIndexChanged.connect(self._val_mode)
        conn.lay.addLayout(form)
        ll.addWidget(conn)

        lt = look.Card(tr("Line under test"), tr("Written to PHY_CTRL / PHY_TIMING before the tests"))
        form = QFormLayout()
        self.v_type = QComboBox()
        for key, d in U.LINE_TYPES.items():
            self.v_type.addItem(d["label"], key)
        self.v_type.setCurrentIndex(max(0, self.v_type.findData(st.get("line_type", "rs485-4w"))))
        self.v_de = QComboBox()
        self.v_de.addItem(tr("From the line type"), "")
        for key, text in self._de_labels():
            self.v_de.addItem(text, key)
        self.v_de.setCurrentIndex(max(0, self.v_de.findData(st.get("de", ""))))
        self.v_lead = self._us_box(float(st.get("lead_us", 10.0)))
        tail = st.get("tail_us", "auto")
        self.v_tail = self._us_box(10.0 if tail == "auto" else float(tail))
        self.v_tail_auto = QCheckBox(tr("1 bit time"))
        self.v_tail_auto.setChecked(tail == "auto")
        self.v_tail_auto.toggled.connect(lambda on: self.v_tail.setEnabled(not on))
        self.v_tail.setEnabled(not self.v_tail_auto.isChecked())
        form.addRow(tr("Line type"), self.v_type)
        form.addRow(tr("DE mode"), self.v_de)
        self._lt_form = form
        form.addRow(tr("DE lead"), self.v_lead)
        r = QHBoxLayout()
        r.addWidget(self.v_tail, 1)
        r.addWidget(self.v_tail_auto)
        form.addRow(tr("DE tail"), r)
        lt.lay.addLayout(form)
        ll.addWidget(lt)

        mx = look.Card(tr("Test matrix"))
        form = QFormLayout()
        self.v_mode = QComboBox()
        self.v_mode.addItem(tr("All tests"), "")
        self.v_mode.addItem(tr("DE lead sweep"), "lead")
        self.v_mode.addItem(tr("DE tail sweep"), "tail")
        self.v_mode.setToolTip(tr("Sweep: the smallest lead / tail that works with DE switched per transmission, "
                                  "at the last baud rate and the first format (needs the line adapter)"))
        self.v_mode.setCurrentIndex(max(0, self.v_mode.findData(st.get("sweep") or "")))
        self.v_bauds = QLineEdit(", ".join(str(b) for b in st.get("bauds", [9600, 19200, 38400, 57600, 115200])))
        self.v_formats = QLineEdit(", ".join(st.get("formats", ["8N1", "8E1", "8O1", "8N2", "7E1", "7O1", "8M1", "8S1"])))
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
        ll.addLayout(look.hline(
            look.button(tr("Open settings…"), "ghost", self.open_val_settings, tr("Load validation settings (JSON)")),
            look.button(tr("Save settings…"), "ghost", self.save_val_settings, tr("Save the validation settings (JSON)"))))
        ll.addStretch(1)
        self.v_mode.currentIndexChanged.connect(self._mode_changed)
        self._val_inputs = [self.v_pc, self.v_com_a, self.v_com_b, self.v_swap, self.v_line, self.v_refresh, self.v_loop, self.v_type, self.v_de, self.v_lead, self.v_tail,
                            self.v_tail_auto, self.v_mode, self.v_bauds, self.v_formats, self.v_count, self.v_quick,
                            self.v_burst, self.v_trials, self.v_res, self.v_smode, self.v_traffic]
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.setSpacing(8)
        self.run_btn = look.button(tr("Start"), "primary", self.start_validation,
                                   tr("Run the tests on the board; the registers are restored at the end"))
        self.stop_btn = look.button(tr("Stop"), None, self.stop_validation)
        self.v_summary = look.Pill("")
        self.v_save = look.button(tr("Save report…"), "ghost", self.save_report)
        self.v_save.setEnabled(False)
        rl.addLayout(look.hline(self.run_btn, self.stop_btn, 8, self.v_summary, "stretch", self.v_save, stretch=False))
        self.v_status = look.label(tr("Connect the Debug Bridge, choose the line and start."), "muted", wrap=True)
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
        vs.addWidget(self.vlog)
        vs.setSizes([420, 160])
        rl.addWidget(vs, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 700])
        lay.addWidget(split, 1)
        self._fill_line_ports()
        if st.get("line"):
            self.v_line.setCurrentText(st["line"])
        lines = st.get("lines") or {}
        self._val_lines = lines
        self._mode_changed()
        self._val_mode()
        return page

    def _val_mode(self, *_):
        """Show the connection fields of a single IP or of a pair."""
        pair = self.paired
        pc = self.v_pc.currentData()
        f = self._conn_form
        f.setRowVisible(self.v_pc, pair)
        f.setRowVisible(self._line_row, (not pair) or pc == "one")
        f.setRowVisible(self.v_com_a, pair and pc == "two")
        f.setRowVisible(self.v_com_b, pair and pc == "two")
        f.setRowVisible(self.v_loop, not pair)
        f.setRowVisible(self.v_swap, pair)
        self._lt_form.setRowVisible(self.v_type, not pair)
        self._lt_form.setRowVisible(self.v_de, not pair)
        if pair:
            peer = self.pair["peer"]
            for w, name in ((self.v_com_a, self.ip_name), (self.v_com_b, peer)):
                lbl = f.labelForField(w)
                if lbl is not None:
                    lbl.setText(tr("COM on {0}'s bus").format(name))
            self.v_com_a.setCurrentText(self._val_lines.get(self.ip_name, self.v_com_a.currentText()))
            self.v_com_b.setCurrentText(self._val_lines.get(peer, self.v_com_b.currentText()))

    def _fill_line_ports(self):
        ports = serial_ports()
        for box, none in ((self.v_line, True), (self.v_com_a, False), (self.v_com_b, False)):
            cur = box.currentText()
            box.clear()
            if none:
                box.addItem(tr("None"), "")
            for p in ports:
                box.addItem(p, p)
            if cur:
                box.setCurrentText(cur)

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
        pair, pc, lines, line_type, de, loop = None, "one", {}, self.v_type.currentData(), self.v_de.currentData(), \
            self.v_loop.isChecked()
        if self.paired:
            peer = self._peer()
            pair = {"a": self.ip_name, "a_base": int(self.ip.get("base", 0)), "b": peer["name"],
                    "b_base": int(peer["base"]), "tx": "a" if self.pair["role"] == "tx" else "b"}
            pc = self.v_pc.currentData()
            lines = {self.ip_name: self.v_com_a.currentText().strip(), peer["name"]: self.v_com_b.currentText().strip()}
            tx_de = self.pair.get("tx_de", "high")
            line_type, de, loop = ("rs485-4w" if tx_de == "tx" else "rs422"), "", False
        return V.Settings(base=int(self.ip.get("base", 0)), clk=self.clk_hz,
                          line=self._line_port() if not pair or pc == "one" else "",
                          loopback=loop, line_type=line_type, pair=pair, pc=pc, lines=lines,
                          swap=self.v_swap.isChecked(),
                          de=de, lead_us=self.v_lead.value(),
                          tail_us="auto" if self.v_tail_auto.isChecked() else self.v_tail.value(),
                          bauds=self.v_bauds.text(), formats=self.v_formats.text(), count=self.v_count.value(),
                          quick=self.v_quick.isChecked(), burst_all=self.v_burst.isChecked(),
                          sweep=self.v_mode.currentData() or None, sweep_mode=self.v_smode.currentData(),
                          sweep_trials=self.v_trials.value(), sweep_resolution=self.v_res.value())

    def _set_val_form(self, s):
        self.v_pc.setCurrentIndex(max(0, self.v_pc.findData(s.pc)))
        self.v_line.setCurrentText(s.line or tr("None"))
        self._val_lines = dict(s.lines or {})
        self.v_loop.setChecked(s.loopback)
        self.v_swap.setChecked(s.swap)
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
        self._val_mode()

    def save_val_settings(self):
        try:
            d = self._val_settings().to_dict()
        except ValueError as exc:
            return QMessageBox.warning(self, tr("Validation"), str(exc))
        path, _ = QFileDialog.getSaveFileName(self, tr("Save settings"),
                                              os.path.join(self._preset_dir(), f"{self.ip_name}_validation.json"),
                                              "JSON (*.json)")
        if path:
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
            if "clk" in d:
                self.clk.setValue(float(d["clk"]) / 1e6)
            d = {k: v for k, v in d.items() if k not in ("hexbus", "hexbus_baud", "report", "base", "clk")}
            self._set_val_form(V.Settings.from_dict(d))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, tr("Could not open"), str(exc))

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
        used = [s.line] if s.line else []
        if s.pair and s.pc == "two":
            used = [v for v in s.lines.values() if v]
            if len(used) != 2 or used[0] == used[1]:
                return QMessageBox.warning(self, tr("Validation"), tr("Choose a different COM port for each bus."))
        if s.sweep and not used:
            return QMessageBox.warning(self, tr("Validation"), tr("The DE sweep needs the line adapter."))
        if s.pair and s.pc == "one" and not s.line and not s.sweep:
            return QMessageBox.warning(self, tr("Validation"), tr("Choose the 4-wire adapter's COM port."))
        if used and V.serial is None:
            return QMessageBox.critical(self, tr("Validation"), "pyserial is not installed (pip install pyserial)")
        if self.client.port and self.client.port in used:
            return QMessageBox.warning(self, tr("Validation"), tr("The line adapter cannot be the Debug Bridge's own port."))
        self._save_settings(clk_mhz=self.clk.value(), validation=s.to_dict())
        self.stop_rx()
        self.results.setRowCount(0)
        self.vlog.clear()
        self.v_save.setEnabled(False)
        self.cancel.clear()
        self.report = V.Report(on_row=self._row.emit, on_log=self._vlog.emit, cancel=self.cancel)
        self._settings_run = s
        self.validating = True
        self._update_controls()
        self._mode_changed()
        self.v_summary.set(tr("Running…"), "accent")
        self.v_status.setText(tr("Running on {0} @ {1}…").format(
            " ⇄ ".join(self._names()) if s.pair else self.ip_name, f"0x{s.base:08X}"))
        self._t0 = time.monotonic()
        client, report, quiet = self.client, self.report, not self.v_traffic.isChecked()

        def job():
            port, ports = None, {}
            client.lock()
            try:
                if s.line:
                    port = V.open_line(s.line)
                if s.pair and s.pc == "two":
                    for name, com in s.lines.items():
                        ports[name] = V.open_line(com)
                return V.run_validation(ValidationLink(client, quiet), s, report, port, ports)
            finally:
                for p in [port] + list(ports.values()):
                    if p is not None:
                        p.close()
                try:
                    client.unlock()
                except AxilError:
                    pass

        threading.Thread(target=lambda: self._run_thread(job), daemon=True).start()

    def _run_thread(self, job):
        # the validation runs on its own thread: the window keeps answering AXIL Studio's events
        try:
            self._vdone.emit(job())
        except Exception as exc:  # noqa: BLE001
            self._vfail.emit(exc)

    def _failed(self, exc):
        self.report.add("FAIL", "AXIL Studio" if isinstance(exc, AxilError) else tr("Line adapter"), str(exc))
        self._finished("error")

    def stop_validation(self):
        if self.validating:
            self.cancel.set()
            self.v_summary.set(tr("Stopping…"), "warn")
            self.stop_btn.setEnabled(False)

    def _finished(self, result):
        self.validating = False
        self._update_controls()
        self._mode_changed()
        c = self.report.counts()
        text = tr("{0} passed · {1} failed · {2} skipped").format(c["PASS"], c["FAIL"], c["SKIP"])
        if result == "stopped":
            self.v_summary.set(tr("Stopped"), "warn")
        elif c["FAIL"] or result == "error":
            self.v_summary.set(tr("Failed"), "err")
        else:
            self.v_summary.set(tr("Passed"), "ok")
        self.v_status.setText(f"{text} · {time.monotonic() - self._t0:.1f} s")
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
        if self.validating and self.report:
            c = self.report.counts()
            self.v_status.setText(tr("Running · {0}: {1} passed, {2} failed").format(row.get("section", ""), c["PASS"], c["FAIL"]))

    def _color_row(self, r):
        colors = {"PASS": "success", "FAIL": "danger", "SKIP": "warning", "INFO": "muted"}
        it = self.results.item(r, 0)
        if it is not None:
            it.setForeground(look.color(colors.get(it.data(Qt.ItemDataRole.UserRole), "text")))
            it.setFont(look.bold(it.font()))

    def save_report(self):
        if not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Save report"), os.path.join(
            self._preset_dir(), f"{self.ip_name}_validation_{time.strftime('%Y%m%d_%H%M')}.json"), "JSON (*.json)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self.report.to_dict(self._settings_run, getattr(self, "_stopped", False)), fh, indent=2)
            except OSError as exc:
                QMessageBox.critical(self, tr("Could not save"), str(exc))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("axiluart_phy interface")
    try:
        client = AxilClient.from_env("axiluart_phy UART interface")
    except (AxilError, OSError) as exc:
        look.use_language(os.environ.get("AXIL_STUDIO_LANG", "en"))
        QMessageBox.critical(None, tr("UART interface"), tr("This window is opened from AXIL Studio's UART-to-AXI Debug "
                                                           "Bridge (IP interface of axiluart_phy).") + f"\n\n{exc}")
        return 2
    look.use_language(client.lang)
    look.apply(app, client.theme)
    w = UartWindow(client)
    w.show()
    code = app.exec()
    w.bus.shutdown()
    client.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
