"""Slave Interface Wrapper: register map -> AXI4-Lite slave HDL.

The register map itself is designed in the Register Map Editor; this tool takes one (a project
IP, a .regspec.json file or an IP handed over by another tool) and generates the wrapper,
testbench, instantiation templates, C header, documentation and Excel map, runs the testbench
with iverilog and packs existing AXI4-Lite modules for HDL Designer.
"""

import copy
import os
import re
import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import axilpack
from axil_studio.core import regmap_xlsx
from axil_studio import ROOT, theme, ui
from axil_studio.tools.base import ToolPage
from axil_studio.tools.regmap import SPEC_FILTER, fill_regmap_tree, regmap_tree
from axil_studio.core.model import hex32


def output_files(spec):
    """Names of the files generate_all() writes for `spec`."""
    m = spec.module
    names = [f"{m}.v", f"{m}_tb.v", f"{m}_inst.v", f"{m}_comp.vhd"]
    if spec.packed:
        names += [f"{m}_pk.v", f"{m}_pk_inst.v", f"{m}_pk_comp.vhd"]
    names += [f"{m}_regs.h", f"{m}_regs.md", f"{m}.regspec.json", f"{m}.axproj.json"]
    if regmap_xlsx.available():
        names.append(f"{m}_regmap.xlsx")
    return names


class WrapperPage(ToolPage):
    tool_id = "wrapper"
    title = "Slave Interface Wrapper"
    short_title = "Slave Interface Wrapper"
    subtitle = "Register haritasından AXI4-Lite slave HDL, testbench, header ve dokümantasyonu üret."
    description = ("Register Map Editor'da tasarlanan IP'den AXI4-Lite slave HDL, testbench, C header, doküman "
                   "ve Excel kütük haritası üretir; hazır IP'ler için HDL Designer wrapper'ı çıkarır.")
    icon_name = "wrapper"
    version = "0.2.0"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.spec = None
        self.origin = None          # ("project", ip name) | ("file", path) | ("other", label)
        self.outdir = None
        self._loading = False
        self._jobs = ui.Async(self)
        self.has_iverilog = bool(shutil.which("iverilog"))

        self.gen_btn = self.header.add(ui.button("Üret…", "play", "primary", self.generate,
                                                 "Wrapper, testbench, header, doküman ve Excel'i üret (Ctrl+G)"))

        bar = ui.ActionBar()
        bar.add("IP seç…", "library", self.choose_ip, "IP Library'den projedeki ya da bir kütüphanedeki IP'yi seç")
        bar.add("Dosyadan aç…", "folder", self.open_spec, ".regspec.json dosyası")
        bar.sep()
        self.edit_btn = bar.add("Register haritasını düzenle", "regmap", self.edit_regmap,
                                "Register Map Editor'da aç")
        bar.sep()
        self.xlsx_btn = bar.add("Excel'e aktar…", "file", self.export_xlsx, "Kütük haritası (.xlsx)",
                                regmap_xlsx.available())
        self.tb_btn = bar.add("Testbench çalıştır", "play", self.run_testbench,
                              "iverilog ile kendini kontrol eden testbench" if self.has_iverilog
                              else "iverilog bulunamadı", self.has_iverilog)
        bar.add("Hazır IP için HDL Designer wrapper'ı…", "wrapper", self.pack_existing,
                "Mevcut bir AXI4-Lite modülünün üstüne paketli REQ/RSP portlu <modül>_pk.v bloğu üretir")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: IP + output options
        left = ui.Card(margins=16, spacing=10)
        left.lay.addWidget(ui.label("IP", "section"))
        self.ip_title = ui.label("", "cardTitle")
        self.ip_sub = ui.label("", "muted", wrap=True)
        left.lay.addWidget(self.ip_title)
        left.lay.addWidget(self.ip_sub)
        left.lay.addWidget(ui.Divider())
        left.lay.addWidget(ui.label("ÇIKTI", "section"))
        self.packed_cb = QCheckBox("HDL Designer için paketli AXI wrapper'ı")
        self.packed_cb.setToolTip("S_AXI_* portlu IP'nin üzerine, HDL Designer'da kullanmak için paketli\n"
                                  "SLAVE_AXI_REQ / SLAVE_AXI_RSP portlu bir blok (<modül>_pk.v) da üretir.")
        self.packed_cb.toggled.connect(self._packed_changed)
        left.lay.addWidget(self.packed_cb)
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        left.lay.addWidget(self.files, 1)
        left.lay.addWidget(ui.label("DOĞRULAMA", "section"))
        self.issues = ui.IssueList("Hata yok")
        left.lay.addWidget(self.issues)
        left.setMinimumWidth(340)
        split.addWidget(left)

        # ---- right: register overview + generated text previews
        right = ui.Card(margins=0)
        self.tabs = ui.DetachableTabs(self.title)
        right.lay.addWidget(self.tabs)
        rtab = QWidget()
        rl = QVBoxLayout(rtab)
        rl.setContentsMargins(12, 12, 12, 12)
        self.regs = regmap_tree()
        self.regs.itemDoubleClicked.connect(self._reg_double_clicked)
        self.regs.setToolTip("Çift tık: Register Map Editor'da düzenle")
        rl.addWidget(self.regs)
        self.tabs.addTab(rtab, "Register haritası")
        self.previews = {}
        for key, title in (("verilog", "Verilog önizleme"), ("header", "C header"), ("doc", "Doküman")):
            tab = QWidget()
            tl = QVBoxLayout(tab)
            tl.setContentsMargins(12, 12, 12, 12)
            ed = QPlainTextEdit()
            ed.setReadOnly(True)
            ed.setProperty("mono", "true")
            ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            tl.addWidget(ed)
            self.tabs.addTab(tab, title)
            self.previews[key] = (tab, ed)
        self.tabs.currentChanged.connect(lambda *_: self._refresh_preview())
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 880])
        self.body.addWidget(split, 1)

        ips = ctx.project.ips
        if ips:
            self.load_project_ip(ips[0].name, quiet=True)
        else:
            self._refresh()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Wrapper")
        win = self.window()

        def act(text, slot, shortcut=None, enabled=True):
            a = QAction(text, win)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.setEnabled(enabled)
            m.addAction(a)
            return a
        act("IP seç (IP Library)…", self.choose_ip, "Ctrl+Shift+O")
        act("Dosyadan aç…", self.open_spec)
        act("Register haritasını düzenle", self.edit_regmap)
        m.addSeparator()
        act("Üret…", self.generate, "Ctrl+G")
        act("Excel'e aktar…", self.export_xlsx, None, regmap_xlsx.available())
        act("Testbench'i çalıştır (iverilog)", self.run_testbench, None, self.has_iverilog)
        act("Hazır IP için HDL Designer wrapper'ı…", self.pack_existing)

    # ================================================================ source
    def load_project_ip(self, name, quiet=False):
        ip = self.ctx.project.get_ip(name)
        if ip is None:
            return False
        self.spec = G.spec_from_ipblock(ip)
        self.origin = ("project", name)
        self._refresh()
        if not quiet:
            self.window().raise_()
            self.status(f"{name} seçildi")
        return True

    def load_spec(self, spec, origin=""):
        """Take a register map from another tool (not in the project yet)."""
        self.spec, self.origin = spec, ("other", origin or "dışarıdan")
        self._refresh()
        self.window().raise_()
        self.status(f"{spec.module} açıldı" + (f" ({origin})" if origin else ""))
        return True

    def choose_ip(self):
        name = self.pick_ip("HDL üretilecek IP", "Projedeki ya da açık kütüphanelerdeki bir IP'yi seç. "
                                                 "Kütüphane IP'si önce projeye alınır.")
        if name:
            self.load_project_ip(name)

    def open_spec(self):
        path, _ = QFileDialog.getOpenFileName(self, "Register spesifikasyonu aç", self.outdir or ROOT, SPEC_FILTER)
        if not path:
            return
        try:
            spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error("Açılamadı", exc)
        self.spec, self.origin, self.outdir = spec, ("file", path), os.path.dirname(path)
        self._refresh()
        self.status(f"Açıldı: {path}")

    def edit_regmap(self, reg=None):
        if self.spec is None:
            return
        if self.origin and self.origin[0] == "project":
            self.edit_register_map(self.origin[1], reg)
        else:
            w = self.open_tool("regmap")
            w.page.load_spec(copy.deepcopy(self.spec), "Slave Interface Wrapper'dan")

    def _reg_double_clicked(self, item, _col):
        while item.parent() is not None:
            item = item.parent()
        self.edit_regmap(item.text(0))

    def on_project_replaced(self):
        self.spec = self.origin = None
        ips = self.ctx.project.ips
        if ips:
            self.load_project_ip(ips[0].name, quiet=True)
        else:
            self._refresh()

    def on_project_modified(self):
        """The register map may have been edited in the Register Map Editor: follow it."""
        if self.origin and self.origin[0] == "project":
            ip = self.ctx.project.get_ip(self.origin[1])
            if ip is None:
                self.origin = ("other", f"{self.origin[1]} projeden kaldırıldı")
            else:
                self.spec = G.spec_from_ipblock(ip)
        self._refresh()

    def _packed_changed(self, on):
        if self._loading or self.spec is None:
            return
        self.spec.packed = on
        if self.origin and self.origin[0] == "project":
            ip = self.ctx.project.get_ip(self.origin[1])
            if ip is not None:          # the option is kept with the IP definition
                ip.regspec = self.spec.to_dict()
                self.ctx.mark_modified(self.tool_id)
        self._refresh()

    # ================================================================ view
    def _refresh(self):
        spec = self.spec
        has = spec is not None
        for w in (self.gen_btn, self.edit_btn, self.packed_cb):
            w.setEnabled(has)
        self.xlsx_btn.setEnabled(has and regmap_xlsx.available())
        self.tb_btn.setEnabled(has and self.has_iverilog)
        self.files.clear()
        if not has:
            self.ip_title.setText("IP seçilmedi")
            self.ip_sub.setText("IP seç ile projedeki ya da kütüphanedeki bir IP'yi seç, ya da bir .regspec.json "
                                "dosyası aç. Register haritası Register Map Editor'da tasarlanır.")
            self.issues.set_issues([])
            fill_regmap_tree(self.regs, None)
            self._refresh_preview()
            return
        o = self.origin or ("other", "")
        where = {"project": "projedeki IP", "file": f"dosya: {o[1]}", "other": o[1]}[o[0]]
        nf = sum(len(r.fields) for r in spec.registers)
        self.ip_title.setText(f"{spec.module}   ·   {spec.version}")
        n_inst = len(self.ctx.project.instances_of(spec.module)) if o[0] == "project" else 0
        self.ip_sub.setText(f"{where} · {len(spec.registers)} register, {nf} bit alanı · adres alanı "
                            f"0x{spec.span:X} byte" + (f" · adres haritasında {n_inst} kez" if n_inst else ""))
        self._loading = True
        self.packed_cb.setChecked(spec.packed)
        self._loading = False
        mono = theme.mono_font()
        for name in output_files(spec):
            it = QListWidgetItem(name)
            it.setFont(mono)
            self.files.addItem(it)
        errs = spec.validate()
        self.issues.set_issues(errs)
        self.gen_btn.setEnabled(not errs)
        fill_regmap_tree(self.regs, G.to_ipblock(spec, 0))
        self._refresh_preview()

    def _refresh_preview(self):
        for key, (tab, ed) in self.previews.items():
            if not self.tabs.is_shown(tab):
                continue
            if self.spec is None:
                text = ""
            else:
                spec = self._out_spec()
                errs = spec.validate()
                try:
                    if errs:
                        raise G.SpecError("\n".join(errs))
                    if key == "verilog":
                        text = G.gen_verilog(spec)   # the IP itself (the _pk wrapper is separate)
                    elif key == "header":
                        text = G.gen_header(spec)
                    else:
                        text = G.gen_markdown(spec)
                except G.SpecError as exc:
                    text = "// Register haritası geçerli değil (Register Map Editor'da düzelt):\n// " \
                           + str(exc).replace("\n", "\n// ")
            bar = ed.verticalScrollBar().value()
            ed.setPlainText(text)
            ed.verticalScrollBar().setValue(bar)

    def on_theme(self):
        self.issues.retint()
        self._refresh()

    # ================================================================ outputs
    def _ready(self):
        if self.spec is None:
            QMessageBox.information(self, self.title, "Önce bir IP seç.")
            return False
        if self.spec.validate():
            QMessageBox.warning(self, self.title, "Register haritasında hata var. Soldaki listede gösterilen "
                                                  "hataları Register Map Editor'da düzeltip tekrar dene.")
            return False
        return True

    def _project_base(self):
        """Base of the IP's first address-map instance (0 when it is not placed or not in the project)."""
        if self.origin and self.origin[0] == "project":
            insts = self.ctx.project.instances_of(self.origin[1])
            if insts:
                return insts[0].base
        return 0

    def _out_spec(self):
        """Copy of the spec for the outputs; the base (C header, Excel, test address) is the IP's
        address in the project's address map: it is never set here."""
        spec = copy.deepcopy(self.spec)
        spec.base = self._project_base()
        return spec

    def generate(self):
        if not self._ready():
            return
        outdir = QFileDialog.getExistingDirectory(self, "Çıktı klasörü", self.outdir or ROOT)
        if not outdir:
            return
        self.outdir = outdir
        try:
            paths = G.generate_all(self._out_spec(), outdir)
        except Exception as exc:  # noqa: BLE001
            return self.error("Üretim başarısız", exc)
        names = "\n".join(os.path.basename(p) for p in paths)
        text = f"{len(paths)} dosya oluşturuldu:\n{outdir}\n\n{names}"
        if self.origin and self.origin[0] == "project":
            QMessageBox.information(self, "Üretim tamamlandı", text)
        elif ui.confirm(self, "Üretim tamamlandı", text + "\n\nIP projeye de eklensin mi?", ok="Projeye ekle",
                        cancel="Kapat"):
            self.add_to_project()

    def add_to_project(self):
        name = self.spec.module
        if self.ctx.project.get_ip(name) and not ui.confirm(
                self, self.title, f"Projede “{name}” adlı bir IP var. Register haritası bununla değiştirilsin mi?",
                ok="Değiştir"):
            return
        self.ctx.put_ip(G.to_ipblock(copy.deepcopy(self.spec), 0, with_spec=True), self.tool_id)
        self.origin = ("project", name)
        self._refresh()
        self.status(f"{name} projeye eklendi; adres haritasına Address Map Editor'dan yerleştirilir")

    def export_xlsx(self):
        if not self._ready():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Kütük haritası (Excel)",
                                              os.path.join(self.outdir or ROOT, f"{self.spec.module}_regmap.xlsx"),
                                              "Excel (*.xlsx)")
        if not path:
            return
        base = self._project_base()
        try:
            G.export_xlsx(self.spec, path, base)
        except (OSError, RuntimeError) as exc:
            return self.error("Aktarılamadı", exc)
        self.status(f"Excel'e aktarıldı: {path} (base {hex32(base)}; Özet sayfasındaki sarı hücreden değiştirilebilir)")

    def run_testbench(self):
        if not self._ready():
            return
        d = self.outdir or os.path.join(ROOT, "axilgen_out")
        spec = self._out_spec()
        self.status("Testbench çalışıyor…", 0)

        def job():
            G.generate_all(spec, d)
            m = spec.module
            exe = os.path.join(d, f"{m}_tb.vvp")
            srcs = [os.path.join(d, f) for f in [f"{m}_tb.v"] + G.hdl_files(spec)]
            subprocess.run(["iverilog", "-g2012", "-o", exe] + srcs, check=True, capture_output=True, text=True)
            return subprocess.run(["vvp", "-n", exe], capture_output=True, text=True, timeout=120).stdout

        def ok(out):
            lines = [ln for ln in out.splitlines() if ln.startswith(("FAIL", "TIMEOUT")) or "checks" in ln]
            summary = lines[-1] if lines else out[-500:]
            self.status(summary)
            box = QMessageBox.information if "PASSED" in summary else QMessageBox.critical
            box(self, "Testbench", "\n".join(lines[-20:]) or out[-2000:])

        def err(exc):
            self.status("Testbench başarısız")
            msg = exc.stderr[-2000:] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            QMessageBox.critical(self, "Testbench", msg)

        self._jobs.run(job, ok, err)

    def pack_existing(self):
        src, _ = QFileDialog.getOpenFileName(self, "HDL Designer wrapper'ı üretilecek AXI4-Lite modülü (.v)",
                                             self.outdir or ROOT,
                                             "Verilog (*.v *.sv);;Tüm dosyalar (*)")
        if not src:
            return
        try:
            with open(src, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            infos = []
            for n in re.findall(r"^\s*module\s+([A-Za-z_]\w*)", text, re.M):
                try:
                    info = axilpack.parse_module(text, n)
                except axilpack.ParseError:
                    continue
                if axilpack.axi_groups(info):
                    infos.append(info)
            if not infos:
                raise axilpack.ParseError("Dosyada S_AXI_* / M_AXI_* portlu ANSI başlıklı modül bulunamadı")
        except (OSError, axilpack.ParseError) as exc:
            return self.error("Paketlenemedi", exc)
        d = QFileDialog.getExistingDirectory(self, "Çıktı klasörü", os.path.dirname(src))
        if not d:
            return
        out = []
        for info in infos:
            for name, text in G.hdl_designer_files(info).items():
                with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            groups = ", ".join(axilpack.PACKED_NAME[g] + "_REQ/RSP" for g in axilpack.axi_groups(info))
            out.append(f"{info['name']}_pk.v (+ _inst.v, _comp.vhd)  →  {groups}")
        QMessageBox.information(self, "HDL Designer wrapper'ı üretildi",
                                "\n".join(out) + f"\n\n{d}\n\nHDL Designer'da _pk bloğunu kullan. Orijinal "
                                "modül dosyası da projede bulunmalı.")

    def shutdown(self):
        self._jobs.shutdown()
