"""
axilxbar wrapper generator.

ZipCPU's axilxbar exposes every master/slave port as one flattened vector,
which is awkward in block-diagram tools (HDL Designer bundles, SmartDesign).
This module generates a wrapper with one named AXI4-Lite interface per port:

    MASTER<i>_AXI_*   connect to master i
    SLAVE<j>_AXI_*    connect to slave j

Capacity (number of S/M ports) is chosen per project. Every port has an
enable generic (MASTER<i>_EN / SLAVE<j>_EN), and every slave port has SLAVE<j>_ADDR /
SLAVE<j>_MASK generics, so the address map and the set of used ports can be
changed in the block diagram without regenerating. A disabled slave port is
terminated inside the wrapper by a responder that answers DECERR; a disabled
master port is tied idle. Unused ports may be left unconnected.

Required RTL from wb2axip: axilxbar.v (Verilog-2005 patch: `localparam AW` in
its parameter list changed to `parameter`), addrdecode.v, skidbuffer.v
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import List

from . import axilpack as PK
from .model import hex32, parse_int

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
    "packed": "HDL Designer için paketli, port başına (MASTER0_AXI_REQ/RSP, SLAVE0_AXI_REQ/RSP)",
    "flat": "HDL Designer için paketli düz vektör (MASTERS_AXI_REQ[NM*111-1:0], NM/NS generic)",
    "signals": "Ayrık sinyaller (MASTER0_AXI_AWADDR …, 19 sinyal/port)",
}
# value driven into the crossbar by an unused master port
IDLE_MASTER = {"BREADY": "1'b1", "RREADY": "1'b1"}


@dataclass
class XbarSlave:
    name: str = "spare"       # label only (comments, docs)
    base: int = 0
    mask: int = 0xFFFFF000
    span: int = 0             # bytes used by the IP (checked against the window)
    enabled: bool = True
    external: bool = False    # not an IP of the project (address entered by hand)
    fixed: bool = False       # keeps its base when the map is auto-addressed

    @property
    def window(self):
        return ((~self.mask) & 0xFFFFFFFF) + 1


@dataclass
class XbarMaster:
    name: str = "master"
    enabled: bool = True


@dataclass
class XbarSpec:
    module: str = "axil_xbar"
    masters: List[XbarMaster] = field(default_factory=lambda: [XbarMaster("hexbus")])
    slaves: List[XbarSlave] = field(default_factory=list)
    style: str = "packed"     # packed: MASTER<i>/SLAVE<j>_AXI_REQ/RSP, flat: one REQ/RSP vector per side
                              # (NM/NS generics), signals: 19 named signals per port

    @property
    def packed(self):
        return self.style == "packed"

    @property
    def nm(self):
        return len(self.masters)

    @property
    def ns(self):
        return len(self.slaves)

    def resize(self, nm=None, ns=None):
        """Change capacity: adds disabled ports, removes only trailing disabled ones."""
        if nm is not None:
            while self.nm < nm:
                self.masters.append(XbarMaster(f"master{self.nm}", False))
            while self.nm > max(nm, 1) and not self.masters[-1].enabled:
                self.masters.pop()
        if ns is not None:
            while self.ns < ns:
                self.slaves.append(XbarSlave(f"spare{self.ns}", 0, 0xFFFFF000, 0, False))
            while self.ns > max(ns, 1) and not self.slaves[-1].enabled:
                self.slaves.pop()

    def validate(self):
        errs = []
        if self.style not in STYLES:
            errs.append(f"Bilinmeyen port stili: {self.style}")
        if not IDENT.match(self.module or ""):
            errs.append("Modül adı geçerli bir Verilog tanımlayıcısı değil")
        if not 1 <= self.nm <= 16:
            errs.append("Master kapasitesi 1..16 olmalı")
        if not 1 <= self.ns <= 64:
            errs.append("Slave kapasitesi 1..64 olmalı")
        if not any(m.enabled for m in self.masters):
            errs.append("En az bir master etkin olmalı")
        if not any(s.enabled for s in self.slaves):
            errs.append("En az bir slave etkin olmalı")
        for i, s in enumerate(self.slaves):
            inv = (~s.mask) & 0xFFFFFFFF
            tag = f"SLAVE{i} ({s.name})"
            if inv & (inv + 1):
                errs.append(f"{tag}: maske bitişik olmalı (ör. 0xFFFFF000)")
            if not s.enabled:
                continue
            if s.base & inv:
                errs.append(f"{tag}: base {hex32(s.base)} pencereye ({s.window:#x}) hizalı değil")
            if s.span > s.window:
                errs.append(f"{tag}: IP {s.span:#x} byte kullanıyor, pencere {s.window:#x}")
            for j in range(i):
                o = self.slaves[j]
                if o.enabled and not ((s.base ^ o.base) & s.mask & o.mask):
                    errs.append(f"{tag} ile SLAVE{j} ({o.name}) adres aralıkları çakışıyor")
        return errs

    # ------------------------------------------------------------ persistence
    def to_dict(self):
        return {"format": "axil-xbar", "version": 1, "module": self.module, "style": self.style,
                "masters": [asdict(m) for m in self.masters],
                "slaves": [{**asdict(s), "base": hex32(s.base), "mask": hex32(s.mask), "span": f"0x{s.span:X}"}
                           for s in self.slaves]}

    @staticmethod
    def from_dict(d):
        return XbarSpec(d.get("module", "axil_xbar"),
                        [XbarMaster(m.get("name", "master"), bool(m.get("enabled", True))) for m in d["masters"]],
                        [XbarSlave(s.get("name", "spare"), parse_int(s["base"]), parse_int(s["mask"]),
                                   parse_int(s.get("span", 0)), bool(s.get("enabled", True)),
                                   bool(s.get("external", False)), bool(s.get("fixed", False)))
                         for s in d["slaves"]],
                        d.get("style") or ("packed" if d.get("packed", False) else "signals"))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return XbarSpec.from_dict(json.load(fh))


def window_mask(span, min_window=0x1000):
    w = max(min_window, 4)
    while w < span:
        w <<= 1
    return (~(w - 1)) & 0xFFFFFFFF


def slaves_from_project(project, min_window=0x1000):
    """Slave ports of a project's address map (see addrmap.py)."""
    from . import addrmap
    return addrmap.xbar_slaves(project)


def auto_address(spec, start=0x40000000):
    """Give every enabled, non-fixed slave a base address in port order: SLAVE0 first, each
    window aligned to its size, skipping the windows of fixed slaves. Returns the list of
    (port, old_base, new_base) that changed."""
    fixed = [(s.base, s.base + s.window) for s in spec.slaves if s.enabled and s.fixed]
    cur, changed = start & 0xFFFFFFFF, []
    for j, s in enumerate(spec.slaves):
        if not s.enabled or s.fixed:
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
            raise ValueError(f"SLAVE{j} ({s.name}) 32 bit adres alanına sığmıyor")
        if base != s.base:
            changed.append((j, s.base, base))
            s.base = base
        cur = base + w
    return changed


def sort_by_address(spec):
    """Port order = address order; spare ports go to the end."""
    used = sorted((s for s in spec.slaves if s.enabled), key=lambda s: s.base)
    spare = [s for s in spec.slaves if not s.enabled]
    spec.slaves = used + spare
    for j, s in enumerate(spec.slaves):
        if not s.enabled:
            s.name = f"spare{j}"


def xbar_ports(spec):
    """[(direction, range, name)] of the generated wrapper's ports, read from its header.
    Works for an unfinished spec too (no slave yet, overlaps, bad name): the port list does not
    depend on addresses or EN, so a valid stand-in with the same port counts and style is used."""
    import copy
    probe = copy.deepcopy(spec)
    if probe.validate():
        if not IDENT.match(probe.module or ""):
            probe.module = "axil_xbar"
        for m in probe.masters:
            m.enabled = True
        if not probe.slaves:
            probe.slaves.append(XbarSlave("spare0"))
        if not probe.masters:
            probe.masters.append(XbarMaster("master0"))
        for j, sl in enumerate(probe.slaves):
            sl.enabled, sl.base, sl.mask, sl.span = True, j * 0x1000, 0xFFFFF000, 0
    v = gen_xbar_flat(probe) if probe.style == "flat" else gen_xbar(probe)
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


def _null_module(name):
    return f"""
// DECERR responder for disabled slave ports.
module {name} #(
    parameter integer DW = 32
) (
    input  wire          ACLK,
    input  wire          ARESETN,
    input  wire          AWVALID,
    output wire          AWREADY,
    input  wire          WVALID,
    output wire          WREADY,
    output reg           BVALID,
    input  wire          BREADY,
    output wire [1:0]    BRESP,
    input  wire          ARVALID,
    output wire          ARREADY,
    output reg           RVALID,
    input  wire          RREADY,
    output wire [DW-1:0] RDATA,
    output wire [1:0]    RRESP
);

    // Accept address and data together, one response outstanding
    assign AWREADY = AWVALID && WVALID && (!BVALID || BREADY);
    assign WREADY  = AWREADY;
    assign ARREADY = !RVALID || RREADY;

    always @(posedge ACLK)
        if (!ARESETN)
            BVALID <= 1'b0;
        else if (AWREADY)
            BVALID <= 1'b1;
        else if (BREADY)
            BVALID <= 1'b0;

    always @(posedge ACLK)
        if (!ARESETN)
            RVALID <= 1'b0;
        else if (ARVALID && ARREADY)
            RVALID <= 1'b1;
        else if (RREADY)
            RVALID <= 1'b0;

    assign BRESP = 2'b11;
    assign RRESP = 2'b11;
    assign RDATA = {{DW{{1'b0}}}};

endmodule
"""


def gen_xbar(spec: XbarSpec, opt_lowpower=1, opt_linger=4, lgmaxburst=5):
    errs = spec.validate()
    if errs:
        raise ValueError("\n".join(errs))
    nm, ns, mod = spec.nm, spec.ns, spec.module
    null = f"{mod}_null"
    L = ["////////////////////////////////////////////////////////////////////////////////",
         f"// Module: {mod}",
         "//",
         f"// AXI4-Lite crossbar with {nm} master port(s) and {ns} slave port(s), built",
         "// on ZipCPU axilxbar. One named interface per port:"]
    if spec.packed:
        L += ["//   MASTER<i>_AXI_REQ/RSP  connect to a master's MASTER_AXI_REQ/RSP",
              "//   SLAVE<j>_AXI_REQ/RSP   connect to a slave's  SLAVE_AXI_REQ/RSP",
              "// Packed bus: 32-bit address and data."] + PK.layout_lines()
    else:
        L += ["//   MASTER<i>_AXI_*  connect to a master's M_AXI_*",
              "//   SLAVE<j>_AXI_*   connect to a slave's  S_AXI_*"]
    L += [
         "//",
         "// Generics per port:",
         "//   MASTER<i>_EN                  1: port in use, 0: tied idle",
         "//   SLAVE<j>_EN                   1: port in use, 0: answered internally with DECERR",
         "//   SLAVE<j>_ADDR, SLAVE<j>_MASK  slave j is selected when (addr & MASK) == ADDR",
         "// Unused ports may be left unconnected. Unmapped addresses return DECERR.",
         "//",
         "// Default map:",
         "//   Port     EN  Base        Mask        Window    IP"]
    for i, m in enumerate(spec.masters):
        L.append(f"//   {'MASTER' + str(i):<9}{int(m.enabled):<4}{'':24}{'':10}{m.name}")
    for j, s in enumerate(spec.slaves):
        L.append(f"//   {'SLAVE' + str(j):<9}{int(s.enabled):<4}{hex32(s.base)}  {hex32(s.mask)}  {s.window:#9x}  {s.name}")
    L += ["//",
          "// Requires axilxbar.v, addrdecode.v, skidbuffer.v (wb2axip, Apache 2.0,",
          "// Gisselquist Technology LLC). Verilog-2005.",
          "////////////////////////////////////////////////////////////////////////////////",
          "",
          "`default_nettype none",
          "",
          f"module {mod} #(",
          "    parameter integer C_AXI_ADDR_WIDTH = 32,",
          "    parameter integer C_AXI_DATA_WIDTH = 32,"]
    for i, m in enumerate(spec.masters):
        L.append(f"    parameter [0:0] MASTER{i}_EN = 1'b{int(m.enabled)},{'':22}// {m.name}")
    for j, s in enumerate(spec.slaves):
        L.append(f"    parameter [0:0] SLAVE{j}_EN = 1'b{int(s.enabled)},{'':22}// {s.name}")
        L.append(f"    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE{j}_ADDR = {_h32(s.base)},")
        L.append(f"    parameter [C_AXI_ADDR_WIDTH-1:0] SLAVE{j}_MASK = {_h32(s.mask)},")
    L += [f"    parameter [0:0]  OPT_LOWPOWER = 1'b{opt_lowpower},",
          f"    parameter integer OPT_LINGER   = {opt_linger},",
          f"    parameter integer LGMAXBURST   = {lgmaxburst}",
          ") (",
          "    input  wire ACLK,",
          "    input  wire ARESETN,"]
    ports = []
    for i, m in enumerate(spec.masters):
        ports.append(f"    // Master port {i}: {m.name}")
        if spec.packed:
            ports.append(f"    input  wire [{PK.REQ_W - 1}:0] MASTER{i}_AXI_REQ,")
            ports.append(f"    output wire [{PK.RSP_W - 1}:0]  MASTER{i}_AXI_RSP,")
            continue
        for sig, d, wk in AXIL_SIGNALS:
            ports.append(f"    {'input ' if d == 'in' else 'output'} wire {_w(wk):<26}MASTER{i}_AXI_{sig},")
    for j, s in enumerate(spec.slaves):
        ports.append(f"    // Slave port {j}: {s.name}")
        if spec.packed:
            ports.append(f"    output wire [{PK.REQ_W - 1}:0] SLAVE{j}_AXI_REQ,")
            ports.append(f"    input  wire [{PK.RSP_W - 1}:0]  SLAVE{j}_AXI_RSP,")
            continue
        for sig, d, wk in AXIL_SIGNALS:
            ports.append(f"    {'output' if d == 'in' else 'input '} wire {_w(wk):<26}SLAVE{j}_AXI_{sig},")
    ports[-1] = ports[-1].rstrip(",")
    L += ports
    L += [");", ""]
    if spec.packed:
        req = lambda p: ", ".join(f"{p}_AXI_{n}" for n, _ in PK.REQ_FIELDS)
        rsp = lambda p: ", ".join(f"{p}_AXI_{n}" for n, _ in PK.RSP_FIELDS)
        wdecl = lambda key: _w(key)
        for p in [f"MASTER{i}" for i in range(nm)] + [f"SLAVE{j}" for j in range(ns)]:
            L.append(f"    // {p}: packed <-> signals")
            for sig, _, wk in AXIL_SIGNALS:
                L.append(f"    wire {wdecl(wk):<26}{p}_AXI_{sig};")
            if p.startswith("MASTER"):
                L.append(f"    assign {{{req(p)}}} = {p}_AXI_REQ;")
                L.append(f"    assign {p}_AXI_RSP = {{{rsp(p)}}};")
            else:
                L.append(f"    assign {p}_AXI_REQ = {{{req(p)}}};")
                L.append(f"    assign {{{rsp(p)}}} = {p}_AXI_RSP;")
            L.append("")
    L += [
          "    localparam integer AW = C_AXI_ADDR_WIDTH;",
          "    localparam integer DW = C_AXI_DATA_WIDTH;",
          "    localparam integer SW = C_AXI_DATA_WIDTH/8;",
          "",
          "    // Flattened crossbar ports"]
    for sig, _, wk in AXIL_SIGNALS:
        L.append(f"    wire [{nm}*{_wn(wk)}-1:0] s_{sig.lower()};")
    for sig, _, wk in AXIL_SIGNALS:
        L.append(f"    wire [{ns}*{_wn(wk)}-1:0] m_{sig.lower()};")
    L.append("")

    def sl(idx, wk):
        w = _wn(wk)
        return f"[{idx}*{w} +: {w}]" if w != "1" else f"[{idx}]"

    # ---------------------------------------------------------------- masters
    L.append("    // Master ports")
    L.append("    generate")
    for i in range(nm):
        L.append(f"        if (MASTER{i}_EN) begin : g_s{i}")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"            assign s_{sig.lower()}{sl(i, wk)} = MASTER{i}_AXI_{sig};")
            else:
                L.append(f"            assign MASTER{i}_AXI_{sig} = s_{sig.lower()}{sl(i, wk)};")
        L.append(f"        end else begin : g_s{i}_off")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"            assign s_{sig.lower()}{sl(i, wk)} = {IDLE_MASTER.get(sig, '0')};")
            else:
                L.append(f"            assign MASTER{i}_AXI_{sig} = 0;")
        L.append("        end")
    L.append("    endgenerate")
    L.append("")

    # ---------------------------------------------------------------- slaves
    L.append("    // Slave ports")
    L.append("    generate")
    for j in range(ns):
        L.append(f"        if (SLAVE{j}_EN) begin : g_m{j}")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"            assign SLAVE{j}_AXI_{sig} = m_{sig.lower()}{sl(j, wk)};")
            else:
                L.append(f"            assign m_{sig.lower()}{sl(j, wk)} = SLAVE{j}_AXI_{sig};")
        L.append(f"        end else begin : g_m{j}_off")
        for sig, d, wk in AXIL_SIGNALS:
            if d == "in":
                L.append(f"            assign SLAVE{j}_AXI_{sig} = 0;")
        conn = {"AWVALID": "m_awvalid", "AWREADY": "m_awready", "WVALID": "m_wvalid", "WREADY": "m_wready",
                "BVALID": "m_bvalid", "BREADY": "m_bready", "BRESP": "m_bresp", "ARVALID": "m_arvalid",
                "ARREADY": "m_arready", "RVALID": "m_rvalid", "RREADY": "m_rready", "RDATA": "m_rdata",
                "RRESP": "m_rresp"}
        wk_of = {s: w for s, _, w in AXIL_SIGNALS}
        L.append(f"            {null} #(.DW(DW)) u_null (")
        L.append("                .ACLK(ACLK), .ARESETN(ARESETN),")
        items = [f".{p}({n}{sl(j, wk_of[p])})" for p, n in conn.items()]
        for k in range(0, len(items), 3):
            tail = "," if k + 3 < len(items) else ""
            L.append("                " + ", ".join(items[k:k + 3]) + tail)
        L.append("            );")
        L.append("        end")
    L.append("    endgenerate")
    L.append("")

    rev = list(reversed(range(ns)))
    L += ["    // A disabled slave port gets an all-ones address/mask; any access that",
          "    // still reaches it is answered by its DECERR responder.",
          "    axilxbar #(",
          "        .C_AXI_DATA_WIDTH (C_AXI_DATA_WIDTH),",
          "        .C_AXI_ADDR_WIDTH (C_AXI_ADDR_WIDTH),",
          f"        .NM               ({nm}),",
          f"        .NS               ({ns}),",
          "        .SLAVE_ADDR       ({"]
    L += [f"            SLAVE{j}_EN ? SLAVE{j}_ADDR : {{AW{{1'b1}}}}" + ("," if k < ns - 1 else "")
          for k, j in enumerate(rev)]
    L += ["        }),", "        .SLAVE_MASK       ({"]
    L += [f"            SLAVE{j}_EN ? SLAVE{j}_MASK : {{AW{{1'b1}}}}" + ("," if k < ns - 1 else "")
          for k, j in enumerate(rev)]
    L += ["        }),",
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
    L += ["    );", "", "endmodule", _null_module(null), "`default_nettype wire", ""]
    return "\n".join(L)


def gen_xbar_markdown(spec: XbarSpec):
    L = [f"# {spec.module}", "",
         f"AXI4-Lite crossbar (axilxbar), {spec.nm} master port(s) x {spec.ns} slave port(s).", "",
         "| Port | EN | IP | Base | Mask | Window | Used |", "|---|---|---|---|---|---|---|"]
    for i, m in enumerate(spec.masters):
        L.append(f"| MASTER{i} | {int(m.enabled)} | {m.name} | | | | |")
    for j, s in enumerate(spec.slaves):
        L.append(f"| SLAVE{j} | {int(s.enabled)} | {s.name} | {hex32(s.base)} | {hex32(s.mask)} | {s.window:#x} | "
                 f"{f'0x{s.span:X}' if s.span else ''} |")
    L += ["", "Master i connects to `MASTER<i>_AXI_*`, slave j to `SLAVE<j>_AXI_*`.",
          "Enable or move a port by changing `MASTER<i>_EN`, `SLAVE<j>_EN`, `SLAVE<j>_ADDR`, `SLAVE<j>_MASK`.",
          "Disabled and unmapped addresses return DECERR. Keep the debug project addresses in sync.", ""]
    return "\n".join(L)


def gen_xbar_flat(spec: XbarSpec, opt_lowpower=1, opt_linger=4, lgmaxburst=5):
    """Parametric crossbar: NM/NS are generics, ports are concatenated packed vectors."""
    errs = spec.validate()
    if errs:
        raise ValueError("\n".join(errs))
    masters = [m for m in spec.masters if m.enabled]
    slaves = [s for s in spec.slaves if s.enabled]
    nm, ns, mod = len(masters), len(slaves), spec.module
    RQ, RS = PK.REQ_W, PK.RSP_W
    L = ["////////////////////////////////////////////////////////////////////////////////",
         f"// Module: {mod}",
         "//",
         "// Parametric AXI4-Lite crossbar on ZipCPU axilxbar. Every interface is a",
         f"// packed request/response pair ({RQ} + {RS} bits), concatenated per side:",
         f"//   MASTERS_AXI_REQ[i*{RQ} +: {RQ}] / MASTERS_AXI_RSP[i*{RS} +: {RS}]  master i (MASTER_AXI_REQ/RSP)",
         f"//   SLAVES_AXI_REQ[j*{RQ} +: {RQ}]  / SLAVES_AXI_RSP[j*{RS} +: {RS}]   slave j  (SLAVE_AXI_REQ/RSP)",
         "// NM, NS, SLAVE_ADDR and SLAVE_MASK are generics; slave 0 sits in the LSBs of",
         "// SLAVE_ADDR/SLAVE_MASK. Unmapped addresses return DECERR.",
         "//",
         "// Packed layout (32-bit address and data):"] + PK.layout_lines() + [
         "//",
         "// Default map:",
         "//   Port     Base        Mask        IP"]
    for i, m in enumerate(masters):
        L.append(f"//   {'MASTER' + str(i):<9}{'':24}{m.name}")
    for j, s in enumerate(slaves):
        L.append(f"//   {'SLAVE' + str(j):<9}{hex32(s.base)}  {hex32(s.mask)}  {s.name}")
    rev = list(reversed(slaves))
    L += ["//",
          "// Requires axilxbar.v, addrdecode.v, skidbuffer.v (wb2axip, Apache 2.0,",
          "// Gisselquist Technology LLC). Verilog-2005.",
          "////////////////////////////////////////////////////////////////////////////////",
          "",
          "`default_nettype none",
          "",
          f"module {mod} #(",
          f"    parameter integer NM = {nm},",
          f"    parameter integer NS = {ns},",
          "    // { slave NS-1, ..., slave 0 }",
          "    parameter [NS*32-1:0] SLAVE_ADDR = {",
          "\n".join(f"        {_h32(s.base)}{',' if k < ns - 1 else ' '}  // SLAVE{ns - 1 - k}: {s.name}"
                    for k, s in enumerate(rev)),
          "    },",
          "    parameter [NS*32-1:0] SLAVE_MASK = {",
          ",\n".join(f"        {_h32(s.mask)}" for s in rev),
          "    },",
          f"    parameter [0:0]  OPT_LOWPOWER = 1'b{opt_lowpower},",
          f"    parameter integer OPT_LINGER   = {opt_linger},",
          f"    parameter integer LGMAXBURST   = {lgmaxburst}",
          ") (",
          "    input  wire                ACLK,",
          "    input  wire                ARESETN,",
          f"    input  wire [NM*{RQ}-1:0] MASTERS_AXI_REQ,",
          f"    output wire [NM*{RS}-1:0]  MASTERS_AXI_RSP,",
          f"    output wire [NS*{RQ}-1:0] SLAVES_AXI_REQ,",
          f"    input  wire [NS*{RS}-1:0]  SLAVES_AXI_RSP",
          ");",
          ""]
    widths = dict(PK.REQ_FIELDS + PK.RSP_FIELDS)
    for side, n in (("s", "NM"), ("m", "NS")):
        for f, w in PK.REQ_FIELDS + PK.RSP_FIELDS:
            L.append(f"    wire [{n}*{w}-1:0] {side}_{f.lower()};")
    L += ["", "    genvar i, j;", "", "    // Master side: unpack requests, pack responses",
          "    generate for (i = 0; i < NM; i = i + 1) begin : g_s"]
    L.append("        assign { " + ", ".join(f"s_{f.lower()}[i*{w} +: {w}]" if w > 1 else f"s_{f.lower()}[i]"
                                          for f, w in PK.REQ_FIELDS) + f" }} = MASTERS_AXI_REQ[i*{RQ} +: {RQ}];")
    L.append(f"        assign MASTERS_AXI_RSP[i*{RS} +: {RS}] = {{ " + ", ".join(
        f"s_{f.lower()}[i*{w} +: {w}]" if w > 1 else f"s_{f.lower()}[i]" for f, w in PK.RSP_FIELDS) + " };")
    L += ["    end endgenerate", "", "    // Slave side: pack requests, unpack responses",
          "    generate for (j = 0; j < NS; j = j + 1) begin : g_m"]
    L.append(f"        assign SLAVES_AXI_REQ[j*{RQ} +: {RQ}] = {{ " + ", ".join(
        f"m_{f.lower()}[j*{w} +: {w}]" if w > 1 else f"m_{f.lower()}[j]" for f, w in PK.REQ_FIELDS) + " };")
    L.append("        assign { " + ", ".join(f"m_{f.lower()}[j*{w} +: {w}]" if w > 1 else f"m_{f.lower()}[j]"
                                          for f, w in PK.RSP_FIELDS) + f" }} = SLAVES_AXI_RSP[j*{RS} +: {RS}];")
    L += ["    end endgenerate", "",
          "    axilxbar #(",
          "        .C_AXI_DATA_WIDTH (32),",
          "        .C_AXI_ADDR_WIDTH (32),",
          "        .NM               (NM),",
          "        .NS               (NS),",
          "        .SLAVE_ADDR       (SLAVE_ADDR),",
          "        .SLAVE_MASK       (SLAVE_MASK),",
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


def write_xbar(spec: XbarSpec, outdir):
    os.makedirs(outdir, exist_ok=True)
    v = os.path.join(outdir, f"{spec.module}.v")
    md = os.path.join(outdir, f"{spec.module}.md")
    js = os.path.join(outdir, f"{spec.module}.xbar.json")
    with open(v, "w", encoding="utf-8") as fh:
        fh.write(gen_xbar_flat(spec) if spec.style == "flat" else gen_xbar(spec))
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(gen_xbar_markdown(spec))
    spec.save(js)
    return [v, md, js]
