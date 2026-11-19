"""IP Library: reusable IP register maps in library files shared between projects.

Several libraries (.axlib.json) can be open at once; they stay open across projects and a
project remembers the libraries it took IPs from. IPs move both ways: a library IP is added to
the project at a chosen base address, a project IP (or a wrapper spec) is stored in a library.
Every library change is written to its file immediately.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QMessageBox, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

import axilgen as G
from axil_studio import ROOT, theme, ui
from axil_studio.dialogs import BaseDialog
from axil_studio.tools.base import ToolPage
from library import LibEntry
from model import Project, hex32, single_line

LIB_FILTER = "IP kütüphanesi (*.axlib.json);;JSON (*.json);;Tüm dosyalar (*)"
ROLE = Qt.ItemDataRole.UserRole


def ip_window(ip):
    span = max((r.offset for r in ip.registers), default=0) + 4
    w = 0x1000
    while w < span:
        w <<= 1
    return w


def open_tool(page, tool_id):
    """Open (or raise) another tool's window that shares this page's project."""
    from PySide6.QtWidgets import QApplication
    from axil_studio.hub import HubWindow, ToolWindow
    hub = next((w for w in QApplication.topLevelWidgets() if isinstance(w, HubWindow) and w.ctx is page.ctx), None)
    if hub is not None:
        return hub.open_tool_window(tool_id)
    w = ToolWindow(page.ctx, tool_id, owned=False)
    page._windows = getattr(page, "_windows", []) + [w]
    w.show()
    return w


class IPLibraryPage(ToolPage):
    tool_id = "library"
    title = "IP Library"
    short_title = "IP Library"
    subtitle = "Projedeki IP'leri gör, kütüphanelerden IP ekle, IP'leri başka projelerde kullanmak için sakla."
    description = ("Birden fazla IP kütüphanesi (.axlib.json) açılabilir; IP'ler projeler arasında paylaşılır, "
                   "projeye eklenir ya da projeden kütüphaneye kaydedilir.")
    icon_name = "library"
    legacy_script = None

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        bar = ui.ActionBar()
        bar.add("Yeni kütüphane…", "plus", self.new_library, "Yeni bir .axlib.json dosyası oluştur")
        bar.add("Kütüphane aç…", "folder", self.open_library)
        self.close_btn = bar.add("Kütüphaneyi kapat", None, self.close_library, "Listeden çıkar (dosya silinmez)")
        bar.sep()
        bar.add("İçe aktar: register spesifikasyonu…", "wrapper", self.import_spec,
                ".regspec.json dosyasını seçili kütüphaneye ekle")
        bar.add("İçe aktar: proje dosyası…", "file", self.import_project,
                "Başka bir .axproj.json dosyasındaki IP'leri seçili kütüphaneye ekle")
        bar.stretch()
        self.body.addWidget(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = QSplitter(Qt.Orientation.Vertical)
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
        self.lib_tree.itemDoubleClicked.connect(lambda *_: self.add_to_project())
        libs.lay.addWidget(self.lib_tree, 1)
        left.addWidget(libs)
        proj = ui.Card("Bu projedeki IP'ler", margins=14)
        self.proj_tree = QTreeWidget()
        self.proj_tree.setHeaderLabels(["Ad", "Sürüm", "Base", "Kaynak"])
        self.proj_tree.setRootIsDecorated(False)
        self.proj_tree.setUniformRowHeights(True)
        h = self.proj_tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.proj_tree.currentItemChanged.connect(lambda cur, _p: self._picked(self.proj_tree, cur))
        proj.lay.addWidget(self.proj_tree, 1)
        left.addWidget(proj)
        left.setSizes([420, 300])
        left.setMinimumWidth(360)
        split.addWidget(left)

        self.detail = ui.Card(margins=16)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.d_title = ui.label("IP seç", "cardTitle")
        self.d_sub = ui.label("Soldan bir kütüphane IP'si ya da projedeki bir IP seç.", "muted", wrap=True)
        col.addWidget(self.d_title)
        col.addWidget(self.d_sub)
        top.addLayout(col, 1)
        self.detail.lay.addLayout(top)
        self.d_desc = ui.label("", None, wrap=True)
        self.detail.lay.addWidget(self.d_desc)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.add_btn = ui.button("Projeye ekle…", "arrow", "primary", self.add_to_project,
                                 "Bu IP'yi projeye bir base adresle ekle")
        self.store_btn = ui.button("Kütüphaneye kaydet…", "save", None, self.store_project_ip,
                                   "Projedeki bu IP'yi bir kütüphaneye kaydet")
        self.wrapper_btn = ui.button("Wrapper'da aç", "wrapper", None, self.open_in_wrapper,
                                     "Register spesifikasyonunu Slave Interface Wrapper'da düzenle")
        self.rename_btn = ui.button("Yeniden adlandır…", None, "ghost", self.rename_entry)
        self.del_btn = ui.button("Sil", None, "ghost", self.delete_selected)
        for b in (self.add_btn, self.store_btn, self.wrapper_btn, self.rename_btn, self.del_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        self.detail.lay.addLayout(brow)
        self.regs = QTreeWidget()
        self.regs.setHeaderLabels(["Register / bit alanı", "Offset / bitler", "Tip", "Veri tipi", "Açıklama"])
        self.regs.setUniformRowHeights(True)
        self.regs.header().setStretchLastSection(True)
        self.detail.lay.addWidget(self.regs, 1)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 860])
        self.body.addWidget(split, 1)

        ctx.libraries_changed.connect(self._refresh)
        ctx.state_changed.connect(self._refresh)
        self._sel = None          # ("lib", library, entry) | ("proj", ip) | ("libroot", library)
        self._refresh()

    # ================================================================ menus
    def build_menus(self, menubar):
        m = menubar.addMenu("&Library")
        win = self.window()
        for text, slot in (("Yeni kütüphane…", self.new_library), ("Kütüphane aç…", self.open_library),
                           ("Kütüphaneyi kapat", self.close_library), (None, None),
                           ("İçe aktar: register spesifikasyonu…", self.import_spec),
                           ("İçe aktar: proje dosyası…", self.import_project), (None, None),
                           ("Seçili IP'yi projeye ekle…", self.add_to_project),
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
        keep = self._sel
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
                        it.setForeground(0, theme.manager().color("muted"))
                        parent.addChild(it)
                    if keep and keep[0] == "lib" and keep[1] is lib and keep[2].name == e.name \
                            and keep[2].version == e.version:
                        target = it
            self.lib_tree.addTopLevelItem(top)
            if keep and keep[0] == "libroot" and keep[1] is lib:
                target = top
        if not self.ctx.libraries:
            it = QTreeWidgetItem(["Açık kütüphane yok: Yeni kütüphane ya da Kütüphane aç", "", ""])
            it.setForeground(0, theme.manager().color("muted"))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.lib_tree.addTopLevelItem(it)
        for i in range(self.lib_tree.topLevelItemCount()):
            self.lib_tree.topLevelItem(i).setExpanded(True)       # libraries open, older versions folded
        self.lib_tree.blockSignals(False)

        self.proj_tree.blockSignals(True)
        self.proj_tree.clear()
        ptarget = None
        for ip in sorted(self.ctx.project.ips, key=lambda i: i.base):
            it = QTreeWidgetItem([ip.name, ip.version or "—", hex32(ip.base), ip.source or "projede"])
            it.setFont(2, mono)
            if not ip.source:
                it.setForeground(3, theme.manager().color("muted"))
            it.setData(0, ROLE, ("proj", ip, None))
            self.proj_tree.addTopLevelItem(it)
            if keep and keep[0] == "proj" and keep[1].name == ip.name:
                ptarget = it
        if not self.ctx.project.ips:
            it = QTreeWidgetItem(["Projede IP yok", "", "", ""])
            it.setForeground(0, QColor(t["muted"]))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.proj_tree.addTopLevelItem(it)
        self.proj_tree.blockSignals(False)
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
        self.regs.clear()
        kind = key[0] if key else None
        self.add_btn.setVisible(kind == "lib")
        self.store_btn.setVisible(kind == "proj")
        self.wrapper_btn.setVisible(kind == "lib" and bool(key[2].regspec))
        self.rename_btn.setVisible(kind == "lib")
        self.del_btn.setVisible(kind in ("lib", "proj"))
        self.del_btn.setText("Projeden kaldır" if kind == "proj" else "Kütüphaneden sil")
        self.close_btn.setEnabled(bool(key and kind in ("lib", "libroot")))
        if not key:
            self.d_title.setText("IP seç")
            self.d_sub.setText("Soldan bir kütüphane IP'si ya da projedeki bir IP seç.")
            self.d_desc.setText("")
            return
        if kind == "libroot":
            lib = key[1]
            self.d_title.setText(lib.name)
            self.d_sub.setText(f"{len(lib.entries)} IP · {lib.path}")
            self.d_desc.setText(lib.desc)
            return
        if kind == "lib":
            lib, e = key[1], key[2]
            ip = e.ip
            used = self.ctx.project.get_ip(ip.name)
            others = [x.version for x in lib.versions(ip.name) if x.version != e.version]
            self.d_title.setText(f"{ip.name}   ·   sürüm {e.version}")
            self.d_sub.setText(f"Kütüphane: {lib.name}" + (f" · {e.updated}" if e.updated else "")
                               + (f" · diğer sürümler: {', '.join(others)}" if others else "")
                               + (" · register spesifikasyonu var" if e.regspec else "")
                               + (f" · projede {hex32(used.base)} adresinde (sürüm {used.version or '—'})" if used else ""))
        else:
            ip = key[1]
            self.d_title.setText(f"{ip.name}" + (f"   ·   sürüm {ip.version}" if ip.version else ""))
            self.d_sub.setText(f"Projede · base {hex32(ip.base)}" + (f" · kaynak: {ip.source}" if ip.source else ""))
        self.d_desc.setText(ip.desc or "")
        mono = theme.mono_font()
        muted = theme.manager().color("muted")
        import numfmt as N
        for reg in ip.registers:
            it = QTreeWidgetItem([reg.name, f"0x{reg.offset:02X}", reg.access, "", single_line(reg.desc)])
            it.setFont(1, mono)
            bf = it.font(0)
            bf.setBold(True)
            it.setFont(0, bf)
            for f in sorted(reg.fields, key=lambda f: -f.lsb):
                ch = QTreeWidgetItem([f.name, f.bits, f.kind or reg.access, N.label(f.fmt, f.width),
                                      single_line(f.desc) + (f"  ({len(f.values)} değer açıklaması)" if f.values else "")])
                ch.setFont(1, mono)
                ch.setForeground(3, muted)
                it.addChild(ch)
            self.regs.addTopLevelItem(it)
        for c in range(4):
            self.regs.resizeColumnToContents(c)

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
        path, _ = QFileDialog.getSaveFileName(self, "Yeni IP kütüphanesi",
                                              os.path.join(os.path.dirname(self.ctx.path or ROOT), "ip_library.axlib.json"),
                                              LIB_FILTER)
        if not path:
            return None
        if not path.endswith(".json"):
            path += ".axlib.json"
        name, ok = QInputDialog.getText(self, "Yeni IP kütüphanesi", "Kütüphane adı:",
                                        text=os.path.basename(path).split(".")[0])
        if not ok or not name.strip():
            return None
        try:
            lib = self.ctx.new_library(path, name.strip())
        except OSError as exc:
            self.error("Oluşturulamadı", exc)
            return None
        self.status(f"Kütüphane oluşturuldu: {path}")
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
        self.ctx.close_library(self._sel[1])
        self._sel = None

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
            ip.source = ""
            ip.version = ip.version or "1.0.0"
            lib.put(LibEntry(ip))
        self.ctx.save_library(lib)
        self.status(f"{lib.name} kütüphanesine aktarıldı")

    # ================================================================ library <-> project
    def add_to_project(self):
        if not self._sel or self._sel[0] != "lib":
            return
        lib, e = self._sel[1], self._sel[2]
        name = e.name
        if self.ctx.project.get_ip(name):
            choice, ok = QInputDialog.getItem(
                self, "Projede aynı adlı IP var", f"{name} projede zaten var:",
                ["Yeni bir adla ekle (ikinci kopya)", "Projedekini kütüphanedekiyle değiştir"], 0, False)
            if not ok:
                return
            if choice.startswith("Yeni"):
                k = 1
                while self.ctx.project.get_ip(f"{name}_{k}"):
                    k += 1
                new, ok = QInputDialog.getText(self, "IP adı", "Projedeki adı:", text=f"{name}_{k}")
                if not ok or not new.strip() or self.ctx.project.get_ip(new.strip()):
                    return
                name = new.strip()
        w = ip_window(e.ip)
        base = BaseDialog.ask(self, f"Projeye ekle: {name}", self.ctx.suggest_base(name, w), w,
                              "IP'nin bu projedeki interconnect adresi. Address Map & Interconnect Configurator'da "
                              "otomatik adresleme açıksa sıraya göre yeniden verilir.")
        if base is None:
            return
        replaced = self.ctx.add_from_library(lib, e, base, name if name != e.name else None)
        self.status(f"{name} {e.version} projeye {'güncellendi' if replaced else 'eklendi'} ({hex32(base)})")

    def store_project_ip(self):
        if not self._sel or self._sel[0] != "proj":
            return
        ip = self._sel[1]
        lib = self._selected_library()
        if lib is None:
            return
        latest = lib.get(ip.name)
        default = ip.version or "1.0.0"
        if latest and G.version_key(latest.version) >= G.version_key(default):
            default = G.bump_version(latest.version)
        version, ok = QInputDialog.getText(
            self, "Kütüphaneye kaydet", f"{ip.name} sürümü"
            + (f" (kütüphanedeki en yeni: {latest.version})" if latest else "") + ":", text=default)
        version = version.strip()
        if not ok or not version:
            return
        if not G.VERSION_RE.match(version):
            QMessageBox.warning(self, self.title, "Sürüm rakam ve nokta ile yazılmalı, ör. 1.0.0")
            return
        if lib.get(ip.name, version) and not ui.confirm(
                self, self.title, f"{lib.name} kütüphanesinde {ip.name} {version} var. Üzerine yazılsın mı?",
                ok="Üzerine yaz"):
            return
        import copy
        stored = copy.deepcopy(ip)
        stored.version = version
        spec = None
        if latest and latest.regspec:
            spec = dict(latest.regspec, ip_version=version)
        self.ctx.store_in_library(lib, stored, spec)
        ip.version = version
        ip.source = ip.source or lib.name
        self.ctx.mark_modified(self.tool_id)
        self.ctx.use_library(lib)
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
            if ui.confirm(self, "Projeden kaldır", f"{ip.name} projeden kaldırılacak; interconnect ve debug "
                                                   "listelerinden de çıkar.", ok="Kaldır"):
                self.ctx.project.ips = [x for x in self.ctx.project.ips if x is not ip]
                self._sel = None
                self.ctx.mark_modified(self.tool_id)
                self._refresh()

    def open_in_wrapper(self):
        if not self._sel or self._sel[0] != "lib" or not self._sel[2].regspec:
            return
        spec = G.ModuleSpec.from_dict(self._sel[2].regspec)
        w = open_tool(self, "wrapper")
        w.page.load_spec(spec, f"{self._sel[1].name} kütüphanesinden")

