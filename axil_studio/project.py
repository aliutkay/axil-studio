"""Project shared by every tool in the hub.

The common file is the existing debug project (.axproj.json, model.Project):
  * Slave Interface Wrapper adds / replaces the IPs it generates,
  * Address Map & Interconnect Configurator builds the slave windows from it,
  * UART-to-AXI Debug Bridge reads and writes its registers on the board.
"""

import os

from PySide6.QtCore import QObject, QSettings, Signal

from library import IPLibrary, LibEntry
from model import Project

PROJECT_FILTER = "AXIL proje (*.axproj.json);;JSON (*.json);;Tüm dosyalar (*)"
MAX_RECENT = 8


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
        self.project = Project("Adsız proje")
        self.dirty = False
        self.libraries = []          # open IPLibrary objects (shared by every project)
        self.library_errors = []
        for path in QSettings().value("libraries/open", []) or []:
            self._open_library_quiet(path)

    # ------------------------------------------------------------ properties
    @property
    def path(self):
        return self.project.path

    @property
    def display_name(self):
        return self.project.name + (" •" if self.dirty else "")

    # ------------------------------------------------------------ file ops
    def new(self, name="Adsız proje"):
        self.project = Project(name)
        self.dirty = False
        self.replaced.emit()
        self.state_changed.emit()

    def open(self, path):
        self.project = Project.load(path)
        self.dirty = False
        self._push_recent(path)
        for rel in self.project.libraries:          # libraries this project uses
            self._open_library_quiet(self._abs(rel))
        self.libraries_changed.emit()
        self.replaced.emit()
        self.state_changed.emit()

    def save(self, path=None):
        from axil_studio import APP_NAME, version_text
        self.project.generator = f"{APP_NAME} {version_text()}"
        self.project.save(path)
        self.dirty = False
        self._push_recent(self.project.path)
        self.state_changed.emit()

    # ------------------------------------------------------------ edits
    def mark_modified(self, source=""):
        self.dirty = True
        self.modified.emit(source)
        self.state_changed.emit()

    def upsert_ip(self, ip, source=""):
        """Add the IP, or replace the one with the same name. Returns True if replaced."""
        old = self.project.get_ip(ip.name)
        if old:
            self.project.ips[self.project.ips.index(old)] = ip
        else:
            self.project.ips.append(ip)
        self.mark_modified(source)
        return old is not None

    # ------------------------------------------------------------ address map
    def suggest_base(self, name, window, min_base=0x40000000):
        """Base for IP `name`: its current base if it is already in the project, otherwise the
        first window-aligned address after the IPs already placed."""
        ip = self.project.get_ip(name)
        if ip:
            return ip.base
        top = min_base
        for other in self.project.ips:
            span = max((r.offset for r in other.registers), default=0) + 4
            w = 0x1000
            while w < span:
                w <<= 1
            top = max(top, other.base + w)
        return (top + window - 1) & ~(window - 1) & 0xFFFFFFFF

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

    def library_of(self, name):
        return next((lib for lib in self.libraries if lib.name == name), None)

    def use_library(self, lib):
        """Record that this project uses `lib` (re-opened with the project)."""
        rel = self._rel(lib.path)
        if rel not in self.project.libraries:
            self.project.libraries.append(rel)
            self.mark_modified("library")

    def add_from_library(self, lib, entry, base, name=None):
        ip = entry.instance(base, name)
        ip.source = lib.name
        replaced = self.upsert_ip(ip, "library")
        self.use_library(lib)
        return replaced

    def store_in_library(self, lib, ip, regspec=None):
        """Put a project IP (and its register spec when known) into a library."""
        import copy
        replaced = lib.put(LibEntry(copy.deepcopy(ip), regspec))
        self.save_library(lib)
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
