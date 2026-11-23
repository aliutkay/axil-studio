"""Project shared by every tool in the hub.

The common file is the project (.axproj.json, model.Project). It holds
  * IP definitions (register maps): Register Map Editor writes them, Slave Interface Wrapper
    generates HDL from them, IP Library moves them to and from library files,
  * the address map (IP instances with base addresses; one IP may be placed several times):
    Address Map Editor edits it, Interconnect Configurator builds the crossbar from it,
  * UART-to-AXI Debug Bridge reads and writes the registers of the instances on the board.
"""

import copy
import os
import re

from axil_studio.core import addrmap
from axil_studio.history import History

from PySide6.QtCore import QObject, QSettings, Signal

from axil_studio.core.library import IPLibrary, LibEntry
from axil_studio.core.model import Project
from axil_studio.i18n import tr

PROJECT_FILTER = tr("AXIL project (*.axproj.json);;JSON (*.json);;All files (*)")
MAX_RECENT = 8


def rename_interface_settings(project, old_name, new_name):
    """Keep debug['interfaces'] entries and 4-wire pair peer links in sync when an instance is renamed."""
    if not old_name or old_name == new_name or not project.debug:
        return
    ifs = project.debug.get("interfaces")
    if not isinstance(ifs, dict):
        return
    if old_name in ifs and new_name not in ifs:
        ifs[new_name] = ifs.pop(old_name)
    for st in ifs.values():
        if isinstance(st, dict) and isinstance(st.get("pair"), dict) and st["pair"].get("peer") == old_name:
            st["pair"]["peer"] = new_name


class ProjectContext(QObject):
    # the whole project was replaced (new / open)
    replaced = Signal()
    # contents changed (IP added, edited ...); argument: the tool id that made the change or ""
    modified = Signal(str)
    # dirty flag or path changed
    state_changed = Signal()
    # an IP library was opened, closed or changed
    libraries_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = Project(tr("Untitled project"))
        self.dirty = False
        self.history = History()            # undo / redo of the project
        self.history.reset(self._snapshot())
        self.libraries = []          # open IPLibrary objects (shared by every project)
        self.library_errors = []
        opened = QSettings().value("libraries/open", []) or []
        if isinstance(opened, str):
            opened = [opened]
        for path in opened:
            self._open_library_quiet(path)

    # ------------------------------------------------------------ properties
    @property
    def path(self):
        return self.project.path

    @property
    def display_name(self):
        return self.project.name + (" •" if self.dirty else "")

    # ------------------------------------------------------------ file ops
    def new(self, name=tr("Untitled project")):
        self.project = Project(name)
        self.dirty = False
        self.history.reset(self._snapshot())
        self.replaced.emit()
        self.state_changed.emit()

    def open(self, path):
        self.project = Project.load(path)
        self.dirty = False
        self.history.reset(self._snapshot())
        self._push_recent(path)
        for rel in self.project.libraries:          # libraries this project uses
            self._open_library_quiet(self._abs(rel))
        self.libraries_changed.emit()
        self.replaced.emit()
        self.state_changed.emit()

    def save(self, path=None):
        from axil_studio import APP_NAME, version_text
        self.project.generator = f"{APP_NAME} {version_text()}"
        old_path, old_libs = self.project.path, list(self.project.libraries)
        if path:      # library paths are relative to the project file: re-base them on the new place
            libs = [self._abs(p) for p in self.project.libraries]
            self.project.path = path
            self.project.libraries = [self._rel(p) for p in libs]
        try:
            self.project.save(path)
        except Exception:
            self.project.path, self.project.libraries = old_path, old_libs    # still the old file
            raise
        self.dirty = False
        self.history.mark_saved()
        self._push_recent(self.project.path)
        self.state_changed.emit()

    # ------------------------------------------------------------ edits
    def mark_modified(self, source=""):
        """Something changed: the address map follows (an IP that grew gets a larger window,
        automatic addresses move up), then every tool is told."""
        try:
            addrmap.readdress(self.project)
        except ValueError:
            pass                    # reported by the Address Map Editor
        self.history.push(self._snapshot())
        self.dirty = not self.history.is_saved()
        self.modified.emit(source)
        self.state_changed.emit()

    # ------------------------------------------------------------ undo / redo
    def _snapshot(self):
        """The project's contents (not its file name or generator tag)."""
        data = self.project.to_data()
        data.pop("generator", None)
        return data

    def can_undo(self):
        return self.history.can_undo()

    def can_redo(self):
        return self.history.can_redo()

    def undo(self):
        return self._restore(self.history.undo())

    def redo(self):
        return self._restore(self.history.redo())

    def _restore(self, state):
        if state is None:
            return False
        old = self.project
        proj = Project.from_data(dict(state, format=state.get("format")), old.path)
        proj.generator = old.generator
        self.project = proj
        self.dirty = not self.history.is_saved()
        self.modified.emit("undo")
        self.state_changed.emit()
        return True

    # ------------------------------------------------------------ IP definitions
    def put_ip(self, ip, source="", old_name=None):
        """Add an IP definition, or replace the one named `old_name` (default: ip.name). A rename
        is followed by the address-map instances of the IP. Returns True if one was replaced."""
        ip.base = 0
        old = self.project.get_ip(old_name or ip.name)
        if old_name and old_name != ip.name:
            other = self.project.get_ip(ip.name)
            if other is not None and other is not old:
                self.project.ips.remove(other)
            for inst in self.project.instances_of(old_name):
                inst.ip = ip.name
                # instances named after the IP follow the rename: OLD_0 -> NEW_0 (OLD -> NEW)
                m = re.fullmatch(re.escape(old_name) + r"(_\d+)?", inst.name)
                if m:
                    new_inst = ip.name + (m.group(1) or "")
                    if not any(i.name.lower() == new_inst.lower() for i in self.project.all_instances()):
                        old_inst = inst.name
                        inst.name = new_inst
                        rename_interface_settings(self.project, old_inst, new_inst)
        if old:
            self.project.ips[self.project.ips.index(old)] = ip
        else:
            self.project.ips.append(ip)
        self.mark_modified(source)
        return old is not None

    def remove_ip(self, name, source=""):
        """Remove an IP definition and all of its address-map instances."""
        self.project.ips = [x for x in self.project.ips if x.name != name]
        self.project.remove_instances(lambda i: not i.bridge and i.ip == name)
        self.mark_modified(source)

    def unique_ip_name(self, stem):
        if not self.project.get_ip(stem):
            return stem
        k = 2
        while self.project.get_ip(f"{stem}_v{k}"):
            k += 1
        return f"{stem}_v{k}"

    # ------------------------------------------------------------ address map
    def address_ips(self):
        """One IPBlock per IP instance in the address map (instance name and base)."""
        return self.project.address_ips()

    def add_instance(self, ip_name, name=None, source=""):
        """Place IP definition `ip_name` in the address map (once more); returns the instance."""
        inst = addrmap.add_instance(self.project, ip_name, name)
        self.mark_modified(source)
        return inst

    # ------------------------------------------------------------ IP libraries
    def _abs(self, path):
        if os.path.isabs(path) or not self.path:
            return path
        return os.path.normpath(os.path.join(os.path.dirname(self.path), path))

    def _rel(self, path):
        if not self.path:
            return os.path.abspath(path)
        try:
            return os.path.relpath(path, os.path.dirname(self.path))
        except ValueError:          # other drive (Windows)
            return os.path.abspath(path)

    def _remember_open(self):
        QSettings().setValue("libraries/open", [lib.path for lib in self.libraries if lib.path])

    def _open_library_quiet(self, path):
        path = os.path.abspath(path)
        if any(os.path.abspath(lib.path) == path for lib in self.libraries if lib.path):
            return next(lib for lib in self.libraries if lib.path and os.path.abspath(lib.path) == path)
        try:
            lib = IPLibrary.load(path)
        except Exception as exc:  # noqa: BLE001 - reported by the library tool
            self.library_errors.append(f"{path}: {exc}")
            return None
        self.libraries.append(lib)
        self._remember_open()
        return lib

    def open_library(self, path):
        lib = self._open_library_quiet(path)
        if lib is None:
            raise ValueError(self.library_errors[-1])
        self.libraries_changed.emit()
        return lib

    def new_library(self, path, name):
        lib = IPLibrary(name, path)
        self.save_library(lib)
        self.libraries.append(lib)
        self._remember_open()
        self.libraries_changed.emit()
        return lib

    def close_library(self, lib):
        self.libraries = [x for x in self.libraries if x is not lib]
        self._remember_open()
        self.libraries_changed.emit()

    def save_library(self, lib):
        """Libraries are written to disk after every change."""
        from axil_studio import APP_NAME, version_text
        lib.save(generator=f"{APP_NAME} {version_text()}")
        self.libraries_changed.emit()

    def use_library(self, lib):
        """Record that this project uses `lib` (re-opened with the project)."""
        rel = self._rel(lib.path)
        if rel not in self.project.libraries:
            self.project.libraries.append(rel)
            self.mark_modified("library")

    def import_from_library(self, lib, entry, name=None, source="library"):
        """Copy a library IP into the project's IP definitions (no address: it is placed with
        add_instance). Returns the definition's name in the project."""
        ip = entry.definition(name)
        ip.source = lib.name
        self.put_ip(ip, source)
        self.use_library(lib)
        return ip.name

    def store_in_library(self, lib, ip, regspec=None):
        """Put a project IP (and its register spec when known) into a library."""
        stored = copy.deepcopy(ip)
        stored.base, stored.regspec, stored.ref = 0, None, ""
        keep = list(lib.entries)
        replaced = lib.put(LibEntry(stored, regspec if regspec is not None else copy.deepcopy(ip.regspec)))
        try:
            self.save_library(lib)
        except Exception:
            lib.entries = keep          # the library stays as it is on disk
            raise
        return replaced

    # ------------------------------------------------------------ recent files
    @staticmethod
    def recent():
        items = QSettings().value("project/recent", []) or []
        if isinstance(items, str):
            items = [items]
        return [p for p in items if os.path.exists(p)]

    def _push_recent(self, path):
        if not path:
            return
        path = os.path.abspath(path)
        items = [p for p in self.recent() if p != path]
        QSettings().setValue("project/recent", [path] + items[:MAX_RECENT - 1])
