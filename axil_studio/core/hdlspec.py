"""HDL specs: the only input of the Verilog generators.

AXIL Studio works in two parts:

    part 1  tools / command line  ->  HDL spec        (what the user designed, reduced to what the
                                                        code needs)
    part 2  HDL spec              ->  Verilog files    (this module's generate(); nothing else read)

An HDL spec holds only data that changes the generated Verilog. Names that only appear in comments
or documents (master / slave names, descriptions, value meanings, display formats, IP version) are
not part of it; neither is an address the code does not contain.

IP wrapper (<module>.hdlspec.json, "axil-ip-hdlspec"):
    module                  Verilog module name (ports are named after registers and fields)
    hdl_designer_wrapper    also <module>_pk.v (SLAVE_AXI_REQ/RSP)
    registers[]             name, offset, wr_strobe, rd_strobe,
      fields[]              name, lsb, width, kind (RW RO PULSE W1C CONST), reset (RW / CONST),
                            irq (W1C), sync (RO)

Interconnect (<module>.hdlspec.json, "axil-xbar-hdlspec"):
    module                  Verilog module name
    hdl_designer_wrapper    also <module>_pk.v (MASTER<i>/SLAVE<j>_AXI_REQ/RSP)
    masters                 number of master ports (NM)
    slaves[]                addr, mask of every slave port (NS = their count)
    parameters              OPT_LOWPOWER, OPT_LINGER, LGMAXBURST handed to axilxbar

Verilog from an HDL spec file, with no project and no tool:
    python -m axil_studio.core.hdlspec my_ip.hdlspec.json -o out
"""

import json
import os

from . import axilgen as G
from . import axilpack
from . import xbargen as X
from .model import hex32, parse_int, write_json
from axil_studio.i18n import tr

IP_FORMAT = "axil-ip-hdlspec"
XBAR_FORMAT = "axil-xbar-hdlspec"
FORMAT_VERSION = 1
XBAR_PARAMETERS = {"OPT_LOWPOWER": 1, "OPT_LINGER": 4, "LGMAXBURST": 5}     # gen_xbar defaults


class HdlSpecError(ValueError):
    pass


# ============================================================================ part 1: spec -> HDL spec
def ip_hdlspec(spec: "G.ModuleSpec"):
    """The HDL spec of a register map (axilgen.ModuleSpec): registers, fields and their kinds."""
    regs = []
    for r in sorted(spec.registers, key=lambda r: r.offset):
        fields = [{"name": f.name, "lsb": f.lsb, "width": f.width, "kind": f.kind,
                   "reset": f"0x{f.reset:X}", "irq": bool(f.irq), "sync": bool(f.sync)}
                  for f in sorted(r.fields, key=lambda f: f.lsb)]
        regs.append({"name": r.name, "offset": f"0x{r.offset:02X}", "wr_strobe": bool(r.wr_strobe),
                     "rd_strobe": bool(r.rd_strobe), "fields": fields})
    return {"format": IP_FORMAT, "version": FORMAT_VERSION, "module": spec.module,
            "hdl_designer_wrapper": bool(spec.packed), "registers": regs}


def xbar_hdlspec(spec: "X.XbarSpec", parameters=None):
    """The HDL spec of a crossbar (xbargen.XbarSpec): port counts, slave addresses and masks."""
    return {"format": XBAR_FORMAT, "version": FORMAT_VERSION, "module": spec.module,
            "hdl_designer_wrapper": spec.packed, "masters": spec.nm,
            "slaves": [{"addr": hex32(s.base), "mask": hex32(s.mask)} for s in spec.slaves],
            "parameters": dict(XBAR_PARAMETERS, **(parameters or {}))}


def project_xbar_specs(project, style=None):
    """One XbarSpec per crossbar of a project, top first (the address map is laid out first; a
    subsystem with a single slave and no extra master has none)."""
    from . import addrmap
    try:
        addrmap.readdress(project)
    except ValueError:
        pass                    # reported by addrmap.validate()
    return [addrmap.xbar_spec(project, b, style) for b in addrmap.xbar_levels(project)]


def project_hdlspecs(project):
    """Every HDL spec of a project: one per IP definition, one per interconnect."""
    return {"ips": [ip_hdlspec(G.spec_from_ipblock(ip)) for ip in project.ips],
            "interconnects": [xbar_hdlspec(s) for s in project_xbar_specs(project)]}


def ip_base(project, ip_name):
    """Address of a project IP for its C header and Excel: its first address-map instance (0 when
    not placed). Not part of any HDL spec: the IP's Verilog does not depend on it."""
    insts = project.instances_of(ip_name)
    return insts[0].base if insts else 0


# ============================================================================ files
def kind_of(d):
    if d.get("format") == IP_FORMAT:
        return "ip"
    if d.get("format") == XBAR_FORMAT:
        return "xbar"
    raise HdlSpecError(tr("This file is not an HDL spec (axil-ip-hdlspec / axil-xbar-hdlspec)"))


def load(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    kind_of(d)
    return d


def save(d, path):
    kind_of(d)
    write_json(path, d, ensure_ascii=True)


def file_name(d):
    return f"{d['module']}.hdlspec.json"


# ============================================================================ part 2: HDL spec -> Verilog
def _ip_spec(d):
    """axilgen.ModuleSpec holding exactly the HDL spec (nothing else for the generator to see)."""
    regs = []
    for r in d.get("registers", []):
        fields = [G.FieldSpec(str(f["name"]), int(f["lsb"]), int(f.get("width", 1)), str(f.get("kind", "RW")),
                              parse_int(f.get("reset", 0)), "", bool(f.get("irq", False)),
                              bool(f.get("sync", False)))
                  for f in r.get("fields", [])]
        regs.append(G.RegSpec(str(r["name"]), parse_int(r["offset"]), "", bool(r.get("wr_strobe", False)),
                              bool(r.get("rd_strobe", False)), fields))
    return G.ModuleSpec(str(d.get("module", "")), "", regs, bool(d.get("hdl_designer_wrapper", True)), "")


def _xbar_spec(d):
    """xbargen.XbarSpec holding exactly the HDL spec (ports are numbered, not named)."""
    n = int(d.get("masters", 1))
    return X.XbarSpec(str(d.get("module", "")), [X.XbarMaster(f"master{i}") for i in range(n)],
                      [X.XbarSlave(f"slave{j}", parse_int(s["addr"]), parse_int(s["mask"]))
                       for j, s in enumerate(d.get("slaves", []))],
                      "packed" if d.get("hdl_designer_wrapper", True) else "signals")


def validate(d):
    """Errors that keep the HDL spec from becoming Verilog (empty: usable)."""
    try:
        kind = kind_of(d)
        if kind == "ip":
            return _ip_spec(d).validate(hdl_only=True)
        errs = _xbar_spec(d).validate()
        p = dict(XBAR_PARAMETERS, **d.get("parameters", {}))
        if set(p) != set(XBAR_PARAMETERS):
            errs.append(tr("Unknown parameter: {0}").format(', '.join(sorted(set(p) - set(XBAR_PARAMETERS)))))
        return errs
    except (KeyError, TypeError, ValueError) as exc:
        return [tr("Cannot read the HDL spec: {0}").format(exc)]


def verilog_names(d):
    """Names of the Verilog files generate() makes, in order."""
    m = d["module"]
    pk = d.get("hdl_designer_wrapper", True)
    if kind_of(d) == "ip":
        return [f"{m}.v"] + ([f"{m}_pk.v"] if pk else []) + [f"{m}_inst.v"] + \
            ([f"{m}_pk_inst.v"] if pk else []) + [f"{m}_tb.v"]
    return [f"{m}.v"] + ([f"{m}_pk.v"] if pk else [])


def verilog(d, only=None):
    """{file name: Verilog text} of an HDL spec (or the text of file `only`). Raises HdlSpecError
    when the HDL spec is not usable."""
    errs = validate(d)
    if errs:
        raise HdlSpecError("\n".join(errs))
    if kind_of(d) == "ip":
        spec = _ip_spec(d)
        m = spec.module
        files = {f"{m}.v": lambda: G.gen_verilog(spec)}
        if spec.packed:
            files[f"{m}_pk.v"] = lambda: G.hdl_designer_files(axilpack.parse_module(G.gen_verilog(spec), m))[f"{m}_pk.v"]
        files[f"{m}_inst.v"] = lambda: G.gen_inst(spec)
        if spec.packed:
            files[f"{m}_pk_inst.v"] = lambda: G.hdl_designer_files(
                axilpack.parse_module(G.gen_verilog(spec), m))[f"{m}_pk_inst.v"]
        files[f"{m}_tb.v"] = lambda: G.gen_testbench(spec)
    else:
        spec = _xbar_spec(d)
        p = dict(XBAR_PARAMETERS, **d.get("parameters", {}))
        args = (int(p["OPT_LOWPOWER"]), int(p["OPT_LINGER"]), int(p["LGMAXBURST"]))
        files = {f"{spec.module}.v": lambda: X.gen_xbar(spec, *args)}
        if spec.packed:
            files[f"{spec.module}_pk.v"] = lambda: X.gen_xbar_pk(spec, *args)
    if only is not None:
        return files[only]()
    return {name: make() for name, make in files.items()}


def generate(d, outdir, with_spec=True):
    """Write the Verilog files of an HDL spec (and the HDL spec itself); returns the paths."""
    texts = verilog(d)
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for name, text in texts.items():
        p = os.path.join(outdir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        paths.append(p)
    if with_spec:
        p = os.path.join(outdir, file_name(d))
        save(d, p)
        paths.append(p)
    return paths


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m axil_studio.core.hdlspec",
                                 description="Verilog from an HDL spec file (axil-ip-hdlspec / axil-xbar-hdlspec)")
    ap.add_argument("hdlspec", help="<module>.hdlspec.json")
    ap.add_argument("-o", "--outdir", default="hdl_out")
    ap.add_argument("--check", action="store_true", help="only validate the HDL spec")
    a = ap.parse_args(argv)
    try:
        d = load(a.hdlspec)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    errs = validate(d)
    if errs:
        raise SystemExit("\n".join("error: " + e for e in errs))
    if a.check:
        print("ok")
        return
    for p in generate(d, a.outdir, with_spec=False):
        print(p)


if __name__ == "__main__":
    main()
