"""IP interfaces: separate applications made for one kind of IP, started from the UART-to-AXI
Debug Bridge and connected to it through a local service.

An IP definition names its interface by id in its register map (`IPBlock.panel`, kept in the project
and in IP libraries). The application itself lives anywhere: its folder holds an `axil-interface.json`
manifest

    {"format": "axil-interface", "protocol": 1, "id": "axiluart_phy", "version": "1.0.0",
     "title": {"en": "UART interface", "tr": "UART arayüzü"}, "desc": "...",
     "command": ["{python}", "uart_ui.py"]}

`command` is started in the manifest's folder; "{python}" is the Python that runs AXIL Studio (any
other program works too: the interface talks to Studio over a TCP socket on 127.0.0.1, see
interfaces/README.md for the protocol and interfaces/sdk/axil_client.py for the Python client).

Where interfaces are looked for, first found wins for an id:
    1. the folders in the settings (IP interface folders), so a version under development wins
    2. the folders of the open IP libraries (a library shipped with its interface)
    3. <AXIL Studio>/interfaces (the interfaces that come with AXIL Studio)
Each folder is searched together with its sub-folders, two levels deep.

This module has no Qt; host.py holds the service and the launcher.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List

from axil_studio import ROOT

MANIFEST = "axil-interface.json"
FORMAT = "axil-interface"
PROTOCOL = 1
BUILTIN_DIR = os.path.join(ROOT, "interfaces")
SDK_DIR = os.path.join(BUILTIN_DIR, "sdk")
SETTINGS_KEY = "interfaces/folders"


class ManifestError(ValueError):
    pass


@dataclass
class Interface:
    id: str
    title: object               # str or {"en": ..., "tr": ...}
    command: List[str]
    folder: str
    desc: object = ""
    version: str = ""
    protocol: int = PROTOCOL
    source: str = ""            # "settings" | "library" | "builtin"
    shadowed: List[str] = field(default_factory=list)    # folders of other copies with the same id

    @staticmethod
    def _text(v, lang):
        if isinstance(v, dict):
            return v.get(lang) or v.get("en") or next(iter(v.values()), "")
        return str(v or "")

    def label(self, lang="en"):
        return self._text(self.title, lang) or self.id

    def description(self, lang="en"):
        return self._text(self.desc, lang)

    @property
    def path(self):
        return os.path.join(self.folder, MANIFEST)

    def argv(self):
        """The command with "{python}" replaced and relative script paths made absolute."""
        out = []
        for i, part in enumerate(self.command):
            part = str(part).replace("{python}", sys.executable).replace("{folder}", self.folder)
            if i > 0 and not os.path.isabs(part) and os.path.exists(os.path.join(self.folder, part)):
                part = os.path.join(self.folder, part)
            out.append(part)
        return out


def load_manifest(path, source=""):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if d.get("format") != FORMAT:
        raise ManifestError(f"{path}: not an IP interface manifest")
    iid = str(d.get("id", "")).strip()
    cmd = d.get("command")
    if not iid or not isinstance(cmd, list) or not cmd:
        raise ManifestError(f"{path}: 'id' and 'command' (a list) are required")
    proto = int(d.get("protocol", PROTOCOL))
    return Interface(iid, d.get("title") or iid, [str(c) for c in cmd], os.path.dirname(os.path.abspath(path)),
                     d.get("desc", ""), str(d.get("version", "")), proto, source)


def _manifests(folder, depth=2):
    """Manifest files in `folder` and its sub-folders (`depth` levels)."""
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    path = os.path.join(folder, MANIFEST)
    if os.path.isfile(path):
        found.append(path)
    if depth > 0:
        try:
            subs = sorted(e.path for e in os.scandir(folder) if e.is_dir() and not e.name.startswith((".", "__")))
        except OSError:
            subs = []
        for sub in subs:
            found += _manifests(sub, depth - 1)
    return found


def search_folders(settings_folders=(), library_paths=()):
    """[(folder, source)] in search order, without duplicates."""
    out, seen = [], set()
    items = [(f, "settings") for f in settings_folders]
    items += [(os.path.dirname(os.path.abspath(p)), "library") for p in library_paths if p]
    items.append((BUILTIN_DIR, "builtin"))
    for folder, src in items:
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen:
            seen.add(key)
            out.append((folder, src))
    return out


def discover(settings_folders=(), library_paths=()):
    """{id: Interface}; returns also the manifest errors found on the way: (found, errors)."""
    found: Dict[str, Interface] = {}
    errors = []
    for folder, src in search_folders(settings_folders, library_paths):
        for path in _manifests(folder):
            try:
                it = load_manifest(path, src)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
                continue
            if it.id in found:
                found[it.id].shadowed.append(it.folder)
            else:
                found[it.id] = it
    return found, errors
