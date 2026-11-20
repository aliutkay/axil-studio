"""Interconnect Configurator: the project's address map -> axilxbar crossbar wrapper.

The slave ports come from the Address Map Editor (port j = address-map entry j, with its base
and window); this tool sets the crossbar itself: module name, port style and masters, shows the
block diagram and the Verilog, and generates the wrapper. Its settings are stored in the
project file ("interconnect"); addresses are only edited in the Address Map Editor.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
                               QPlainTextEdit, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from axil_studio.core import addrmap as A
from axil_studio.core import xbargen as X
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import size_text
from axil_studio.tools.base import ToolPage
from axil_studio.tools.xbar_diagram import BlockDiagram
from axil_studio.core.model import hex32

XBAR_FILTER = "Interconnect ayarları (*.xbar.json);;JSON (*.json);;Tüm dosyalar (*)"
STYLE_SHORT = {"packed": "HDL Designer (paketli)", "signals": "Ayrık AXI sinyalleri"}


# ============================================================================ page
class InterconnectPage(ToolPage):
    tool_id = "interconnect"
    title = "Interconnect Configurator"
    short_title = "Interconnect Configurator"
    subtitle = "Adres haritasındaki slave'lerle axilxbar crossbar'ını yapılandır ve wrapper'ını üret."
    description = ("Master'ları ve port stilini ayarlar, adres haritasından slave portlarını alır, blok "
                   "diyagramını çizer ve ZipCPU axilxbar için isimli portlu wrapper üretir.")
    icon_name = "interconnect"
    version = "0.1.0"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.module = "axil_xbar"
        self.style = "packed"
        self.masters = [X.XbarMaster("hexbus")]
        self._loading = False

        self.gen_btn = self.header.add(ui.button("Üret…", "play", "primary", self.generate,
                                                 "axilxbar wrapper'ını ve dokümanını üret"))
        bar = ui.ActionBar()
        bar.add("Adres haritasını düzenle…", "addrmap", lambda: self.edit_address_map(),
                "Slave'ler, adresleri ve sıraları Address Map Editor'da düzenlenir")
        bar.sep()
        bar.add("Ayar dosyası aç…", "folder", self.import_xbar, ".xbar.json dosyasından crossbar ayarlarını al")
        bar.add("Ayar dosyası kaydet…", "save", self.export_xbar, "Ayarları ve slave portlarını .xbar.json olarak kaydet")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: crossbar, masters, validation
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("CROSSBAR", "section"))
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.module_ed = QLineEdit()
        self.style_cb = QComboBox()
        for key, text in X.STYLES.items():
            if key in STYLE_SHORT:
                self.style_cb.addItem(STYLE_SHORT[key], key)
                self.style_cb.setItemData(self.style_cb.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        form.addRow("Modül adı", self.module_ed)
        form.addRow("Port stili", self.style_cb)
        left.lay.addLayout(form)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("MASTER'LAR", "section"))
        self.master_tbl = QTableWidget(0, 2)
        self.master_tbl.setHorizontalHeaderLabels(["Port", "Ad"])
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
        self.master_tbl.setMaximumHeight(160)
        self.master_tbl.setToolTip("Ada çift tıklayarak değiştir")
        self.master_tbl.itemChanged.connect(self._master_changed)
        self.master_tbl.itemSelectionChanged.connect(self._update_diagram)
        left.lay.addWidget(self.master_tbl)
        mbar = QHBoxLayout()
        mbar.setSpacing(4)
        mbar.addWidget(ui.button("Master", "plus", "ghost", self.add_master, "Yeni master"))
        mbar.addWidget(ui.button("Sil", None, "ghost", self.delete_master, "Seçili master'ı sil"))
        mbar.addStretch(1)
        left.lay.addLayout(mbar)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("DOĞRULAMA", "section"))
        self.issues = ui.IssueList("Hata yok")
        self.issues.setMaximumHeight(16777215)
        left.lay.addWidget(self.issues, 1)
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
        srow.addWidget(ui.label("Slave portları adres haritasından gelir (port j = haritadaki j. satır). Ad, adres, "
                                "boyut ve sıra Address Map Editor'da değiştirilir; satıra çift tıkla.", "muted",
                                wrap=True), 1)
        srow.addWidget(ui.button("Address Map Editor", "addrmap", "ghost", lambda: self.edit_address_map()))
        st.addLayout(srow)
        self.slave_tbl = QTableWidget(0, 5)
        self.slave_tbl.setHorizontalHeaderLabels(["Port", "Ad", "IP", "Adres", "Boyut"])
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
        drow.addWidget(ui.button("−", None, "ghost", lambda: self.diagram.zoom_by(1 / 1.2), "Uzaklaş"))
        self.zoom_lbl = ui.label("100%", "muted")
        self.zoom_lbl.setMinimumWidth(44)
        self.zoom_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drow.addWidget(self.zoom_lbl)
        drow.addWidget(ui.button("+", None, "ghost", lambda: self.diagram.zoom_by(1.2), "Yakınlaş"))
        drow.addWidget(ui.button("Sığdır", None, "ghost", lambda: self.diagram.set_zoom(None), "Genişliğe sığdır"))
        drow.addWidget(ui.button("100%", None, "ghost", lambda: self.diagram.set_zoom(1.0), "Gerçek boyut"))
        drow.addSpacing(12)
        drow.addWidget(ui.label("Fare tekerleği: yakınlaş / uzaklaş · sürükle: kaydır", "muted"))
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
        self.diagram.zoomChanged.connect(lambda z: self.zoom_lbl.setText(f"{round(z * 100)}%"))
        scroll = QScrollArea()
        scroll.setWidget(self.diagram)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.diagram.scroll = scroll
        dt.addWidget(scroll, 1)
        self.tabs.addTab(dia_tab, "Blok diyagramı")

        pv_tab = self.pv_tab = QWidget()
        pv = QVBoxLayout(pv_tab)
        pv.setContentsMargins(12, 12, 12, 12)
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
                           ("Ayar dosyası aç…", self.import_xbar), ("Ayar dosyası kaydet…", self.export_xbar),
                           (None, None), ("Üret…", self.generate)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            m.addAction(a)

    # ================================================================ project storage
    def spec(self):
        """The crossbar: these settings + the slave ports of the address map."""
        return X.XbarSpec(self.module, self.masters, A.xbar_slaves(self.ctx.project), self.style)

    def load_from_project(self):
        d = self.ctx.project.interconnect or {}
        self.module = d.get("module", "axil_xbar")
        self.style = d.get("style", "packed")
        self.masters = [X.XbarMaster(m.get("name", "master"), True) for m in d.get("masters", [])] \
            or [X.XbarMaster("hexbus")]
        self._load_widgets()
        self._refresh()

    def _commit(self):
        d = {"module": self.module, "style": self.style, "masters": [{"name": m.name} for m in self.masters]}
        if d != self.ctx.project.interconnect:
            self.ctx.project.interconnect = d
            self.ctx.mark_modified(self.tool_id)

    def _load_widgets(self):
        self._loading = True
        self.module_ed.setText(self.module)
        self.style_cb.setCurrentIndex(max(0, self.style_cb.findData(self.style)))
        self._loading = False

    def on_project_replaced(self):
        self.load_from_project()

    def on_project_modified(self):
        self._refresh()

    def _after_change(self):
        self._commit()
        self._refresh()

    # ================================================================ view
    def _refresh(self):
        t = theme.manager().tokens
        mono = theme.mono_font()
        spec = self.spec()
        self._loading = True
        self.master_tbl.setRowCount(spec.nm)
        for i, m in enumerate(spec.masters):
            port = QTableWidgetItem(f"MASTER{i}")
            port.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            port.setFont(mono)
            self.master_tbl.setItem(i, 0, port)
            self.master_tbl.setItem(i, 1, QTableWidgetItem(m.name))

        sel = self._sel_slave()
        bad = A.overlapping(self.ctx.project)
        insts = self.ctx.project.instances
        self.slave_tbl.setRowCount(spec.ns)
        for j, (inst, s) in enumerate(zip(insts, spec.slaves)):
            ip = "harici" if inst.external else inst.ip
            cells = [(f"SLAVE{j}", True), (s.name, False), (ip, False), (hex32(s.base), True),
                     (size_text(s.window), False)]
            for col, (text, is_mono) in enumerate(cells):
                it = QTableWidgetItem(text)
                if is_mono:
                    it.setFont(mono)
                it.setToolTip(f"{hex32(s.base)} – {hex32(s.base + s.window - 1)}\nMASK {hex32(s.mask)}\n"
                              "Çift tık: Address Map Editor'da düzenle")
                if col == 3 and j in bad:
                    it.setForeground(QColor(t["danger"]))
                self.slave_tbl.setItem(j, col, it)
        self.slave_tbl.resizeColumnsToContents()
        self.slave_tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if sel is not None and sel < spec.ns:
            self.slave_tbl.selectRow(sel)
        self._loading = False

        if not spec.slaves:
            errs = ["Adres haritası boş: Address Map Editor'da IP ekle."]
        else:
            errs = spec.validate()
        if not errs:
            self.issues.ok_text = f"Hata yok · {spec.nm} master · {spec.ns} slave"
        self.issues.set_issues(errs)
        self.gen_btn.setEnabled(not errs)
        self._update_diagram()

    def _sel_slave(self):
        rows = self.slave_tbl.selectionModel().selectedRows() if self.slave_tbl.selectionModel() else []
        return rows[0].row() if rows else None

    def _open_slave(self, j):
        insts = self.ctx.project.instances
        if 0 <= j < len(insts):
            self.edit_address_map(insts[j].name)

    def _update_diagram(self):
        if self._loading:
            return
        rows = self.master_tbl.selectionModel().selectedRows() if self.master_tbl.selectionModel() else []
        s = self._sel_slave()
        sel = ("s", s) if s is not None else (("m", rows[0].row()) if rows else None)
        self.diagram.set_spec(self.spec(), sel, A.overlapping(self.ctx.project))
        self._refresh_preview()

    def _refresh_preview(self):
        if not self.tabs.is_shown(self.pv_tab):
            return
        spec = self.spec()
        errs = spec.validate() if spec.slaves else ["Adres haritası boş"]
        if errs:
            text = "// Ayarlarda hata var, Verilog üretilemiyor:\n// " + "\n// ".join(errs)
        else:
            text = X.gen_xbar(spec)
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
        self.module = self.module_ed.text().strip()
        self.style = self.style_cb.currentData()
        self._after_change()

    def _master_changed(self, item):
        if self._loading or item.column() != 1:
            return
        m = self.masters[item.row()]
        m.name = item.text().strip() or m.name
        self._after_change()

    def add_master(self):
        if len(self.masters) >= 16:
            return
        self.masters.append(X.XbarMaster(f"master{len(self.masters)}", True))
        self._after_change()
        self.master_tbl.selectRow(len(self.masters) - 1)

    def delete_master(self):
        rows = self.master_tbl.selectionModel().selectedRows()
        if not rows:
            return
        i = rows[0].row()
        if len(self.masters) == 1:
            QMessageBox.warning(self, self.title, "Interconnect'te en az bir master olmalı.")
            return
        m = self.masters[i]
        if not ui.confirm(self, "Master'ı sil", f"MASTER{i} ({m.name}) silinecek. Sonraki master'ların "
                                                 "port numarası bir azalır."):
            return
        del self.masters[i]
        self._after_change()

    def rename_master(self, i):
        m = self.masters[i]
        name, ok = QInputDialog.getText(self, f"MASTER{i}", "Ad:", text=m.name)
        if ok and name.strip():
            m.name = name.strip()
            self._after_change()

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
        self.module, self.style = spec.module, spec.style if spec.style in STYLE_SHORT else "packed"
        self.masters = [X.XbarMaster(m.name, True) for m in spec.masters if m.enabled] or [X.XbarMaster("hexbus")]
        self._load_widgets()
        self._after_change()
        self.status(f"Crossbar ayarları alındı: {path} (slave'ler ve adresler Address Map Editor'dan gelir)")

    def export_xbar(self):
        path, _ = QFileDialog.getSaveFileName(self, "Interconnect ayar dosyası",
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT),
                                                           f"{self.module}.xbar.json"), XBAR_FILTER)
        if not path:
            return
        try:
            self.spec().save(path)
        except OSError as exc:
            return self.error("Kaydedilemedi", exc)
        self.status(f"Kaydedildi: {path}")

    def generate(self):
        spec = self.spec()
        errs = spec.validate() if spec.slaves else ["Adres haritası boş"]
        if errs:
            QMessageBox.warning(self, self.title, "Crossbar ya da adres haritasında hata var. Soldaki listede "
                                                  "gösterilen hataları düzeltip tekrar dene.")
            return
        outdir = QFileDialog.getExistingDirectory(self, "Çıktı klasörü", os.path.dirname(self.ctx.path or ROOT))
        if not outdir:
            return
        try:
            paths = X.write_xbar(spec, outdir)
        except Exception as exc:  # noqa: BLE001
            return self.error("Üretim başarısız", exc)
        hint = {
            "packed": "Bağlantı: master MASTER_AXI_REQ/RSP → MASTER<i>_AXI_REQ/RSP, "
                      "IP'nin _pk bloğu SLAVE_AXI_REQ/RSP → SLAVE<j>_AXI_REQ/RSP.",
            "signals": "Bağlantı: MASTER<i>_AXI_* ve SLAVE<j>_AXI_* portları IP'lerin M_AXI_* / S_AXI_* "
                       "portlarına bağlanır.",
        }.get(spec.style, "")
        QMessageBox.information(self, "Üretim tamamlandı",
                                f"{outdir}\n\n" + "\n".join(os.path.basename(p) for p in paths)
                                + f"\n\n{hint}\n\nHDL projesine hdl/axilxbar.v ile wb2axip'ten addrdecode.v ve "
                                  "skidbuffer.v dosyaları da eklenmelidir.")
