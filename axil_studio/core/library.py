"""IP libraries: reusable IP register maps shared between projects.

A library is a JSON file (.axlib.json) holding IP definitions without an address. An IP can
be kept in several versions (name + version is the key). Each entry
keeps the register map (model.IPBlock form, as the debug bridge and the interconnect use it)
and, when the IP came from the wrapper generator, its full register spec (axilgen.ModuleSpec
form) so the HDL can be generated again. Several libraries can be open at once and any number
of projects can refer to the same library file.
"""

import copy
import datetime
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

from .model import IPBlock, write_json
from axil_studio.i18n import tr

LIBRARY_FORMAT = "axil-ip-library"


@dataclass
class LibEntry:
    ip: IPBlock                      # register map (no address: placed in a project's address map)
    regspec: Optional[dict] = None   # axilgen.ModuleSpec.to_dict() when known
    updated: str = ""

    @property
    def name(self):
        return self.ip.name

    @property
    def version(self):
        return self.ip.version or "1.0.0"

    def to_dict(self):
        ip = self.ip.to_dict(with_base=False)
        ip.pop("regspec", None)
        d = {"ip": ip, "updated": self.updated}
        if self.regspec:
            d["regspec"] = self.regspec
        return d

    @staticmethod
    def from_dict(d):
        return LibEntry(IPBlock.from_dict(d["ip"]), d.get("regspec"), d.get("updated", ""))

    def definition(self, name=None):
        """A copy of the IP (register map + spec) for a project; it gets its addresses in the
        project's address map."""
        ip = copy.deepcopy(self.ip)
        ip.base = 0
        ip.regspec = copy.deepcopy(self.regspec)
        if name:
            ip.name = name
            if ip.regspec:
                ip.regspec["module"] = name
        return ip


@dataclass
class IPLibrary:
    name: str = tr("New library")
    path: Optional[str] = None
    desc: str = ""
    entries: List[LibEntry] = field(default_factory=list)

    def versions(self, name):
        """Entries of one IP, newest version first."""
        from .axilgen import version_key
        return sorted((e for e in self.entries if e.name == name), key=lambda e: version_key(e.version), reverse=True)

    def get(self, name, version=None) -> Optional[LibEntry]:
        """The given version of an IP, or its newest version."""
        vs = self.versions(name)
        if version is None:
            return vs[0] if vs else None
        return next((e for e in vs if e.version == version), None)

    def names(self):
        return sorted({e.name for e in self.entries}, key=str.lower)

    def put(self, entry: LibEntry):
        """Add a version, or replace the same name + version; returns True when it replaced one."""
        entry.updated = datetime.date.today().isoformat()
        if not entry.ip.version:
            entry.ip.version = "1.0.0"
        old = self.get(entry.name, entry.version)
        if old:
            self.entries[self.entries.index(old)] = entry
        else:
            self.entries.append(entry)
        return old is not None

    def remove(self, name, version=None):
        self.entries = [e for e in self.entries if not (e.name == name and (version is None or e.version == version))]

    def save(self, path=None, generator=""):
        self.path = path or self.path
        data = {"format": LIBRARY_FORMAT, "version": 1, "name": self.name, "desc": self.desc,
                "entries": [e.to_dict() for e in self.entries]}
        if generator:
            data["generator"] = generator
        write_json(self.path, data)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != LIBRARY_FORMAT:
            raise ValueError(tr("This file is not an IP library"))
        return IPLibrary(data.get("name") or os.path.basename(path).split(".")[0], path, data.get("desc", ""),
                         [LibEntry.from_dict(e) for e in data.get("entries", [])])
