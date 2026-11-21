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

LIB_FILTER = "IP kütüphanesi (*.axlib.json);;JSON (*.json);;Tüm dosyalar (*)"
ROLE = Qt.ItemDataRole.UserRole


class IPLibraryPage(ToolPage):
    tool_id = "library"
    title = "IP Library"
    short_title = "IP Library"
    subtitle = "Projedeki tüm IP'leri gör, IP kütüphaneleri oluştur, kütüphanelerden projeye IP al."
    description = ("Projedeki IP'leri ve adres haritasındaki örneklerini listeler; birden fazla IP kütüphanesi "
                   "(.axlib.json) açılır, IP'ler projeye alınır ya da projeden kütüphaneye kaydedilir.")
    icon_name = "library"
    version = "0.2.1"

    picked = Signal()          # picker mode: an IP was double-clicked

    def __init__(self, ctx, parent=None, picker=False):
        self.picker = picker
        super().__init__(ctx, parent)
        if picker:      # inside the picker dialog: no outer margins; the bar keeps the library commands
            self.outer.setContentsMargins(0, 0, 0, 0)
        bar = self.header       # the tool's commands sit in the top bar
        bar.add("Yeni kütüphane…", "plus", self.new_library, "Yeni bir .axlib.json dosyası oluştur")
        bar.add("Kütüphane aç…", "folder", self.open_library)
        self.close_btn = bar.add("Kütüphaneyi kapat", None, self.close_library, "Listeden çıkar (dosya silinmez)")
        bar.sep()
        bar.add("İçe aktar: register spesifikasyonu…", "regmap", self.import_spec,
                ".regspec.json dosyasını seçili kütüphaneye ekle")
        bar.add("İçe aktar: proje dosyası…", "file", self.import_project,
                "Başka bir .axproj.json dosyasındaki IP'leri seçili kütüphaneye ekle")

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QSplitter(Qt.Orientation.Vertical)
        proj = ui.Card("Bu projedeki IP'ler", "Alt satırlar: adres haritasındaki örnekler", margins=14)
        self.proj_tree = QTreeWidget()
        self.proj_tree.setHeaderLabels(["Ad", "Sürüm", "Adres", "Kaynak"])
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
        libs = ui.Card("Kütüphaneler", margins=14)
        self.lib_tree = QTreeWidget()
        self.lib_tree.setHeaderLabels(["Ad", "Register", "Sürüm"])
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
        self.d_title = ui.label("IP seç", "cardTitle")
        self.d_sub = ui.label("", "muted", wrap=True)
        col.addWidget(self.d_title)
        col.addWidget(self.d_sub)
        self.detail.lay.addLayout(col)
        self.d_desc = ui.label("", None, wrap=True)
        self.detail.lay.addWidget(self.d_desc)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.map_btn = ui.button("Adres haritasına ekle", "addrmap", "primary", self.add_to_address_map,
                                 "IP'yi (gerekirse önce projeye alıp) adres haritasına bir kez daha yerleştir")
        self.import_btn = ui.button("Projeye al", "arrow", None, self.import_to_project,
                                    "Kütüphanedeki IP'yi projenin IP'lerine kopyala (adres vermeden)")
        self.regmap_btn = ui.button("Register Map Editor'da aç", "regmap", None, self.open_in_regmap)
        self.wrapper_btn = ui.button("Wrapper'da aç", "wrapper", None, self.open_in_wrapper,
                                     "HDL'i Slave Interface Wrapper'da üret")
        self.addr_btn = ui.button("Address Map Editor'da aç", "addrmap", None, self.open_in_addrmap)
        self.store_btn = ui.button("Kütüphaneye kaydet…", "save", None, self.store_project_ip,
                                   "Projedeki bu IP'yi bir kütüphaneye kaydet")
        self.rename_btn = ui.button("Yeniden adlandır…", None, "ghost", self.rename_entry)
        self.del_btn = ui.button("Sil", None, "ghost", self.delete_selected)
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
        for text, slot in (("Yeni kütüphane…", self.new_library), ("Kütüphane aç…", self.open_library),
                           ("Kütüphaneyi kapat", self.close_library), (None, None),
                           ("İçe aktar: register spesifikasyonu…", self.import_spec),
                           ("İçe aktar: proje dosyası…", self.import_project), (None, None),
                           ("Seçili IP'yi adres haritasına ekle", self.add_to_address_map),
                           ("Seçili kütüphane IP'sini projeye al", self.import_to_project),
                           ("Seçili proje IP'sini kütüphaneye kaydet…", self.store_project_ip)):
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
                                   f"{len(insts)} örnek" if insts else "yerleşmedi", ip.source or "projede"])
            bf = top.font(0)
            bf.setBold(True)
            top.setFont(0, bf)
            top.setToolTip(0, single_line(ip.desc))
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
                ch.setToolTip(0, f"{ip.name} örneği · çift tık: Address Map Editor")
                top.addChild(ch)
                if keep and keep[0] == "inst" and keep[1].name == inst.name:
                    ptarget = ch
            self.proj_tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not proj.ips:
            it = QTreeWidgetItem(["Projede IP yok: Register Map Editor'da oluştur ya da kütüphaneden al", "", "", ""])
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
                    label = e.name if k == 0 else f"önceki sürüm {e.version}"
                    it = QTreeWidgetItem([label, str(len(e.ip.registers)),
                                          f"{e.version}" + (f"  ({len(versions)} sürüm)" if k == 0 and len(versions) > 1
                                                            else "")])
                    it.setData(0, ROLE, ("lib", lib, e))
                    it.setToolTip(0, single_line(e.ip.desc) + ("\nRegister spesifikasyonu var" if e.regspec else ""))
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
            it = QTreeWidgetItem(["Açık kütüphane yok: Yeni kütüphane ya da Kütüphane aç", "", ""])
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
            self.status("Açılamayan kütüphane: " + "; ".join(self.ctx.library_errors), 10000)
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
        self.del_btn.setText("Projeden kaldır" if kind == "proj" else "Kütüphaneden sil")
        self.close_btn.setEnabled(bool(key and kind in ("lib", "libroot")))
        ip = None
        if not key:
            self.d_title.setText("IP seç")
            self.d_sub.setText("Soldan projedeki bir IP'yi ya da bir kütüphane IP'sini seç.")
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
            self.d_title.setText(f"{ip.name}   ·   sürüm {e.version}")
            self.d_sub.setText(f"Kütüphane: {lib.name}" + (f" · {e.updated}" if e.updated else "")
                               + (f" · diğer sürümler: {', '.join(others)}" if others else "")
                               + (" · register spesifikasyonu var" if e.regspec else "")
                               + (f" · projede var (sürüm {used.version or '—'}, adres haritasında "
                                  f"{len(self.ctx.project.instances_of(used.name))} kez)" if used else ""))
        elif kind == "proj":
            ip = key[1]
            insts = self.ctx.project.instances_of(ip.name)
            self.d_title.setText(f"{ip.name}" + (f"   ·   sürüm {ip.version}" if ip.version else ""))
            self.d_sub.setText("Projede" + (f" · kaynak: {ip.source}" if ip.source else "")
                               + (" · adres haritasında: " + ", ".join(f"{i.name} @ {hex32(i.base)}" for i in insts)
                                  if insts else " · adres haritasında yok"))
        else:
            inst = key[1]
            ip = self.ctx.project.get_ip(inst.ip)
            self.d_title.setText(f"{inst.name}   ·   {hex32(inst.base)}")
            self.d_sub.setText(f"{inst.ip} IP'sinin adres haritasındaki örneği · "
                               + ("sabit adres" if inst.fixed else "otomatik adres"))
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
            if ask and ui.confirm(self, self.title, "Açık kütüphane yok. Yeni bir kütüphane oluşturulsun mu?",
                                  ok="Oluştur"):
                return self.new_library()
            return None
        if not ask:
            return None
        name, ok = QInputDialog.getItem(self, "Kütüphane seç", "Kütüphane:", [lib.name for lib in libs], 0, False)
        return next((lib for lib in libs if lib.name == name), None) if ok else None

    # ================================================================ library files
    def new_library(self):
        lib = _new_library_file(self, self.ctx)
        if lib:
            self.status(f"Kütüphane oluşturuldu: {lib.path}")
        return lib

    def open_library(self):
        path, _ = QFileDialog.getOpenFileName(self, "IP kütüphanesi aç", os.path.dirname(self.ctx.path or ROOT),
                                              LIB_FILTER)
        if not path:
            return
        try:
            lib = self.ctx.open_library(path)
        except Exception as exc:  # noqa: BLE001
            return self.error("Açılamadı", exc)
        self.status(f"Kütüphane açıldı: {lib.name}")

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
        path, _ = QFileDialog.getOpenFileName(self, "Register spesifikasyonu", ROOT,
                                              "Register spesifikasyonu (*.regspec.json);;JSON (*.json)")
        if not path:
            return
        try:
            spec = G.ModuleSpec.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error("Açılamadı", exc)
        if lib.get(spec.module, spec.version) and not ui.confirm(
                self, self.title, f"{lib.name} kütüphanesinde {spec.module} {spec.version} var. Üzerine yazılsın mı?",
                ok="Üzerine yaz"):
            return
        replaced = lib.put(LibEntry(G.to_ipblock(spec, 0), spec.to_dict()))
        self.ctx.save_library(lib)
        self.status(f"{spec.module} {spec.version} {lib.name} kütüphanesine "
                    f"{'güncellendi' if replaced else 'eklendi'}")

    def import_project(self):
        lib = self._selected_library()
        if lib is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Proje dosyası", ROOT, "AXIL proje (*.axproj.json);;JSON (*.json)")
        if not path:
            return
        try:
            proj = Project.load(path)
        except Exception as exc:  # noqa: BLE001
            return self.error("Açılamadı", exc)
        if not proj.ips:
            QMessageBox.information(self, self.title, "Bu proje dosyasında IP yok.")
            return
        names = [ip.name for ip in proj.ips]
        choice, ok = QInputDialog.getItem(self, "İçe aktar", "IP:", ["Tümü"] + names, 0, False)
        if not ok:
            return
        for ip in proj.ips if choice == "Tümü" else [proj.ips[names.index(choice)]]:
            spec = ip.regspec
            ip.source, ip.regspec, ip.base = "", None, 0
            ip.version = ip.version or "1.0.0"
            lib.put(LibEntry(ip, spec))
        self.ctx.save_library(lib)
        self.status(f"{lib.name} kütüphanesine aktarıldı")

    # ================================================================ library <-> project
    def _import(self, lib, e):
        """Bring a library IP into the project's IP definitions; returns its name there or None."""
        proj = self.ctx.project
        old = proj.get_ip(e.name)
        if old is None:
            name = self.ctx.import_from_library(lib, e, source=self.tool_id)
            self.status(f"{name} {e.version} projeye alındı")
            return name
        if old.version == e.version and old.source == lib.name:
            return old.name                       # the same IP is already in the project
        n = len(proj.instances_of(old.name))
        choice, ok = QInputDialog.getItem(
            self, "Projede aynı adlı IP var",
            f"{e.name} projede zaten var (sürüm {old.version or '—'}, adres haritasında {n} kez):",
            [f"Projedekini kullan ({old.version or '—'})",
             f"Projedekini kütüphanedekiyle değiştir ({e.version})",
             "Yeni bir adla ayrı IP olarak al"], 0, False)
        if not ok:
            return None
        if choice.startswith("Projedekini kullan"):
            return old.name
        if choice.startswith("Projedekini kütüphane"):
            name = self.ctx.import_from_library(lib, e, source=self.tool_id)
            self.status(f"{name} {e.version} sürümüne güncellendi ({n} örnek)")
            return name
        new, ok = QInputDialog.getText(self, "IP adı", "Projedeki adı:", text=self.ctx.unique_ip_name(e.name))
        new = new.strip()
        if not ok or not new:
            return None
        if proj.get_ip(new):
            QMessageBox.warning(self, self.title, f"Projede “{new}” adlı bir IP zaten var.")
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
        self.status(f"{inst.name} adres haritasına eklendi ({hex32(inst.base)})")
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
        self.ctx.store_in_library(lib, stored, spec)
        ip.version = version
        if ip.regspec:
            ip.regspec = dict(ip.regspec, ip_version=version)
        ip.source = ip.source or lib.name
        self.ctx.use_library(lib)
        self.ctx.mark_modified(self.tool_id)
        self.status(f"{ip.name} {version} {lib.name} kütüphanesine kaydedildi")

    def rename_entry(self):
        if not self._sel or self._sel[0] != "lib":
            return
        lib, e = self._sel[1], self._sel[2]
        new, ok = QInputDialog.getText(self, "Yeniden adlandır", "Ad:", text=e.name)
        new = new.strip()
        if not ok or not new or new == e.name:
            return
        if lib.get(new):
            QMessageBox.warning(self, self.title, f"{lib.name} kütüphanesinde {new} zaten var.")
            return
        for x in lib.versions(e.name):      # every version takes the new name
            x.ip.name = new
            if x.regspec:
                x.regspec["module"] = new
        self.ctx.save_library(lib)

    def delete_selected(self):
        if not self._sel:
            return
        if self._sel[0] == "lib":
            lib, e = self._sel[1], self._sel[2]
            if ui.confirm(self, "Kütüphaneden sil", f"{e.name} {e.version}, {lib.name} kütüphanesinden silinecek. "
                                                    "Diğer sürümler ve projelerdeki kopyalar etkilenmez."):
                lib.remove(e.name, e.version)
                self._sel = ("libroot", lib, None)
                self.ctx.save_library(lib)
        elif self._sel[0] == "proj":
            ip = self._sel[1]
            n = len(self.ctx.project.instances_of(ip.name))
            if ui.confirm(self, "Projeden kaldır", f"{ip.name} projeden kaldırılacak"
                                                   + (f"; adres haritasındaki {n} örneği de çıkar" if n else "")
                                                   + ".", ok="Kaldır"):
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
            w.page.load_spec(spec, f"{lib.name} kütüphanesinden {e.version}", lib.name)

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
        self.ok = box.addButton("Seç", QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton("Vazgeç", QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.name = None

    def accept(self):
        name = self.page.selected_project_ip()
        if not name:
            if self.page._sel is None or self.page._sel[0] == "libroot":
                QMessageBox.information(self, self.windowTitle(), "Bir IP seç (projedeki ya da bir kütüphanedeki).")
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
    path, _ = QFileDialog.getSaveFileName(parent, "Yeni IP kütüphanesi",
                                          os.path.join(os.path.dirname(ctx.path or ROOT), "ip_library.axlib.json"),
                                          LIB_FILTER)
    if not path:
        return None
    if not path.endswith(".json"):
        path += ".axlib.json"
    name, ok = QInputDialog.getText(parent, "Yeni IP kütüphanesi", "Kütüphane adı:",
                                    text=os.path.basename(path).split(".")[0])
    if not ok or not name.strip():
        return None
    try:
        return ctx.new_library(path, name.strip())
    except OSError as exc:
        QMessageBox.critical(parent, "Oluşturulamadı", str(exc))
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
        lay.addWidget(ui.label(f"{ip_name} IP'sinin kaydedileceği kütüphaneyi seç. Kütüphanelerdeki IP'ler ve "
                               "sürümleri listelenir; bu IP'nin kayıtları vurgulanır.", "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Kütüphane / IP", "Sürümler", "Güncelleme"])
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda *_: self._update())
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(ui.button("Yeni kütüphane…", "plus", "ghost", self._new_library))
        row.addWidget(ui.button("Kütüphane aç…", "folder", "ghost", self._open_library))
        row.addStretch(1)
        row.addWidget(ui.label("Sürüm", "muted"))
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
            it = QTreeWidgetItem(["Açık kütüphane yok: Yeni kütüphane ya da Kütüphane aç", "", ""])
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
            text, color = "Bir kütüphane seç ya da yeni bir kütüphane oluştur.", t["muted"]
        elif not G.VERSION_RE.match(v):
            text, color = "Sürüm rakam ve nokta ile yazılmalı, ör. 1.0.0", t["danger"]
        else:
            have = [e.version for e in lib.versions(self.ip_name)]
            if v in have:
                text, color = f"{lib.name}: {self.ip_name} {v} zaten var, üzerine yazılacak.", t["warning"]
            elif have:
                text, color = (f"{lib.name}: {self.ip_name} için yeni sürüm {v} eklenecek "
                               f"(mevcut: {', '.join(have)})."), t["success"]
            else:
                text, color = f"{lib.name}: {self.ip_name} yeni IP olarak eklenecek ({v}).", t["success"]
        self.info.setText(text)
        self.info.setStyleSheet(f"color: {color};")
        self.changed.emit()

    def _new_library(self):
        lib = _new_library_file(self, self.ctx)
        if lib:
            self._fill(lib)

    def _open_library(self):
        path, _ = QFileDialog.getOpenFileName(self, "IP kütüphanesi aç", os.path.dirname(self.ctx.path or ROOT),
                                              LIB_FILTER)
        if not path:
            return
        try:
            lib = self.ctx.open_library(path)
        except Exception as exc:  # noqa: BLE001
            return QMessageBox.critical(self, "Açılamadı", str(exc))
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
        lay.addWidget(ui.label("Projedeki IP'ler. Register haritası aşağıdaki adla kaydedilir; listeden bir IP "
                               "seçmek adını alır (o IP'nin haritası bununla değiştirilir).", "muted", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["IP", "Sürüm", "Register", "Adres haritasında", "Kaynak"])
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
                                  ", ".join(i.name for i in ctx.project.instances_of(ip.name)) if n else "yerleşmedi",
                                  ip.source or "projede"])
            it.setData(0, ROLE, ip.name)
            if ip.name == current:
                for c in range(5):
                    it.setForeground(c, QColor(t["accent"]))
                it.setToolTip(0, "Düzenlenen IP")
            self.tree.addTopLevelItem(it)
        if not ctx.project.ips:
            it = QTreeWidgetItem(["Projede henüz IP yok", "", "", "", ""])
            it.setForeground(0, QColor(t["muted"]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(it)
        self.tree.itemClicked.connect(lambda it, _c: it.data(0, ROLE) and self.name_ed.setText(it.data(0, ROLE)))
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(ui.label("Ad", "muted"))
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
            text, color = "Ad boş olamaz.", t["danger"]
        elif other is not None and name != self.current:
            n = len(proj.instances_of(name))
            text, color = (f"Projede “{name}” var: register haritası bununla değiştirilecek"
                           + (f" (adres haritasında {n} örnek bu haritayı kullanır)." if n else "."), t["warning"])
        elif self.current and proj.get_ip(self.current) and name != self.current:
            text, color = f"“{self.current}” IP'si “{name}” olarak yeniden adlandırılacak; örnekleri de izler.", \
                t["warning"]
        elif other is not None:
            text, color = f"Projedeki “{name}” güncellenecek.", t["success"]
        else:
            text, color = f"“{name}” projeye yeni IP olarak eklenecek.", t["success"]
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
        lay.addWidget(ui.label("Register haritası bir register spesifikasyonu dosyası (.regspec.json) olarak yazılır; "
                               "başka bir projeye ya da komut satırına (axilgen) taşımak için.", "muted", wrap=True))
        row = QHBoxLayout()
        self.path_ed = QLineEdit(path)
        self.path_ed.setProperty("mono", "true")
        self.path_ed.textChanged.connect(lambda *_: self._update())
        row.addWidget(self.path_ed, 1)
        row.addWidget(ui.button("Gözat…", "folder", "ghost", self._browse))
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
        path, _ = QFileDialog.getSaveFileName(self, "Register spesifikasyonu", self.path() or ROOT,
                                              "Register spesifikasyonu (*.regspec.json);;JSON (*.json)")
        if path:
            self.path_ed.setText(path)

    def _update(self):
        t = theme.manager().tokens
        if not self.path():
            text, color = "Dosya yolu yaz ya da Gözat.", t["muted"]
        elif not self.valid():
            text, color = "Klasör bulunamadı.", t["danger"]
        elif self.overwrites():
            text, color = "Dosya var, üzerine yazılacak.", t["warning"]
        else:
            text, color = "Yeni dosya olarak yazılacak.", t["success"]
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
        self.ok = box.addButton("Kaydet", QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton("Vazgeç", QDialogButtonBox.ButtonRole.RejectRole)
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
        self.ok.setText("Üzerine yaz" if pane.valid() and pane.overwrites() else "Kaydet")

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
        r = _PaneDialog(parent, f"Kütüphaneye kaydet: {ip_name}",
                        [("library", "Kütüphane", LibraryPane(ctx, ip_name, version, prefer))]).ask()
        return (r[1], r[2]) if r else None


class ProjectSaveDialog:
    @staticmethod
    def ask(parent, ctx, name, current=None):
        """The project IP name or None."""
        r = _PaneDialog(parent, "Projeye kaydet", [("project", "Proje", ProjectPane(ctx, name, current))]).ask()
        return r[1] if r else None


class SaveAsDialog:
    """"Farklı kaydet" of a register map: the project, an IP library or a .regspec.json file.
    Returns ("project", name) | ("library", library, version) | ("file", path) | None."""

    ORDER = ("project", "library", "file")

    @staticmethod
    def ask(parent, ctx, name, version, current_project=None, prefer_lib="", path="", select="project"):
        panes = [("project", "Proje", ProjectPane(ctx, name, current_project)),
                 ("library", "Kütüphane", LibraryPane(ctx, name, version, prefer_lib)),
                 ("file", "Dosya (.regspec.json)", FilePane(path))]
        return _PaneDialog(parent, f"Farklı kaydet: {name}", panes, SaveAsDialog.ORDER.index(select)).ask()
