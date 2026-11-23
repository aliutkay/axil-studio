"""IP Library: every IP of the project, and reusable IPs in library files shared between projects.

The project side lists the project's IP definitions with their address-map instances (an IP
can be placed several times). Several libraries (.axlib.json) can be open at once; they stay
open across projects and a project remembers the libraries it took IPs from. IPs move both
ways: a library IP is brought into the project (and optionally placed in the address map), a
project IP is stored in a library. Every library change is written to its file immediately.

Wherever the studio asks for an IP (Address Map Editor "IP ekle", Register Map Editor and Slave
Interface Wrapper "IP seç/aç") this page opens as a picker dialog (IPPickerDialog).
"""

import copy
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLineEdit, QMessageBox, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from axil_studio.core import axilgen as G
from axil_studio import ROOT, theme, ui
from axil_studio.tools.base import ToolPage
from axil_studio.tools.regmap import fill_regmap_tree, regmap_tree
from axil_studio.core.library import LibEntry
from axil_studio.core.model import Project, hex32, single_line
from axil_studio.i18n import tr

LIB_FILTER = tr("IP library (*.axlib.json);;JSON (*.json);;All files (*)")
ROLE = Qt.ItemDataRole.UserRole


def _panel_note(ctx, ip):
    """Tooltip line for an IP that names its own interface."""
    from axil_studio.interfaces.host import ip_tooltip_line
    return ip_tooltip_line(ctx, getattr(ip, "panel", ""))


class IPLibraryPage(ToolPage):
    tool_id = "library"
    title = "IP Library"
    short_title = "IP Library"
    subtitle = tr("Manage the project's IPs and reusable, versioned IP libraries.")
    description = tr("IP management for the project and shared IP libraries (.axlib.json): versioned IPs, their address-map instances, and transfer between projects and libraries.")
    icon_name = "library"

    picked = Signal()          # picker mode: an IP was double-clicked

    def __init__(self, ctx, parent=None, picker=False):
        self.picker = picker
        super().__init__(ctx, parent)
        if picker:      # inside the picker dialog: no outer margins; the bar keeps the library commands
            self.outer.setContentsMargins(0, 0, 0, 0)
        bar = self.header       # the tool's commands sit in the top bar
        bar.add(tr("New library…"), "plus", self.new_library, tr("Create a new .axlib.json file"))
        bar.add(tr("Open library…"), "folder", self.open_library)
        self.close_btn = bar.add(tr("Close library"), None, self.close_library, tr("Remove from the list (the file is not deleted)"))
        bar.sep()
        bar.add(tr("Import: register spec…"), "regmap", self.import_spec,
                tr("Add a .regspec.json file to the selected library"))
        bar.add(tr("Import: project file…"), "file", self.import_project,
                tr("Add the IPs of another .axproj.json file to the selected library"))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QSplitter(Qt.Orientation.Vertical)
        proj = ui.Card(tr("IPs in this project"), tr("Sub-rows: instances in the address map"), margins=14)
        self.proj_tree = QTreeWidget()
        self.proj_tree.setHeaderLabels([tr("Name"), tr("Version"), tr("Address"), tr("Source")])
        self.proj_tree.setUniformRowHeights(True)
        h = self.proj_tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.proj_tree.currentItemChanged.connect(lambda cur, _p: self._picked(self.proj_tree, cur))
        self.proj_tree.itemDoubleClicked.connect(lambda *_: self._activated())
        proj.lay.addWidget(self.proj_tree, 1)
        left.addWidget(proj)
        libs = ui.Card(tr("Libraries"), margins=14)
        self.lib_tree = QTreeWidget()
        self.lib_tree.setHeaderLabels([tr("Name"), "Register", tr("Version")])
        self.lib_tree.setUniformRowHeights(True)
        h = self.lib_tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.lib_tree.currentItemChanged.connect(lambda cur, _p: self._picked(self.lib_tree, cur))
        self.lib_tree.itemDoubleClicked.connect(lambda *_: self._activated())
        libs.lay.addWidget(self.lib_tree, 1)
        left.addWidget(libs)
        left.setSizes([440, 260] if picker else [320, 400])
        left.setMinimumWidth(360)
        split.addWidget(left)

        self.detail = ui.Card(margins=16)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.d_title = ui.label(tr("Select IP"), "cardTitle")
        self.d_sub = ui.label("", "muted", wrap=True)
        col.addWidget(self.d_title)
        col.addWidget(self.d_sub)
        self.detail.lay.addLayout(col)
        self.d_desc = ui.label("", None, wrap=True)
        self.detail.lay.addWidget(self.d_desc)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.map_btn = ui.button(tr("Add to address map"), "addrmap", "primary", self.add_to_address_map,
                                 tr("Place the IP in the address map once more (bringing it into the project first if needed)"))
        self.import_btn = ui.button(tr("Bring into project"), "arrow", None, self.import_to_project,
                                    tr("Copy the library IP into the project's IPs (without an address)"))
        self.regmap_btn = ui.button(tr("Open in Register Map Editor"), "regmap", None, self.open_in_regmap)
        self.wrapper_btn = ui.button(tr("Open in Wrapper"), "wrapper", None, self.open_in_wrapper,
                                     tr("Generate the HDL in the Slave Interface Wrapper"))
        self.addr_btn = ui.button(tr("Open in Address Map Editor"), "addrmap", None, self.open_in_addrmap)
        self.store_btn = ui.button(tr("Store in library…"), "save", None, self.store_project_ip,
                                   tr("Store this project IP in a library"))
        self.rename_btn = ui.button(tr("Rename…"), None, "ghost", self.rename_entry)
        self.del_btn = ui.button(tr("Delete"), None, "ghost", self.delete_selected)
        self.buttons = (self.map_btn, self.import_btn, self.regmap_btn, self.wrapper_btn, self.addr_btn,
                        self.store_btn, self.rename_btn, self.del_btn)
        for b in self.buttons:
            brow.addWidget(b)
        brow.addStretch(1)
        self.detail.lay.addLayout(brow)
        self.regs = regmap_tree()
        self.detail.lay.addWidget(self.regs, 1)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 840])
        self.body.addWidget(split, 1)

        ctx.libraries_changed.connect(self._refresh)
        ctx.state_changed.connect(self._refresh)
        # ("lib", library, entry) | ("libroot", library, None) | ("proj", ip, None) | ("inst", instance, None)
        self._sel = None
        self._refresh()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Library")
        win = self.window()
        for text, slot in ((tr("New library…"), self.new_library), (tr("Open library…"), self.open_library),
                           (tr("Close library"), self.close_library), (None, None),
                           (tr("Import: register spec…"), self.import_spec),
                           (tr("Import: project file…"), self.import_project), (None, None),
                           (tr("Add the selected IP to the address map"), self.add_to_address_map),
                           (tr("Bring the selected library IP into the project"), self.import_to_project),
                           (tr("Store the selected project IP in a library…"), self.store_project_ip)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, win)
            a.triggered.connect(slot)
            m.addAction(a)

    # ================================================================ view
    def on_project_modified(self):
        self._refresh()

    def _refresh(self):
        t = theme.manager().tokens
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        keep = self._sel
        proj = self.ctx.project

        # ---- project IPs and their address-map instances
        self.proj_tree.blockSignals(True)
        self.proj_tree.clear()
        ptarget = None
        for ip in sorted(proj.ips, key=lambda i: i.name.lower()):
            insts = proj.instances_of(ip.name)
            top = QTreeWidgetItem([ip.name, ip.version or "—",
                                   tr("{0} instances").format(len(insts)) if insts else tr("not placed"), ip.source or tr("in project")])
            bf = top.font(0)
            bf.setBold(True)
            top.setFont(0, bf)
            top.setToolTip(0, single_line(ip.desc) + _panel_note(self.ctx, ip))
            if not insts:
                top.setForeground(2, muted)
            if not ip.source:
                top.setForeground(3, muted)
            top.setData(0, ROLE, ("proj", ip, None))
            if keep and keep[0] == "proj" and keep[1].name == ip.name:
                ptarget = top
            for inst in insts:
                ch = QTreeWidgetItem([inst.name, "", hex32(inst.base), ""])
                ch.setFont(2, mono)
                ch.setData(0, ROLE, ("inst", inst, None))
                ch.setToolTip(0, tr("Instance of {0} · double-click: Address Map Editor").format(ip.name))
                top.addChild(ch)
                if keep and keep[0] == "inst" and keep[1].name == inst.name:
                    ptarget = ch
            self.proj_tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not proj.ips:
            it = QTreeWidgetItem([tr("No IPs in the project: create one in the Register Map Editor or take one from a library"), "", "", ""])
            it.setForeground(0, QColor(t["muted"]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.proj_tree.addTopLevelItem(it)
        self.proj_tree.blockSignals(False)

        # ---- libraries
        self.lib_tree.blockSignals(True)
        self.lib_tree.clear()
        target = None
        for lib in self.ctx.libraries:
            top = QTreeWidgetItem([lib.name, f"{len(lib.entries)} IP", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setToolTip(0, lib.path or "")
            top.setData(0, ROLE, ("libroot", lib, None))
            for name in lib.names():
                versions = lib.versions(name)          # newest first
                parent = None
                for k, e in enumerate(versions):
                    label = e.name if k == 0 else tr("previous version {0}").format(e.version)
                    it = QTreeWidgetItem([label, str(len(e.ip.registers)),
                                          f"{e.version}" + (tr("  ({0} versions)").format(len(versions)) if k == 0 and len(versions) > 1
                                                            else "")])
                    it.setData(0, ROLE, ("lib", lib, e))
                    it.setToolTip(0, single_line(e.ip.desc) + (tr("\nHas a register spec") if e.regspec else "")
                                  + _panel_note(self.ctx, e.ip))
                    if k == 0:
                        top.addChild(it)
                        parent = it
                    else:
                        it.setForeground(0, muted)
                        parent.addChild(it)
                    if keep and keep[0] == "lib" and keep[1] is lib and keep[2].name == e.name \
                            and keep[2].version == e.version:
                        target = it
            self.lib_tree.addTopLevelItem(top)
            if keep and keep[0] == "libroot" and keep[1] is lib:
                target = top
        if not self.ctx.libraries:
            it = QTreeWidgetItem([tr("No open library: New library or Open library"), "", ""])
            it.setForeground(0, muted)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.lib_tree.addTopLevelItem(it)
        for i in range(self.lib_tree.topLevelItemCount()):
            self.lib_tree.topLevelItem(i).setExpanded(True)       # libraries open, older versions folded
        self.lib_tree.blockSignals(False)

        if target is not None:
            self.lib_tree.setCurrentItem(target)
            self._picked(self.lib_tree, target)
        elif ptarget is not None:
            self.proj_tree.setCurrentItem(ptarget)
            self._picked(self.proj_tree, ptarget)
        else:
            self._show(None)
        if self.ctx.library_errors:
            self.status(tr("Library that could not be opened:").rstrip() + " " + "; ".join(self.ctx.library_errors), 10000)
            self.ctx.library_errors.clear()

    def _picked(self, tree, item):
        key = item.data(0, ROLE) if item else None
        if key is None:
            return
        other = self.proj_tree if tree is self.lib_tree else self.lib_tree
        other.blockSignals(True)
        other.clearSelection()
        other.setCurrentItem(None)
        other.blockSignals(False)
        self._show(key)

    def _show(self, key):
        self._sel = key
        kind = key[0] if key else None
        visible = {
            "lib": (self.map_btn, self.import_btn, self.regmap_btn, self.rename_btn, self.del_btn),
            "proj": (self.map_btn, self.regmap_btn, self.wrapper_btn, self.store_btn, self.del_btn),
            "inst": (self.addr_btn, self.regmap_btn),
        }.get(kind, ())
        for b in self.buttons:
            b.setVisible(not self.picker and b in visible)
        self.del_btn.setText(tr("Remove from project") if kind == "proj" else tr("Delete from library"))
        self.close_btn.setEnabled(bool(key and kind in ("lib", "libroot")))
        ip = None
        if not key:
            self.d_title.setText(tr("Select IP"))
            self.d_sub.setText(tr("Select a project IP or a library IP on the left."))
            self.d_desc.setText("")
        elif kind == "libroot":
            lib = key[1]
            self.d_title.setText(lib.name)
            self.d_sub.setText(f"{len(lib.entries)} IP · {lib.path}")
            self.d_desc.setText(lib.desc)
        elif kind == "lib":
            lib, e = key[1], key[2]
            ip = e.ip
            used = self.ctx.project.get_ip(ip.name)
            others = [x.version for x in lib.versions(ip.name) if x.version != e.version]
            self.d_title.setText(tr("{0}   ·   version {1}").format(ip.name, e.version))
            self.d_sub.setText(tr("Library: {0}").format(lib.name) + (f" · {e.updated}" if e.updated else "")
                               + (tr(" · other versions: {0}").format(', '.join(others)) if others else "")
                               + (tr(" · has a register spec") if e.regspec else "")
                               + (tr(" · in the project (version {0}, {1}× in the address map)").format(used.version or '—', len(self.ctx.project.instances_of(used.name))) if used else ""))
        elif kind == "proj":
            ip = key[1]
            insts = self.ctx.project.instances_of(ip.name)
            self.d_title.setText(f"{ip.name}" + (tr("   ·   version {0}").format(ip.version) if ip.version else ""))
            self.d_sub.setText(tr("In the project") + (tr(" · source: {0}").format(ip.source) if ip.source else "")
                               + (tr(" · in the address map:").rstrip() + " " + ", ".join(f"{i.name} @ {hex32(i.base)}" for i in insts)
                                  if insts else tr(" · not in the address map")))
        else:
            inst = key[1]
            ip = self.ctx.project.get_ip(inst.ip)
            self.d_title.setText(f"{inst.name}   ·   {hex32(inst.base)}")
            self.d_sub.setText(tr("Address-map instance of the IP {0} ·").format(inst.ip)
                               + (tr("fixed address") if inst.fixed else tr("automatic address")))
        if ip is not None and kind != "inst":
            self.d_desc.setText(ip.desc or "")
        elif kind == "inst":
            self.d_desc.setText(ip.desc if ip else "")
        fill_regmap_tree(self.regs, ip)

    def selected_project_ip(self):
        """Picker: the project IP definition name for the selection (a library IP is brought into
        the project first); None when nothing usable is selected."""
        k = self._sel
        if not k:
            return None
        if k[0] == "proj":
            return k[1].name
        if k[0] == "inst":
            return k[1].ip or None
        if k[0] == "lib":
            return self._import(k[1], k[2])
        return None

    def _activated(self):
        if self.picker:
            if self._sel and self._sel[0] in ("proj", "inst", "lib"):
                self.picked.emit()
        elif self._sel and self._sel[0] == "inst":
            self.open_in_addrmap()
        elif self._sel and self._sel[0] in ("proj", "lib"):
            self.open_in_regmap()

    def _selected_library(self, ask=True):
        """The library of the selection, or one chosen by the user."""
        if self._sel and self._sel[0] in ("lib", "libroot"):
            return self._sel[1]
        libs = self.ctx.libraries
        if len(libs) == 1:
            return libs[0]
        if not libs:
            if ask and ui.confirm(self, self.title, tr("No library is open. Create a new library?"),
                                  ok=tr("Create")):
                return self.new_library()
            return None
        if not ask:
            return None
        name, ok = QInputDialog.getItem(self, tr("Select library"), tr("Library:"), [lib.name for lib in libs], 0, False)
        return next((lib for lib in libs if lib.name == name), None) if ok else None

    # ================================================================ library files
    def new_library(self):
        lib = _new_library_file(self, self.ctx)
        if lib:
            self.status(tr("Library created: {0}").format(lib.path))
        return lib

    def open_library(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open IP library"), os.path.dirname(self.ctx.path or ROOT),
                                              LIB_FILTER)
        if not path:
            return
        try:
            lib = self.ctx.open_library(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        self.status(tr("Library opened: {0}").format(lib.name))

    def close_library(self):
        if not self._sel or self._sel[0] not in ("lib", "libroot"):
            return
        lib = self._sel[1]
        self._sel = None
        self.ctx.close_library(lib)

    # ================================================================ import into a library
    def import_spec(self):
        lib = self._selected_library()
        if lib is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Register spec"), ROOT,
                                              tr("Register spec (*.regspec.json);;JSON (*.json)"))
        if not path:
            return
        try:
            spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        if lib.get(spec.module, spec.version) and not ui.confirm(
                self, self.title, tr("The library {0} already has {1} {2}. Overwrite it?").format(lib.name, spec.module, spec.version),
                ok=tr("Overwrite")):
            return
        keep = list(lib.entries)
        replaced = lib.put(LibEntry(G.to_ipblock(spec, 0), spec.to_dict()))
        try:
            self.ctx.save_library(lib)
        except OSError as exc:
            lib.entries = keep
            return self.error(tr("Could not save"), exc)
        self.status(tr("{0} {1}: {3} in the library {2}").format(spec.module, spec.version, lib.name, tr("updated") if replaced else tr("added")))

    def import_project(self):
        lib = self._selected_library()
        if lib is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Project file"), ROOT, tr("AXIL project (*.axproj.json);;JSON (*.json)"))
        if not path:
            return
        try:
            proj = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error(tr("Could not open"), exc)
        if not proj.ips:
            QMessageBox.information(self, self.title, tr("This project file has no IPs."))
            return
        names = [ip.name for ip in proj.ips]
        choice, ok = QInputDialog.getItem(self, tr("Import"), "IP:", [tr("All")] + names, 0, False)
        if not ok:
            return
        keep = list(lib.entries)
        for ip in proj.ips if choice == tr("All") else [proj.ips[names.index(choice)]]:
            spec = ip.regspec
            ip.source, ip.regspec, ip.base = "", None, 0
            ip.version = ip.version or "1.0.0"
            lib.put(LibEntry(ip, spec))
        try:
            self.ctx.save_library(lib)
        except OSError as exc:
            lib.entries = keep
            return self.error(tr("Could not save"), exc)
        self.status(tr("Imported into the library {0}").format(lib.name))

    # ================================================================ library <-> project
    def _import(self, lib, e):
        """Bring a library IP into the project's IP definitions; returns its name there or None."""
        proj = self.ctx.project
        old = proj.get_ip(e.name)
        if old is None:
            name = self.ctx.import_from_library(lib, e, source=self.tool_id)
            self.status(tr("{0} {1} brought into the project").format(name, e.version))
            return name
        if old.version == e.version and old.source == lib.name:
            return old.name                       # the same IP is already in the project
        n = len(proj.instances_of(old.name))
        choice, ok = QInputDialog.getItem(
            self, tr("The project has an IP with the same name"),
            tr("{0} is already in the project (version {1}, {2}× in the address map):").format(e.name, old.version or '—', n),
            [tr("Use the project's ({0})").format(old.version or '—'),
             tr("Replace the project's with the library's ({0})").format(e.version),
             tr("Bring it in as a separate IP with a new name")], 0, False)
        if not ok:
            return None
        if choice.startswith(tr("Use the project's")):
            return old.name
        if choice.startswith(tr("Replace the project's with the library's")):
            name = self.ctx.import_from_library(lib, e, source=self.tool_id)
            self.status(tr("{0} updated to version {1} ({2} instances)").format(name, e.version, n))
            return name
        new, ok = QInputDialog.getText(self, tr("IP name"), tr("Name in the project:"), text=self.ctx.unique_ip_name(e.name))
        new = new.strip()
        if not ok or not new:
            return None
        if proj.get_ip(new):
            QMessageBox.warning(self, self.title, tr("The project already has an IP named “{0}”.").format(new))
            return None
        return self.ctx.import_from_library(lib, e, new, source=self.tool_id)

    def import_to_project(self):
        if self._sel and self._sel[0] == "lib":
            self._import(self._sel[1], self._sel[2])

    def add_to_address_map(self):
        if not self._sel or self._sel[0] not in ("lib", "proj"):
            return
        name = self._import(self._sel[1], self._sel[2]) if self._sel[0] == "lib" else self._sel[1].name
        if not name:
            return
        inst = self.ctx.add_instance(name, source=self.tool_id)
        self.status(tr("{0} added to the address map ({1})").format(inst.name, hex32(inst.base)))
        self.edit_address_map(inst.name)

    def store_project_ip(self):
        if not self._sel or self._sel[0] != "proj":
            return
        ip = self._sel[1]
        default = ip.version or "1.0.0"
        holder = next((lib for lib in self.ctx.libraries if lib.get(ip.name)), None)
        if holder and G.version_key(holder.get(ip.name).version) >= G.version_key(default):
            default = G.bump_version(holder.get(ip.name).version)
        res = LibrarySaveDialog.ask(self, self.ctx, ip.name, default, ip.source)
        if not res:
            return
        lib, version = res
        stored = copy.deepcopy(ip)
        stored.version = version
        spec = dict(ip.regspec, ip_version=version) if ip.regspec else None
        try:
            self.ctx.store_in_library(lib, stored, spec)
        except OSError as exc:
            return self.error(tr("Could not store in the library"), exc)
        ip.version = version
        if ip.regspec:
            ip.regspec = dict(ip.regspec, ip_version=version)
        ip.source = ip.source or lib.name
        self.ctx.use_library(lib)
        self.ctx.mark_modified(self.tool_id)
        self.status(tr("{0} {1} stored in the library {2}").format(ip.name, version, lib.name))

    def rename_entry(self):
        if not self._sel or self._sel[0] != "lib":
            return
        lib, e = self._sel[1], self._sel[2]
        new, ok = QInputDialog.getText(self, tr("Rename"), tr("Name:"), text=e.name)
        new = new.strip()
        if not ok or not new or new == e.name:
            return
        if lib.get(new):
            QMessageBox.warning(self, self.title, tr("The library {0} already has {1}.").format(lib.name, new))
            return
        old_name = e.name
        affected = list(lib.versions(old_name))
        for x in affected:      # every version takes the new name
            x.ip.name = new
            if x.regspec:
                x.regspec["module"] = new
        try:
            self.ctx.save_library(lib)
        except OSError as exc:
            for x in affected:
                x.ip.name = old_name
                if x.regspec:
                    x.regspec["module"] = old_name
            return self.error(tr("Could not save"), exc)

    def delete_selected(self):
        if not self._sel:
            return
        if self._sel[0] == "lib":
            lib, e = self._sel[1], self._sel[2]
            if ui.confirm(self, tr("Delete from library"), tr("{0} {1} will be deleted from the library {2}. Other versions and copies in projects are not affected.").format(e.name, e.version, lib.name)):
                keep = list(lib.entries)
                lib.remove(e.name, e.version)
                try:
                    self.ctx.save_library(lib)
                except OSError as exc:
                    lib.entries = keep
                    return self.error(tr("Could not save"), exc)
                self._sel = ("libroot", lib, None)
        elif self._sel[0] == "proj":
            ip = self._sel[1]
            n = len(self.ctx.project.instances_of(ip.name))
            if ui.confirm(self, tr("Remove from project"), tr("{0} will be removed from the project").format(ip.name)
                                                   + (tr("; its {0} instances in the address map are removed too").format(n) if n else "")
                                                   + ".", ok=tr("Remove")):
                self._sel = None
                self.ctx.remove_ip(ip.name, self.tool_id)
                self._refresh()

    # ================================================================ other tools
    def open_in_regmap(self):
        if not self._sel:
            return
        kind = self._sel[0]
        if kind == "proj":
            self.edit_register_map(self._sel[1].name)
        elif kind == "inst" and self._sel[1].ip:
            self.edit_register_map(self._sel[1].ip)
        elif kind == "lib":
            lib, e = self._sel[1], self._sel[2]
            spec = G.ModuleSpec.from_dict(e.regspec) if e.regspec else G.spec_from_ipblock(e.ip)
            w = self.open_tool("regmap")
            w.page.load_spec(spec, tr("{1} from the library {0}").format(lib.name, e.version), lib.name)

    def open_in_wrapper(self):
        if self._sel and self._sel[0] == "proj":
            self.open_tool("wrapper").page.load_project_ip(self._sel[1].name)

    def open_in_addrmap(self):
        if self._sel and self._sel[0] == "inst":
            self.edit_address_map(self._sel[1].name)


# ============================================================================ picker
class IPPickerDialog(QDialog):
    """The IP Library as a chooser: every "add / select an IP" action of the studio opens it."""

    def __init__(self, parent, ctx, title, note=""):
        super().__init__(parent)
        self.setWindowTitle(f"{title} — IP Library")
        self.setObjectName("Page")
        self.resize(1080, 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(ui.label(title, "title"))
        if note:
            lay.addWidget(ui.label(note, "muted", wrap=True))
        self.page = IPLibraryPage(ctx, self, picker=True)
        self.page.picked.connect(self.accept)
        lay.addWidget(self.page, 1)
        box = QDialogButtonBox()
        self.ok = box.addButton(tr("Select"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.name = None

    def accept(self):
        name = self.page.selected_project_ip()
        if not name:
            if self.page._sel is None or self.page._sel[0] == "libroot":
                QMessageBox.information(self, self.windowTitle(), tr("Select an IP (of the project or of a library)."))
            return
        self.name = name
        super().accept()

    @classmethod
    def pick(cls, parent, ctx, title, note=""):
        d = cls(parent, ctx, title, note)
        try:
            return d.name if d.exec() == QDialog.DialogCode.Accepted else None
        finally:
            d.page.shutdown()
            d.deleteLater()


# ============================================================================ save dialogs
def _new_library_file(parent, ctx):
    """Ask for a path and a name, create the library; returns it or None."""
    path, _ = QFileDialog.getSaveFileName(parent, tr("New IP library"),
                                          os.path.join(os.path.dirname(ctx.path or ROOT), "ip_library.axlib.json"),
                                          LIB_FILTER)
    if not path:
        return None
    if not path.endswith(".json"):
        path += ".axlib.json"
    name, ok = QInputDialog.getText(parent, tr("New IP library"), tr("Library name:"),
                                    text=os.path.basename(path).split(".")[0])
    if not ok or not name.strip():
        return None
    try:
        return ctx.new_library(path, name.strip())
    except OSError as exc:
        QMessageBox.critical(parent, tr("Could not create"), str(exc))
        return None


class LibraryPane(QWidget):
    """Where an IP goes in the libraries: every open library with its IPs and their versions, the
    version to store, and what will happen (new IP / new version / overwrite)."""

    changed = Signal()

    def __init__(self, ctx, ip_name, version, prefer=""):
        super().__init__()
        self.ctx, self.ip_name = ctx, ip_name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(ui.label(tr("Select the library to store {0} in. The libraries' IPs and versions are listed; the entries of this IP are highlighted.").format(ip_name), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Library / IP"), tr("Versions"), tr("Updated")])
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda *_: self._update())
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(ui.button(tr("New library…"), "plus", "ghost", self._new_library))
        row.addWidget(ui.button(tr("Open library…"), "folder", "ghost", self._open_library))
        row.addStretch(1)
        row.addWidget(ui.label(tr("Version"), "muted"))
        self.version_ed = QLineEdit(version or "1.0.0")
        self.version_ed.setProperty("mono", "true")
        self.version_ed.setMaximumWidth(140)
        self.version_ed.textChanged.connect(lambda *_: self._update())
        row.addWidget(self.version_ed)
        lay.addLayout(row)
        self.info = ui.label("", None, wrap=True)
        lay.addWidget(self.info)
        # preselect: the library that already holds this IP, then `prefer`, then the first one
        holder = next((lib for lib in ctx.libraries if lib.get(ip_name)), None) \
            or next((lib for lib in ctx.libraries if lib.name == prefer), None)
        self._fill(holder)

    def _fill(self, select=None):
        t = theme.manager().tokens
        self.tree.clear()
        target = None
        for lib in self.ctx.libraries:
            top = QTreeWidgetItem([lib.name, f"{len(lib.names())} IP", ""])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setToolTip(0, lib.path or "")
            top.setData(0, ROLE, lib)
            for name in lib.names():
                vs = lib.versions(name)
                ch = QTreeWidgetItem([name, ", ".join(e.version for e in vs), vs[0].updated if vs else ""])
                ch.setData(0, ROLE, lib)
                if name == self.ip_name:
                    for c in range(3):
                        ch.setForeground(c, QColor(t["accent"]))
                    bf = ch.font(0)
                    bf.setBold(True)
                    ch.setFont(0, bf)
                else:
                    ch.setForeground(0, QColor(t["muted"]))
                top.addChild(ch)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
            if lib is select or (target is None and select is None):
                target = top
        if not self.ctx.libraries:
            it = QTreeWidgetItem([tr("No open library: New library or Open library"), "", ""])
            it.setForeground(0, QColor(t["muted"]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(it)
        if target is not None:
            self.tree.setCurrentItem(target)
        self._update()

    def library(self):
        it = self.tree.currentItem()
        return it.data(0, ROLE) if it else None

    def version(self):
        return self.version_ed.text().strip()

    def valid(self):
        return self.library() is not None and bool(G.VERSION_RE.match(self.version()))

    def overwrites(self):
        lib = self.library()
        return bool(lib and lib.get(self.ip_name, self.version()))

    def _update(self):
        lib, v = self.library(), self.version()
        t = theme.manager().tokens
        if lib is None:
            text, color = tr("Select a library or create a new one."), t["muted"]
        elif not G.VERSION_RE.match(v):
            text, color = tr("Write the version with digits and dots, e.g. 1.0.0"), t["danger"]
        else:
            have = [e.version for e in lib.versions(self.ip_name)]
            if v in have:
                text, color = tr("{0}: {1} {2} already exists and will be overwritten.").format(lib.name, self.ip_name, v), t["warning"]
            elif have:
                text, color = (tr("{0}: new version {2} of {1} will be added (existing: {3}).").format(lib.name, self.ip_name, v, ', '.join(have))), t["success"]
            else:
                text, color = tr("{0}: {1} will be added as a new IP ({2}).").format(lib.name, self.ip_name, v), t["success"]
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")
        self.changed.emit()

    def _new_library(self):
        lib = _new_library_file(self, self.ctx)
        if lib:
            self._fill(lib)

    def _open_library(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open IP library"), os.path.dirname(self.ctx.path or ROOT),
                                              LIB_FILTER)
        if not path:
            return
        try:
            lib = self.ctx.open_library(path)
        except Exception as exc:  # noqa: BLE001
            return QMessageBox.critical(self, tr("Could not open"), str(exc))
        self._fill(lib)


class ProjectPane(QWidget):
    """Saving a register map into the project: every IP of the project is listed, the name decides
    whether the map is new, updates an IP or replaces another one."""

    changed = Signal()

    def __init__(self, ctx, name, current=None):
        super().__init__()
        self.ctx, self.current = ctx, current       # current: the project IP being edited (or None)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(ui.label(tr("The project's IPs. The register map is saved under the name below; picking an IP in the list takes its name (its register map is replaced)."), "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["IP", tr("Version"), "Register", tr("In the address map"), tr("Source")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        t = theme.manager().tokens
        for ip in sorted(ctx.project.ips, key=lambda i: i.name.lower()):
            n = len(ctx.project.instances_of(ip.name))
            it = QTreeWidgetItem([ip.name, ip.version or "—", str(len(ip.registers)),
                                  ", ".join(i.name for i in ctx.project.instances_of(ip.name)) if n else tr("not placed"),
                                  ip.source or tr("in project")])
            it.setData(0, ROLE, ip.name)
            if ip.name == current:
                for c in range(5):
                    it.setForeground(c, QColor(t["accent"]))
                it.setToolTip(0, tr("IP being edited"))
            self.tree.addTopLevelItem(it)
        if not ctx.project.ips:
            it = QTreeWidgetItem([tr("No IPs in the project yet"), "", "", "", ""])
            it.setForeground(0, QColor(t["muted"]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(it)
        self.tree.itemClicked.connect(lambda it, _c: it.data(0, ROLE) and self.name_ed.setText(it.data(0, ROLE)))
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(ui.label(tr("Name"), "muted"))
        self.name_ed = QLineEdit(name)
        self.name_ed.textChanged.connect(lambda *_: self._update())
        row.addWidget(self.name_ed, 1)
        lay.addLayout(row)
        self.info = ui.label("", None, wrap=True)
        lay.addWidget(self.info)
        self._update()

    def name(self):
        return self.name_ed.text().strip()

    def valid(self):
        return bool(self.name())

    def overwrites(self):
        return self.ctx.project.get_ip(self.name()) is not None and self.name() != self.current

    def _update(self):
        t = theme.manager().tokens
        name = self.name()
        proj = self.ctx.project
        other = proj.get_ip(name)
        if not name:
            text, color = tr("The name cannot be empty."), t["danger"]
        elif other is not None and name != self.current:
            n = len(proj.instances_of(name))
            text, color = (tr("The project has “{0}”: its register map will be replaced").format(name)
                           + (tr(" ({0} address-map instances use this register map).").format(n) if n else "."), t["warning"])
        elif self.current and proj.get_ip(self.current) and name != self.current:
            text, color = tr("IP “{0}” will be renamed to “{1}”; its instances follow.").format(self.current, name), \
                t["warning"]
        elif other is not None:
            text, color = tr("“{0}” in the project will be updated.").format(name), t["success"]
        else:
            text, color = tr("“{0}” will be added to the project as a new IP.").format(name), t["success"]
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")
        self.changed.emit()


class FilePane(QWidget):
    """Export the register map as a .regspec.json file."""

    changed = Signal()

    def __init__(self, path):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(ui.label(tr("The register map is written as a register spec file (.regspec.json), to take it to another project or to the command line (axilgen)."), "muted", wrap=True))
        row = QHBoxLayout()
        self.path_ed = QLineEdit(path)
        self.path_ed.setProperty("mono", "true")
        self.path_ed.textChanged.connect(lambda *_: self._update())
        row.addWidget(self.path_ed, 1)
        row.addWidget(ui.button(tr("Browse…"), "folder", "ghost", self._browse))
        lay.addLayout(row)
        self.info = ui.label("", None, wrap=True)
        lay.addWidget(self.info)
        lay.addStretch(1)
        self._update()

    def path(self):
        p = self.path_ed.text().strip()
        return p if not p or p.endswith(".json") else p + ".regspec.json"

    def valid(self):
        p = self.path()
        return bool(p) and os.path.isdir(os.path.dirname(os.path.abspath(p)))

    def overwrites(self):
        return bool(self.path()) and os.path.exists(self.path())

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Register spec"), self.path() or ROOT,
                                              tr("Register spec (*.regspec.json);;JSON (*.json)"))
        if path:
            self.path_ed.setText(path)

    def _update(self):
        t = theme.manager().tokens
        if not self.path():
            text, color = tr("Type a file path or Browse."), t["muted"]
        elif not self.valid():
            text, color = tr("Folder not found."), t["danger"]
        elif self.overwrites():
            text, color = tr("The file exists and will be overwritten."), t["warning"]
        else:
            text, color = tr("It will be written as a new file."), t["success"]
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")
        self.changed.emit()


class _PaneDialog(QDialog):
    """A dialog around one or more save panes (tabs); OK reflects the current pane."""

    def __init__(self, parent, title, panes, select=0):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self.panes = panes                      # [(key, label, pane)]
        if len(panes) == 1:
            lay.addWidget(panes[0][2], 1)
            self.tabs = None
        else:
            self.tabs = QTabWidget()
            for _key, label, pane in panes:
                w = QWidget()
                wl = QVBoxLayout(w)
                wl.setContentsMargins(12, 12, 12, 12)
                wl.addWidget(pane)
                self.tabs.addTab(w, label)
            self.tabs.setCurrentIndex(select)
            self.tabs.currentChanged.connect(lambda *_: self._update())
            lay.addWidget(self.tabs, 1)
        box = QDialogButtonBox()
        self.ok = box.addButton(tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for _k, _l, pane in panes:
            pane.changed.connect(self._update)
        self.result_value = None
        self._update()

    def current(self):
        return self.panes[self.tabs.currentIndex() if self.tabs else 0]

    def _update(self):
        _key, _label, pane = self.current()
        self.ok.setEnabled(pane.valid())
        self.ok.setText(tr("Overwrite") if pane.valid() and pane.overwrites() else tr("Save"))

    def accept(self):
        key, _label, pane = self.current()
        if not pane.valid():
            return
        if key == "project":
            self.result_value = ("project", pane.name())
        elif key == "library":
            self.result_value = ("library", pane.library(), pane.version())
        else:
            self.result_value = ("file", pane.path())
        super().accept()

    def ask(self):
        return self.result_value if self.exec() == QDialog.DialogCode.Accepted else None


class LibrarySaveDialog:
    @staticmethod
    def ask(parent, ctx, ip_name, version, prefer=""):
        """(library, version) or None."""
        r = _PaneDialog(parent, tr("Store in library: {0}").format(ip_name),
                        [("library", tr("Library"), LibraryPane(ctx, ip_name, version, prefer))]).ask()
        return (r[1], r[2]) if r else None


class ProjectSaveDialog:
    @staticmethod
    def ask(parent, ctx, name, current=None):
        """The project IP name or None."""
        r = _PaneDialog(parent, tr("Save to project"), [("project", tr("Project"), ProjectPane(ctx, name, current))]).ask()
        return r[1] if r else None


class SaveAsDialog:
    """"Farklı kaydet" of a register map: the project, an IP library or a .regspec.json file.
    Returns ("project", name) | ("library", library, version) | ("file", path) | None."""

    ORDER = ("project", "library", "file")

    @staticmethod
    def ask(parent, ctx, name, version, current_project=None, prefer_lib="", path="", select="project"):
        panes = [("project", tr("Project"), ProjectPane(ctx, name, current_project)),
                 ("library", tr("Library"), LibraryPane(ctx, name, version, prefer_lib)),
                 ("file", tr("File (.regspec.json)"), FilePane(path))]
        return _PaneDialog(parent, tr("Save as: {0}").format(name), panes, SaveAsDialog.ORDER.index(select)).ask()
