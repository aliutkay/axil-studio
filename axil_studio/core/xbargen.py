"""
axilxbar wrapper generator.

ZipCPU's axilxbar (hdl/v2005/interconnect/axilxbar.v) is used as it is: it is only configured through its
parameters (NM, NS, SLAVE_ADDR, SLAVE_MASK, OPT_LOWPOWER, OPT_LINGER, LGMAXBURST, data and
address width). axilxbar exposes every master/slave port as one flattened vector, which is
awkward in block-diagram tools, so two wrappers are generated around it:

    <module>.v       one named AXI4-Lite interface per port, every signal separate:
                       MASTER<i>_AXI_*   connect to master i (its M_AXI_*)
                       SLAVE<j>_AXI_*    connect to slave j  (its S_AXI_*)
                     generics SLAVE<j>_ADDR / SLAVE<j>_MASK are handed to axilxbar unchanged.
    <module>_pk.v    (optional) HDL Designer block around <module>: per port one packed
                     request/response pair, MASTER<i>_AXI_REQ/RSP and SLAVE<j>_AXI_REQ/RSP.

The wrappers only rename and slice signals; they add no logic. Addresses that no slave
decodes are answered with DECERR by axilxbar itself.

Required RTL from wb2axip, in hdl/v2005: interconnect/axilxbar.v, interconnect/addrdecode.v,
common/skidbuffer.v. They differ from wb2axip only in `localparam` written as `parameter` (same
values) and the addrdecode NS = 1 fix, so plain Verilog-2005 tools (HDL Designer) accept them.
The generated wrappers have no `localparam` either (AW / DW / SW are body parameters, local to
the module because it has a parameter list).
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import List

from . import axilpack as PK
from .model import hex32, parse_int, write_json
from axil_studio.i18n import tr

# (suffix, direction at the wrapper's MASTER<i> ports, width key)
AXIL_SIGNALS = (
    ("AWADDR", "in", "AW"), ("AWPROT", "in", 3), ("AWVALID", "in", 1), ("AWREADY", "out", 1),
    ("WDATA", "in", "DW"), ("WSTRB", "in", "SW"), ("WVALID", "in", 1), ("WREADY", "out", 1),
    ("BRESP", "out", 2), ("BVALID", "out", 1), ("BREADY", "in", 1),
    ("ARADDR", "in", "AW"), ("ARPROT", "in", 3), ("ARVALID", "in", 1), ("ARREADY", "out", 1),
    ("RDATA", "out", "DW"), ("RRESP", "out", 2), ("RVALID", "out", 1), ("RREADY", "in", 1),
)
WIDTH_EXPR = {"AW": "C_AXI_ADDR_WIDTH", "DW": "C_AXI_DATA_WIDTH", "SW": "C_AXI_DATA_WIDTH/8"}
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
STYLES = {
    "packed": tr("Separate AXI ports + packed wrapper for HDL Designer (<name>_pk.v: MASTER0_AXI_REQ/RSP, SLAVE0_AXI_REQ/RSP)"),
    "signals": tr("Separate AXI ports only (MASTER0_AXI_AWADDR …, 19 signals per port)"),
}


@dataclass
class XbarSlave:
    """One slave port; every port is in use (the ports come from the project's address map)."""
    name: str = "slave"       # label only (comments, docs)
    base: int = 0
    mask: int = 0xFFFFF000
    span: int = 0             # bytes used by the IP (checked against the window)
    external: bool = False    # not an IP of the project (address entered by hand)
    fixed: bool = False       # keeps its base when the map is auto-addressed

    @property
    def window(self):
        return ((~self.mask) & 0xFFFFFFFF) + 1


@dataclass
class XbarMaster:
    name: str = "master"


@dataclass
class XbarSpec:
    module: str = "axil_xbar"
    masters: List[XbarMaster] = field(default_factory=lambda: [XbarMaster("hexbus")])
    slaves: List[XbarSlave] = field(default_factory=list)
    style: str = "packed"     # signals: <module>.v only (19 named signals per port);
                              # packed: also <module>_pk.v (MASTER<i>/SLAVE<j>_AXI_REQ/RSP per port)

    @property
    def packed(self):
        return self.style == "packed"

    @property
    def nm(self):
        return len(self.masters)

    @property
    def ns(self):
        return len(self.slaves)

    def validate(self):
        errs = []
        if self.style not in STYLES:
            errs.append(tr("Unknown port style: {0}").format(self.style))
        from .axilgen import RESERVED_MODULES, VERILOG_KEYWORDS, VHDL_KEYWORDS
        if not IDENT.match(self.module or ""):
            errs.append(tr("Module name is not a valid Verilog identifier"))
        elif self.module.lower() in VERILOG_KEYWORDS or self.module.lower() in VHDL_KEYWORDS:
            errs.append(tr("Module name '{0}': cannot be a Verilog/VHDL keyword").format(self.module))
        elif "__" in self.module or self.module.endswith("_"):
            errs.append(tr("{0} '{1}': must not contain '__' or end with '_' (VHDL compatibility)").format(tr("Module name"), self.module))
        elif self.module.lower() in RESERVED_MODULES or self.module.lower().removesuffix("_pk") in RESERVED_MODULES:
            errs.append(tr("Module name '{0}': clashes with ZipCPU {1}.v").format(self.module, self.module.lower().removesuffix("_pk")))
        if not 1 <= self.nm <= 16:
            errs.append(tr("Masters must be 1..16"))
        if not 1 <= self.ns <= 64:
            errs.append(tr("Slaves must be 1..64"))
        for i, s in enumerate(self.slaves):
            tag = f"SLAVE{i} ({s.name})"
            if not 0 <= s.base <= 0xFFFFFFFF:
                errs.append(tr("{0}: base address must fit in 32 bits").format(tag))
            if not 0 < s.mask <= 0xFFFFFFFC:
                errs.append(tr("{0}: mask must be contiguous (e.g. 0xFFFFF000)").format(tag))
                continue
            inv = (~s.mask) & 0xFFFFFFFF
            if inv & (inv + 1):
                errs.append(tr("{0}: mask must be contiguous (e.g. 0xFFFFF000)").format(tag))
            if s.base & inv:
                errs.append(tr("{0}: base {1} is not aligned to its window ({2:#x})").format(tag, hex32(s.base), s.window))
            if s.span > s.window:
                errs.append(tr("{0}: the IP uses {1:#x} bytes, the window is {2:#x}").format(tag, s.span, s.window))
            for j in range(i):
                o = self.slaves[j]
                if not ((s.base ^ o.base) & s.mask & o.mask):
                    errs.append(tr("{0} overlaps SLAVE{1} ({2})").format(tag, j, o.name))
        return errs

    # ------------------------------------------------------------ persistence
    def to_dict(self):
        return {"format": "axil-xbar", "version": 1, "module": self.module, "style": self.style,
                "masters": [asdict(m) for m in self.masters],
                "slaves": [{**asdict(s), "base": hex32(s.base), "mask": hex32(s.mask), "span": f"0x{s.span:X}"}
                           for s in self.slaves]}

    @staticmethod
    def from_dict(d):
        """Older files may hold unused ports ("enabled": false); they are left out."""
        return XbarSpec(d.get("module", "axil_xbar"),
                        [XbarMaster(m.get("name", "master")) for m in d["masters"] if m.get("enabled", True)],
                        [XbarSlave(s.get("name", "slave"), parse_int(s["base"]), parse_int(s["mask"]),
                                   parse_int(s.get("span", 0)), bool(s.get("external", False)),
                                   bool(s.get("fixed", False)))
                         for s in d["slaves"] if s.get("enabled", True)],
                        "signals" if d.get("style") == "signals" or (not d.get("style") and not d.get("packed"))
                        else "packed")

    def save(self, path):
        write_json(path, self.to_dict(), ensure_ascii=True)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return XbarSpec.from_dict(json.load(fh))


def slaves_from_project(project, min_window=0x1000):
    """Slave ports of a project's address map (see addrmap.py)."""
    from . import addrmap
    return addrmap.xbar_slaves(project)


def auto_address(spec, start=0x00000000):
    """Give every non-fixed slave a base address in port order: SLAVE0 first, each
    window aligned to its size, skipping the windows of fixed slaves. Returns the list of
    (port, old_base, new_base) that changed."""
    fixed = [(s.base, s.base + s.window) for s in spec.slaves if s.fixed]
    cur, changed = start & 0xFFFFFFFF, []
    for j, s in enumerate(spec.slaves):
        if s.fixed:
            continue
        w = s.window
        base = (cur + w - 1) & ~(w - 1)
        moved = True
        while moved:
            moved = False
            for lo, hi in fixed:
                if base < hi and base + w > lo:
                    base = (hi + w - 1) & ~(w - 1)
                    moved = True
        if base + w > 1 << 32:
            raise ValueError(tr("SLAVE{0} ({1}) does not fit in the 32-bit address space").format(j, s.name))
        if base != s.base:
            changed.append((j, s.base, base))
            s.base = base
        cur = base + w
    return changed


def xbar_ports(spec):
    """[(direction, range, name)] of the generated wrapper's ports, read from its header.
    Works for an unfinished spec too (no slave yet, overlaps, bad name): the port list does not
    depend on addresses, so a valid stand-in with the same port counts and style is used."""
    import copy
    probe = copy.deepcopy(spec)
    if probe.validate():
        probe.module = "axil_xbar"
        if probe.style not in STYLES:
            probe.style = "packed"
        if not probe.slaves:
            probe.slaves.append(XbarSlave("slave0"))
        elif len(probe.slaves) > 64:
            probe.slaves = probe.slaves[:64]
        if not probe.masters:
            probe.masters.append(XbarMaster("master0"))
        elif len(probe.masters) > 16:
            probe.masters = probe.masters[:16]
        for j, sl in enumerate(probe.slaves):
            sl.base, sl.mask, sl.span = j * 0x1000, 0xFFFFF000, 0
    v = gen_xbar_pk(probe) if probe.packed else gen_xbar(probe)
    head = v[:v.index(");") + 2]
    return [(d, (w or "").strip(), n) for d, w, n in
            re.findall(r"^\s*(input|output)\s+wire\s*(\[[^\]]+\])?\s*(\w+)", head, re.M)]


# ============================================================================ Verilog
def _w(key):
    if key == 1:
        return ""
    if isinstance(key, int):
        return f"[{key - 1}:0]"
    return f"[{WIDTH_EXPR[key]}-1:0]"


def _wn(key):
    """Width of one signal as an expression (for slicing)."""
    if isinstance(key, int):
        return str(key)
    return {"AW": "AW", "DW": "DW", "SW": "SW"}[key]


def _h32(v):
    return f"32'h{v >> 16:04X}_{v & 0xFFFF:04X}"


def _header(spec, title, lines):
    L = ["////////////////////////////////////////////////////////////////////////////////",
         f"// Module: {title}", "//"] + [f"// {x}" if x else "//" for x in lines]
    L += ["//", f"// {spec.nm} master port(s), {spec.ns} slave port(s). Address map",
          "// (SLAVE<j>_ADDR / SLAVE<j>_MASK defaults):",
          "//   Port     Base        Mask        Window"]
    for j, s in enumerate(spec.slaves):
        L.append(f"//   {'SLAVE' + str(j):<9}{hex32(s.base)}  {hex32(s.mask)}  {s.window:#9x}")
    return L


def _slave_generics(spec, width):
    out = []
    for j, s in enumerate(spec.slaves):
        out.append(f"    parameter [{width}-1:0] SLAVE{j}_ADDR = {_h32(s.base)},")
        out.append(f"    parameter [{width}-1:0] SLAVE{j}_MASK = {_h32(s.mask)},")
    return out


def gen_xbar(spec: XbarSpec, opt_lowpower=1, opt_linger=4, lgmaxburst=5):
    """<module>.v: axilxbar configured by parameters, every port as separate AXI4-Lite signals."""
    errs = spec.validate()
    if errs:
        raise ValueError("\n".join(errs))
    nm, ns, mod = spec.nm, spec.ns, spec.module
    L = _header(spec, mod, [
        f"AXI4-Lite crossbar, {nm} master port(s) x {ns} slave port(s): ZipCPU axilxbar,",
        "configured only through its parameters. This wrapper adds no logic; it gives",
        "every port its own named AXI4-Lite signals:",
        "  MASTER<i>_AXI_*  connect to master i (its M_AXI_*)",
        "  SLAVE<j>_AXI_*   connect to slave j  (its S_AXI_*)",
        "Slave j is selected when (address & SLAVE<j>_MASK) == SLAVE<j>_ADDR; any other",
        "address is answered with DECERR by axilxbar."])
    L += ["//",
          "// Requires hdl/v2005/interconnect/axilxbar.v, addrdecode.v and",
          "// hdl/v2005/common/skidbuffer.v (wb2axip, Apache 2.0, Gisselquist Technology LLC).",
          "// Verilog-2005, no localparam.",
          "////////////////////////////////////////////////////////////////////////////////",
          "",
          "`default_nettype none",
          "",
          f"module {mod} #(",
          "    parameter integer C_AXI_ADDR_WIDTH = 32,",
          "    parameter integer C_AXI_DATA_WIDTH = 32,"]
    L += _slave_generics(spec, "C_AXI_ADDR_WIDTH")
    L += [f"    parameter [0:0]   OPT_LOWPOWER = 1'b{opt_lowpower},",
          f"    parameter integer OPT_LINGER   = {opt_linger},",
          f"    parameter integer LGMAXBURST   = {lgmaxburst}",
          ") (",
          "    input  wire ACLK,",
          "    input  wire ARESETN,"]
    ports = []
    for i, m in enumerate(spec.masters):
        ports.append(f"    // Master port {i}")
        for sig, d, wk in AXIL_SIGNALS:
            ports.append(f"    {'input ' if d == 'in' else 'output'} wire {_w(wk):<26}MASTER{i}_AXI_{sig},")
    for j, s in enumerate(spec.slaves):
        ports.append(f"    // Slave port {j}")
        for sig, d, wk in AXIL_SIGNALS:
            ports.append(f"    {'output' if d == 'in' else 'input '} wire {_w(wk):<26}SLAVE{j}_AXI_{sig},")
    ports[-1] = ports[-1].rstrip(",")
    L += ports
    L += [");",
          "",
          "    // fixed shorthands (body parameters are local: the module has a parameter list)",
          "    parameter integer AW = C_AXI_ADDR_WIDTH;",
          "    parameter integer DW = C_AXI_DATA_WIDTH;",
          "    parameter integer SW = C_AXI_DATA_WIDTH/8;",
          "",
          "    // axilxbar's flattened ports: port k in bits [k*width +: width]"]
    for sig, _, wk in AXIL_SIGNALS:
        L.append(f"    wire [{nm}*{_wn(wk)}-1:0] s_{sig.lower()};")
    for sig, _, wk in AXIL_SIGNALS:
        L.append(f"    wire [{ns}*{_wn(wk)}-1:0] m_{sig.lower()};")
    L.append("")

    def sl(idx, wk):
        w = _wn(wk)
        return f"[{idx}*{w} +: {w}]" if w != "1" else f"[{idx}]"

    for i, m in enumerate(spec.masters):
        L.append(f"    // MASTER{i}")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"    assign s_{sig.lower()}{sl(i, wk)} = MASTER{i}_AXI_{sig};")
            else:
                L.append(f"    assign MASTER{i}_AXI_{sig} = s_{sig.lower()}{sl(i, wk)};")
        L.append("")
    for j, s in enumerate(spec.slaves):
        L.append(f"    // SLAVE{j}")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"    assign SLAVE{j}_AXI_{sig} = m_{sig.lower()}{sl(j, wk)};")
            else:
                L.append(f"    assign m_{sig.lower()}{sl(j, wk)} = SLAVE{j}_AXI_{sig};")
        L.append("")

    rev = list(reversed(range(ns)))
    L += ["    axilxbar #(",
          "        .C_AXI_DATA_WIDTH (C_AXI_DATA_WIDTH),",
          "        .C_AXI_ADDR_WIDTH (C_AXI_ADDR_WIDTH),",
          f"        .NM               ({nm}),",
          f"        .NS               ({ns}),",
          "        // { slave NS-1, ..., slave 0 }",
          "        .SLAVE_ADDR       ({ " + ", ".join(f"SLAVE{j}_ADDR" for j in rev) + " }),",
          "        .SLAVE_MASK       ({ " + ", ".join(f"SLAVE{j}_MASK" for j in rev) + " }),",
          "        .OPT_LOWPOWER     (OPT_LOWPOWER),",
          "        .OPT_LINGER       (OPT_LINGER),",
          "        .LGMAXBURST       (LGMAXBURST)",
          "    ) u_axilxbar (",
          "        .S_AXI_ACLK    (ACLK),",
          "        .S_AXI_ARESETN (ARESETN),"]
    conns = [f"        .S_AXI_{sig:<8}(s_{sig.lower()})," for sig, _, _ in AXIL_SIGNALS]
    conns += [f"        .M_AXI_{sig:<8}(m_{sig.lower()})," for sig, _, _ in AXIL_SIGNALS]
    conns[-1] = conns[-1].rstrip(",")
    L += conns
    L += ["    );", "", "endmodule", "", "`default_nettype wire", ""]
    return "\n".join(L)


def gen_xbar_pk(spec: XbarSpec, opt_lowpower=1, opt_linger=4, lgmaxburst=5):
    """<module>_pk.v: HDL Designer block around <module>: one packed REQ/RSP pair per port."""
    errs = spec.validate()
    if errs:
        raise ValueError("\n".join(errs))
    nm, ns, mod = spec.nm, spec.ns, spec.module
    name = f"{mod}_pk"
    L = _header(spec, name, [
        f"HDL Designer block around {mod} (AXI4-Lite crossbar on ZipCPU axilxbar). Every",
        "port is one packed request/response pair; the block only packs and unpacks",
        "signals:",
        "  MASTER<i>_AXI_REQ/RSP  connect to a master's MASTER_AXI_REQ/RSP",
        "  SLAVE<j>_AXI_REQ/RSP   connect to a slave's  SLAVE_AXI_REQ/RSP",
        "Packed bus: 32-bit address and data."] + [x[3:] for x in PK.layout_lines()])
    L += ["//",
          f"// Requires {mod}.v, hdl/v2005/interconnect/axilxbar.v, addrdecode.v and",
          "// hdl/v2005/common/skidbuffer.v. Verilog-2005, no localparam.",
          "////////////////////////////////////////////////////////////////////////////////",
          "",
          "`default_nettype none",
          "",
          f"module {name} #("]
    L += _slave_generics(spec, "32")
    L += [f"    parameter [0:0]   OPT_LOWPOWER = 1'b{opt_lowpower},",
          f"    parameter integer OPT_LINGER   = {opt_linger},",
          f"    parameter integer LGMAXBURST   = {lgmaxburst}",
          ") (",
          "    input  wire ACLK,",
          "    input  wire ARESETN,"]
    ports = []
    for i, m in enumerate(spec.masters):
        ports.append(f"    // Master port {i}")
        ports.append(f"    input  wire [{PK.REQ_W - 1}:0] MASTER{i}_AXI_REQ,")
        ports.append(f"    output wire [{PK.RSP_W - 1}:0]  MASTER{i}_AXI_RSP,")
    for j, s in enumerate(spec.slaves):
        ports.append(f"    // Slave port {j}")
        ports.append(f"    output wire [{PK.REQ_W - 1}:0] SLAVE{j}_AXI_REQ,")
        ports.append(f"    input  wire [{PK.RSP_W - 1}:0]  SLAVE{j}_AXI_RSP,")
    ports[-1] = ports[-1].rstrip(",")
    L += ports + [");", ""]
    req = lambda p: ", ".join(f"{p}_AXI_{n}" for n, _ in PK.REQ_FIELDS)
    rsp = lambda p: ", ".join(f"{p}_AXI_{n}" for n, _ in PK.RSP_FIELDS)
    width = dict(PK.REQ_FIELDS + PK.RSP_FIELDS)
    pnames = [f"MASTER{i}" for i in range(nm)] + [f"SLAVE{j}" for j in range(ns)]
    for p in pnames:
        L.append(f"    // {p}: packed <-> signals")
        for sig, _, _ in AXIL_SIGNALS:
            w = width[sig]
            L.append(f"    wire {f'[{w - 1}:0]' if w > 1 else '':<8}{p}_AXI_{sig};")
        if p.startswith("MASTER"):
            L.append(f"    assign {{{req(p)}}} = {p}_AXI_REQ;")
            L.append(f"    assign {p}_AXI_RSP = {{{rsp(p)}}};")
        else:
            L.append(f"    assign {p}_AXI_REQ = {{{req(p)}}};")
            L.append(f"    assign {{{rsp(p)}}} = {p}_AXI_RSP;")
        L.append("")
    L += [f"    {mod} #(",
          "        .C_AXI_ADDR_WIDTH (32),",
          "        .C_AXI_DATA_WIDTH (32),"]
    for j in range(ns):
        L.append(f"        .SLAVE{j}_ADDR      (SLAVE{j}_ADDR),")
        L.append(f"        .SLAVE{j}_MASK      (SLAVE{j}_MASK),")
    L += ["        .OPT_LOWPOWER     (OPT_LOWPOWER),",
          "        .OPT_LINGER       (OPT_LINGER),",
          "        .LGMAXBURST       (LGMAXBURST)",
          "    ) u_xbar (",
          "        .ACLK    (ACLK),",
          "        .ARESETN (ARESETN),"]
    conns = [f"        .{p}_AXI_{sig:<8}({p}_AXI_{sig})," for p in pnames for sig, _, _ in AXIL_SIGNALS]
    conns[-1] = conns[-1].rstrip(",")
    L += conns + ["    );", "", "endmodule", "", "`default_nettype wire", ""]
    return "\n".join(L)


def gen_xbar_markdown(spec: XbarSpec):
    L = [f"# {spec.module}", "",
         f"AXI4-Lite crossbar (ZipCPU axilxbar), {spec.nm} master port(s) x {spec.ns} slave port(s).", "",
         "| Port | Name | Base | Mask | Window | Used |", "|---|---|---|---|---|---|"]
    for i, m in enumerate(spec.masters):
        L.append(f"| MASTER{i} | {m.name} | | | | |")
    for j, s in enumerate(spec.slaves):
        L.append(f"| SLAVE{j} | {s.name} | {hex32(s.base)} | {hex32(s.mask)} | {s.window:#x} | "
                 f"{f'0x{s.span:X}' if s.span else ''} |")
    L += ["", f"`{spec.module}.v`: master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`."]
    if spec.packed:
        L.append(f"`{spec.module}_pk.v` (HDL Designer): `MASTER<i>_AXI_REQ/RSP`, `SLAVE<j>_AXI_REQ/RSP`.")
    L += ["Move a slave by changing `SLAVE<j>_ADDR` / `SLAVE<j>_MASK`; unmapped addresses return DECERR.",
          "Keep the project's address map (Address Map Editor) in sync.", ""]
    return "\n".join(L)


def output_names(spec: XbarSpec):
    """Names of the files write_xbar() writes, in order."""
    m = spec.module
    return [f"{m}.v"] + ([f"{m}_pk.v"] if spec.packed else []) + [f"{m}.md", f"{m}.hdlspec.json"]


def write_xbar(spec: XbarSpec, outdir):
    """<module>.v and <module>_pk.v (packed style), made from the HDL spec alone (hdlspec.py:
    port counts, slave addresses and masks); <module>.md (with the port names) and
    <module>.hdlspec.json, the HDL spec itself."""
    from . import hdlspec as H
    d = H.xbar_hdlspec(spec)
    files = dict(H.verilog(d))
    files[f"{spec.module}.md"] = gen_xbar_markdown(spec)
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for name, text in files.items():
        p = os.path.join(outdir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        paths.append(p)
    p = os.path.join(outdir, H.file_name(d))
    H.save(d, p)
    return paths + [p]
