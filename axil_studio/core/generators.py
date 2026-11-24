"""HDL generators: a separate application per kind of IP that turns meaningful settings (clock, baud
rate, line type, number of pins ...) into the IP's parameter values. AXIL Studio then writes
<instance>_pk.v: the IP with packed AXI ports and no parameters (no generics for a VHDL top), the
values built in, one module per instance so two instances can have different settings.

An IP definition names its generator (`IPBlock.hdl_gen`, kept in the project and in IP libraries).
The application lives in a folder with an `axil-generator.json` manifest:

    {"format": "axil-generator", "protocol": 1, "id": "axiluart_phy", "version": "1.0.0",
     "title": {"en": "UART PHY generator", "tr": "UART PHY üreteci"}, "desc": "...",
     "module": "axiluart_phy", "source": "../../hdl/v2005/uart_phy/axiluart_phy.v",
     "command": ["{python}", "gen.py"]}

`module` is the Verilog module the block wraps; `source` its file (relative to the manifest). The
generators are looked for where the IP interfaces are (interfaces.search_folders: settings folders,
library folders) and in <AXIL Studio>/generators.

Protocol (the application is started in its folder, generators/sdk on PYTHONPATH):
    <command> --in IN.json --out OUT.json [--batch] [--lang tr]
IN:  {"name": "UART1", "ip": "axiluart_phy", "base": 327680, "settings": {...}}  (settings {} = defaults)
OUT: {"settings": {...}, "params": {"INITIAL_SETUP": "31'd434", ...}, "summary": "115200 8N1 ..."}
With --batch the application computes OUT from the settings without a window (Generate all).
Exit code 0 = OUT written; the window's Cancel exits with 3 and writes nothing; other codes are
errors (stderr says why).

This module has no Qt.
"""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List

from axil_studio import ROOT
from axil_studio.i18n import tr

MANIFEST = "axil-generator.json"
FORMAT = "axil-generator"
PROTOCOL = 1
BUILTIN_DIR = os.path.join(ROOT, "generators")
SDK_DIR = os.path.join(BUILTIN_DIR, "sdk")
CANCELLED = 3


class GeneratorError(ValueError):
    pass


@dataclass
class Generator:
    id: str
    title: object
    module: str
    source: str                 # absolute path of the module's Verilog file
    command: List[str]
    folder: str
    desc: object = ""
    version: str = ""
    protocol: int = PROTOCOL
    shadowed: List[str] = field(default_factory=list)

    @staticmethod
    def _text(v, lang):
        if isinstance(v, dict):
            return v.get(lang) or v.get("en") or next(iter(v.values()), "")
        return str(v or "")

    def label(self, lang="en"):
        return self._text(self.title, lang) or self.id

    def description(self, lang="en"):
        return self._text(self.desc, lang)

    def argv(self):
        out = []
        for i, part in enumerate(self.command):
            part = str(part).replace("{python}", sys.executable).replace("{folder}", self.folder)
            if i > 0 and not os.path.isabs(part) and os.path.exists(os.path.join(self.folder, part)):
                part = os.path.join(self.folder, part)
            out.append(part)
        return out


def load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise GeneratorError(f"{path}: not an HDL generator manifest")
    gid, cmd = str(d.get("id", "")).strip(), d.get("command")
    module, source = str(d.get("module", "")).strip(), str(d.get("source", "")).strip()
    if not gid or not module or not source or not isinstance(cmd, list) or not cmd:
        raise GeneratorError(f"{path}: 'id', 'module', 'source' and 'command' (a list) are required")
    folder = os.path.dirname(os.path.abspath(path))
    src = source if os.path.isabs(source) else os.path.normpath(os.path.join(folder, source))
    return Generator(gid, d.get("title") or gid, module, src, [str(c) for c in cmd], folder,
                     d.get("desc", ""), str(d.get("version", "")), int(d.get("protocol", PROTOCOL)))


def _manifests(folder, depth=2):
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    if os.path.isfile(os.path.join(folder, MANIFEST)):
        found.append(os.path.join(folder, MANIFEST))
    if depth > 0:
        try:
            subs = sorted(e.path for e in os.scandir(folder) if e.is_dir() and not e.name.startswith((".", "__")))
        except OSError:
            subs = []
        for sub in subs:
            found += _manifests(sub, depth - 1)
    return found


def discover(settings_folders=(), library_paths=()):
    """({id: Generator}, [errors]): the IP interface folders, the library folders, then the built-in ones."""
    from axil_studio import interfaces
    folders = [f for f, _ in interfaces.search_folders(settings_folders, library_paths)
               if os.path.normcase(os.path.abspath(f)) != os.path.normcase(os.path.abspath(interfaces.BUILTIN_DIR))]
    folders.append(BUILTIN_DIR)
    found: Dict[str, Generator] = {}
    errors = []
    for folder in folders:
        for path in _manifests(folder):
            try:
                g = load_manifest(path)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(str(exc))
                continue
            if g.id in found:
                found[g.id].shadowed.append(g.folder)
            else:
                found[g.id] = g
    return found, errors


# ----------------------------------------------------------------------------- project side
def module_name(name):
    """The block of an instance / block called `name`: <name>_pk (lower case)."""
    return f"{name.lower()}_pk"


def blocks(project):
    """{name: entry} of the project's HDL generator settings (created when missing)."""
    if project.hdl_gen is None:
        project.hdl_gen = {}
    return project.hdl_gen.setdefault("blocks", {})


def rows(project):
    """[(name, generator id, kind)] the HDL Generator lists: address-map instances whose IP has a
    generator ("instance"), then the blocks added by hand ("block", e.g. a bus master)."""
    out = []
    for inst, _ in project.walk():
        d = project.definition_of(inst)
        if d is not None and getattr(d, "hdl_gen", "") and not inst.bridge:
            out.append((inst.name, d.hdl_gen, "instance"))
    names = {n for n, _, _ in out}
    for name, e in sorted((project.hdl_gen or {}).get("blocks", {}).items()):
        if e.get("free") and name not in names:
            out.append((name, e.get("generator", ""), "block"))
    return out


def instance_base(project, name):
    inst = project.get_instance(name)
    return inst.base if inst is not None else 0


def app_command(gen, infile, outfile, batch=False, lang="en"):
    """(argv, env) to start the generator application."""
    argv = gen.argv() + ["--in", infile, "--out", outfile] + (["--batch"] if batch else []) + ["--lang", lang]
    env = dict(os.environ)
    env["PYTHONPATH"] = SDK_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return argv, env


def request(project, name, gen):
    e = blocks(project).get(name, {})
    return {"name": name, "ip": gen.module, "base": instance_base(project, name),
            "settings": dict(e.get("settings") or {})}


def run_batch(gen, req, timeout=60, lang="en"):
    """Run the application without a window; returns its OUT data (GeneratorError on failure)."""
    with tempfile.TemporaryDirectory(prefix="axilgen_") as tmp:
        fin, fout = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
        with open(fin, "w", encoding="utf-8") as fh:
            json.dump(req, fh)
        argv, env = app_command(gen, fin, fout, True, lang)
        try:
            r = subprocess.run(argv, cwd=gen.folder, env=env, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GeneratorError(tr("{0}: could not run the generator: {1}").format(req["name"], exc)) from exc
        if r.returncode != 0 or not os.path.isfile(fout):
            raise GeneratorError(tr("{0}: the generator failed: {1}").format(
                req["name"], (r.stderr or r.stdout).strip()[-500:] or r.returncode))
        return read_result(fout)


def read_result(path):
    with open(path, encoding="utf-8") as fh:
        out = json.load(fh)
    if not isinstance(out, dict) or not isinstance(out.get("params"), dict):
        raise GeneratorError(tr("The generator gave no parameter values"))
    return out


def store(project, name, gen_id, result, free=False):
    """Keep a generator's result in the project (settings, params, summary)."""
    e = blocks(project).setdefault(name, {})
    e.update({"generator": gen_id, "settings": result.get("settings") or {},
              "params": {k: str(v) for k, v in result["params"].items()},
              "summary": str(result.get("summary", ""))})
    if free:
        e["free"] = True
    return e


def block_text(gen, name, params, summary=""):
    """<name>_pk.v of the module with the parameter values built in."""
    from . import axilpack
    with open(gen.source, encoding="utf-8", errors="replace") as fh:
        info = axilpack.parse_module(fh.read(), gen.module)
    text = axilpack.gen_pack_wrapper(info, module_name(name), params)
    note = f"// HDL Generator ({gen.id}): block of {name}" + (f": {summary}" if summary else "") + "\n"
    return text.replace("// HDL Designer block:", note + "// HDL Designer block:", 1)


def write_block(gen, name, params, outdir, summary=""):
    path = os.path.join(outdir, module_name(name) + ".v")
    text = block_text(gen, name, params, summary)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def generate_all(project, gens, outdir, names=None, lang="en"):
    """Run every listed block's generator in batch mode with its stored settings (defaults when it
    has none), store the result and write <name>_pk.v. Returns ([paths], [errors])."""
    paths, errors = [], []
    for name, gid, kind in rows(project):
        if names is not None and name not in names:
            continue
        gen = gens.get(gid)
        if gen is None:
            errors.append(tr("{0}: generator '{1}' not found").format(name, gid))
            continue
        try:
            res = run_batch(gen, request(project, name, gen), lang=lang)
            e = store(project, name, gid, res, free=(kind == "block"))
            paths.append(write_block(gen, name, e["params"], outdir, e.get("summary", "")))
        except (GeneratorError, OSError, ValueError) as exc:
            errors.append(str(exc))
    return paths, errors
