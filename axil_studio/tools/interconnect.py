"""Interconnect Configurator: the project's address map -> axilxbar crossbar wrappers.

Every bus of the address map that needs one is a crossbar: the top bus and every subsystem with
more than one slave or extra masters (a subsystem with a single slave connects straight to it).
The slave ports come from the Address Map Editor (port j = entry j of that bus, with its base and
window; a subsystem is one port with its whole window); this tool sets each crossbar itself:
module name and masters (in a subsystem MASTER0 carries the requests from the upper bus, more can
be added), the port style (all of them), shows the whole system's or one crossbar's block diagram
and the Verilog, and generates one wrapper per crossbar. The top's settings are stored in the
project file ("interconnect"), a subsystem's on the subsystem ("xbar"); addresses are only edited
in the Address Map Editor.
"""

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
                               QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from axil_studio.core import cmdseq
from axil_studio.core import addrmap as A
from axil_studio.core import hdlspec as H
from axil_studio.core import xbargen as X
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import size_text
from axil_studio.tools.base import ToolPage
from axil_studio.tools.xbar_diagram import DETAILS, BlockDiagram, SystemDiagram
from axil_studio.core.model import hex32
from axil_studio.i18n import tr

XBAR_FILTER = tr("Interconnect settings (*.xbar.json);;JSON (*.json);;All files (*)")
STYLE_SHORT = {"packed": tr("Separate + HDL Designer (_pk)"), "signals": tr("Separate ports only")}


# ============================================================================ page
class InterconnectPage(ToolPage):
    tool_id = "interconnect"
    title = "Interconnect Configurator"
    short_title = "Interconnect Configurator"
    subtitle = tr("Configure the system's AXI4-Lite crossbars and generate their HDL.")
    description = tr("Crossbar configuration for every bus of the address map: masters, module names and port style, with slave ports and address decoding that follow the address map, system and crossbar block diagrams (PDF, draw.io) and HDL wrappers for ZipCPU axilxbar.")
    icon_name = "interconnect"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.level_name = ""             # "" = the top interconnect, else a subsystem's name
        self._loading = False

        self.gen_btn = self.header.add_right(ui.button(tr("Generate…"), "play", "primary", self.generate,
                                                 tr("Generate the axilxbar wrappers and documents of every interconnect")))
        bar = self.header       # the tool's commands sit in the top bar
        bar.add(tr("Edit address map…"), "addrmap", lambda: self.edit_address_map(),
                tr("Slaves, subsystems, their addresses and order are edited in the Address Map Editor"))
        bar.sep()
        bar.add(tr("Import (.xbar.json)…"), "folder", self.import_xbar,
                tr("Take the selected interconnect's settings from an .xbar.json file (settings are kept in the project)"))
        bar.add(tr("Export (.xbar.json)…"), "save", self.export_xbar,
                tr("Write the selected interconnect's settings and slave ports as .xbar.json (for the command line)"))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: interconnects, the selected one's settings and masters
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label(tr("INTERCONNECTS"), "section"))
        self.level_tree = QTreeWidget()
        self.level_tree.setHeaderHidden(True)
        self.level_tree.setColumnCount(2)
        self.level_tree.setRootIsDecorated(True)
        self.level_tree.setIndentation(16)
        self.level_tree.setMaximumHeight(170)
        self.level_tree.header().setStretchLastSection(False)
        self.level_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.level_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.level_tree.setToolTip(tr("The top bus and every subsystem; a subsystem with a single slave has no crossbar"))
        self.level_tree.currentItemChanged.connect(self._level_picked)
        left.lay.addWidget(self.level_tree)
        left.lay.addWidget(ui.Divider())
        self.level_title = ui.label("CROSSBAR", "section")
        left.lay.addWidget(self.level_title)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.module_ed = QLineEdit()
        self.style_cb = QComboBox()
        for key, text in X.STYLES.items():
            if key in STYLE_SHORT:
                self.style_cb.addItem(STYLE_SHORT[key], key)
                self.style_cb.setItemData(self.style_cb.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        self.style_cb.setToolTip(tr("For every interconnect"))
        form.addRow(tr("Module name"), self.module_ed)
        form.addRow("Wrapper", self.style_cb)
        left.lay.addLayout(form)
        left.lay.addWidget(ui.label(tr("MASTERS"), "section"))
        self.master_tbl = QTableWidget(0, 3)
        self.master_tbl.setHorizontalHeaderLabels(["Port", tr("Name"), ""])
        self.master_tbl.verticalHeader().setVisible(False)
        self.master_tbl.setShowGrid(False)
        self.master_tbl.setAlternatingRowColors(True)
        self.master_tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.master_tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.master_tbl.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                        QAbstractItemView.EditTrigger.EditKeyPressed)
        mh = self.master_tbl.horizontalHeader()
        mh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        mh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ui.actions_column(mh, 2)
        self.master_tbl.setMaximumHeight(160)
        self.master_tbl.setToolTip(tr("Double-click the name to change it"))
        self.master_tbl.itemChanged.connect(self._master_changed)
        self.master_tbl.itemSelectionChanged.connect(self._update_diagram)
        left.lay.addWidget(self.master_tbl)
        mbar = QHBoxLayout()
        mbar.setSpacing(4)
        mbar.addWidget(ui.button("Master", "plus", "ghost", self.add_master, tr("New master on this interconnect")))
        mbar.addStretch(1)
        left.lay.addLayout(mbar)
        self.master_note = ui.note("")
        left.lay.addWidget(self.master_note)
        left.lay.addStretch(1)
        self.issues = ui.IssueList(tr("No errors"))     # shown in the window's status bar
        left.setMinimumWidth(320)
        split.addWidget(left)

        # ---- right: tabs
        right = ui.Card(margins=0)
        self.tabs = ui.DetachableTabs(self.title)
        right.lay.addWidget(self.tabs)

        sl_tab = self.sl_tab = QWidget()
        st = QVBoxLayout(sl_tab)
        st.setContentsMargins(16, 12, 16, 12)
        st.setSpacing(8)
        srow = QHBoxLayout()
        self.slave_note = ui.note("")
        srow.addWidget(self.slave_note, 1)
        srow.addWidget(ui.button("Address Map Editor", "addrmap", "ghost", lambda: self.edit_address_map()))
        st.addLayout(srow)
        self.slave_tbl = QTableWidget(0, 5)
        self.slave_tbl.setHorizontalHeaderLabels(["Port", tr("Name"), tr("Type / IP"), tr("Address"), tr("Size")])
        self.slave_tbl.verticalHeader().setVisible(False)
        self.slave_tbl.setShowGrid(False)
        self.slave_tbl.setAlternatingRowColors(True)
        self.slave_tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.slave_tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.slave_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.slave_tbl.cellDoubleClicked.connect(lambda r, _c: self._open_slave(r))
        self.slave_tbl.itemSelectionChanged.connect(self._update_diagram)
        st.addWidget(self.slave_tbl, 1)
        self.tabs.addTab(sl_tab, tr("Slave ports"))

        dia_tab = self.dia_tab = QWidget()
        dt = QVBoxLayout(dia_tab)
        dt.setContentsMargins(16, 12, 16, 12)
        dt.setSpacing(8)
        drow = QHBoxLayout()
        drow.setSpacing(4)
        self.view_cb = QComboBox()
        self.view_cb.addItem(tr("Whole system"), "system")
        self.view_cb.addItem(tr("Selected interconnect (with pins)"), "level")
        self.view_cb.setToolTip(tr("Whole system: every bus and subsystem; selected interconnect: one crossbar with the pins of the generated HDL"))
        self.view_cb.currentIndexChanged.connect(lambda *_: self._switch_view())
        drow.addWidget(self.view_cb)
        self.sig_cb = QComboBox()
        for key, text in DETAILS.items():
            self.sig_cb.addItem(text, key)
        self.sig_cb.setToolTip(tr("Connection detail: Master / slave (one arrow per connection) · AXI Lite simplified (AW / W / B / AR / R channels) · AXI Lite detailed (every pin) · HDL Designer (packed REQ / RSP). Does not change the generated files; exports use this view."))
        self.sig_cb.currentIndexChanged.connect(lambda *_: self._switch_view())
        drow.addWidget(self.sig_cb)
        drow.addSpacing(8)
        drow.addWidget(ui.button("−", None, "ghost", lambda: self._dia().zoom_by(1 / 1.2), tr("Zoom out")))
        self.zoom_lbl = ui.label("100%", "muted")
        self.zoom_lbl.setMinimumWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drow.addWidget(self.zoom_lbl)
        drow.addWidget(ui.button("+", None, "ghost", lambda: self._dia().zoom_by(1.2), tr("Zoom in")))
        drow.addWidget(ui.button(tr("Fit"), None, "ghost", lambda: self._dia().set_zoom(None), tr("Fit to width")))
        drow.addWidget(ui.button("100%", None, "ghost", lambda: self._dia().set_zoom(1.0), tr("Actual size")))
        drow.addStretch(1)
        drow.addWidget(ui.button("PDF…", "file", "ghost", lambda: self.export_diagram("pdf"),
                                 tr("Save the diagram as PDF (on white)")))
        drow.addWidget(ui.button("draw.io…", "file", "ghost", lambda: self.export_diagram("drawio"),
                                 tr("Save the diagram as a draw.io (diagrams.net) file: every block, text and connection can be edited")))
        dt.addLayout(drow)
        self.diagram = BlockDiagram()
        self.diagram.slaveClicked.connect(lambda j: self.slave_tbl.selectRow(j))
        self.diagram.slaveActivated.connect(self._open_slave)
        self.diagram.masterClicked.connect(lambda i: self.master_tbl.selectRow(i))
        self.diagram.masterActivated.connect(self.rename_master)
        self.diagram.zoomChanged.connect(self._zoom_shown)
        self.system = SystemDiagram()
        self.system.levelClicked.connect(self.select_level)
        self.system.instClicked.connect(self._inst_clicked)
        self.system.instActivated.connect(lambda n: self.edit_address_map(n))
        self.system.zoomChanged.connect(self._zoom_shown)
        self.dia_stack = QStackedWidget()
        for d in (self.system, self.diagram):
            scroll = QScrollArea()
            scroll.setWidget(d)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            d.scroll = scroll
            self.dia_stack.addWidget(scroll)
        dt.addWidget(self.dia_stack, 1)
        self.tabs.addTab(dia_tab, tr("Block diagram"))

        pv_tab = self.pv_tab = QWidget()
        pv = QVBoxLayout(pv_tab)
        pv.setContentsMargins(12, 12, 12, 12)
        frow = QHBoxLayout()
        frow.addWidget(ui.label(tr("File"), "muted"))
        self.vfile_cb = QComboBox()
        self.vfile_cb.setMinimumWidth(420)
        self.vfile_cb.setToolTip(tr("File to preview: the wrappers of each interconnect, or ZipCPU axilxbar.v (used through its parameters only)"))
        self.vfile_cb.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        frow.addWidget(self.vfile_cb)
        frow.addStretch(1)
        pv.addLayout(frow)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setProperty("mono", "true")
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        pv.addWidget(self.preview)
        self.tabs.addTab(pv_tab, tr("Verilog preview"))
        self.spec_view = ui.SpecView(
            tr("HDL spec (read-only): the wrapper Verilog is made from this alone. Only data that changes the code: master and slave counts, slave addresses and masks, HDL Designer wrapper, axilxbar parameters; no port names. Command line: python -m axil_studio.core.hdlspec <file> -o out"),
            self.save_spec, tr("Save (.hdlspec.json)…"), tr("Write the shown HDL spec to a file"))
        self.spec_scope = QComboBox()
        self.spec_scope.addItem(tr("Selected interconnect"), "level")
        self.spec_scope.addItem(tr("All interconnects"), "all")
        self.spec_scope.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        self.spec_view.top.insertWidget(1, self.spec_scope)
        self.tabs.addTab(self.spec_view, "Spec")
        self.tabs.currentChanged.connect(lambda *_: self._refresh_preview())

        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 930])
        self.body.addWidget(split, 1)

        self.module_ed.editingFinished.connect(self._settings_changed)
        self.style_cb.currentIndexChanged.connect(self._settings_changed)
        self.load_from_project()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Interconnect")
        win = self.window()
        for text, slot in ((tr("Edit address map (Address Map Editor)…"), lambda: self.edit_address_map()),
                           (tr("Add master"), self.add_master), (None, None),
                           (tr("Slave ports"), lambda: self.tabs.show_page(self.sl_tab)),
                           (tr("Block diagram"), lambda: self.tabs.show_page(self.dia_tab)),
                           (tr("Verilog preview"), lambda: self.tabs.show_page(self.pv_tab)), (None, None),
                           (tr("Import (.xbar.json)…"), self.import_xbar), (tr("Export (.xbar.json)…"), self.export_xbar),
                           (None, None), (tr("Generate…"), self.generate)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            m.addAction(a)

    # ================================================================ project storage
    @property
    def proj(self):
        return self.ctx.project

    @property
    def level(self):
        """The selected bus: None (top) or a subsystem."""
        b = self.proj.get_instance(self.level_name) if self.level_name else None
        return b if b is not None and b.bridge else None

    @property
    def style(self):
        return (self.proj.interconnect or {}).get("style", "packed")

    def _top(self):
        d = dict(self.proj.interconnect or {})
        d.setdefault("module", "axil_xbar")
        d.setdefault("style", "packed")
        d.setdefault("masters", [{"name": "hexbus"}])
        return d

    @property
    def masters(self):
        """Editable master names of the selected interconnect (a bridge's MASTER0 is the bridge)."""
        b = self.level
        if b is None:
            return [m.get("name", "master") for m in self._top()["masters"]]
        return b.xbar_masters

    def _set_masters(self, names):
        b = self.level
        if b is None:
            d = self._top()
            d["masters"] = [{"name": n} for n in names]
            self.proj.interconnect = d
        else:
            b.xbar = dict(b.xbar or {}, masters=list(names))
        self._after_change()

    def spec(self, bridge="sel"):
        """The crossbar of a level (default: the selected one)."""
        return A.xbar_spec(self.proj, self.level if bridge == "sel" else bridge, self.style)

    def load_from_project(self):
        if self.level_name and self.level is None:
            self.level_name = ""
        self._refresh()

    def _commit(self):
        self.ctx.mark_modified(self.tool_id)

    def on_project_replaced(self):
        self.level_name = ""
        self.load_from_project()

    def on_project_modified(self):
        self.load_from_project()

    def _after_change(self):
        self._commit()
        self._refresh()

    def select_level(self, name):
        self.level_name = name or ""
        self._refresh()

    def _level_picked(self, cur, _prev):
        if self._loading or cur is None:
            return
        self.level_name = cur.data(0, Qt.ItemDataRole.UserRole) or ""
        self._refresh()

    # ================================================================ view
    def _fill_levels(self):
        self.level_tree.clear()
        mono = theme.mono_font()

        muted = QColor(theme.manager().tokens["muted"])

        def add(parent_item, bridge):
            sp = self.spec(bridge)
            if A.has_xbar(self.proj, bridge):
                label = sp.module + (tr("  (top)") if bridge is None else "")
                it = QTreeWidgetItem([label, f"{sp.nm}M · {sp.ns}S"])
                it.setToolTip(0, tr("The top bus's interconnect") if bridge is None else
                              tr("Crossbar of the subsystem {0} · {1} – {2}").format(bridge.name, hex32(bridge.base), hex32(bridge.base + A.window_of(self.proj, bridge) - 1)))
            else:
                it = QTreeWidgetItem([tr("{0}  (direct)").format(bridge.name), "1S"])
                for c in (0, 1):
                    it.setForeground(c, muted)
                it.setToolTip(0, self._direct_text(bridge))
            it.setData(0, Qt.ItemDataRole.UserRole, bridge.name if bridge else "")
            it.setFont(1, mono)
            (parent_item.addChild if parent_item else self.level_tree.addTopLevelItem)(it)
            if (bridge.name if bridge else "") == self.level_name:
                self.level_tree.setCurrentItem(it)
            for c in self.proj.level(bridge):
                if c.bridge:
                    add(it, c)
            it.setExpanded(True)
        add(None, None)

    def _refresh(self):
        t = theme.manager().tokens
        mono = theme.mono_font()
        b = self.level
        spec = self.spec()
        self._loading = True
        self._fill_levels()
        direct = not A.has_xbar(self.proj, b)
        self.level_title.setText((tr("SUBSYSTEM · ") if direct else tr("CROSSBAR · ")) + (tr("TOP BUS") if b is None else b.name))
        self.module_ed.setEnabled(not direct)
        self.module_ed.setText(spec.module)
        self.module_ed.setPlaceholderText(f"axil_xbar_{b.name}" if b else "axil_xbar")
        self.style_cb.setCurrentIndex(max(0, self.style_cb.findData(self.style)))
        fixed = 0 if b is None else 1         # a bridge's MASTER0 is the bridge itself
        self.master_tbl.setRowCount(spec.nm)
        for i, m in enumerate(spec.masters):
            port = QTableWidgetItem(f"MASTER{i}")
            port.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            port.setFont(mono)
            self.master_tbl.setItem(i, 0, port)
            name = QTableWidgetItem(m.name)
            blank = QTableWidgetItem("")
            blank.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.master_tbl.setItem(i, 2, blank)
            if i < fixed:
                name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                name.setForeground(QColor(t["accent"]))
                name.setToolTip(tr("Subsystem {0}: requests from the upper bus (fixed)").format(b.name))
                self.master_tbl.setItem(i, 1, name)
                self.master_tbl.removeCellWidget(i, 2)
            else:
                self.master_tbl.setItem(i, 1, name)
                ui.table_row_actions(self.master_tbl, i, 2, lambda *_, i=i: self.rename_master(i),
                                     lambda *_, i=i: self.delete_master(i),
                                     edit_tip=tr("Rename master"), delete_tip=tr("Delete master"))
        if direct:
            self.master_note.label.setText(tr("No crossbar: the single slave is connected directly."))
            self.master_note.help.set_text(self._direct_text(b) + tr("\nAdding a master (e.g. a local DMA) gives the subsystem a crossbar."))
        else:
            self.master_note.label.setText("" if b is None else tr("MASTER0 is {0} (requests from the upper bus).").format(b.name))
            self.master_note.help.set_text("" if b is None else tr("MASTER0 is always {0} (requests from the upper bus). Added masters (e.g. a local DMA) reach only this interconnect's slaves.").format(b.name))
        self.master_note.setVisible(b is not None)
        if b is None:
            short = tr("Slave ports come from the address map (port j = row j).")
            more = tr("The top bus's slave ports come from the address map (port j = row j). Name, address, size and order are changed in the Address Map Editor; double-click a row (a subsystem row selects that subsystem).")
        else:
            short = tr("Slaves inside the {0} window; addresses are global.").format(b.name)
            more = tr("Subsystem {0}: its slaves are inside its window ({1} – {2}); addresses are global.").format(b.name, hex32(b.base + b.self_window), hex32(b.base + A.window_of(self.proj, b) - 1))
        self.slave_note.label.setText(short)
        self.slave_note.help.set_text(more)
        sel = self._sel_slave()
        bad = A.overlapping(self.proj, b) | (A.outside(self.proj, b) if b is not None else set())
        insts = self.proj.level(b)
        self.slave_tbl.setRowCount(spec.ns)
        for j, (inst, s) in enumerate(zip(insts, spec.slaves)):
            kind = (A.BRIDGE_LABEL + (tr(" · crossbar") if A.has_xbar(self.proj, inst) else tr(" · direct"))
                    if inst.bridge else tr("external") if inst.external else inst.ip)
            cells = [(f"SLAVE{j}", True), (s.name, False), (kind, False), (hex32(s.base), True),
                     (size_text(s.window), False)]
            for col, (text, is_mono) in enumerate(cells):
                it = QTableWidgetItem(text)
                if is_mono:
                    it.setFont(mono)
                it.setToolTip(f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}\nMASK {hex32(s.mask)}\n"
                              + (tr("Double-click: select the subsystem") if inst.bridge
                                 else tr("Double-click: edit in the Address Map Editor")))
                if col == 2 and inst.bridge:
                    it.setForeground(QColor(t["accent"]))
                if col == 3 and j in bad:
                    it.setForeground(QColor(t["danger"]))
                self.slave_tbl.setItem(j, col, it)
        self.slave_tbl.resizeColumnsToContents()
        self.slave_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if sel is not None and sel < spec.ns:
            self.slave_tbl.selectRow(sel)
        self._loading = False

        errs = self.all_errors()
        if not errs:
            n = len(A.xbar_levels(self.proj))
            self.issues.ok_text = (tr("No errors · {0} crossbars · selected: {1} masters, {2} slaves").format(n, spec.nm, spec.ns))
        self.issues.set_issues(errs)
        self.gen_btn.setEnabled(not errs)
        self._update_diagram()

    def all_errors(self):
        """Errors of every interconnect and of the address map."""
        if not self.proj.instances:
            return [tr("Address map is empty: add IPs in the Address Map Editor.")]
        errs = []
        for b in A.xbar_levels(self.proj):
            sp = self.spec(b)
            where = "" if b is None else f"{sp.module}: "
            if not sp.slaves:
                if b is None:
                    errs.append(tr("No slaves on the top bus"))
                continue            # an empty subsystem is reported by the address map below
            errs += [where + e for e in sp.validate()]
        for e in A.validate(self.proj):
            if e not in errs:
                errs.append(e)
        return errs

    def _sel_slave(self):
        rows = self.slave_tbl.selectionModel().selectedRows() if self.slave_tbl.selectionModel() else []
        return rows[0].row() if rows else None

    def _open_slave(self, j):
        insts = self.proj.level(self.level)
        if 0 <= j < len(insts):
            if insts[j].bridge:
                self.select_level(insts[j].name)
            else:
                self.edit_address_map(insts[j].name)

    def _inst_clicked(self, name):
        inst = self.proj.get_instance(name)
        if inst is None:
            return
        par = self.proj.parent_of(inst)
        self.level_name = par.name if par else ""
        self._refresh()
        self.slave_tbl.selectRow(self.proj.level(par).index(inst))

    def _system_view(self):
        """The whole system is shown when asked for, and for a subsystem without a crossbar."""
        return self.view_cb.currentData() == "system" or not A.has_xbar(self.proj, self.level)

    def _direct_text(self, b):
        child = b.children[0].name if b.children else "—"
        return tr("{0} has a single slave ({1}) and no extra masters: no crossbar is generated; the port of {0} on the upper bus connects straight to {1}.").format(b.name, child)

    def _dia(self):
        return self.system if self._system_view() else self.diagram

    def _zoom_shown(self, z):
        if self.sender() is self._dia():
            self.zoom_lbl.setText(f"{round(z * 100)}%")

    def _switch_view(self):
        self.dia_stack.setCurrentIndex(0 if self._system_view() else 1)
        self._update_diagram()
        self.zoom_lbl.setText(f"{round(self._dia()._scale * 100)}%")

    def _update_diagram(self):
        if self._loading:
            return
        b = self.level
        rows = self.master_tbl.selectionModel().selectedRows() if self.master_tbl.selectionModel() else []
        s = self._sel_slave()
        sel = ("s", s) if s is not None else (("m", rows[0].row()) if rows else None)
        bad = A.overlapping(self.proj, b) | (A.outside(self.proj, b) if b is not None else set())
        self.dia_stack.setCurrentIndex(0 if self._system_view() else 1)
        if self._system_view():
            insts = self.proj.level(b)
            sel_name = insts[s].name if s is not None and s < len(insts) else None
            self.system.set_project(self.proj, self.sig_cb.currentData(), b.name if b else "", sel_name)
        else:
            spec = self.spec()
            spec.style = "packed" if self.sig_cb.currentData() == "packed" else "signals"
            self.diagram.detail = self.sig_cb.currentData()
            kinds = ["bridge" if i.bridge else "ext" if i.external else "ip" for i in self.proj.level(b)]
            self.diagram.set_spec(spec, sel, bad, kinds)
        self._refresh_preview()

    def export_diagram(self, fmt):
        d = self._dia()
        system = d is self.system
        stem = (f"{self.proj.name}_sistem" if system else self.spec().module) + \
            {"simple": "", "axi": "_axi", "axi_full": "_axi_detay", "packed": "_hdld"}[self.sig_cb.currentData()]
        stem = re.sub(r"[^\w.-]+", "_", stem)
        ext, flt = (".pdf", "PDF (*.pdf)") if fmt == "pdf" else (".drawio", "draw.io (*.drawio)")
        path, _ = QFileDialog.getSaveFileName(self, tr("Save block diagram"),
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT), stem + ext), flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        try:
            if fmt == "pdf":
                d.export_pdf(path)
            else:
                d.export_drawio(path, stem)
        except (OSError, RuntimeError) as exc:
            return self.error(tr("Could not save"), exc)
        self.status(tr("Block diagram saved ({0}): {1}").format(self.sig_cb.currentText(), path))

    def _files(self):
        out = []
        for b in A.xbar_levels(self.proj):
            sp = self.spec(b)
            out.append(((b.name if b else "", "main"), tr("{0}.v  —  wrapper with separate AXI ports").format(sp.module)))
            if sp.packed:
                out.append(((b.name if b else "", "pk"), tr("{0}_pk.v  —  HDL Designer wrapper (REQ/RSP)").format(sp.module)))
        for b in self.proj.bridges():
            if b.sequencer:
                out.append(((b.name, "seq"), tr("{0}  —  cmd_seq instance of {1} (template)").format(cmdseq.template_name(b), b.name)))
        out.append((("", "core"), tr("axilxbar.v  —  ZipCPU, used through its parameters only (not changed)")))
        return out

    def spec_data(self, scope=None):
        """What the Spec tab shows (core/hdlspec.py): the selected interconnect's HDL spec, or all."""
        if (scope or self.spec_scope.currentData()) == "all":
            return {"interconnects": [H.xbar_hdlspec(self.spec(b)) for b in A.xbar_levels(self.proj)]}
        if not A.has_xbar(self.proj, self.level):
            return {"crossbar": None, "note": self._direct_text(self.level)}
        return H.xbar_hdlspec(self.spec())

    def save_spec(self):
        """The shown HDL spec(s) as <module>.hdlspec.json (all of them: one file each, in a folder)."""
        start = os.path.dirname(self.ctx.path or ROOT)
        try:
            if self.spec_scope.currentData() == "all":
                d = QFileDialog.getExistingDirectory(self, tr("Folder for the HDL specs"), start)
                if not d:
                    return
                for x in self.spec_data("all")["interconnects"]:
                    H.save(x, os.path.join(d, H.file_name(x)))
                self.status(tr("{0} HDL specs saved: {1}").format(len(A.xbar_levels(self.proj)), d))
                return
            x = self.spec_data("level")
            if x.get("crossbar", 1) is None:
                return self.status(x["note"])
            path, _ = QFileDialog.getSaveFileName(self, "HDL spec", os.path.join(start, H.file_name(x)),
                                                  "HDL spec (*.hdlspec.json);;JSON (*.json)")
            if not path:
                return
            H.save(x, path)
        except OSError as exc:
            return self.error(tr("Could not save"), exc)
        self.status(tr("Saved: {0}").format(path))

    def _refresh_preview(self):
        if self.tabs.is_shown(self.spec_view):
            self.spec_view.set_data(self.spec_data())
        if not self.tabs.is_shown(self.pv_tab):
            return
        files = self._files()
        cur = self.vfile_cb.currentData() or (self.level_name, "main")
        cur = tuple(cur)
        self.vfile_cb.blockSignals(True)
        self.vfile_cb.clear()
        for key, text in files:
            self.vfile_cb.addItem(text, key)
        i = next((k for k, (key, _) in enumerate(files) if key == cur), 0)
        self.vfile_cb.setCurrentIndex(i)
        self.vfile_cb.blockSignals(False)
        lvl, which = files[i][0]
        bridge = self.proj.get_instance(lvl) if lvl else None
        spec = self.spec(bridge)
        errs = spec.validate() if spec.slaves else [tr("This interconnect has no slaves")]
        if which == "seq":
            text = cmdseq.instance_template(bridge, self.proj)
        elif which == "core":
            try:
                with open(os.path.join(ROOT, "hdl", "v2005", "interconnect", "axilxbar.v"), encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                text = tr("// cannot read hdl/v2005/interconnect/axilxbar.v: {0}").format(exc)
        elif errs:
            text = tr("// The settings have errors, no Verilog can be generated:\n//") + "\n// ".join(errs)
        else:                             # made from the HDL spec alone, as when generating
            d = H.xbar_hdlspec(spec)
            text = H.verilog(d, f"{spec.module}_pk.v" if which == "pk" else f"{spec.module}.v")
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
        module = self.module_ed.text().strip()
        style = self.style_cb.currentData()
        b = self.level
        d = self._top()
        d["style"] = style
        if b is None:
            d["module"] = module or "axil_xbar"
        else:
            b.xbar = dict(b.xbar or {}, module="" if module in ("", f"axil_xbar_{b.name}") else module)
        self.proj.interconnect = d
        self._after_change()

    def _master_changed(self, item):
        if self._loading or item.column() != 1:
            return
        k = item.row() - (0 if self.level is None else 1)
        names = self.masters
        if 0 <= k < len(names):
            names[k] = item.text().strip() or names[k]
            self._set_masters(names)

    def add_master(self):
        names = self.masters
        if len(names) + (0 if self.level is None else 1) >= 16:
            return
        names.append(f"master{len(names) + (0 if self.level is None else 1)}")
        self._set_masters(names)
        self.master_tbl.selectRow(self.master_tbl.rowCount() - 1)

    def delete_master(self, i=None):
        if i is None:
            rows = self.master_tbl.selectionModel().selectedRows()
            if not rows:
                return
            i = rows[0].row()
        k = i - (0 if self.level is None else 1)
        names = self.masters
        if not 0 <= k < len(names):
            return
        if self.level is None and len(names) == 1:
            QMessageBox.warning(self, self.title, tr("An interconnect needs at least one master."))
            return
        if not ui.confirm(self, tr("Delete master"), tr("MASTER{0} ({1}) will be deleted. The port numbers of the following masters go down by one.").format(i, names[k])):
            return
        del names[k]
        self._set_masters(names)

    def rename_master(self, i):
        k = i - (0 if self.level is None else 1)
        names = self.masters
        if not 0 <= k < len(names):
            return
        name, ok = QInputDialog.getText(self, f"MASTER{i}", tr("Name:"), text=names[k])
        if ok and name.strip():
            names[k] = name.strip()
            self._set_masters(names)

    # ================================================================ files / output
    def import_xbar(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Interconnect settings file"), os.path.dirname(self.ctx.path or ROOT),
                                              XBAR_FILTER)
        if not path:
            return
        try:
            spec = X.XbarSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error(tr("Could not open"), exc)
        b = self.level
        d = self._top()
        d["style"] = spec.style if spec.style in STYLE_SHORT else "packed"
        names = [m.name for m in spec.masters]
        if b is None:
            d["module"] = spec.module
            d["masters"] = [{"name": n} for n in names] or [{"name": "hexbus"}]
        else:
            b.xbar = {"module": spec.module, "masters": names[1:]}     # MASTER0 stays the bridge
        self.proj.interconnect = d
        self._after_change()
        self.status(tr("Crossbar settings taken from {0} (slaves and addresses come from the Address Map Editor)").format(path))

    def export_xbar(self):
        spec = self.spec()
        path, _ = QFileDialog.getSaveFileName(self, tr("Interconnect settings file"),
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{spec.module}.xbar.json"), XBAR_FILTER)
        if not path:
            return
        try:
            spec.save(path)
        except OSError as exc:
            return self.error(tr("Could not save"), exc)
        self.status(tr("Saved: {0}").format(path))

    def generate(self):
        errs = self.all_errors()
        if errs:
            QMessageBox.warning(self, self.title, tr("The interconnects or the address map have errors. Fix the errors shown in the status bar and try again."))
            return
        outdir = QFileDialog.getExistingDirectory(self, tr("Output folder"), os.path.dirname(self.ctx.path or ROOT))
        if not outdir:
            return
        paths = []
        try:
            for b in A.xbar_levels(self.proj):
                paths += X.write_xbar(self.spec(b), outdir)
            paths += cmdseq.write_templates(self.proj, outdir)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Generation failed"), exc)
        hint = (tr("<module>.v: the MASTER<i>_AXI_* and SLAVE<j>_AXI_* ports connect to the masters' M_AXI_* and the slaves' S_AXI_* ports. A subsystem's SLAVE port on the upper crossbar connects to MASTER0 of its own crossbar, or straight to its slave when it has a single one."))
        if any(b.sequencer for b in self.proj.bridges()):
            hint += tr("\n<subsystem>_cmd_seq_inst.v: a command sequencer subsystem has cmd_seq between its upper SLAVE port and its MASTER0 (hdl/cmd_seq).")
        if self.style == "packed":
            hint += tr("\n<module>_pk.v (HDL Designer) wraps <module>.v: MASTER<i>/SLAVE<j>_AXI_REQ/RSP per port.")
        QMessageBox.information(self, tr("Generation complete"),
                                f"{outdir}\n\n" + "\n".join(os.path.basename(p) for p in paths)
                                + tr("\n\n{0}\n\naxilxbar is configured through its parameters only. Also add hdl/v2005/interconnect/axilxbar.v, hdl/v2005/interconnect/addrdecode.v and hdl/v2005/common/skidbuffer.v to the HDL project (Verilog-2005, no localparam).").format(hint))
