"""Interconnect Configurator: the project's address map -> axilxbar crossbar wrappers.

Every bus of the address map is one interconnect: the top bus and the bus behind every
Sub-bus Bridge. The slave ports come from the Address Map Editor (port j = entry j of that bus,
with its base and window; a bridge is one port with its whole window); this tool sets each
crossbar itself: module name and masters (behind a bridge MASTER0 is the bridge, more can be
added), the port style (all of them), shows the whole system's or one crossbar's block diagram
and the Verilog, and generates one wrapper per interconnect (nothing for the bridges). The top's
settings are stored in the project file ("interconnect"), a bridge's on the bridge ("xbar");
addresses are only edited in the Address Map Editor.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
                               QPlainTextEdit, QScrollArea, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from axil_studio.core import addrmap as A
from axil_studio.core import xbargen as X
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import size_text
from axil_studio.tools.base import ToolPage
from axil_studio.tools.xbar_diagram import BlockDiagram, SystemDiagram
from axil_studio.core.model import hex32

XBAR_FILTER = "Interconnect ayarları (*.xbar.json);;JSON (*.json);;Tüm dosyalar (*)"
STYLE_SHORT = {"packed": "Ayrık + HDL Designer (_pk)", "signals": "Yalnızca ayrık portlar"}


# ============================================================================ page
class InterconnectPage(ToolPage):
    tool_id = "interconnect"
    title = "Interconnect Configurator"
    short_title = "Interconnect Configurator"
    subtitle = ("Adres haritasındaki bus'larla axilxbar crossbar'larını yapılandır ve wrapper'larını üret "
                "(her Sub-bus Bridge'in arkasında bir interconnect).")
    description = ("Her interconnect'in modül adını ve master'larını ayarlar, slave portlarını adres haritasından "
                   "alır, tüm sistemin blok diyagramını çizer ve ZipCPU axilxbar için isimli portlu wrapper'lar üretir.")
    icon_name = "interconnect"
    version = "0.3.0"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.level_name = ""             # "" = the top interconnect, else a Sub-bus Bridge's name
        self._loading = False

        self.gen_btn = self.header.add_right(ui.button("Üret…", "play", "primary", self.generate,
                                                 "Tüm interconnect'lerin axilxbar wrapper'larını ve dokümanlarını üret"))
        bar = self.header       # the tool's commands sit in the top bar
        bar.add("Adres haritasını düzenle…", "addrmap", lambda: self.edit_address_map(),
                "Slave'ler, Sub-bus Bridge'ler, adresleri ve sıraları Address Map Editor'da düzenlenir")
        bar.sep()
        bar.add("İçe aktar (.xbar.json)…", "folder", self.import_xbar,
                ".xbar.json dosyasından seçili interconnect'in ayarlarını al (ayarlar projede saklanır)")
        bar.add("Dışa aktar (.xbar.json)…", "save", self.export_xbar,
                "Seçili interconnect'in ayarlarını ve slave portlarını .xbar.json olarak yaz (komut satırı için)")

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: interconnects, the selected one's settings and masters
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("INTERCONNECT'LER", "section"))
        self.level_tree = QTreeWidget()
        self.level_tree.setHeaderHidden(True)
        self.level_tree.setColumnCount(2)
        self.level_tree.setRootIsDecorated(True)
        self.level_tree.setIndentation(16)
        self.level_tree.setMaximumHeight(170)
        self.level_tree.header().setStretchLastSection(False)
        self.level_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.level_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.level_tree.setToolTip("Üst bus'ın ve her Sub-bus Bridge'in arkasındaki interconnect")
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
        self.style_cb.setToolTip("Tüm interconnect'ler için")
        form.addRow("Modül adı", self.module_ed)
        form.addRow("Wrapper", self.style_cb)
        left.lay.addLayout(form)
        left.lay.addWidget(ui.label("MASTER'LAR", "section"))
        self.master_tbl = QTableWidget(0, 3)
        self.master_tbl.setHorizontalHeaderLabels(["Port", "Ad", ""])
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
        self.master_tbl.setToolTip("Ada çift tıklayarak değiştir")
        self.master_tbl.itemChanged.connect(self._master_changed)
        self.master_tbl.itemSelectionChanged.connect(self._update_diagram)
        left.lay.addWidget(self.master_tbl)
        mbar = QHBoxLayout()
        mbar.setSpacing(4)
        mbar.addWidget(ui.button("Master", "plus", "ghost", self.add_master, "Bu interconnect'e yeni master"))
        mbar.addStretch(1)
        left.lay.addLayout(mbar)
        self.master_note = ui.label("", "muted", wrap=True)
        left.lay.addWidget(self.master_note)
        left.lay.addStretch(1)
        self.issues = ui.IssueList("Hata yok")     # shown in the window's status bar
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
        self.slave_note = ui.label("", "muted", wrap=True)
        srow.addWidget(self.slave_note, 1)
        srow.addWidget(ui.button("Address Map Editor", "addrmap", "ghost", lambda: self.edit_address_map()))
        st.addLayout(srow)
        self.slave_tbl = QTableWidget(0, 5)
        self.slave_tbl.setHorizontalHeaderLabels(["Port", "Ad", "Tür / IP", "Adres", "Boyut"])
        self.slave_tbl.verticalHeader().setVisible(False)
        self.slave_tbl.setShowGrid(False)
        self.slave_tbl.setAlternatingRowColors(True)
        self.slave_tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.slave_tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.slave_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.slave_tbl.cellDoubleClicked.connect(lambda r, _c: self._open_slave(r))
        self.slave_tbl.itemSelectionChanged.connect(self._update_diagram)
        st.addWidget(self.slave_tbl, 1)
        self.tabs.addTab(sl_tab, "Slave portları")

        dia_tab = self.dia_tab = QWidget()
        dt = QVBoxLayout(dia_tab)
        dt.setContentsMargins(16, 12, 16, 12)
        dt.setSpacing(8)
        drow = QHBoxLayout()
        drow.setSpacing(4)
        self.view_cb = QComboBox()
        self.view_cb.addItem("Tüm sistem", "system")
        self.view_cb.addItem("Seçili interconnect (pinlerle)", "level")
        self.view_cb.setToolTip("Tüm sistem: bütün bus'lar ve Sub-bus Bridge'ler; seçili interconnect: tek crossbar, "
                                "üretilen HDL'deki pinlerle")
        self.view_cb.currentIndexChanged.connect(lambda *_: self._switch_view())
        drow.addWidget(self.view_cb)
        drow.addSpacing(8)
        drow.addWidget(ui.button("−", None, "ghost", lambda: self._dia().zoom_by(1 / 1.2), "Uzaklaş"))
        self.zoom_lbl = ui.label("100%", "muted")
        self.zoom_lbl.setMinimumWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drow.addWidget(self.zoom_lbl)
        drow.addWidget(ui.button("+", None, "ghost", lambda: self._dia().zoom_by(1.2), "Yakınlaş"))
        drow.addWidget(ui.button("Sığdır", None, "ghost", lambda: self._dia().set_zoom(None), "Genişliğe sığdır"))
        drow.addWidget(ui.button("100%", None, "ghost", lambda: self._dia().set_zoom(1.0), "Gerçek boyut"))
        drow.addSpacing(12)
        drow.addWidget(ui.label("Tekerlek: yakınlaş · sürükle: kaydır", "muted"))
        drow.addStretch(1)
        self.all_sig_cb = QCheckBox("Tüm sinyalleri göster")
        self.all_sig_cb.setToolTip("Ayrık sinyal stilinde her AXI sinyalini ayrı pin olarak çizer")
        self.all_sig_cb.toggled.connect(lambda on: self.diagram.set_all_signals(on))
        drow.addWidget(self.all_sig_cb)
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
        self.tabs.addTab(dia_tab, "Blok diyagramı")

        pv_tab = self.pv_tab = QWidget()
        pv = QVBoxLayout(pv_tab)
        pv.setContentsMargins(12, 12, 12, 12)
        frow = QHBoxLayout()
        frow.addWidget(ui.label("Dosya", "muted"))
        self.vfile_cb = QComboBox()
        self.vfile_cb.setMinimumWidth(420)
        self.vfile_cb.setToolTip("Önizlenecek dosya: her interconnect'in wrapper'ları ya da yalnızca parametreleriyle "
                                 "kullanılan ZipCPU axilxbar.v")
        self.vfile_cb.currentIndexChanged.connect(lambda *_: self._refresh_preview())
        frow.addWidget(self.vfile_cb)
        frow.addStretch(1)
        pv.addLayout(frow)
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
        self.load_from_project()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Interconnect")
        win = self.window()
        for text, slot in (("Adres haritasını düzenle (Address Map Editor)…", lambda: self.edit_address_map()),
                           ("Master ekle", self.add_master), (None, None),
                           ("Slave portları", lambda: self.tabs.show_page(self.sl_tab)),
                           ("Blok diyagramı", lambda: self.tabs.show_page(self.dia_tab)),
                           ("Verilog önizleme", lambda: self.tabs.show_page(self.pv_tab)), (None, None),
                           ("İçe aktar (.xbar.json)…", self.import_xbar), ("Dışa aktar (.xbar.json)…", self.export_xbar),
                           (None, None), ("Üret…", self.generate)):
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
        """The selected interconnect: None (top) or a Sub-bus Bridge."""
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

        def add(parent_item, bridge):
            sp = self.spec(bridge)
            label = sp.module + ("  (üst)" if bridge is None else "")
            it = QTreeWidgetItem([label, f"{sp.nm}M · {sp.ns}S"])
            it.setData(0, Qt.ItemDataRole.UserRole, bridge.name if bridge else "")
            it.setFont(1, mono)
            it.setToolTip(0, "Üst bus'ın interconnect'i" if bridge is None else
                          f"{bridge.name} ({A.BRIDGE_LABEL}) arkasındaki interconnect · "
                          f"{hex32(bridge.base)} – {hex32(bridge.base + A.window_of(self.proj, bridge) - 1)}")
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
        self.level_title.setText("CROSSBAR · " + ("ÜST BUS" if b is None else b.name))
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
                name.setToolTip(f"{A.BRIDGE_LABEL} {b.name}: üst bus'tan gelen istekler (sabit)")
                self.master_tbl.setItem(i, 1, name)
                self.master_tbl.removeCellWidget(i, 2)
            else:
                self.master_tbl.setItem(i, 1, name)
                ui.table_row_actions(self.master_tbl, i, 2, lambda i=i: self.rename_master(i),
                                     lambda i=i: self.delete_master(i),
                                     edit_tip="Master'ın adını değiştir", delete_tip="Master'ı sil")
        self.master_note.setText("" if b is None else
                                 f"MASTER0 her zaman {b.name}'dir (üst bus'tan gelen istekler). Eklenen master'lar "
                                 "(ör. yerel DMA) yalnızca bu interconnect'in slave'lerine erişir.")
        self.master_note.setVisible(b is not None)
        if b is None:
            self.slave_note.setText("Üst bus'ın slave portları adres haritasından gelir (port j = haritadaki j. satır). "
                                    "Ad, adres, boyut ve sıra Address Map Editor'da değiştirilir; satıra çift tıkla "
                                    "(Sub-bus Bridge satırı: arkasındaki interconnect'e geç).")
        else:
            self.slave_note.setText(f"{b.name} arkasındaki interconnect: slave'ler {b.name} penceresinin içindedir "
                                    f"({hex32(b.base + b.self_window)} – "
                                    f"{hex32(b.base + A.window_of(self.proj, b) - 1)}); adresler globaldir.")
        sel = self._sel_slave()
        bad = A.overlapping(self.proj, b) | (A.outside(self.proj, b) if b is not None else set())
        insts = self.proj.level(b)
        self.slave_tbl.setRowCount(spec.ns)
        for j, (inst, s) in enumerate(zip(insts, spec.slaves)):
            kind = A.BRIDGE_LABEL if inst.bridge else "harici" if inst.external else inst.ip
            cells = [(f"SLAVE{j}", True), (s.name, False), (kind, False), (hex32(s.base), True),
                     (size_text(s.window), False)]
            for col, (text, is_mono) in enumerate(cells):
                it = QTableWidgetItem(text)
                if is_mono:
                    it.setFont(mono)
                it.setToolTip(f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}\nMASK {hex32(s.mask)}\n"
                              + ("Çift tık: arkasındaki interconnect'e geç" if inst.bridge
                                 else "Çift tık: Address Map Editor'da düzenle"))
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
            n = len(A.levels(self.proj))
            self.issues.ok_text = (f"Hata yok · {n} interconnect · seçili: {spec.nm} master, {spec.ns} slave")
        self.issues.set_issues(errs)
        self.gen_btn.setEnabled(not errs)
        self._update_diagram()

    def all_errors(self):
        """Errors of every interconnect and of the address map."""
        if not self.proj.instances:
            return ["Adres haritası boş: Address Map Editor'da IP ekle."]
        errs = []
        for b in A.levels(self.proj):
            sp = self.spec(b)
            where = "" if b is None else f"{sp.module}: "
            if not sp.slaves:
                errs.append(f"{where}slave yok ({b.name} içine Address Map Editor'da IP ekle)")
                continue
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

    def _dia(self):
        return self.system if self.view_cb.currentData() == "system" else self.diagram

    def _zoom_shown(self, z):
        if self.sender() is self._dia():
            self.zoom_lbl.setText(f"{round(z * 100)}%")

    def _switch_view(self):
        self.dia_stack.setCurrentIndex(0 if self.view_cb.currentData() == "system" else 1)
        self.all_sig_cb.setVisible(self.view_cb.currentData() != "system")
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
        if self.view_cb.currentData() == "system":
            insts = self.proj.level(b)
            sel_name = insts[s].name if s is not None and s < len(insts) else None
            self.system.set_project(self.proj, self.style, b.name if b else "", sel_name)
        else:
            self.diagram.set_spec(self.spec(), sel, bad)
        self._refresh_preview()

    def _files(self):
        out = []
        for b in A.levels(self.proj):
            sp = self.spec(b)
            out.append(((b.name if b else "", "main"), f"{sp.module}.v  —  ayrık AXI portlu wrapper"))
            if sp.packed:
                out.append(((b.name if b else "", "pk"), f"{sp.module}_pk.v  —  HDL Designer wrapper'ı (REQ/RSP)"))
        out.append((("", "core"), "axilxbar.v  —  ZipCPU, yalnızca parametreleriyle (değiştirilmez)"))
        return out

    def _refresh_preview(self):
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
        errs = spec.validate() if spec.slaves else ["Bu interconnect'te slave yok"]
        if which == "core":
            try:
                with open(os.path.join(ROOT, "hdl", "axilxbar.v"), encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                text = f"// hdl/axilxbar.v okunamadı: {exc}"
        elif errs:
            text = "// Ayarlarda hata var, Verilog üretilemiyor:\n// " + "\n// ".join(errs)
        elif which == "pk":
            text = X.gen_xbar_pk(spec)    # HDL Designer block around the main wrapper
        else:
            text = X.gen_xbar(spec)       # axilxbar configured by parameters, separate AXI ports
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
            QMessageBox.warning(self, self.title, "Interconnect'te en az bir master olmalı.")
            return
        if not ui.confirm(self, "Master'ı sil", f"MASTER{i} ({names[k]}) silinecek. Sonraki master'ların "
                                                 "port numarası bir azalır."):
            return
        del names[k]
        self._set_masters(names)

    def rename_master(self, i):
        k = i - (0 if self.level is None else 1)
        names = self.masters
        if not 0 <= k < len(names):
            return
        name, ok = QInputDialog.getText(self, f"MASTER{i}", "Ad:", text=names[k])
        if ok and name.strip():
            names[k] = name.strip()
            self._set_masters(names)

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
        self.status(f"Crossbar ayarları alındı: {path} (slave'ler ve adresler Address Map Editor'dan gelir)")

    def export_xbar(self):
        spec = self.spec()
        path, _ = QFileDialog.getSaveFileName(self, "Interconnect ayar dosyası",
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{spec.module}.xbar.json"), XBAR_FILTER)
        if not path:
            return
        try:
            spec.save(path)
        except OSError as exc:
            return self.error("Kaydedilemedi", exc)
        self.status(f"Kaydedildi: {path}")

    def generate(self):
        errs = self.all_errors()
        if errs:
            QMessageBox.warning(self, self.title, "Interconnect'lerde ya da adres haritasında hata var. Alt çubukta "
                                                  "gösterilen hataları düzeltip tekrar dene.")
            return
        outdir = QFileDialog.getExistingDirectory(self, "Çıktı klasörü", os.path.dirname(self.ctx.path or ROOT))
        if not outdir:
            return
        paths = []
        try:
            for b in A.levels(self.proj):
                paths += X.write_xbar(self.spec(b), outdir)
        except Exception as exc:  # noqa: BLE001
            return self.error("Üretim başarısız", exc)
        hint = ("<modül>.v: MASTER<i>_AXI_* ve SLAVE<j>_AXI_* portları master'ların M_AXI_* ve slave'lerin S_AXI_* "
                "portlarına bağlanır. Bir Sub-bus Bridge, üst interconnect'in SLAVE portuna ve arkasındaki "
                "interconnect'in MASTER0 portuna bağlanan bloktur (bu araç onun için dosya üretmez).")
        if self.style == "packed":
            hint += "\n<modül>_pk.v (HDL Designer) <modül>.v'yi sarar: port başına MASTER<i>/SLAVE<j>_AXI_REQ/RSP."
        QMessageBox.information(self, "Üretim tamamlandı",
                                f"{outdir}\n\n" + "\n".join(os.path.basename(p) for p in paths)
                                + f"\n\n{hint}\n\naxilxbar yalnızca parametreleriyle yapılandırılır. HDL projesine "
                                  "hdl/ klasöründeki axilxbar.v, addrdecode.v ve skidbuffer.v de eklenmelidir.")
