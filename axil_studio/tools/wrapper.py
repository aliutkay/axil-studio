"""Slave Interface Wrapper: register map -> AXI4-Lite slave HDL.

The register map itself is designed in the Register Map Editor; this tool takes one (a project
IP, a .regspec.json file or an IP handed over by another tool) and generates the wrapper,
testbench, instantiation templates and documentation, runs the testbench
with iverilog and packs existing AXI4-Lite modules for HDL Designer.
"""

import copy
import os
import re
import shutil
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio.core import axilpack
from axil_studio.core import hdlspec as H
from axil_studio import ROOT, theme, ui
from axil_studio.tools.base import ToolPage
from axil_studio.tools.regmap import SPEC_FILTER, fill_regmap_tree, regmap_tree
from axil_studio.core.model import hex32
from axil_studio.i18n import tr


# what each generated Verilog file is (preview selector)
def verilog_label(spec, name):
    m = spec.module
    return {f"{m}.v": "IP (easyaxil, S_AXI_*)", f"{m}_pk.v": tr("HDL Designer wrapper (SLAVE_AXI_REQ/RSP)"),
            f"{m}_inst.v": tr("instantiation template"), f"{m}_pk_inst.v": tr("HDL Designer wrapper instantiation template"),
            f"{m}_tb.v": "testbench"}.get(name, "")


def output_files(spec):
    """Names of the files generate_all() writes for `spec`."""
    return G.output_texts_names(spec, xlsx=False)


class WrapperPage(ToolPage):
    tool_id = "wrapper"
    title = "Slave Interface Wrapper"
    short_title = "Slave Interface Wrapper"
    subtitle = tr("Generate AXI4-Lite slave HDL, testbench and documentation from a register map.")
    description = tr("AXI4-Lite slave generation from a register map: synthesizable Verilog, a self-checking testbench and register documentation, plus HDL Designer wrappers for existing AXI4-Lite modules.")
    icon_name = "wrapper"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.spec = None
        self._vfile_module = ""
        self.origin = None          # ("project", ip name) | ("file", path) | ("other", label)
        self.outdir = None
        self._loading = False
        self._jobs = ui.Async(self)
        self.has_iverilog = bool(shutil.which("iverilog"))

        self.gen_btn = self.header.add_right(ui.button(tr("Generate…"), "play", "primary", self.generate,
                                                 tr("Generate the HDL, testbench and document (Ctrl+G)")))

        bar = self.header       # the tool's commands sit in the top bar
        bar.add(tr("Select IP…"), "library", self.choose_ip, tr("Select an IP of the project or of a library (IP Library)"))
        bar.add(tr("Import…"), "folder", self.open_spec, tr("Generate HDL from a .regspec.json file"))
        bar.sep()
        self.edit_btn = bar.add(tr("Edit register map"), "regmap", self.edit_regmap,
                                tr("Open in Register Map Editor"))
        bar.sep()
        self.tb_btn = bar.add(tr("Run testbench"), "play", self.run_testbench,
                              tr("Self-checking testbench with iverilog") if self.has_iverilog
                              else tr("iverilog not found"), self.has_iverilog)
        bar.add(tr("HDL Designer wrapper for a ready-made IP…"), "wrapper", self.pack_existing,
                tr("Makes a <module>_pk.v block with packed REQ/RSP ports around an existing AXI4-Lite module"))

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
        left.lay.addWidget(ui.label(tr("OUTPUT"), "section"))
        self.packed_cb = QCheckBox(tr("Packed AXI wrapper for HDL Designer"))
        self.packed_cb.setToolTip(tr("Besides the IP with S_AXI_* ports, also generates a block with packed\nSLAVE_AXI_REQ / SLAVE_AXI_RSP ports (<module>_pk.v) for HDL Designer."))
        self.packed_cb.toggled.connect(self._packed_changed)
        left.lay.addWidget(self.packed_cb)
        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        left.lay.addWidget(self.files, 1)
        self.issues = ui.IssueList(tr("No errors"))     # shown in the window's status bar
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
        self.regs.setToolTip(tr("Double-click: edit in the Register Map Editor"))
        rl.addWidget(self.regs)
        self.tabs.addTab(rtab, tr("Register map"))
        self.previews = {}
        for key, title in (("verilog", tr("Verilog preview")), ("doc", tr("Document"))):
            tab = QWidget()
            tl = QVBoxLayout(tab)
            tl.setContentsMargins(12, 12, 12, 12)
            if key == "verilog":
                frow = QHBoxLayout()
                frow.addWidget(ui.label(tr("File"), "muted"))
                self.vfile_cb = QComboBox()
                self.vfile_cb.setMinimumWidth(360)
                self.vfile_cb.setToolTip(tr("Verilog file to preview: IP, HDL Designer wrapper, instantiation templates, testbench"))
                self.vfile_cb.currentIndexChanged.connect(lambda *_: self._refresh_preview())
                frow.addWidget(self.vfile_cb)
                frow.addStretch(1)
                tl.addLayout(frow)
            ed = QPlainTextEdit()
            ed.setReadOnly(True)
            ed.setProperty("mono", "true")
            ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            tl.addWidget(ed)
            self.tabs.addTab(tab, title)
            self.previews[key] = (tab, ed)
        self.spec_view = ui.SpecView(
            tr("HDL spec (read-only): the Verilog files are made from this alone. Only data that changes the code: registers, bit fields and their types, strobes, reset values; no descriptions, value meanings, version or address. Command line: python -m axil_studio.core.hdlspec <file> -o out"),
            self.save_hdlspec, tr("Save (.hdlspec.json)…"), tr("Write the HDL spec to a file"))
        self.tabs.addTab(self.spec_view, "Spec")
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
        act(tr("Select IP (IP Library)…"), self.choose_ip, "Ctrl+Shift+O")
        act(tr("Import (.regspec.json)…"), self.open_spec)
        act(tr("Edit register map"), self.edit_regmap)
        m.addSeparator()
        act(tr("Generate…"), self.generate, "Ctrl+G")
        act(tr("Run the testbench (iverilog)"), self.run_testbench, None, self.has_iverilog)
        act(tr("HDL Designer wrapper for a ready-made IP…"), self.pack_existing)

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
            self.status(tr("{0} selected").format(name))
        return True

    def load_spec(self, spec, origin=""):
        """Take a register map from another tool (not in the project yet)."""
        self.spec, self.origin = spec, ("other", origin or tr("external"))
        self._refresh()
        self.window().raise_()
        self.status(tr("{0} opened").format(spec.module) + (f" ({origin})" if origin else ""))
        return True

    def choose_ip(self):
        name = self.pick_ip(tr("IP to generate HDL for"), tr("Select an IP of the project or of an open library. A library IP is brought into the project first."))
        if name:
            self.load_project_ip(name)

    def open_spec(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open register spec"), self.outdir or ROOT, SPEC_FILTER)
        if not path:
            return
        try:
            spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return self.error(tr("Could not open"), exc)
        self.spec, self.origin, self.outdir = spec, ("file", path), os.path.dirname(path)
        self._refresh()
        self.status(tr("Opened: {0}").format(path))

    def edit_regmap(self, reg=None):
        if self.spec is None:
            return
        if self.origin and self.origin[0] == "project":
            self.edit_register_map(self.origin[1], reg)
        else:
            w = self.open_tool("regmap")
            w.page.load_spec(copy.deepcopy(self.spec), tr("from the Slave Interface Wrapper"))

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
                self.origin = ("other", tr("{0} was removed from the project").format(self.origin[1]))
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
        self.tb_btn.setEnabled(has and self.has_iverilog)
        self.files.clear()
        if not has:
            self.ip_title.setText(tr("No IP selected"))
            self.ip_sub.setText(tr("Select an IP of the project or of a library with Select IP, or open a .regspec.json file. The register map is designed in the Register Map Editor."))
            self.issues.set_issues([])
            fill_regmap_tree(self.regs, None)
            self._fill_vfiles()
            self._refresh_preview()
            return
        o = self.origin or ("other", "")
        where = {"project": tr("project IP"), "file": tr("file: {0}").format(o[1]), "other": o[1]}[o[0]]
        nf = sum(len(r.fields) for r in spec.registers)
        self.ip_title.setText(f"{spec.module}   ·   {spec.version}")
        n_inst = len(self.ctx.project.instances_of(spec.module)) if o[0] == "project" else 0
        self.ip_sub.setText(tr("{0} · {1} registers, {2} bit fields · address space 0x{3:X} bytes").format(where, len(spec.registers), nf, spec.span) + (tr(" · {0}× in the address map").format(n_inst) if n_inst else ""))
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
        self._fill_vfiles()
        self._refresh_preview()

    def _fill_vfiles(self):
        """Verilog files of the current IP in the preview selector (keeps the choice by kind)."""
        cur = self.vfile_cb.currentData() or ""
        suffix = cur[len(self._vfile_module):] if cur.startswith(self._vfile_module) else ".v"
        self.vfile_cb.blockSignals(True)
        self.vfile_cb.clear()
        if self.spec is not None:
            for name in G.verilog_files(self.spec):
                self.vfile_cb.addItem(f"{name}  —  {verilog_label(self.spec, name)}", name)
            self._vfile_module = self.spec.module
            i = self.vfile_cb.findData(self.spec.module + suffix)
            self.vfile_cb.setCurrentIndex(max(0, i))
        self.vfile_cb.blockSignals(False)

    def spec_data(self):
        """What the Spec tab shows: the HDL spec (core/hdlspec.py), or None without an IP."""
        return None if self.spec is None else H.ip_hdlspec(self.spec)

    def save_hdlspec(self):
        if self.spec is None:
            return
        d = H.ip_hdlspec(self.spec)
        path, _ = QFileDialog.getSaveFileName(self, "HDL spec", os.path.join(self.outdir or ROOT, H.file_name(d)),
                                              "HDL spec (*.hdlspec.json);;JSON (*.json)")
        if not path:
            return
        try:
            H.save(d, path)
        except OSError as exc:
            return self.error(tr("Could not save"), exc)
        self.status(tr("Saved: {0}").format(path))

    def _refresh_preview(self):
        if self.tabs.is_shown(self.spec_view):
            data = self.spec_data()
            self.spec_view.set_data(data if data is not None else tr("No IP selected."))
        for key, (tab, ed) in self.previews.items():
            if not self.tabs.is_shown(tab):
                continue
            if self.spec is None:
                text = ""
            else:
                spec, base = self.spec, self._project_base()
                errs = spec.validate()
                try:
                    if errs:
                        raise G.SpecError("\n".join(errs))
                    if key == "verilog":
                        name = self.vfile_cb.currentData() or f"{spec.module}.v"
                        text = G.output_texts(spec, name, base)
                    else:
                        text = G.gen_markdown(spec)
                except G.SpecError as exc:
                    text = tr("// The register map is not valid (fix it in the Register Map Editor):\n//") \
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
            QMessageBox.information(self, self.title, tr("Select an IP first."))
            return False
        if self.spec.validate():
            QMessageBox.warning(self, self.title, tr("The register map has errors. Fix the errors listed on the left in the Register Map Editor and try again."))
            return False
        return True

    def _project_base(self):
        """Generation address: the IP's first address-map instance; 0 when it is not placed (or not
        a project IP)."""
        if self.origin and self.origin[0] == "project":
            return H.ip_base(self.ctx.project, self.origin[1])
        return 0

    def generate(self):
        if not self._ready():
            return
        outdir = QFileDialog.getExistingDirectory(self, tr("Output folder"), self.outdir or ROOT)
        if not outdir:
            return
        self.outdir = outdir
        try:
            paths = G.generate_all(self.spec, outdir, self._project_base(), xlsx=False)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Generation failed"), exc)
        names = "\n".join(os.path.basename(p) for p in paths)
        text = tr("{0} files created:\n{1}\n\n{2}").format(len(paths), outdir, names)
        if self.origin and self.origin[0] == "project":
            QMessageBox.information(self, tr("Generation complete"), text)
        elif ui.confirm(self, tr("Generation complete"), text + tr("\n\nAdd the IP to the project too?"), ok=tr("Add to project"),
                        cancel=tr("Close")):
            self.add_to_project()

    def add_to_project(self):
        name = self.spec.module
        if self.ctx.project.get_ip(name) and not ui.confirm(
                self, self.title, tr("The project has an IP named “{0}”. Replace its register map with this one?").format(name),
                ok=tr("Replace")):
            return
        self.ctx.put_ip(G.to_ipblock(copy.deepcopy(self.spec), 0, with_spec=True), self.tool_id)
        self.origin = ("project", name)
        self._refresh()
        self.status(tr("{0} added to the project; place it in the address map with the Address Map Editor").format(name))

    def run_testbench(self):
        if not self._ready():
            return
        d = self.outdir or os.path.join(ROOT, "axilgen_out")
        spec, base = copy.deepcopy(self.spec), self._project_base()
        self.status(tr("Running the testbench…"), 0)

        def job():
            G.generate_all(spec, d, base, xlsx=False)
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
            self.status(tr("Testbench failed"))
            msg = exc.stderr[-2000:] if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            QMessageBox.critical(self, "Testbench", msg)

        self._jobs.run(job, ok, err)

    def pack_existing(self):
        src, _ = QFileDialog.getOpenFileName(self, tr("AXI4-Lite module to wrap for HDL Designer (.v)"),
                                             self.outdir or ROOT,
                                             tr("Verilog (*.v *.sv);;All files (*)"))
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
                raise axilpack.ParseError(tr("No module with ANSI header and S_AXI_* / M_AXI_* ports found in the file"))
        except (OSError, axilpack.ParseError) as exc:
            return self.error(tr("Could not wrap"), exc)
        d = QFileDialog.getExistingDirectory(self, tr("Output folder"), os.path.dirname(src))
        if not d:
            return
        out = []
        for info in infos:
            values = self.ask_pack_values(info)
            if values is None:
                return
            try:
                for name, text in G.hdl_designer_files(info, values).items():
                    with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(text)
            except (OSError, axilpack.ParseError) as exc:
                return self.error(tr("Could not write"), exc)
            groups = ", ".join(axilpack.PACKED_NAME[g] + "_REQ/RSP" for g in axilpack.axi_groups(info))
            out.append(f"{info['name']}_pk.v (+ _pk_inst.v)  →  {groups}")
        QMessageBox.information(self, tr("HDL Designer wrapper generated"),
                                "\n".join(out) + tr("\n\n{0}\n\nUse the _pk block in HDL Designer. The original module file must be in the project too.").format(d))

    def ask_pack_values(self, info):
        """Parameter values fixed in <module>_pk.v (it has no generics): {name: text} of the changed
        ones, {} for the defaults, None when cancelled."""
        params = [(p, d.split("=", 1)[1].strip()) for k, d, p in info["params"] if k == "parameter"]
        if not params:
            return {}
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("{0}_pk parameter values").format(info["name"]))
        lay = QVBoxLayout(dlg)
        note = QLabel(tr("The HDL Designer block has no generics (a VHDL top would turn 32-bit values into "
                         "overflowing integers). These values are fixed in it; change them here if needed."))
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        edits = {}
        for p, default in params:
            e = QLineEdit(default)
            e.setPlaceholderText(default)
            edits[p] = (e, default)
            form.addRow(p, e)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            values = {p: e.text().strip() for p, (e, d) in edits.items() if e.text().strip() and e.text().strip() != d}
            try:
                axilpack.param_values(info, values)
                return values
            except axilpack.ParseError as exc:
                QMessageBox.warning(self, tr("Parameter values"), str(exc))

    def shutdown(self):
        self._jobs.shutdown()
