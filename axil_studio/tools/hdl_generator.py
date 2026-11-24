"""HDL Generator: every block of the project that has an HDL generator (core/generators.py), in one
list. Each block is set up in its IP's own generator application (meaningful settings: clock, baud
rate, line type, pins ...); AXIL Studio writes <name>_pk.v, the IP with packed AXI ports and no
parameters, the values built in, one module per instance.

The list holds the address-map instances whose IP names a generator, and blocks added by hand (a
bus master such as the hexbus Debug Bridge, which is not in the address map). Settings and the last
computed values are kept in the project; "Generate all" runs every generator again without a window
with the stored settings, so a whole design follows a change at once.
"""

import json
import os
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QHeaderView, QInputDialog, QLabel, QMessageBox, QTreeWidget,
                               QTreeWidgetItem)

from axil_studio import ui
from axil_studio.core import generators as GN
from axil_studio.i18n import language, tr
from axil_studio.tools.base import ToolPage

ROLE = Qt.ItemDataRole.UserRole


class HdlGeneratorPage(ToolPage):
    tool_id = "hdlgen"
    title = "HDL Generator"
    short_title = "HDL Generator"
    subtitle = tr("Set up IP blocks with their own generators and write parameterless HDL Designer blocks.")
    description = tr("IP blocks set up with meaningful values (clock, baud rate, line type, pins) in each IP's own generator application; writes <instance>_pk.v per instance: packed AXI ports, no parameters, port widths and values built in.")
    icon_name = "generator"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.gens = {}
        self._procs = []
        bar = self.header
        self.cfg_btn = bar.add(tr("Configure…"), "edit", self.configure_selected,
                               tr("Open the block's generator application"))
        bar.add(tr("Add block…"), "plus", self.add_block,
                tr("A block that is not in the address map (e.g. the hexbus Debug Bridge)"))
        self.rm_btn = bar.add(tr("Remove block"), "trash", self.remove_block, tr("Only blocks added by hand"))
        bar.sep()
        self.gen_sel_btn = bar.add(tr("Generate selected"), "play", self.generate_selected)
        bar.add(tr("Output folder…"), "folder", self.choose_outdir)
        bar.add(tr("Reload generators"), "refresh", self.reload)
        self.gen_all_btn = ui.button(tr("Generate all"), "play", "primary", self.generate_all,
                                     tr("Run every generator with its stored settings and write all blocks"))
        bar.add_right(self.gen_all_btn)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Name"), tr("Module"), tr("Generator"), tr("Settings")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        h = self.tree.header()
        for i in range(3):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self.configure_selected())
        self.body.addWidget(self.tree, 1)
        self.out_label = QLabel()
        self.out_label.setWordWrap(True)
        self.body.addWidget(self.out_label)
        self.reload()

    # ------------------------------------------------------------ data
    @property
    def proj(self):
        return self.ctx.project

    def reload(self):
        from axil_studio.interfaces.host import settings_folders
        libs = [lib.path for lib in getattr(self.ctx, "libraries", []) if getattr(lib, "path", None)]
        self.gens, errors = GN.discover(settings_folders(), libs)
        if errors:
            self.status("; ".join(errors), 10000)
        self.refresh()

    def refresh(self):
        sel = self.selected_name()
        self.tree.clear()
        stored = (self.proj.hdl_gen or {}).get("blocks", {})
        for name, gid, kind in GN.rows(self.proj):
            gen = self.gens.get(gid)
            e = stored.get(name, {})
            summary = e.get("summary") or tr("defaults (not configured yet)")
            it = QTreeWidgetItem([name, GN.module_name(name) + ".v",
                                  gen.label(language()) if gen else tr("{0} (not found)").format(gid),
                                  summary + ("" if kind == "instance" else "  · " + tr("added by hand"))])
            it.setData(0, ROLE, (name, gid, kind))
            if gen is None:
                it.setToolTip(2, tr("No generator '{0}' in the IP interface folders, the library folders or <AXIL Studio>/generators").format(gid))
            else:
                it.setToolTip(2, gen.description(language()))
            self.tree.addTopLevelItem(it)
            if name == sel:
                self.tree.setCurrentItem(it)
        if not self.tree.topLevelItemCount():
            it = QTreeWidgetItem([tr("No block with an HDL generator: IPs name one in their library entry (axiluart_phy, axilgpio …); add a bus master with \"Add block…\".")])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree.addTopLevelItem(it)
        out = self.outdir()
        self.out_label.setText(tr("Output folder: {0}").format(out) if out else
                               tr("Output folder: not chosen yet (asked at the first generation)"))
        self._update_buttons()

    def selected_name(self):
        it = self.tree.currentItem()
        d = it.data(0, ROLE) if it else None
        return d[0] if d else None

    def _selected(self):
        it = self.tree.currentItem()
        return it.data(0, ROLE) if it else None

    def _update_buttons(self):
        d = self._selected()
        self.cfg_btn.setEnabled(bool(d and d[1] in self.gens))
        self.rm_btn.setEnabled(bool(d and d[2] == "block"))
        self.gen_sel_btn.setEnabled(bool(d and d[1] in self.gens))

    def outdir(self):
        d = (self.proj.hdl_gen or {}).get("outdir", "")
        if d and not os.path.isabs(d) and self.ctx.path:
            d = os.path.normpath(os.path.join(os.path.dirname(self.ctx.path), d))
        return d

    def _set_outdir(self, d):
        if self.proj.hdl_gen is None:
            self.proj.hdl_gen = {}
        if self.ctx.path:
            try:
                d = os.path.relpath(d, os.path.dirname(self.ctx.path))
            except ValueError:
                pass
        self.proj.hdl_gen["outdir"] = d.replace(os.sep, "/")
        self.ctx.mark_modified(self.tool_id)

    def choose_outdir(self):
        d = QFileDialog.getExistingDirectory(self, tr("Output folder"), self.outdir() or os.path.dirname(self.ctx.path or ""))
        if d:
            self._set_outdir(d)
            self.refresh()
        return d

    def on_project_modified(self):
        self.refresh()

    # ------------------------------------------------------------ blocks added by hand
    def add_block(self):
        if not self.gens:
            return QMessageBox.information(self, self.title, tr("No HDL generator found."))
        ids = sorted(self.gens)
        labels = [f"{self.gens[i].label(language())} ({i})" for i in ids]
        lab, ok = QInputDialog.getItem(self, tr("Add block"), tr("Generator"), labels, 0, False)
        if not ok:
            return
        gid = ids[labels.index(lab)]
        name, ok = QInputDialog.getText(self, tr("Add block"), tr("Block name (the module is <name>_pk)"),
                                        text=gid.split("_")[0].upper())
        name = (name or "").strip()
        if not ok or not name:
            return
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            return QMessageBox.warning(self, tr("Add block"), tr("Use letters, digits and _ (not starting with a digit)."))
        if any(n == name for n, _, _ in GN.rows(self.proj)) or self.proj.get_instance(name) is not None:
            return QMessageBox.warning(self, tr("Add block"), tr("'{0}' is already used.").format(name))
        GN.blocks(self.proj)[name] = {"generator": gid, "settings": {}, "free": True}
        self.ctx.mark_modified(self.tool_id)
        self.refresh()
        self.select(name)

    def remove_block(self):
        d = self._selected()
        if not d or d[2] != "block":
            return
        GN.blocks(self.proj).pop(d[0], None)
        self.ctx.mark_modified(self.tool_id)
        self.refresh()

    def select(self, name):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            d = it.data(0, ROLE)
            if d and d[0] == name:
                self.tree.setCurrentItem(it)
                return True
        return False

    # ------------------------------------------------------------ configure (the generator's window)
    def configure_selected(self):
        d = self._selected()
        if d:
            self.configure(d[0])

    def configure(self, name):
        row = next((r for r in GN.rows(self.proj) if r[0] == name), None)
        if row is None:
            return
        gen = self.gens.get(row[1])
        if gen is None:
            return QMessageBox.warning(self, self.title, tr("Generator '{0}' not found.").format(row[1]))
        tmp = tempfile.mkdtemp(prefix="axilgen_")
        fin, fout = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
        with open(fin, "w", encoding="utf-8") as fh:
            json.dump(GN.request(self.proj, name, gen), fh)
        argv, env = GN.app_command(gen, fin, fout, False, language())
        p = QProcess(self)
        p.setWorkingDirectory(gen.folder)
        penv = QProcessEnvironment.systemEnvironment()
        penv.insert("PYTHONPATH", env["PYTHONPATH"])
        p.setProcessEnvironment(penv)
        p.finished.connect(lambda code, _st, p=p: self._configured(p, name, row, fout, code))
        self._procs.append(p)
        self.status(tr("{0}: generator window open").format(name))
        p.start(argv[0], argv[1:])
        return p

    def _configured(self, p, name, row, fout, code):
        if p in self._procs:
            self._procs.remove(p)
        err = bytes(p.readAllStandardError()).decode(errors="replace").strip()
        if code == GN.CANCELLED:
            self.status(tr("{0}: not changed").format(name))
            return
        try:
            if code != 0:
                raise GN.GeneratorError(err or tr("exit code {0}").format(code))
            res = GN.read_result(fout)
        except (OSError, ValueError) as exc:
            return QMessageBox.warning(self, self.title, tr("{0}: the generator failed: {1}").format(name, exc))
        self.apply_result(name, row[1], res, row[2] == "block")

    def apply_result(self, name, gid, res, free=False):
        GN.store(self.proj, name, gid, res, free)
        self.ctx.mark_modified(self.tool_id)
        self.refresh()
        self.status(tr("{0}: {1}").format(name, res.get("summary", "")))

    # ------------------------------------------------------------ generate
    def _ensure_outdir(self):
        d = self.outdir()
        if not d or not os.path.isdir(d):
            d = self.choose_outdir()
        return d

    def generate_selected(self):
        d = self._selected()
        if d:
            return self._generate([d[0]])

    def generate_all(self):
        return self._generate(None)

    def _generate(self, names):
        out = self._ensure_outdir()
        if not out:
            return None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            paths, errors = GN.generate_all(self.proj, self.gens, out, names, language())
        finally:
            QApplication.restoreOverrideCursor()
        self.ctx.mark_modified(self.tool_id)
        self.refresh()
        text = f"{out}\n\n" + "\n".join(os.path.basename(x) for x in paths)
        if errors:
            text += "\n\n" + tr("Errors:") + "\n" + "\n".join(errors)
            QMessageBox.warning(self, tr("Generation"), text)
        else:
            QMessageBox.information(self, tr("Generation complete"), text + "\n\n" + tr(
                "Each <name>_pk.v has no parameters: use it in HDL Designer as it is. Add the IP's own files "
                "too (hdl/v2005 …)."))
        return paths, errors

    def shutdown(self):
        for p in list(self._procs):
            p.kill()
            p.waitForFinished(1000)
