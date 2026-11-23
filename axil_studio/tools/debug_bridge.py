"""UART-to-AXI Debug Bridge: register access on the board over the ZipCPU hexbus bridge.

The address map comes from the project (every IP instance of the Address Map Editor with its
IP's register map); IPs that are not in the project can be added here for debugging only, from a
register spec, another project file or an IP library, and any address can be accessed raw.
Register maps and project addresses are not edited here: the Register Map Editor and the
Address Map Editor open for that. Every bit field gets an input that matches its data type and kind: a number box
in the field's format, a switch for booleans, a list for defined values, a button for PULSE
commands and for clearing W1C flags.

An IP whose register map names an IP interface (IPBlock.panel, e.g. the UART interface of
axiluart_phy) opens it from here: the interface is a separate application found through its
manifest; it connects to this page's local service and works through this page's connection and
request queue (see axil_studio.interfaces).
"""

import collections
import copy
import csv
import datetime
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import hexbus
from axil_studio.core import numfmt as N
from axil_studio import ROOT, icons, theme, ui
from axil_studio import interfaces as ifc
from axil_studio.interfaces import host as ifhost
from axil_studio.i18n import language
from axil_studio.dialogs import BaseDialog, CustomValueDialog, FieldMapDialog
from axil_studio.tools.base import ToolPage
from axil_studio.core.model import KIND_READS, KIND_WRITES
from axil_studio.core.model import (Field, IPBlock, Project, Register, hex32, load_config, parse_int, save_config,
                   single_line)
from axil_studio.i18n import tr

BAUDS = ["115200", "230400", "460800", "921600", "1000000", "2000000"]
LOG_LINES = 2000
HISTORY_MAX = 50000          # samples kept per watched register
HISTORY_SHOWN = 500          # newest samples listed in the history view
ROLE = Qt.ItemDataRole.UserRole
# field table columns
F_NAME, F_BITS, F_KIND, F_TYPE, F_READ, F_WRITE, F_MEAN = range(7)


def raw_ip(addr):
    """A one-register IP for raw access to any address."""
    return IPBlock(tr("Raw address"), addr & ~3, "", [Register("DATA", 0, "RW", 0, tr("32-bit access to any address"),
                                                         [Field("DATA", 0, 32, "", "", [], "hex")])])


class DebugBridgePage(ToolPage):
    tool_id = "debug"
    title = "UART-to-AXI Debug Bridge"
    short_title = "UART-to-AXI Debug Bridge"
    subtitle = tr("Read and write AXI4-Lite registers on hardware over UART.")
    description = tr("Register access on hardware through a UART hexbus bridge, organized by the project's address map: decoded register views, periodic reads and a built-in simulator for work without a board.")
    icon_name = "debug"

    _log = Signal(str, str)
    connection_changed = Signal(bool)       # for IP interfaces

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.link = hexbus.HexbusLink(log_cb=self._log.emit)
        self.bus = ui.Async(self)
        self.ip = self.reg = None
        self.read_value = None        # last value read from the board
        self.staged = None            # value that tr("Write") will write
        self.last_written = {}        # address -> value (base for write-only registers)
        self.raw = raw_ip(0x00000000)
        self.group = None             # "project" | "extra" | "raw"
        self.reads = {}               # address -> (value, "HH:MM:SS", source): last value read, any screen
        self.watch = []               # live watch rows: dict(key, value, changed, read, count, flash)
        self.config = []              # config rows: dict(group, ip, reg, value)
        self.cfg_path = None
        self.cfg_edit = None          # config row loaded into the register panel
        self._polling = False
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self._loading = False
        self.service = None           # local service of the IP interfaces (started on first use)
        self.ifaces = {}              # interface id -> interfaces.Interface (found on disk)
        self._exclusive = None        # IP interface holding the link (hardware validation)
        self._was_connected = False

        # ---- connection bar
        conn = ui.Card(margins=12)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(ui.label("Port", "muted"))
        self.port_cb = QComboBox()
        self.port_cb.setMinimumWidth(200)
        row.addWidget(self.port_cb)
        self.refresh_btn = QToolButton()
        self.refresh_btn.setProperty("variant", "ghost")
        self.refresh_btn.setToolTip(tr("Refresh ports"))
        self.refresh_btn.clicked.connect(self.refresh_ports)
        row.addWidget(self.refresh_btn)
        row.addSpacing(10)
        row.addWidget(ui.label(tr("Baud"), "muted"))
        self.baud_cb = QComboBox()
        self.baud_cb.setEditable(True)
        self.baud_cb.addItems(BAUDS)
        self.baud_cb.setCurrentText("115200")
        self.baud_cb.setMinimumWidth(110)
        row.addWidget(self.baud_cb)
        row.addSpacing(10)
        self.conn_btn = ui.button(tr("Connect"), "plug", "primary", self.toggle_connection)
        row.addWidget(self.conn_btn)
        row.addStretch(1)
        self.conn_pill = ui.Pill("● " + tr("Not connected"), "off")
        row.addWidget(self.conn_pill)
        conn.lay.addLayout(row)
        self.body.addWidget(conn)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: address map
        left = ui.Card(tr("Address map"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Name"), tr("Address"), tr("Access")])
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(self._select)
        self.tree.itemDoubleClicked.connect(self._tree_double_click)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        left.lay.addWidget(self.tree, 1)
        lbar = QHBoxLayout()
        lbar.setSpacing(4)
        add_menu = QMenu(self)
        add_menu.addAction(tr("From a register spec (.regspec.json)…"), self.add_from_spec)
        add_menu.addAction(tr("From another project file (.axproj.json)…"), self.add_from_project)
        add_menu.addAction(tr("From an IP library…"), self.add_from_library)
        add_btn = ui.button(tr("Extra IP"), "plus", "ghost", None,
                            tr("Add an IP that is not in the project, for debugging only (to add an IP to the project: Address Map Editor)"))
        add_btn.setMenu(add_menu)
        lbar.addWidget(add_btn)
        self.remove_btn = ui.button(tr("Remove"), None, "ghost", self.remove_extra, tr("Remove the selected extra IP from the list"))
        lbar.addWidget(self.remove_btn)
        lbar.addStretch(1)
        left.lay.addLayout(lbar)
        left.setMinimumWidth(320)
        split.addWidget(left)

        # ---- right: tabs on top: register · live watch · config · traffic
        right = ui.Card(margins=0)
        self.rtabs = ui.DetachableTabs(self.title)
        right.lay.addWidget(self.rtabs)
        self.reg_card = QWidget()
        self.reg_card.lay = QVBoxLayout(self.reg_card)
        self.reg_card.lay.setContentsMargins(16, 14, 16, 14)
        self.reg_card.lay.setSpacing(10)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.reg_title = ui.label(tr("Select a register"), "cardTitle")
        self.reg_desc = ui.label(tr("Select a register in the map on the left."), "muted", wrap=True)
        col.addWidget(self.reg_title)
        col.addWidget(self.reg_desc)
        top.addLayout(col, 1)
        self.panel_btn = ui.button(tr("IP interface"), "external", None, lambda: self.open_panel(),
                                   tr("Open the IP's own interface (it uses this connection)"))
        self.panel_btn.hide()
        top.addWidget(self.panel_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.access_pill = ui.Pill("")
        self.access_pill.hide()
        top.addWidget(self.access_pill, 0, Qt.AlignmentFlag.AlignTop)
        self.reg_card.lay.addLayout(top)

        # raw address row (only for "Ham adres erişimi")
        self.raw_row = QWidget()
        rr = QHBoxLayout(self.raw_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(ui.label(tr("Address"), "muted"))
        self.raw_ed = QLineEdit(hex32(self.raw.base))
        self.raw_ed.setProperty("mono", "true")
        self.raw_ed.setMaximumWidth(160)
        self.raw_ed.editingFinished.connect(self._raw_addr_changed)
        rr.addWidget(self.raw_ed)
        rr.addStretch(1)
        self.reg_card.lay.addWidget(self.raw_row)
        self.raw_row.hide()

        vrow = QHBoxLayout()
        vrow.setSpacing(8)
        vrow.addWidget(ui.label(tr("Read"), "muted"))
        self.read_lbl = ui.label("—", "mono")
        self.read_lbl.setMinimumWidth(100)
        self.read_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vrow.addWidget(self.read_lbl)
        self.read_btn = ui.button(tr("Read value"), "read", None, self.read_reg, "F5")
        vrow.addWidget(self.read_btn)
        vrow.addSpacing(16)
        vrow.addWidget(ui.label(tr("To write"), "muted"))
        self.value_ed = QLineEdit()
        self.value_ed.setProperty("mono", "true")
        self.value_ed.setPlaceholderText("0x00000000")
        self.value_ed.setMinimumWidth(120)
        self.value_ed.setMaximumWidth(150)
        self.value_ed.returnPressed.connect(self.write_reg)
        self.value_ed.textEdited.connect(self._value_edited)
        vrow.addWidget(self.value_ed)
        self.write_btn = ui.button(tr("Write"), "write", "primary", self.write_reg, tr("Write the whole register (Ctrl+Enter)"))
        vrow.addWidget(self.write_btn)
        self.reset_btn = ui.button(tr("Revert"), None, "ghost", self._reset_staged, tr("Reset the value to write to the value read"))
        vrow.addWidget(self.reset_btn)
        vrow.addStretch(1)
        self.reg_card.lay.addLayout(vrow)
        vrow = QHBoxLayout()
        vrow.setSpacing(8)
        self.stamp = ui.label("", "muted")
        vrow.addWidget(self.stamp)
        vrow.addStretch(1)
        self.watch_btn = ui.button(tr("Watch"), None, "ghost", lambda: self.add_watch([self._cur_key()]),
                                   tr("Add this register to live watch"))
        vrow.addWidget(self.watch_btn)
        self.cfg_btn = ui.button(tr("Add to config"), None, "ghost", self.config_from_panel,
                                 tr("Add the value to write to the config list"))
        vrow.addWidget(self.cfg_btn)
        vrow.addSpacing(8)
        vrow.addWidget(ui.label(tr("Display"), "muted"))
        self.view_cb = QComboBox()
        self.view_cb.addItem(tr("Data type"), "")
        for key, text in N.BASE_FORMATS:
            if key not in ("ufix", "sfix", "enum"):
                self.view_cb.addItem(text, key)
        self.view_cb.setToolTip(tr("How bit field values are shown. Default: the data type set in the Register Map Editor"))
        self.view_cb.currentIndexChanged.connect(lambda *_: self._fill_fields())
        vrow.addWidget(self.view_cb)
        self.reg_card.lay.addLayout(vrow)

        self.fields = QTableWidget(0, 7)
        self.fields.setHorizontalHeaderLabels([tr("Bit field"), tr("Bits"), tr("Type"), tr("Data type"), tr("Read"), tr("To write"),
                                               tr("Meaning")])
        self.fields.verticalHeader().setVisible(False)
        self.fields.verticalHeader().setDefaultSectionSize(38)
        self.fields.setShowGrid(False)
        self.fields.setAlternatingRowColors(True)
        self.fields.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.fields.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.fields.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.fields.customContextMenuRequested.connect(self._field_menu_at)
        self.reg_card.lay.addWidget(self.fields, 1)
        self.rtabs.addTab(self.reg_card, "Register")
        self.watch_tab = self._build_watch_tab()
        self.cfg_tab = self._build_config_tab()
        self.rtabs.addTab(self.watch_tab, tr("Live watch"))
        self.rtabs.addTab(self.cfg_tab, "Config")
        # traffic log below the tabs (resizable)
        log_card = ui.Card(tr("Traffic"), margins=12)
        log_card.add_action(ui.button(tr("Clear"), None, "ghost", lambda: self.log.clear()))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("mono", "true")
        self.log.setMaximumBlockCount(LOG_LINES)
        log_card.lay.addWidget(self.log, 1)
        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.setChildrenCollapsible(True)
        vsplit.addWidget(right)
        vsplit.addWidget(log_card)
        vsplit.setStretchFactor(0, 1)
        vsplit.setSizes([640, 150])
        split.addWidget(vsplit)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900])
        self.body.addWidget(split, 1)

        self._log.connect(self._append_log)
        self.refresh_interfaces()
        self.refresh_ports()
        self._fill_tree()
        self._update_controls()
        self.on_theme()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Debug")
        win = self.window()
        for text, slot, key in ((tr("Connect / disconnect"), self.toggle_connection, "Ctrl+K"),
                                (tr("Read register"), self.read_reg, "F5"),
                                (tr("Write register"), self.write_reg, "Ctrl+Return"),
                                (tr("Refresh ports"), self.refresh_ports, None),
                                (None, None, None),
                                (tr("Add IP: from a register spec…"), self.add_from_spec, None),
                                (tr("Add IP: from a project file…"), self.add_from_project, None)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(QKeySequence(key))
            m.addAction(a)
        m.addSeparator()
        m.addAction(tr("IP interface folders…"), self.interface_folders)
        m.addSeparator()
        m.addAction(tr("Clear traffic"), self.log.clear)

    # ================================================================ connection
    def refresh_ports(self):
        cur = self.port_cb.currentText()
        self.port_cb.clear()
        self.port_cb.addItems(hexbus.list_ports())
        self.port_cb.setCurrentText(cur or hexbus.SIM_PORT)

    def toggle_connection(self):
        if self.link.connected:
            self.link.close()
            self._log.emit("INFO", tr("Connection closed"))
            self._update_controls()
            return
        port = self.port_cb.currentText()
        try:
            baud = int(self.baud_cb.currentText())
        except ValueError:
            return self.error(tr("Invalid baud rate"), self.baud_cb.currentText())
        self.conn_btn.setEnabled(False)
        self.conn_pill.set(tr("Connecting…"), "accent")

        def done(_):
            self._log.emit("INFO", tr("Connected to {0} @ {1}").format(port, baud))
            self._update_controls()

        def fail(exc):
            self._update_controls()
            self.error(tr("Could not connect"), exc)
        self.bus.run(lambda: self.link.open(port, baud), done, fail)

    def _update_controls(self):
        on = self.link.connected
        if on != self._was_connected:
            self._was_connected = on
            self.connection_changed.emit(on)
        self.conn_btn.setEnabled(self._exclusive is None)
        self.conn_btn.setText(tr("Disconnect") if on else tr("Connect"))
        self.conn_btn.setProperty("variant", None if on else "primary")
        theme.repolish(self.conn_btn)
        ui.refresh_icons(self.conn_btn.parentWidget())
        self.port_cb.setEnabled(not on)
        self.baud_cb.setEnabled(not on)
        self.refresh_btn.setEnabled(not on)
        if on:
            self.conn_pill.set("● " + tr("Connected · {0}").format(self.port_cb.currentText()), "ok")
        else:
            self.conn_pill.set("● " + tr("Not connected"), "off")
        reg = self.reg
        free = on and self._exclusive is None
        self.read_btn.setEnabled(bool(free and reg and reg.readable))
        self.write_btn.setEnabled(bool(free and reg and reg.writable))
        self._update_panel_btn()
        self.value_ed.setEnabled(bool(reg and reg.writable))
        self.reset_btn.setEnabled(bool(reg and reg.writable))
        self.watch_btn.setEnabled(bool(reg and reg.readable))
        self.cfg_btn.setEnabled(bool(reg and reg.writable))
        self.cfg_btn.setText(tr("Update config row") if self.cfg_edit is not None else tr("Add to config"))
        self._update_watch_controls()
        self._update_config_controls()
        for row in range(self.fields.rowCount()):
            holder = self.fields.cellWidget(row, F_WRITE)
            for b in (holder.findChildren(type(self.write_btn)) if holder else []):
                if b.property("command"):
                    b.setEnabled(on)

    # ================================================================ address map
    def _extra(self):
        """IPs added in the debugger only (stored in the project under "debug")."""
        d = self.ctx.project.debug or {}
        return [IPBlock.from_dict(x) for x in d.get("extra_ips", [])]

    def _set_extra(self, ips):
        d = dict(self.ctx.project.debug or {})
        d["extra_ips"] = [ip.to_dict() for ip in ips]
        self.ctx.project.debug = d if d["extra_ips"] else None
        self.ctx.mark_modified(self.tool_id)

    def _find_ip(self, group, name):
        if group == "raw":
            return self.raw
        ips = self.ctx.address_ips() if group == "project" else self._extra()
        return next((ip for ip in ips if ip.name == name), None)

    def _fill_tree(self):
        sel = self._sel_key()
        self.tree.blockSignals(True)
        self.tree.clear()
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        target = None

        def add_group(title, ips, group):
            if not ips and group == "extra":
                return
            g = QTreeWidgetItem([title, "", ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            g.setForeground(0, muted)
            g.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(g)
            if not ips:
                e = QTreeWidgetItem([tr("Address map is empty (Address Map Editor)"), "", ""])
                e.setForeground(0, muted)
                e.setFlags(Qt.ItemFlag.ItemIsEnabled)
                g.addChild(e)
            if group == "project":           # the address map's tree: IPs under their subsystem
                by_name = {ip.name: ip for ip in ips}

                def level(items, parent_item):
                    for inst in sorted(items, key=lambda i: i.base):
                        if inst.bridge:
                            node = QTreeWidgetItem([inst.name, hex32(inst.base), ""])
                            node.setFont(1, mono)
                            node.setForeground(0, theme.manager().color("accent"))
                            node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                            node.setToolTip(0, tr("Subsystem: the IPs grouped in it (addresses are global)"))
                            parent_item.addChild(node)
                            level(inst.children, node)
                        elif inst.name in by_name:
                            parent_item.addChild(ip_item(by_name[inst.name], group))
                level(self.ctx.project.instances, g)
                return
            for ip in sorted(ips, key=lambda i: i.base):
                g.addChild(ip_item(ip, group))

        def ip_item(ip, group):
            nonlocal target
            top = QTreeWidgetItem([ip.name, hex32(ip.base), ""])
            top.setFont(1, mono)
            bf = top.font(0)
            bf.setBold(True)
            top.setFont(0, bf)
            top.setToolTip(0, single_line(ip.desc))
            top.setData(0, ROLE, (group, ip.name, None))
            if ip.panel:
                info = self.ifaces.get(ip.panel)
                top.setIcon(0, icons.icon("external", "accent" if info else "muted", 14))
                top.setToolTip(0, single_line(ip.desc) + "\n" + (
                    tr("{0}: double-click to open").format(info.label(language())) if info
                    else tr("IP interface: {0} (not found)").format(ip.panel)))
            for reg in ip.registers:
                it = QTreeWidgetItem([reg.name, hex32(ip.address(reg)), reg.access])
                it.setFont(1, mono)
                it.setData(0, ROLE, (group, ip.name, reg.name))
                it.setToolTip(0, single_line(reg.desc))
                top.addChild(it)
                if sel == (group, ip.name, reg.name):
                    target = it
            return top
        add_group(tr("PROJECT"), self.ctx.address_ips(), "project")
        add_group(tr("EXTRA IPS"), self._extra(), "extra")
        raw = QTreeWidgetItem([tr("Raw address access"), "", "RW"])
        raw.setData(0, ROLE, ("raw", "", "DATA"))
        raw.setToolTip(0, tr("Read / write any address that has no register map"))
        self.tree.addTopLevelItem(raw)
        if sel and sel[0] == "raw":
            target = raw
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
        else:
            self.ip = self.reg = None
            self.remove_btn.setEnabled(False)
            self._show_register()

    def _sel_key(self):
        it = self.tree.currentItem()
        return it.data(0, ROLE) if it else None

    def _select(self, item, _prev=None):
        key = item.data(0, ROLE) if item else None
        self.remove_btn.setEnabled(bool(key and key[0] == "extra"))
        if not key or key[2] is None:
            self.ip = self.reg = None
            self.group = None
        else:
            self.group = key[0]
            self.ip = self._find_ip(key[0], key[1])
            self.reg = self.ip.get_register(key[2]) if self.ip else None
        if self.cfg_edit is not None:          # leaving the config row being edited
            if self.cfg_edit >= len(self.config) or not key or tuple(key) != tuple(self.config[self.cfg_edit]["key"]):
                self.cfg_edit = None
        self.read_value = None
        self.staged = None
        self._show_register()
        self._update_panel_btn()

    def on_project_replaced(self):
        self.refresh_interfaces()          # the project's libraries may hold other interfaces
        self.on_project_modified()

    def on_project_modified(self):
        self._fill_tree()
        self._fill_watch(rebuild=True)
        self._fill_config(rebuild=True)

    # ---------------------------------------------------------------- extra IPs
    def add_from_spec(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Register spec"), ROOT,
                                              tr("Register spec (*.regspec.json);;JSON (*.json)"))
        if not path:
            return
        try:
            spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        w = 0x1000
        while w < spec.span:
            w <<= 1
        base = BaseDialog.ask(self, tr("{0}: base address").format(spec.module), 0x00000000, w,
                              tr("The IP's address on the hardware."))
        if base is None:
            return
        self._add_extra([G.to_ipblock(spec, base)])

    def add_from_project(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Project file"), ROOT, tr("AXIL project (*.axproj.json);;JSON (*.json)"))
        if not path:
            return
        try:
            proj = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        placed = proj.address_ips()
        if not placed:
            QMessageBox.information(self, self.title, tr("The address map of this project file has no IPs."))
            return
        names = [f"{ip.name}  ({hex32(ip.base)})" for ip in placed]
        choice, ok = QInputDialog.getItem(self, tr("Add IP"), tr("IP to add:"), [tr("All")] + names, 0, False)
        if not ok:
            return
        ips = placed if choice == tr("All") else [placed[names.index(choice)]]
        out = []
        for ip in ips:
            ip = copy.deepcopy(ip)
            ip.ref = ""
            out.append(ip)
        self._add_extra(out)

    def add_from_library(self):
        items = [(lib, e) for lib in self.ctx.libraries for e in lib.entries]
        if not items:
            QMessageBox.information(self, self.title, tr("No open IP library, or no IP in the libraries (IP Library)."))
            return
        labels = [f"{lib.name} / {e.name}" for lib, e in items]
        choice, ok = QInputDialog.getItem(self, tr("Add IP from a library"), "IP:", labels, 0, False)
        if not ok:
            return
        lib, e = items[labels.index(choice)]
        w = 0x1000
        span = max((r.offset for r in e.ip.registers), default=0) + 4
        while w < span:
            w <<= 1
        base = BaseDialog.ask(self, tr("{0}: base address").format(e.name), 0x00000000, w, tr("The IP's address on the hardware."))
        if base is None:
            return
        ip = e.definition()
        ip.base, ip.regspec, ip.source = base, None, lib.name
        self._add_extra([ip])

    def _add_extra(self, new):
        taken = {i.name for i in self.ctx.project.all_instances()}
        clash = [ip.name for ip in new if ip.name in taken]
        if clash:
            QMessageBox.warning(self, self.title, tr("The project already has IPs with these names: {0}. They were not added.").format(', '.join(clash)))
        new = [ip for ip in new if ip.name not in taken]
        if not new:
            return
        keep = [ip for ip in self._extra() if ip.name not in {n.name for n in new}]
        self._set_extra(keep + new)
        self._fill_tree()
        self.status(tr("{0} IPs added").format(len(new)))

    def remove_extra(self):
        key = self._sel_key()
        if not key or key[0] != "extra":
            return
        self._set_extra([ip for ip in self._extra() if ip.name != key[1]])
        self._fill_tree()

    def _raw_addr_changed(self):
        try:
            a = parse_int(self.raw_ed.text()) & 0xFFFFFFFC
        except ValueError:
            self.raw_ed.setStyleSheet(f"border-color: {theme.manager().tokens['danger']};")
            return
        self.raw_ed.setStyleSheet("")
        self.raw_ed.setText(hex32(a))
        self.raw = raw_ip(a)
        self.ip, self.reg, self.group = self.raw, self.raw.registers[0], "raw"
        self.read_value = self.staged = None
        self._show_register()

    # ================================================================ register panel
    def _show_register(self):
        reg = self.reg
        is_raw = self.ip is self.raw and reg is not None
        self.raw_row.setVisible(is_raw)
        if not reg:
            self.reg_title.setText(tr("Select a register"))
            self.reg_desc.setText(tr("Select a register in the map on the left."))
            self.access_pill.hide()
            self.fields.setRowCount(0)
            self.value_ed.clear()
            self.read_lbl.setText("—")
            self.stamp.clear()
        else:
            addr = self.ip.address(reg)
            self.reg_title.setText(tr("Raw address access") if is_raw else f"{self.ip.name}.{reg.name}   ·   {hex32(addr)}")
            self.reg_desc.setText(reg.desc or tr("No description"))
            self.access_pill.set(reg.access, "accent")
            self.access_pill.show()
            cached = self.reads.get(addr) if reg.readable else None
            if self.read_value is None and cached:
                self.read_value = cached[0]
            if self.staged is None:
                self.staged = self._base_value()
            self.read_lbl.setText(hex32(self.read_value) if self.read_value is not None else "—")
            self.value_ed.setText(hex32(self.staged))
            if cached:
                self.stamp.setText(tr("Read {0} · {1}").format(cached[1], cached[2]))
            elif self.read_value is None:
                self.stamp.setText(tr("Not read yet") if reg.readable else tr("Write-only register: cannot be read"))
            self._fill_fields()
        self._update_controls()

    def _base_value(self):
        """Starting point of a write: what was read (PULSE and W1C bits cleared, so they are not
        re-triggered); for write-only registers the last written value, else the reset value."""
        reg = self.reg
        clear = reg.mask_of("PULSE", "W1C")
        if reg.readable and self.read_value is not None:
            return self.read_value & ~clear & 0xFFFFFFFF
        return self.last_written.get(self.ip.address(reg), reg.reset) & ~clear & 0xFFFFFFFF

    def _fmt(self, f):
        return self.view_cb.currentData() or f.fmt

    def _fill_fields(self):
        reg = self.reg
        if reg is None:
            return
        self._loading = True
        t = theme.manager().tokens
        mono = theme.mono_font()
        muted = QColor(t["muted"])
        fields = sorted(reg.fields, key=lambda f: -f.lsb)
        self.fields.setRowCount(len(fields))
        for row, f in enumerate(fields):
            fmt = self._fmt(f)
            rv = f.extract(self.read_value) if self.read_value is not None else None
            sv = f.extract(self.staged) if self.staged is not None else 0
            meaning = f.read_meaning(rv) if rv is not None else ""
            cells = [f.name, f.bits, f.kind or reg.access, N.label(fmt, f.width),
                     "" if rv is None else N.format_value(rv, f.width, fmt, f.meanings()),
                     None, meaning or single_line(f.desc)]
            for col, text in enumerate(cells):
                if text is None:
                    continue
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if col in (F_BITS, F_READ):
                    it.setFont(mono)
                if col == F_TYPE or (col == F_MEAN and not meaning):
                    it.setForeground(muted)
                if col == F_READ and rv is not None:
                    it.setToolTip("\n".join(f"{a}: {b}" for a, b in
                                            N.all_views(rv, f.width, N.split(f.fmt, f.width)[1])))
                else:
                    it.setToolTip(f.desc)
                self.fields.setItem(row, col, it)
            self.fields.setCellWidget(row, F_WRITE, self._editor(f, fmt, sv))
        self.fields.resizeColumnsToContents()
        hdr = self.fields.horizontalHeader()
        hdr.setSectionResizeMode(F_MEAN, QHeaderView.ResizeMode.Stretch)
        self.fields.setColumnWidth(F_WRITE, max(self.fields.columnWidth(F_WRITE), 210))
        self._loading = False
        self._update_controls()

    # ---------------------------------------------------------------- typed editors
    def _editor(self, f, fmt, value):
        """Typed input + a "⋯" menu (custom value in any type, register map edits)."""
        holder = self._typed_editor(f, fmt, value)
        more = QToolButton()
        more.setText("⋯")
        more.setProperty("variant", "ghost")
        more.setToolTip(tr("Write a custom value · edit the register map"))
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(self._field_menu(f))
        holder.layout().addWidget(more)
        return holder

    def _field_menu(self, f):
        m = QMenu(self)
        reg = self.reg
        kind = f.kind or reg.access
        if reg.writable and kind not in ("RO", "CONST"):
            m.addAction(tr("Write a custom value…"), lambda: self.custom_value(f))
        if self.group == "project" and self.ip.ref:
            m.addAction(tr("Edit in the Register Map Editor…"),
                        lambda: self.edit_register_map(self.ip.ref, self.reg.name, f.name))
        elif self.group == "extra":
            m.addAction(tr("Data type and value meanings (this extra IP only)…"), lambda: self.edit_field_map(f))
        if m.isEmpty():
            a = m.addAction(tr("No action for this bit field"))
            a.setEnabled(False)
        return m

    def _field_menu_at(self, pos):
        row = self.fields.rowAt(pos.y())
        if row < 0 or self.reg is None:
            return
        name = self.fields.item(row, F_NAME).text()
        f = self.reg.get_field(name)
        if f:
            self._field_menu(f).exec(self.fields.viewport().mapToGlobal(pos))

    def _typed_editor(self, f, fmt, value):
        """Input widget for one bit field, chosen by its kind and data type:
        RO / CONST: none · PULSE: send button · W1C: clear button · defined write values: list ·
        bool (or 1 bit): switch · anything else: a number box in the field's own format."""
        reg = self.reg
        kind = f.kind or reg.access
        base, _ = N.split(fmt, f.width)
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        if not reg.writable or kind in ("RO", "CONST"):
            lay.addWidget(ui.label(tr("read-only"), "muted"))
            lay.addStretch(1)
            return holder
        if kind == "PULSE":
            if f.width == 1:
                b = ui.button(tr("Send pulse"), "play", None, lambda: self.command(f, 1), tr("writes 1 to {0}").format(f.name))
            else:
                ed = self._number_edit(f, fmt, 1, stage=False)
                lay.addWidget(ed, 1)
                b = ui.button(tr("Send"), "play", None, lambda: self._command_from(ed, f, fmt), tr("Send with this value"))
            b.setProperty("command", True)
            lay.addWidget(b)
            return holder
        if kind == "W1C":
            b = ui.button(tr("Clear"), None, None, lambda: self.command(f, (1 << f.width) - 1),
                          tr("Clears the {0} flags by writing 1").format(f.name))
            b.setProperty("command", True)
            lay.addWidget(b)
            lay.addStretch(1)
            return holder
        choices = f.write_choices()
        if choices:
            cb = QComboBox()
            vfmt = "hex" if base == "enum" else fmt
            for e in choices:
                cb.addItem(f"{e.write}  ({N.format_value(e.value, f.width, vfmt)})", e.value)
            if cb.findData(value) < 0:
                cb.addItem(tr("{0}  (undefined)").format(N.format_value(value, f.width, vfmt)), value)
            cb.setCurrentIndex(cb.findData(value))
            cb.currentIndexChanged.connect(lambda _i, cb=cb: self._stage(f, cb.currentData()))
            lay.addWidget(cb, 1)
            return holder
        if base == "bool" or (f.width == 1 and not f.fmt):
            sw = QCheckBox("true" if value else "false")
            sw.setChecked(bool(value))
            sw.toggled.connect(lambda on, sw=sw: (sw.setText("true" if on else "false"), self._stage(f, int(on))))
            lay.addWidget(sw)
            lay.addStretch(1)
            return holder
        lay.addWidget(self._number_edit(f, fmt, value), 1)
        return holder

    def _number_edit(self, f, fmt, value, stage=True):
        vfmt = "hex" if N.split(fmt, f.width)[0] == "enum" else fmt
        ed = QLineEdit(N.format_value(value, f.width, vfmt))
        ed.setProperty("mono", "true")
        ed.setToolTip(tr("{0}, {1} bits · 0x / 0b prefixes also work").format(N.LABELS.get(N.split(vfmt, f.width)[0], vfmt), f.width))
        if stage:
            ed.textEdited.connect(lambda _t, ed=ed: self._typed(ed, f, vfmt))
        return ed

    def _typed(self, ed, f, fmt):
        try:
            v = N.parse_value(ed.text(), f.width, fmt, f.meanings())
        except ValueError as exc:
            ed.setStyleSheet(f"border-color: {theme.manager().tokens['danger']};")
            ed.setToolTip(str(exc))
            return
        ed.setStyleSheet("")
        self._stage(f, v)

    def _stage(self, f, v):
        if self._loading or self.staged is None:
            return
        self.staged = f.insert(self.staged, v) & 0xFFFFFFFF
        self.value_ed.setText(hex32(self.staged))

    def _value_edited(self, text):
        try:
            self.staged = parse_int(text) & 0xFFFFFFFF
        except ValueError:
            return
        self._fill_fields()

    def _reset_staged(self):
        if self.reg is None:
            return
        self.staged = self._base_value()
        self.value_ed.setText(hex32(self.staged))
        self._fill_fields()

    # ================================================================ bus access
    def read_reg(self):
        ip, reg = self.ip, self.reg
        if not (self.link.connected and reg and reg.readable):
            return
        addr = ip.address(reg)

        def ok(value):
            if self.reg is reg:
                self.read_value = value
                self.staged = self._base_value()
                self.value_ed.setText(hex32(self.staged))
                self._fill_fields()
            self._record(addr, value, tr("register panel"))
        self.bus.run(lambda: self.link.read(addr), ok, lambda e: self.error(tr("Read error"), e))

    # ---------------------------------------------------------------- shared read cache
    def _record(self, addr, value, source):
        """A value was read from the board (panel, live watch, config): remember it with the time
        and show it on every screen that displays this address."""
        now = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(now))
        self.reads[addr] = (value, ts, source)
        # register panel
        if self.reg is not None and self.reg.readable and self.ip.address(self.reg) == addr:
            old_base = self._base_value()
            changed = value != self.read_value
            self.read_value = value
            self.read_lbl.setText(hex32(value))
            self.stamp.setText(tr("Read {0} · {1}").format(ts, source))
            editing = self.value_ed.hasFocus() or self.fields.isAncestorOf(QApplication.focusWidget() or self)
            if self.staged == old_base and not editing:
                self.staged = self._base_value()
                self.value_ed.setText(hex32(self.staged))
                if changed:
                    self._fill_fields()
            elif changed:
                self._update_read_cells()
        # live watch rows of the same address
        for w in self.watch:
            ip, reg = self._resolve(w["key"])
            if reg is not None and ip.address(reg) == addr:
                w["flash"] = w["value"] is not None and value != w["value"]
                if value != w["value"]:
                    w["changed"] = ts if w["value"] is not None else "—"
                w["value"] = value
                w["read"] = ts
                w["history"].append((now, value))
        self._fill_watch()
        self._schedule_history()
        self._fill_config()

    def _update_read_cells(self):
        """Refresh the "Okunan" and meaning cells without rebuilding the editors."""
        reg = self.reg
        if reg is None or self.read_value is None:
            return
        for row in range(self.fields.rowCount()):
            f = reg.get_field(self.fields.item(row, F_NAME).text())
            if f is None:
                continue
            rv = f.extract(self.read_value)
            fmt = self._fmt(f)
            self.fields.item(row, F_READ).setText(N.format_value(rv, f.width, fmt, f.meanings()))
            meaning = f.read_meaning(rv)
            self.fields.item(row, F_MEAN).setText(meaning or single_line(f.desc))

    def _write(self, value, what):
        ip, reg = self.ip, self.reg
        if not (self.link.connected and reg and reg.writable):
            return
        addr = ip.address(reg)

        def ok(_):
            self.last_written[addr] = value
            self.stamp.setText(f"{what} {time.strftime('%H:%M:%S')} · {hex32(value)}")
            if reg.readable:
                self.read_reg()
            else:
                self.staged = self._base_value()
                self.value_ed.setText(hex32(self.staged))
                self._fill_fields()
        self.bus.run(lambda: self.link.write(addr, value), ok, lambda e: self.error(tr("Write error"), e))

    def write_reg(self):
        if self.reg is None or not self.reg.writable:
            return
        try:
            value = parse_int(self.value_ed.text()) & 0xFFFFFFFF
        except ValueError:
            return self.error(tr("Invalid value"), self.value_ed.text())
        self._write(value, tr("Written"))

    def command(self, f, v):
        """PULSE / W1C: write this field right away. Other fields keep their values; other PULSE
        and W1C bits are written 0 so they do not fire."""
        base = self._base_value()
        self._write(f.insert(base, v) & 0xFFFFFFFF, tr("{0} sent").format(f.name))

    def _command_from(self, ed, f, fmt):
        try:
            v = N.parse_value(ed.text(), f.width, fmt, f.meanings())
        except ValueError as exc:
            return self.status(f"{f.name}: {exc}")
        self.command(f, v)

    # ================================================================ custom value / map edits
    def custom_value(self, f):
        reg = self.reg
        kind = f.kind or reg.access
        command = kind in ("PULSE", "W1C")
        cur = f.extract(self.staged) if self.staged is not None else 0
        res = CustomValueDialog.ask(self, f, cur, KIND_READS.get(f.kind, reg.readable),
                                    KIND_WRITES.get(f.kind, reg.writable), command,
                                    can_keep=self.group == "extra")
        if res is None:
            return
        if res["meaning"] is not None:
            f.values = [e for e in f.values if e.value != res["meaning"].value] + [res["meaning"]]
            f.values.sort(key=lambda e: e.value)
            self._save_ip()
        if res["now"]:
            self.command(f, res["value"])
        else:
            self.staged = f.insert(self.staged, res["value"]) & 0xFFFFFFFF
            self.value_ed.setText(hex32(self.staged))
            self._fill_fields()

    def edit_field_map(self, f):
        res = FieldMapDialog.ask(self, f, f.kind)
        if res is None:
            return
        f.fmt, f.values = res["fmt"], res["values"]
        self._save_ip()
        self._fill_fields()

    def _save_ip(self):
        """Persist an edit of a debug-only (extra) IP; project IPs are edited in the Register Map Editor."""
        if self.group == "extra":
            self._set_extra([self.ip if x.name == self.ip.name else x for x in self._extra()])
            self.status(tr("Register map updated"))

    # ================================================================ IP interfaces
    def find_ip(self, group, name):
        return self._find_ip(group, name)

    def record(self, addr, value, source):
        self._record(addr, value, source)

    def port_name(self):
        return self.port_cb.currentText() if self.link.connected else ""

    def refresh_interfaces(self):
        """Look for the interface applications again (settings folders, libraries, built-in)."""
        self.ifaces, errors = ifhost.discover(self.ctx)
        for e in errors:
            self._log.emit("ERR", e)

    def interface_folders(self):
        if ifhost.folders_dialog(self, self.ctx):
            self.refresh_interfaces()
            self._fill_tree()
            self._update_panel_btn()

    def _update_panel_btn(self):
        ip = self.ip if self.group in ("project", "extra") else None
        pid = ip.panel if ip is not None else ""
        self.panel_btn.setVisible(bool(pid))
        if pid:
            info = self.ifaces.get(pid)
            self.panel_btn.setText(info.label(language()) if info else tr("IP interface"))
            self.panel_btn.setToolTip((info.description(language()) + "\n" + tr("It uses this connection.")) if info
                                      else tr("IP interface: {0} (not found)").format(pid))

    def _tree_double_click(self, item, _col=0):
        key = item.data(0, ROLE) if item else None
        if key and key[2] is None and key[0] != "raw":
            ip = self._find_ip(key[0], key[1])
            if ip is not None and ip.panel:
                self.open_panel(key[0], key[1])

    def open_panel(self, group=None, name=None):
        """Start (or bring to the front) the IP interface application of an IP."""
        if group is None:
            if self.ip is None:
                return
            group, name = self.group, self.ip.name
        ip = self._find_ip(group, name)
        if ip is None or not ip.panel:
            return
        info = self.ifaces.get(ip.panel)
        if info is None:
            self.refresh_interfaces()
            info = self.ifaces.get(ip.panel)
        if info is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(tr("IP interface"))
            box.setText(tr("The interface '{0}' of {1} was not found.").format(ip.panel, name))
            box.setInformativeText(tr("Add the folder that holds it (with its axil-interface.json) to the IP interface folders."))
            folders = box.addButton(tr("IP interface folders…"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Close)
            box.exec()
            if box.clickedButton() is folders:
                self.interface_folders()
            return None
        if info.protocol != ifc.PROTOCOL:
            self.error(tr("IP interface"), tr("{0} uses protocol {1}; this AXIL Studio speaks {2}.").format(
                info.label(language()), info.protocol, ifc.PROTOCOL))
            return None
        if self.service is None:
            self.service = ifhost.InterfaceService(self)
        try:
            return self.service.open(info, group, name)
        except RuntimeError as exc:
            self.error(tr("IP interface"), exc)
            return None

    def show_register(self, group, ip_name, reg_name=None):
        """Select a register (or the IP) in the tree and bring this window to the front."""
        it = self._find_item((group, ip_name, reg_name)) or self._find_item((group, ip_name, None))
        if it is not None:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
        self.rtabs.show_page(self.reg_card)
        win = self.window()
        win.showNormal()
        win.raise_()
        win.activateWindow()

    def _find_item(self, key):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            data = it.data(0, ROLE)
            if data is not None and tuple(data) == tuple(key):
                return it
            stack += [it.child(i) for i in range(it.childCount())]
        return None

    def set_exclusive(self, owner):
        """An IP interface holds the link (None releases it): live watch stops, Connect and the
        register panel's Read / Write are locked meanwhile."""
        self._exclusive = owner
        if owner is not None and self.poll_timer.isActive():
            self.poll_timer.stop()
            self._log.emit("INFO", tr("Live watch stopped: the UART interface is running a validation"))
        self._update_controls()

    def add_config_rows(self, rows):
        """rows: (group, ip, register, value); shown in the Config tab."""
        for group, ipn, regn, value in rows:
            self.config.append({"key": (group, ipn, regn), "value": value & 0xFFFFFFFF})
        self._fill_config(len(self.config) - 1, rebuild=True)
        self.rtabs.show_page(self.cfg_tab)

    # ================================================================ tree menu
    def _cur_key(self):
        if self.reg is None:
            return None
        if self.group == "raw":
            return ("raw", hex32(self.ip.base), "DATA")
        return (self.group, self.ip.name, self.reg.name)

    def _resolve(self, key):
        """(ip, reg) for a (group, ip, reg) key, or (None, None)."""
        group, ipn, regn = key
        if group == "raw":
            ip = raw_ip(parse_int(ipn))
        else:
            ip = self._find_ip(group, ipn)
        reg = ip.get_register(regn) if ip else None
        return ip, reg

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        key = it.data(0, ROLE) if it else None
        if not key or key[0] == "raw":
            return
        m = QMenu(self)
        ip = self._find_ip(key[0], key[1])
        if ip is not None and ip.panel:
            info = self.ifaces.get(ip.panel)
            m.addAction(tr("Open {0}…").format(info.label(language()) if info else tr("IP interface")),
                        lambda: self.open_panel(key[0], key[1]))
            m.addSeparator()
        if key[2] is None and ip:
            regs = [(key[0], ip.name, r.name) for r in ip.registers if r.readable]
            m.addAction(tr("Watch all its registers ({0})").format(len(regs)), lambda: self.add_watch(regs))
        elif ip:
            reg = ip.get_register(key[2])
            if reg and reg.readable:
                m.addAction(tr("Add to live watch"), lambda: self.add_watch([key]))
            if reg and reg.writable:
                m.addAction(tr("Add to config (with the reset value)"),
                            lambda: self.add_config(key, reg.reset & ~reg.mask_of("PULSE", "W1C")))
        if key[0] == "project" and ip and ip.ref:
            m.addSeparator()
            reg_name = key[2]
            m.addAction(tr("Edit register map (Register Map Editor)…"),
                        lambda: self.edit_register_map(ip.ref, reg_name))
            m.addAction(tr("Edit its address (Address Map Editor)…"), lambda: self.edit_address_map(ip.name))
        if key[0] == "extra":
            m.addSeparator()
            m.addAction(tr("Remove extra IP"), self.remove_extra)
        if not m.isEmpty():
            m.exec(self.tree.viewport().mapToGlobal(pos))

    def _decode(self, reg, v):
        parts = []
        for f in sorted(reg.fields, key=lambda f: -f.lsb):
            fv = f.extract(v)
            meaning = f.read_meaning(fv)
            parts.append(f"{f.name}={meaning or N.format_value(fv, f.width, f.fmt, f.meanings())}")
        return "  ".join(parts)

    # ================================================================ detail trees (watch / config)
    @staticmethod
    def _detail_tree(headers, stretch):
        tree = QTreeWidget()
        tree.setHeaderLabels(headers)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setExpandsOnDoubleClick(False)
        tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tree.header().setStretchLastSection(False)
        tree.header().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        tree.itemClicked.connect(lambda it, _c: it.childCount() and it.setExpanded(not it.isExpanded()))
        return tree

    def _field_rows(self, parent, reg, value, cols, other=None):
        """Child rows of a register: one per bit field with its value in the field's type and its
        meaning. cols = (name, bits, value, meaning[, other]); other: a second value (e.g. the one
        on the device) shown in cols[4]."""
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        fields = sorted(reg.fields, key=lambda f: -f.lsb)
        while parent.childCount() < len(fields):
            parent.addChild(QTreeWidgetItem())
        while parent.childCount() > len(fields):
            parent.removeChild(parent.child(parent.childCount() - 1))
        for k, f in enumerate(fields):
            ch = parent.child(k)
            ch.setText(cols[0], f.name)
            ch.setText(cols[1], f.bits)
            ch.setFont(cols[1], mono)
            ch.setForeground(cols[1], muted)
            if value is None:
                ch.setText(cols[2], "")
                ch.setText(cols[3], single_line(f.desc))
                ch.setForeground(cols[3], muted)
            else:
                fv = f.extract(value)
                ch.setText(cols[2], N.format_value(fv, f.width, f.fmt, f.meanings()))
                ch.setFont(cols[2], mono)
                meaning = f.read_meaning(fv)
                ch.setText(cols[3], meaning or single_line(f.desc))
                ch.setForeground(cols[3], theme.manager().color("text") if meaning else muted)
            if len(cols) > 4:
                ch.setText(cols[4], "" if other is None else
                           N.format_value(f.extract(other), f.width, f.fmt, f.meanings()))
                ch.setFont(cols[4], mono)
                differs = other is not None and value is not None and f.kind not in ("PULSE", "W1C") \
                    and f.extract(other) != f.extract(value)
                ch.setForeground(cols[4], theme.manager().color("danger" if differs else "text"))

    def _reg_name(self, key, ip, reg):
        if reg is None:
            return tr("{0}.{1} (not found)").format(key[1], key[2])
        return tr("Raw {0}").format(hex32(ip.base)) if key[0] == "raw" else f"{ip.name}.{reg.name}"

    # ================================================================ live watch
    def _build_watch_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.poll_btn = ui.button(tr("Start"), "play", "primary", self.toggle_poll, tr("Read continuously at the selected period"))
        row.addWidget(self.poll_btn)
        row.addWidget(ui.label(tr("Period"), "muted"))
        self.period_sp = QSpinBox()
        self.period_sp.setRange(20, 60000)
        self.period_sp.setSingleStep(50)
        self.period_sp.setValue(500)
        self.period_sp.setSuffix(" ms")
        self.period_sp.valueChanged.connect(lambda v: (self.poll_timer.setInterval(v), self._update_watch_controls()))
        row.addWidget(self.period_sp)
        row.addWidget(ui.button(tr("Read once"), "read", "ghost", self._poll, tr("Read the listed registers once")))
        row.addStretch(1)
        self.watch_info = ui.label("", "muted")
        row.addWidget(self.watch_info)
        row.addWidget(ui.button(tr("Expand all"), None, "ghost", lambda: self.watch_tree.expandAll()))
        row.addWidget(ui.button(tr("Collapse all"), None, "ghost", lambda: self.watch_tree.collapseAll()))
        row.addWidget(ui.button(tr("Remove"), None, "ghost", self.remove_watch, tr("Stop watching the selected register")))
        row.addWidget(ui.button(tr("Clear"), None, "ghost", self.clear_watch, tr("Clear the list")))
        lay.addLayout(row)
        self.watch_tree = self._detail_tree([tr("Register / bit field"), tr("Address / bits"), tr("Value"), tr("Meaning"),
                                             tr("Last change"), tr("Last read"), tr("Read")], 3)
        self.watch_tree.itemDoubleClicked.connect(self._watch_open)
        self.watch_tree.setToolTip(tr("Click: expand / collapse bit fields · double-click: open in the Register tab"))
        self.watch_tree.currentItemChanged.connect(lambda *_: self._fill_history())

        hist = QWidget()
        hl = QVBoxLayout(hist)
        hl.setContentsMargins(0, 6, 0, 0)
        hl.setSpacing(6)
        hrow = QHBoxLayout()
        hrow.setSpacing(6)
        self.hist_title = ui.label(tr("HISTORY"), "section")
        hrow.addWidget(self.hist_title)
        hrow.addStretch(1)
        self.hist_changes = QCheckBox(tr("Changes only"))
        self.hist_changes.setChecked(True)
        self.hist_changes.toggled.connect(lambda *_: self._fill_history())
        hrow.addWidget(self.hist_changes)
        hrow.addWidget(ui.button(tr("Export to CSV…"), "save", "ghost", self.export_history,
                                 tr("Write the log of every watched register to a CSV file")))
        hrow.addWidget(ui.button(tr("Clear history"), None, "ghost", self.clear_history))
        hl.addLayout(hrow)
        self.hist_tree = self._detail_tree([tr("Time"), tr("Value"), tr("Changed bit fields"), tr("Bit fields")], 3)
        self.hist_tree.setToolTip(tr("Click: expand / collapse the bit fields at that moment"))
        hl.addWidget(self.hist_tree, 1)

        wsplit = QSplitter(Qt.Orientation.Vertical)
        wsplit.addWidget(self.watch_tree)
        wsplit.addWidget(hist)
        wsplit.setSizes([300, 300])
        lay.addWidget(wsplit, 1)
        self._hist_pending = False
        return tab

    # ---------------------------------------------------------------- watch history
    def _sel_watch(self):
        i = self._watch_index(self.watch_tree.currentItem()) if hasattr(self, "watch_tree") else -1
        return self.watch[i] if 0 <= i < len(self.watch) else None

    def _schedule_history(self):
        """Throttle history refreshes while polling fast."""
        if not self._hist_pending:
            self._hist_pending = True
            QTimer.singleShot(300, self._fill_history)

    def _fill_history(self):
        self._hist_pending = False
        if not hasattr(self, "hist_tree"):
            return
        w = self._sel_watch()
        tree = self.hist_tree
        tree.clear()
        if w is None:
            self.hist_title.setText(tr("HISTORY · select a register"))
            return
        ip, reg = self._resolve(w["key"])
        samples = list(w["history"])
        rows, prev = [], None
        for ts, v in samples:
            changed = prev is None or v != prev
            if changed or not self.hist_changes.isChecked():
                rows.append((ts, v, prev))
            prev = v
        self.hist_title.setText(tr("HISTORY · {0} · {1} reads, {2} changes").format(self._reg_name(w['key'], ip, reg), len(samples), sum(1 for i in range(len(samples)) if i == 0 or samples[i][1] != samples[i - 1][1])))
        mono = theme.mono_font()
        accent = theme.manager().color("accent")
        for ts, v, prev in reversed(rows[-HISTORY_SHOWN:]):
            diff = []
            if reg is not None and prev is not None:
                diff = [f.name for f in sorted(reg.fields, key=lambda f: -f.lsb) if f.extract(v) != f.extract(prev)]
            it = QTreeWidgetItem([datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3], hex32(v),
                                  ", ".join(diff) if prev is not None else tr("first read"),
                                  self._decode(reg, v) if reg is not None else ""])
            it.setFont(0, mono)
            it.setFont(1, mono)
            if diff:
                it.setForeground(2, accent)
            tree.addTopLevelItem(it)
            if reg is not None:
                self._field_rows(it, reg, v, (0, 1, 1, 3))
                for k in range(it.childCount()):          # keep the time column for the field name
                    ch = it.child(k)
                    f = sorted(reg.fields, key=lambda f: -f.lsb)[k]
                    ch.setText(0, f"{f.name} {f.bits}")
                    ch.setText(1, N.format_value(f.extract(v), f.width, f.fmt, f.meanings()))
                    if prev is not None and f.extract(v) != f.extract(prev):
                        ch.setForeground(1, accent)
        for c in (0, 1, 2):
            tree.resizeColumnToContents(c)

    def clear_history(self):
        for w in self.watch:
            w["history"].clear()
        self._fill_history()

    def export_history(self):
        if not any(w["history"] for w in self.watch):
            QMessageBox.information(self, self.title, tr("Nothing recorded yet: start live watch."))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Live watch log"),
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           time.strftime("izleme_%Y%m%d_%H%M%S.csv")), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                wr = csv.writer(fh)
                wr.writerow(["zaman", "register", tr("addresses"), "deger", "bit_alanlari"])
                rows = []
                for w in self.watch:
                    ip, reg = self._resolve(w["key"])
                    name = self._reg_name(w["key"], ip, reg)
                    addr = hex32(ip.address(reg)) if reg else ""
                    for ts, v in w["history"]:
                        rows.append((ts, name, addr, hex32(v), self._decode(reg, v) if reg else ""))
                for ts, *rest in sorted(rows):
                    wr.writerow([datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds"), *rest])
        except OSError as exc:
            return self.error(tr("Could not save"), exc)
        self.status(tr("Log exported: {0}").format(path))

    def add_watch(self, keys):
        have = {w["key"] for w in self.watch}
        added = 0
        for key in keys:
            if key and key not in have:
                ip, reg = self._resolve(key)
                cached = self.reads.get(ip.address(reg)) if reg else None
                self.watch.append({"key": key, "value": cached[0] if cached else None, "changed": None,
                                   "read": cached[1] if cached else None, "count": 0, "flash": False,
                                   "history": collections.deque(maxlen=HISTORY_MAX)})
                added += 1
        self._fill_watch()
        self.status(tr("{0} registers added to live watch").format(added))

    def _watch_index(self, item):
        while item is not None and item.parent() is not None:
            item = item.parent()
        return self.watch_tree.indexOfTopLevelItem(item) if item is not None else -1

    def _watch_open(self, item, _col=0):
        i = self._watch_index(item)
        if 0 <= i < len(self.watch):
            self._goto(self.watch[i]["key"])
            self.rtabs.show_page(self.reg_card)

    def remove_watch(self):
        i = self._watch_index(self.watch_tree.currentItem())
        if 0 <= i < len(self.watch):
            del self.watch[i]
            self._fill_watch(rebuild=True)
            self._fill_history()

    def clear_watch(self):
        self.watch.clear()
        self._fill_watch(rebuild=True)
        self._fill_history()

    def toggle_poll(self):
        if self.poll_timer.isActive():
            self.poll_timer.stop()
        elif self.link.connected and self.watch and self._exclusive is None:
            self.poll_timer.start(self.period_sp.value())
            self._poll()
        self._update_watch_controls()

    def _update_watch_controls(self):
        if not hasattr(self, "poll_btn"):
            return
        running = self.poll_timer.isActive()
        if running and not self.link.connected:
            self.poll_timer.stop()
            running = False
        self.poll_btn.setText(tr("Stop") if running else tr("Start"))
        self.poll_btn.setEnabled(self.link.connected and bool(self.watch) and self._exclusive is None)
        n = len(self.watch)
        self.watch_info.setText((tr("{0} registers · every {1} ms").format(n, self.period_sp.value()) if running else tr("{0} registers").format(n))
                                if n else tr("Add with the Watch button on the Register tab or by right-clicking the tree"))

    def _poll(self):
        if self._polling or not self.link.connected or not self.watch or self._exclusive is not None:
            return
        targets = []
        for w in self.watch:
            ip, reg = self._resolve(w["key"])
            targets.append(ip.address(reg) if ip and reg and reg.readable else None)
        self._polling = True

        def job():
            self.link.quiet = True
            try:
                return [self.link.read(a) if a is not None else None for a in targets]
            finally:
                self.link.quiet = False

        def ok(values):
            self._polling = False
            for w, a, v in zip(self.watch, targets, values):
                if a is not None:
                    w["count"] += 1
            done = set()
            for a, v in zip(targets, values):
                if a is not None and a not in done:
                    done.add(a)
                    self._record(a, v, tr("live watch"))

        def err(exc):
            self._polling = False
            self.poll_timer.stop()
            self._update_watch_controls()
            self.error(tr("Live watch stopped"), exc)
        self.bus.run(job, ok, err)

    def _fill_watch(self, rebuild=False):
        if not hasattr(self, "watch_tree"):
            return
        t = theme.manager().tokens
        mono = theme.mono_font()
        tree = self.watch_tree
        if rebuild or tree.topLevelItemCount() != len(self.watch):
            open_keys = {self.watch[i]["key"] for i in range(min(len(self.watch), tree.topLevelItemCount()))
                         if tree.topLevelItem(i).isExpanded()} if not rebuild else set()
            tree.clear()
            for w in self.watch:
                it = QTreeWidgetItem()
                tree.addTopLevelItem(it)
                it.setExpanded(w["key"] in open_keys)
        for i, w in enumerate(self.watch):
            it = tree.topLevelItem(i)
            ip, reg = self._resolve(w["key"])
            v = w["value"]
            texts = [self._reg_name(w["key"], ip, reg), hex32(ip.address(reg)) if reg else "",
                     hex32(v) if v is not None else "", self._decode(reg, v) if reg and v is not None else "",
                     w["changed"] or "", w.get("read") or "", str(w["count"])]
            for c, text in enumerate(texts):
                it.setText(c, text)
                it.setForeground(c, QColor(t["danger"] if reg is None else
                                           (t["accent"] if w["flash"] and c in (2, 3) else t["text"])))
            bold = it.font(0)
            bold.setBold(True)
            it.setFont(0, bold)
            for c in (1, 2):
                f = QFont(mono)
                f.setBold(w["flash"] and c == 2)
                it.setFont(c, f)
            if reg is not None:
                self._field_rows(it, reg, v, (0, 1, 2, 3))
        for c in (0, 1, 2, 4, 5, 6):
            tree.resizeColumnToContents(c)
        self._update_watch_controls()

    def _goto(self, key):
        """Select a register in the address map."""
        if key[0] == "raw":
            self.raw_ed.setText(key[1])
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it.data(0, ROLE) and it.data(0, ROLE)[0] == "raw":
                    self.tree.setCurrentItem(it)
            self._raw_addr_changed()
            return

        def walk(it):
            if it.data(0, ROLE) == key:
                return it
            for i in range(it.childCount()):
                found = walk(it.child(i))
                if found:
                    return found
            return None
        for i in range(self.tree.topLevelItemCount()):
            found = walk(self.tree.topLevelItem(i))
            if found:
                self.tree.setCurrentItem(found)
                return

    # ================================================================ config
    def _build_config_tab(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.send_btn = ui.button(tr("Send to device"), "write", "primary", self.send_config,
                                  tr("Write the listed values in order"))
        row.addWidget(self.send_btn)
        self.capture_btn = ui.button(tr("Read from device"), "read", "ghost", self.capture_config,
                                     tr("Take the current values of the listed registers from the device"))
        row.addWidget(self.capture_btn)
        self.compare_btn = ui.button(tr("Compare"), None, "ghost", self.compare_config,
                                     tr("Read the device's values; the config values do not change"))
        row.addWidget(self.compare_btn)
        row.addSpacing(8)
        row.addWidget(ui.button(tr("Open…"), "folder", "ghost", self.open_config))
        row.addWidget(ui.button(tr("Save"), "save", "ghost", self.save_config_file,
                                tr("Save to a config file (asks where the first time)")))
        row.addWidget(ui.button(tr("Save as…"), None, "ghost", lambda: self.save_config_file(True)))
        row.addStretch(1)
        self.cfg_info = ui.label("", "muted")
        row.addWidget(self.cfg_info)
        row.addWidget(ui.button("↑", None, "ghost", lambda: self.move_config(-1)))
        row.addWidget(ui.button("↓", None, "ghost", lambda: self.move_config(1)))
        row.addWidget(ui.button(tr("Delete"), None, "ghost", self.delete_config))
        lay.addLayout(row)
        self.cfg_tree = self._detail_tree(["#", tr("Register / bit field"), tr("Address / bits"), tr("Value"), tr("Meaning"),
                                           tr("On device (last read)")], 4)
        self.cfg_tree.itemDoubleClicked.connect(lambda it, _c: self.load_config_row(self._cfg_index(it)))
        self.cfg_tree.setToolTip(tr("Click: expand / collapse bit fields · double-click: edit in the Register tab"))
        lay.addWidget(self.cfg_tree, 1)
        return tab

    def _cfg_index(self, item):
        while item is not None and item.parent() is not None:
            item = item.parent()
        return self.cfg_tree.indexOfTopLevelItem(item) if item is not None else -1

    def add_config(self, key, value):
        self.config.append({"key": key, "value": value & 0xFFFFFFFF})
        self._fill_config(len(self.config) - 1, rebuild=True)

    def config_from_panel(self):
        if self.reg is None or self.staged is None:
            return
        key = self._cur_key()
        if self.cfg_edit is not None and self.cfg_edit < len(self.config) and self.config[self.cfg_edit]["key"] == key:
            self.config[self.cfg_edit]["value"] = self.staged
            row = self.cfg_edit
            self.cfg_edit = None
            self._fill_config(row)
            self.status(tr("Config row updated"))
        else:
            self.add_config(key, self.staged)
            self.status(tr("Added to config: {0} (Config tab)").format(hex32(self.staged)))
        self._update_controls()

    def load_config_row(self, r):
        """Open a config row in the Register tab; "Config satırını güncelle" writes it back."""
        if not 0 <= r < len(self.config):
            return
        row = self.config[r]
        self._goto(row["key"])
        if self.reg is None or self._cur_key() != row["key"]:
            return
        self.cfg_edit = r
        self.staged = row["value"]
        self.value_ed.setText(hex32(self.staged))
        self._fill_fields()
        self._update_controls()
        self.rtabs.show_page(self.reg_card)
        self.status(tr("Editing config row {0}: change the bit fields, then Update config row").format(r + 1))

    def delete_config(self):
        r = self._cfg_index(self.cfg_tree.currentItem())
        if 0 <= r < len(self.config):
            del self.config[r]
            self.cfg_edit = None
            self._fill_config(min(r, len(self.config) - 1), rebuild=True)

    def move_config(self, d):
        r = self._cfg_index(self.cfg_tree.currentItem())
        if 0 <= r < len(self.config) and 0 <= r + d < len(self.config):
            self.config[r], self.config[r + d] = self.config[r + d], self.config[r]
            self.cfg_edit = None
            self._fill_config(r + d, rebuild=True)

    def _fill_config(self, select=None, rebuild=False):
        if not hasattr(self, "cfg_tree"):
            return
        t = theme.manager().tokens
        mono = theme.mono_font()
        tree = self.cfg_tree
        if rebuild or tree.topLevelItemCount() != len(self.config):
            tree.clear()
            for _ in self.config:
                tree.addTopLevelItem(QTreeWidgetItem())
        for r, row in enumerate(self.config):
            it = tree.topLevelItem(r)
            ip, reg = self._resolve(row["key"])
            cached = self.reads.get(ip.address(reg)) if reg is not None else None
            dev = f"{hex32(cached[0])} · {cached[1]}" if cached else ""
            texts = [str(r + 1), self._reg_name(row["key"], ip, reg), hex32(ip.address(reg)) if reg else "",
                     hex32(row["value"]), self._decode(reg, row["value"]) if reg else "", dev]
            for c, text in enumerate(texts):
                it.setText(c, text)
                it.setForeground(c, QColor(t["danger"] if reg is None else t["text"]))
            if cached:
                same = (cached[0] & ~reg.mask_of("PULSE", "W1C")) == (row["value"] & ~reg.mask_of("PULSE", "W1C"))
                it.setForeground(5, QColor(t["success"] if same else t["danger"]))
                it.setToolTip(5, tr("The device value equals the config") if same else tr("The device value differs from the config"))
            bold = it.font(1)
            bold.setBold(True)
            it.setFont(1, bold)
            for c in (2, 3, 5):
                it.setFont(c, mono)
            if reg is not None:
                self._field_rows(it, reg, row["value"], (1, 2, 3, 4, 5), cached[0] if cached else None)
        for c in (0, 1, 2, 3, 5):
            tree.resizeColumnToContents(c)
        if select is not None and 0 <= select < len(self.config):
            tree.setCurrentItem(tree.topLevelItem(select))
        self._update_config_controls()

    def _update_config_controls(self):
        if not hasattr(self, "send_btn"):
            return
        ok = self.link.connected and bool(self.config)
        for b in (self.send_btn, self.capture_btn, self.compare_btn):
            b.setEnabled(ok)
        n = len(self.config)
        self.cfg_info.setText((tr("{0} writes").format(n) + (f" · {os.path.basename(self.cfg_path)}" if self.cfg_path else ""))
                              if n else tr("Add rows with the Add to config button on the Register tab"))

    def send_config(self):
        rows = []
        for row in self.config:
            ip, reg = self._resolve(row["key"])
            if not reg:
                return self.error(tr("Could not send"), tr("{0}.{1} is not in the address map").format(row['key'][1], row['key'][2]))
            rows.append((f"{ip.name}.{reg.name}", ip.address(reg), row["value"], reg.readable))

        def job():
            for name, addr, value, _r in rows:
                self.link.write(addr, value)
            return [(addr, self.link.read(addr)) for _n, addr, _v, readable in rows if readable]

        def ok(readback):
            self.status(tr("Config sent: {0} writes").format(len(rows)))
            self._log.emit("INFO", tr("Config sent ({0} writes)").format(len(rows)))
            for addr, v in readback:
                self._record(addr, v, tr("config read-back"))
        self.bus.run(job, ok, lambda e: self.error(tr("Could not send the config"), e))

    def _read_config_targets(self, on_done):
        targets = []
        for i, row in enumerate(self.config):
            ip, reg = self._resolve(row["key"])
            if reg and reg.readable:
                targets.append((i, ip.address(reg), reg.mask_of("PULSE", "W1C")))

        def job():
            return [(i, a, clear, self.link.read(a)) for i, a, clear in targets]

        def ok(values):
            for i, a, clear, v in values:
                self._record(a, v, "config")
            on_done(values)
        self.bus.run(job, ok, lambda e: self.error(tr("Could not read"), e))

    def capture_config(self):
        def done(values):
            for i, a, clear, v in values:
                self.config[i]["value"] = v & ~clear & 0xFFFFFFFF
            self._fill_config()
            self.status(tr("{0} values taken from the device (write-only registers unchanged)").format(len(values)))
        self._read_config_targets(done)

    def compare_config(self):
        self._read_config_targets(lambda values: self.status(
            tr("{0} registers read; differing ones are red in the On device column").format(len(values))))

    def open_config(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Config file"), os.path.dirname(self.cfg_path or self.ctx.path or ROOT),
                                              "Config (*.axcfg.json);;JSON (*.json)")
        if not path:
            return
        try:
            writes = load_config(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        self.config = []
        for ipn, regn, value in writes:
            group = "extra" if any(x.name == ipn for x in self._extra()) and not self.ctx.project.get_instance(ipn) \
                else "project"
            self.config.append({"key": (group, ipn, regn), "value": value})
        self.cfg_path, self.cfg_edit = path, None
        self._fill_config(rebuild=True)
        self.rtabs.show_page(self.cfg_tab)

    def save_config_file(self, as_new=False):
        """Kaydet: the config's file (asks only the first time); Farklı kaydet: another file."""
        path = self.cfg_path
        if as_new or not path:
            path, _ = QFileDialog.getSaveFileName(self, tr("Config file"),
                                                  self.cfg_path or os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                                                "config.axcfg.json"),
                                                  "Config (*.axcfg.json)")
            if not path:
                return
        writes = [(row["key"][1], row["key"][2], row["value"]) for row in self.config if row["key"][0] != "raw"]
        try:
            save_config(path, writes)
        except OSError as exc:
            return self.error(tr("Could not save"), exc)
        self.cfg_path = path
        skipped = len(self.config) - len(writes)
        self.status(tr("Saved: {0}").format(path) + (tr(" ({0} raw address rows were not written to the file)").format(skipped) if skipped else ""))
        self._update_config_controls()

    # ================================================================ log / theme / close
    def _append_log(self, direction, text):
        if direction == "IDLE":
            return
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {direction:<4} {text}")
        if direction == "ERR" and self.link.ser is not None and not self.link.connected:
            self._update_controls()             # the port went away: show it as disconnected

    def on_theme(self):
        self.refresh_btn.setIcon(icons.icon("refresh", "text", 16))
        if self.reg:
            self._fill_fields()

    def shutdown(self):
        if self.service is not None:
            self.service.shutdown()
        self.poll_timer.stop()
        self.link.close()
        self.bus.shutdown()
