"""
axilgen.py - AXI4-Lite register wrapper generator based on ZipCPU easyaxil.v

A register specification (registers made of typed bit fields) is turned into:
  <module>.v          AXI4-Lite slave: ZipCPU wb2axip easyaxil.v (hdl/easyaxil.v) with this IP's
                      registers in its "AXI-lite register logic" section and IP-side ports
  <module>_pk.v       optional HDL Designer block around it (SLAVE_AXI_REQ/RSP packed ports)
  <module>_tb.v       self-checking Icarus Verilog testbench
  <module>_inst.v     Verilog instantiation template (and <module>_pk_inst.v)
  <module>_regs.h     C header with offsets, masks and shifts
  <module>_regs.md    register map documentation
  <module>.axproj.json  project for AXIL Studio

Field kinds
  RW     control bits written by the bus, driven to the IP     -> output o_<reg>_<field>
  RO     status bits read from the IP (optional 2-FF sync)      -> input  i_<reg>_<field>
  PULSE  write 1 to emit a one-clock pulse, reads 0             -> output o_<reg>_<field>
  W1C    sticky event flags set by the IP, write 1 to clear     -> input  i_<reg>_<field> (set)
         optionally ORed into o_irq
  CONST  read-only constant (ID, version)                       -> no port
Register options
  wr_strobe  one-clock o_<reg>_wr after each write to the register
  rd_strobe  one-clock o_<reg>_rd after each read (e.g. FIFO pop)

Only address bits [REG_AW+1:2] are decoded, so the full interconnect address can be connected.
"""

import json
import os
import random
import re
from dataclasses import dataclass, field
from typing import List

from .model import EnumValue, single_line

SPEC_FORMAT = "axil-regspec"
KINDS = ("RW", "RO", "PULSE", "W1C", "CONST")
KIND_HELP = {
    "RW": "Kontrol: bus yazar, IP'ye çıkış (o_)",
    "RO": "Durum: IP'den giriş (i_), bus okur",
    "PULSE": "1 yazınca 1 clock darbe (o_), 0 okunur",
    "W1C": "IP olay bayrağı (i_ set), tutulur, 1 yazınca silinir",
    "CONST": "Sabit değer (ID, versiyon), port yok",
}

VERILOG_KEYWORDS = set("""
always and assign automatic begin buf bufif0 bufif1 case casex casez cell cmos config deassign
default defparam design disable edge else end endcase endconfig endfunction endgenerate
endmodule endprimitive endspecify endtable endtask event for force forever fork function
generate genvar highz0 highz1 if ifnone incdir include initial inout input instance integer
join large liblist library localparam macromodule medium module nand negedge nmos nor
noshowcancelled not notif0 notif1 or output parameter pmos posedge primitive pull0 pull1
pulldown pullup pulsestyle_onevent pulsestyle_ondetect rcmos real realtime reg release
repeat rnmos rpmos rtran rtranif0 rtranif1 scalared showcancelled signed small specify
specparam strong0 strong1 supply0 supply1 table task time tran tranif0 tranif1 tri tri0
tri1 triand trior trireg unsigned use uwire vectored wait wand weak0 weak1 while wire wor
xnor xor logic bit byte int
""".split())
VHDL_KEYWORDS = set("""
abs access after alias all and architecture array assert attribute begin block body buffer
bus case component configuration constant disconnect downto else elsif end entity exit file
for function generate generic group guarded if impure in inertial inout is label library
linkage literal loop map mod nand new next nor not null of on open or others out package
port postponed procedure process pure range record register reject rem report return rol
ror select severity signal shared sla sll sra srl subtype then to transport type unaffected
units until use variable wait when while with xnor xor
""".split())


VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([-+][0-9A-Za-z.]+)?$")


def version_key(v):
    """Sort key for versions like 1.2.10 or 2.0.0-beta."""
    head = re.split(r"[-+]", str(v), 1)[0]
    nums = [int(x) if x.isdigit() else 0 for x in head.split(".")]
    return nums + [0] * (4 - len(nums)), "-" not in str(v)


def bump_version(v):
    """1.2.3 -> 1.2.4 (last number + 1)."""
    head = re.split(r"[-+]", str(v or "1.0.0"), 1)[0]
    parts = [int(x) if x.isdigit() else 0 for x in head.split(".")] or [1, 0, 0]
    parts[-1] += 1
    return ".".join(str(x) for x in parts)


class SpecError(ValueError):
    pass


def parse_int(text):
    text = str(text).strip().replace("_", "")
    if not text:
        return 0
    return int(text, 0)


@dataclass
class FieldSpec:
    name: str
    lsb: int
    width: int = 1
    kind: str = "RW"
    reset: int = 0          # RW reset value / CONST value
    desc: str = ""
    irq: bool = False       # W1C: contributes to o_irq
    sync: bool = False      # RO: 2-FF synchronizer
    values: list = field(default_factory=list)   # model.EnumValue: value meanings
    fmt: str = ""           # data type / display format (numfmt.py); "" = auto

    @property
    def msb(self):
        return self.lsb + self.width - 1

    @property
    def mask(self):
        return ((1 << self.width) - 1) << self.lsb

    @property
    def bits(self):
        return f"[{self.lsb}]" if self.width == 1 else f"[{self.msb}:{self.lsb}]"

    def to_dict(self):
        d = {"name": self.name, "lsb": self.lsb, "width": self.width, "kind": self.kind,
             "reset": f"0x{self.reset:X}", "desc": self.desc}
        if self.irq:
            d["irq"] = True
        if self.sync:
            d["sync"] = True
        if self.values:
            d["values"] = [e.to_dict() for e in sorted(self.values, key=lambda e: e.value)]
        if self.fmt:
            d["fmt"] = self.fmt
        return d

    @staticmethod
    def from_dict(d):
        return FieldSpec(d["name"], int(d["lsb"]), int(d.get("width", 1)),
                         d.get("kind", "RW").upper(), parse_int(d.get("reset", 0)),
                         d.get("desc", ""), bool(d.get("irq", False)), bool(d.get("sync", False)),
                         [EnumValue.from_dict(e) for e in d.get("values", [])], d.get("fmt", ""))


@dataclass
class RegSpec:
    name: str
    offset: int
    desc: str = ""
    wr_strobe: bool = False
    rd_strobe: bool = False
    fields: List[FieldSpec] = field(default_factory=list)

    @property
    def index(self):
        return self.offset // 4

    def kinds(self):
        return {f.kind for f in self.fields}

    @property
    def access(self):
        """Access type for the debug application."""
        k = self.kinds()
        if k and k <= {"RO", "CONST"}:
            return "RO"
        if k and k <= {"PULSE"}:
            return "WO"
        return "RW"

    @property
    def reset_value(self):
        v = 0
        for f in self.fields:
            if f.kind in ("RW", "CONST"):
                v |= (f.reset << f.lsb) & f.mask
        return v

    def mask_of(self, *kinds):
        m = 0
        for f in self.fields:
            if f.kind in kinds:
                m |= f.mask
        return m

    def to_dict(self):
        d = {"name": self.name, "offset": f"0x{self.offset:02X}", "desc": self.desc,
             "fields": [f.to_dict() for f in sorted(self.fields, key=lambda f: f.lsb)]}
        if self.wr_strobe:
            d["wr_strobe"] = True
        if self.rd_strobe:
            d["rd_strobe"] = True
        return d

    @staticmethod
    def from_dict(d):
        return RegSpec(d["name"], parse_int(d["offset"]), d.get("desc", ""),
                       bool(d.get("wr_strobe", False)), bool(d.get("rd_strobe", False)),
                       [FieldSpec.from_dict(f) for f in d.get("fields", [])])


@dataclass
class ModuleSpec:
    module: str = "my_ip_axil"
    base: int = 0x80000000
    desc: str = ""
    registers: List[RegSpec] = field(default_factory=list)
    packed: bool = True     # also write <module>_pk.v: HDL Designer wrapper with SLAVE_AXI_REQ/RSP ports
    version: str = "1.0.0"  # IP version (written into the generated files and the IP library)
    # base: last slave base address used (Excel / project). The IP itself decodes offsets only;
    # its real base is set where it is connected (interconnect / project), not in the spec.

    # -------------------------------------------------------------- persistence
    def to_dict(self):
        return {"format": SPEC_FORMAT, "version": 1, "module": self.module,
                "base": f"0x{self.base:08X}", "desc": self.desc, "packed": self.packed, "ip_version": self.version,
                "registers": [r.to_dict() for r in sorted(self.registers, key=lambda r: r.offset)]}

    @staticmethod
    def from_dict(d):
        if d.get("format") != SPEC_FORMAT:
            raise SpecError("Bu dosya bir register spesifikasyonu değil")
        return ModuleSpec(d.get("module", "my_ip_axil"), parse_int(d.get("base", 0)),
                          d.get("desc", ""), [RegSpec.from_dict(r) for r in d.get("registers", [])],
                          bool(d.get("packed", True)), str(d.get("ip_version", "1.0.0")))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            return ModuleSpec.from_dict(json.load(fh))

    # -------------------------------------------------------------- derived
    @property
    def regs(self):
        return sorted(self.registers, key=lambda r: r.offset)

    @property
    def reg_aw(self):
        """Word-address bits needed to decode every register."""
        top = max((r.index for r in self.registers), default=0)
        return max(1, top.bit_length())

    @property
    def min_addr_width(self):
        return self.reg_aw + 2

    @property
    def span(self):
        return 4 << self.reg_aw

    def has_irq(self):
        return any(f.irq for r in self.registers for f in r.fields)

    # -------------------------------------------------------------- validation
    def validate(self):
        """Return a list of error strings (empty when the spec is usable)."""
        errs = []
        ident = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

        def check_name(name, what):
            if not ident.match(name or ""):
                errs.append(f"{what} '{name}': harfle başlamalı, sadece harf/rakam/_ içermeli")
            elif name.lower() in VERILOG_KEYWORDS or name.lower() in VHDL_KEYWORDS:
                errs.append(f"{what} '{name}': Verilog/VHDL anahtar kelimesi olamaz")
            elif "__" in name or name.endswith("_"):
                errs.append(f"{what} '{name}': '__' içeremez ve '_' ile bitemez (VHDL uyumu)")

        check_name(self.module, "Modül adı")
        if not VERSION_RE.match(self.version or ""):
            errs.append(f"Sürüm '{self.version}': rakam ve nokta ile yazılmalı, ör. 1.0.0 (sonuna -beta gibi ek olabilir)")
        if not self.registers:
            errs.append("En az bir register tanımlanmalı")
        seen_regs, seen_offs, ports = set(), {}, {}
        for r in self.registers:
            check_name(r.name, "Register")
            if r.name.upper() in seen_regs:
                errs.append(f"Register adı tekrar ediyor: {r.name}")
            seen_regs.add(r.name.upper())
            if r.offset % 4 or r.offset < 0:
                errs.append(f"{r.name}: offset 4'ün katı olmalı (0x{r.offset:X})")
            if r.offset in seen_offs:
                errs.append(f"{r.name}: offset 0x{r.offset:02X}, {seen_offs[r.offset]} ile aynı")
            seen_offs[r.offset] = r.name
            if not r.fields:
                errs.append(f"{r.name}: en az bir alan tanımlanmalı")
            used, seen_f = 0, set()
            for f in r.fields:
                where = f"{r.name}.{f.name}"
                check_name(f.name, "Alan")
                if f.name.upper() in seen_f:
                    errs.append(f"{r.name}: alan adı tekrar ediyor: {f.name}")
                seen_f.add(f.name.upper())
                if f.kind not in KINDS:
                    errs.append(f"{where}: bilinmeyen tip {f.kind}")
                if not (0 <= f.lsb <= 31 and f.width >= 1 and f.msb <= 31):
                    errs.append(f"{where}: alan 32 bitin içinde kalmalı ({f.bits})")
                    continue
                if used & f.mask:
                    errs.append(f"{where}: başka bir alanla çakışıyor")
                used |= f.mask
                if f.reset >> f.width:
                    errs.append(f"{where}: reset/sabit değeri {f.width} bite sığmıyor")
                if f.reset and f.kind not in ("RW", "CONST"):
                    errs.append(f"{where}: reset değeri sadece RW ve CONST için kullanılır")
                if f.irq and f.kind != "W1C":
                    errs.append(f"{where}: IRQ sadece W1C alanlarında kullanılabilir")
                if f.sync and f.kind != "RO":
                    errs.append(f"{where}: senkronizör sadece RO alanlarında kullanılabilir")
                seen_v = set()
                for e in f.values:
                    if e.value < 0 or e.value >> f.width:
                        errs.append(f"{where}: değer tablosundaki 0x{e.value:X}, {f.width} bite sığmıyor")
                    if e.value in seen_v:
                        errs.append(f"{where}: değer tablosunda 0x{e.value:X} tekrar ediyor")
                    seen_v.add(e.value)
                for p in port_names(r, f):
                    if p in ports:
                        errs.append(f"{where}: port adı '{p}', {ports[p]} ile çakışıyor")
                    ports[p] = where
            for flag, suffix in ((r.wr_strobe, "wr"), (r.rd_strobe, "rd")):
                if flag:
                    p = f"o_{r.name.lower()}_{suffix}"
                    if p in ports:
                        errs.append(f"{r.name}: port adı '{p}' çakışıyor")
                    ports[p] = r.name
            if r.rd_strobe and not r.kinds() - {"PULSE"}:
                errs.append(f"{r.name}: okunacak alanı olmayan registerda okuma strobe'u anlamsız")
        return errs


def fbase(reg, f):
    """Signal stem: a field named like its register maps to the bare register name."""
    if f.name.upper() == reg.name.upper():
        return reg.name.lower()
    return f"{reg.name.lower()}_{f.name.lower()}"


def port_names(reg, f):
    base = fbase(reg, f)
    return {"RW": [f"o_{base}"], "PULSE": [f"o_{base}"], "RO": [f"i_{base}"],
            "W1C": [f"i_{base}"], "CONST": []}[f.kind]


def _sig(reg, f, prefix):
    return f"{prefix}_{fbase(reg, f)}"


def _vw(width):
    return "      " if width == 1 else f"[{width - 1}:0]".ljust(6)


def _hex(width, value):
    return f"{width}'h{value:X}"


# ============================================================================
# Verilog wrapper
# ============================================================================
EASYAXIL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "hdl", "easyaxil.v")


def _template_cut(text, start, end, what):
    """(before, after) around the template part from `start` up to (not including) `end`."""
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        raise SpecError(f"easyaxil.v şablonunda beklenen bölüm bulunamadı: {what}")
    return text[:i], text[j:]


def _template_replace(text, old, new, what):
    if text.count(old) != 1:
        raise SpecError(f"easyaxil.v şablonunda beklenen satır bulunamadı: {what}")
    return text.replace(old, new)


def gen_verilog(spec: ModuleSpec, template=EASYAXIL):
    """The IP: ZipCPU wb2axip rtl/easyaxil.v with only the changes easyaxil is meant for
    (module name, address width, IP-side ports, the "AXI-lite register logic" section), plus
    `localparam C_AXI_DATA_WIDTH` written as `parameter` (Verilog-2005), a default branch in
    the read case and the r0..r3 formal register checks left out. The AXI handshake (with and
    without skidbuffers), apply_wstrb and the remaining formal properties are unchanged."""
    errs = spec.validate()
    if errs:
        raise SpecError("\n".join(errs))
    with open(template, encoding="utf-8") as fh:
        t = fh.read()
    m, aw = spec.module, spec.reg_aw
    regs = spec.regs

    # ---------------------------------------------------------------- header
    H = ["////////////////////////////////////////////////////////////////////////////////",
         f"// Module      : {m}",
         f"// IP version  : {spec.version}",
         f"// Description : {single_line(spec.desc) or 'AXI4-Lite register file'}",
         "//",
         "// Generated by AXIL Studio (axilgen) from ZipCPU wb2axip rtl/easyaxil.v; the",
         "// original header follows. Changes to easyaxil.v, all in the places easyaxil",
         "// leaves to the user:",
         "//   - module name, C_AXI_ADDR_WIDTH default, the IP-side ports",
         "//   - \"AXI-lite register logic\": the registers of this IP instead of r0..r3;",
         f"//     word address bits [{aw + 1}:2] are decoded (the map repeats every 0x{spec.span:X} bytes),",
         "//     unused offsets read 0 (default branch of the read case)",
         "//   - C_AXI_DATA_WIDTH: `localparam` in the parameter list (SystemVerilog only)",
         "//     written as `parameter` for Verilog-2005 tools. Do not override it.",
         "//   - formal: the r0..r3 register checks are left out (faxil_slave checks kept)",
         "// With OPT_SKIDBUFFER=1 skidbuffer.v (wb2axip) is needed.",
         "//",
         "// Register map"]
    for r in regs:
        H.append(f"//   0x{r.offset:02X}  {r.name:<16} {r.access}  {single_line(r.desc)}")
        for f in sorted(r.fields, key=lambda f: -f.lsb):
            extra = (" irq" if f.irq else "") + (" sync" if f.sync else "")
            H.append(f"//          {f.bits:<8} {f.name:<16} {f.kind:<5}{extra}  {single_line(f.desc)}")
    H.append("////////////////////////////////////////////////////////////////////////////////")

    # ---------------------------------------------------------------- module, parameters, ports
    t = _template_replace(t, "module\teasyaxil #(", f"module\t{m} #(", "module")
    t = _template_replace(t, "\t\tparameter\tC_AXI_ADDR_WIDTH = 4,",
                          f"\t\tparameter\tC_AXI_ADDR_WIDTH = 32,\t// >= {spec.min_addr_width}", "C_AXI_ADDR_WIDTH")
    t = _template_replace(t, "\t\tlocalparam\tC_AXI_DATA_WIDTH = 32,",
                          "\t\tparameter\tC_AXI_DATA_WIDTH = 32,\t// localparam in easyaxil.v; do not override",
                          "C_AXI_DATA_WIDTH")
    ports = _user_ports(spec)
    if ports:
        rows = ["\t\t// }}}", "\t\t// IP side", "\t\t// {{{"]
        for i, (direction, width, name, comment) in enumerate(ports):
            sep = "," if i < len(ports) - 1 else ""
            rows.append(f"\t\t{direction}\twire\t{_vw(width).strip():<12}\t{name}{sep}\t// {comment}")
        t = _template_replace(t, "\t\toutput\twire\t[1:0]\t\t\t\tS_AXI_RRESP\n",
                              "\t\toutput\twire\t[1:0]\t\t\t\tS_AXI_RRESP,\n" + "\n".join(rows) + "\n",
                              "S_AXI_RRESP")

    # ---------------------------------------------------------------- declarations
    def sel(addr):
        return f"{addr}[REG_AW-1:0]"

    def idx(r):
        return f"{aw}'d{r.index}"

    rw = [r for r in regs if r.mask_of("RW")]
    pulse = [r for r in regs if r.mask_of("PULSE")]
    w1c = [r for r in regs if r.mask_of("W1C")]
    n = lambda r: r.name.lower()
    D = [f"\tlocalparam\tREG_AW = {aw};\t// word address bits decoded", ""]
    if rw:
        D.append("\treg\t[31:0]\t" + ", ".join(f"r_{n(r)}" for r in rw) + ";\t// RW fields")
        D.append("\twire\t[31:0]\t" + ", ".join(f"wskd_r_{n(r)}" for r in rw) + ";")
    if pulse:
        D.append("\treg\t[31:0]\t" + ", ".join(f"p_{n(r)}" for r in pulse) + ";\t// PULSE fields")
    if w1c:
        D.append("\treg\t[31:0]\t" + ", ".join(f"f_{n(r)}" for r in w1c) + ";\t// W1C flags")
        D.append("\twire\t[31:0]\t" + ", ".join(f"w1c_set_{n(r)}" for r in w1c) + ";")
    t = _template_replace(t, "\treg\t[31:0]\tr0, r1, r2, r3;\n\twire\t[31:0]\twskd_r0, wskd_r1, wskd_r2, wskd_r3;\n",
                          "\n".join(D) + "\n", "r0..r3 declarations")

    # ---------------------------------------------------------------- register logic
    read_val, irq_terms, L = {}, [], []
    a = L.append
    if rw:
        a("\t// RW fields: one word per register, written with apply_wstrb()")
        a("\t// apply_wstrb(old_data, new_data, write_strobes)")
        for r in rw:
            a(f"\tassign\twskd_r_{n(r)} = apply_wstrb(r_{n(r)}, wskd_data, wskd_strb);")
        a("")
        for r in rw:
            a(f"\tinitial\tr_{n(r)} = {_hex(32, r.reset_value & r.mask_of('RW'))};")
        a("\talways @(posedge S_AXI_ACLK)")
        a("\tif (i_reset)")
        a("\tbegin")
        for r in rw:
            a(f"\t\tr_{n(r)} <= {_hex(32, r.reset_value & r.mask_of('RW'))};")
        a("\tend else if (axil_write_ready)")
        a("\tbegin")
        a(f"\t\tcase({sel('awskd_addr')})")
        for r in rw:
            a(f"\t\t{idx(r)}:\tr_{n(r)} <= wskd_r_{n(r)} & {_hex(32, r.mask_of('RW'))};")
        a("\t\tendcase")
        a("\tend")
        a("")
    if pulse:
        a("\t// PULSE fields: a 1 written gives a pulse of one clock")
        for r in pulse:
            a(f"\tinitial\tp_{n(r)} = 0;")
        a("\talways @(posedge S_AXI_ACLK)")
        a("\tif (i_reset)")
        a("\tbegin")
        for r in pulse:
            a(f"\t\tp_{n(r)} <= 0;")
        a("\tend else begin")
        for r in pulse:
            a(f"\t\tp_{n(r)} <= 0;")
        a("\t\tif (axil_write_ready)")
        a(f"\t\tcase({sel('awskd_addr')})")
        for r in pulse:
            a(f"\t\t{idx(r)}:\tp_{n(r)} <= apply_wstrb(0, wskd_data, wskd_strb) & {_hex(32, r.mask_of('PULSE'))};")
        a("\t\tendcase")
        a("\tend")
        a("")
    if w1c:
        a("\t// W1C flags: set by the IP input, cleared by writing 1 (set wins)")
        for r in w1c:
            src = {f.name: f"i_{fbase(r, f)}" for f in r.fields if f.kind == "W1C"}
            a(f"\tassign\tw1c_set_{n(r)} = " + _concat_kind(r, src) + ";")
        for r in w1c:
            a(f"\tinitial\tf_{n(r)} = 0;")
        for r in w1c:
            a("\talways @(posedge S_AXI_ACLK)")
            a("\tif (i_reset)")
            a(f"\t\tf_{n(r)} <= 0;")
            a(f"\telse if (axil_write_ready && {sel('awskd_addr')} == {idx(r)})")
            a(f"\t\tf_{n(r)} <= (f_{n(r)} & ~(apply_wstrb(0, wskd_data, wskd_strb) & {_hex(32, r.mask_of('W1C'))}))")
            a(f"\t\t\t\t| w1c_set_{n(r)};")
            a("\telse")
            a(f"\t\tf_{n(r)} <= f_{n(r)} | w1c_set_{n(r)};")
            a("")
    syncs = [(r, f) for r in regs for f in r.fields if f.kind == "RO" and f.sync]
    if syncs:
        a("\t// RO inputs with a 2-FF synchronizer")
        for r, f in syncs:
            b = fbase(r, f)
            a(f"\treg\t{_vw(f.width).strip()}\ts1_{b}, s2_{b};")
            a(f"\tinitial\t{{ s2_{b}, s1_{b} }} = 0;")
            a("\talways @(posedge S_AXI_ACLK)")
            a(f"\t\t{{ s2_{b}, s1_{b} }} <= {{ s1_{b}, i_{b} }};")
        a("")
    strobes = [(r, k) for r in regs for k, on in (("wr", r.wr_strobe), ("rd", r.rd_strobe)) if on]
    if strobes:
        a("\t// Access strobes: one clock, the clock after the bus access")
        for r, k in strobes:
            a(f"\treg\tstb_{n(r)}_{k};")
            a(f"\tinitial\tstb_{n(r)}_{k} = 0;")
            a("\talways @(posedge S_AXI_ACLK)")
            a("\tif (i_reset)")
            a(f"\t\tstb_{n(r)}_{k} <= 0;")
            a("\telse")
            if k == "wr":
                a(f"\t\tstb_{n(r)}_{k} <= axil_write_ready && ({sel('awskd_addr')} == {idx(r)});")
            else:
                a(f"\t\tstb_{n(r)}_{k} <= axil_read_ready && ({sel('arskd_addr')} == {idx(r)});")
            a(f"\tassign\to_{n(r)}_{k} = stb_{n(r)}_{k};")
        a("")
    outs = []
    for r in regs:
        for f in sorted(r.fields, key=lambda f: f.lsb):
            b, sl = fbase(r, f), (f"[{f.msb}:{f.lsb}]" if f.width > 1 else f"[{f.lsb}]")
            if f.kind == "RW":
                outs.append(f"\tassign\to_{b} = r_{n(r)}{sl};")
                read_val[(r.name, f.name)] = f"r_{n(r)}{sl}"
            elif f.kind == "PULSE":
                outs.append(f"\tassign\to_{b} = p_{n(r)}{sl};")
                read_val[(r.name, f.name)] = None
            elif f.kind == "W1C":
                read_val[(r.name, f.name)] = f"f_{n(r)}{sl}"
                if f.irq:
                    irq_terms.append(f"(|f_{n(r)}{sl})")
            elif f.kind == "RO":
                read_val[(r.name, f.name)] = f"s2_{b}" if f.sync else f"i_{b}"
            elif f.kind == "CONST":
                read_val[(r.name, f.name)] = _hex(f.width, f.reset)
    if outs:
        a("\t// IP-side outputs")
        L.extend(outs)
        a("")
    if irq_terms:
        a("\t// Interrupt: any W1C flag marked irq is set")
        a("\tassign\to_irq = " + " | ".join(irq_terms) + ";")
        a("")
    a("\tinitial\taxil_read_data = 0;")
    a("\talways @(posedge S_AXI_ACLK)")
    a("\tif (OPT_LOWPOWER && !S_AXI_ARESETN)")
    a("\t\taxil_read_data <= 0;")
    a("\telse if (!S_AXI_RVALID || S_AXI_RREADY)")
    a("\tbegin")
    a(f"\t\tcase({sel('arskd_addr')})")
    for r in regs:
        a(f"\t\t{idx(r)}:\taxil_read_data\t<= {_concat(r, read_val)};")
    a("\t\tdefault:\taxil_read_data\t<= 0;")
    a("\t\tendcase")
    a("")
    a("\t\tif (OPT_LOWPOWER && !axil_read_ready)")
    a("\t\t\taxil_read_data <= 0;")
    a("\tend")
    a("")
    before, after = _template_cut(t, "\t// apply_wstrb(old_data, new_data, write_strobes)\n",
                                  "\tfunction [C_AXI_DATA_WIDTH-1:0]\tapply_wstrb;", "register logic")
    t = before + "\n".join(L) + "\n" + after

    # ---------------------------------------------------------------- formal: r0..r3 checks
    # the rest of the formal section (faxil_slave checks, cover) is kept
    before, after = _template_cut(t, "`define\tCHECK_REGISTERS\n", "\t// }}}\n\t////", "CHECK_REGISTERS")
    t = before + "\t// (easyaxil's faxil_register checks of r0..r3 do not apply to this register map)\n" + after
    return "\n".join(H) + "\n" + t


def _concat_kind(reg, src):
    """32-bit expression with src[field name] in the field's bits and zeros elsewhere."""
    parts, pos = [], 32
    for f in sorted((f for f in reg.fields if f.name in src), key=lambda f: -f.lsb):
        if f.msb + 1 < pos:
            parts.append(f"{pos - f.msb - 1}'h0")
        parts.append(src[f.name])
        pos = f.lsb
    if pos > 0:
        parts.append(f"{pos}'h0")
    return "{ " + ", ".join(parts) + " }"


def _concat(reg, read_val):
    """32-bit read-back expression with zero filling between fields."""
    parts, pos = [], 32
    for f in sorted(reg.fields, key=lambda f: -f.lsb):
        if f.msb + 1 < pos:
            parts.append(f"{pos - f.msb - 1}'h0")
        expr = read_val[(reg.name, f.name)]
        parts.append(expr if expr is not None else f"{f.width}'h0")
        pos = f.lsb
    if pos > 0:
        parts.append(f"{pos}'h0")
    return "{ " + ", ".join(parts) + " }"


def _user_ports(spec):
    """(direction, width, name, comment) for every IP-side port."""
    ports = []
    for r in spec.regs:
        for f in sorted(r.fields, key=lambda f: f.lsb):
            for p in port_names(r, f):
                d = "output" if p.startswith("o_") else "input"
                comment = f"{r.name}.{f.name} {f.kind}" + (f" sync" if f.sync else "") + \
                          (" (set pulse)" if f.kind == "W1C" else "")
                ports.append((d, f.width, p, comment))
        if r.wr_strobe:
            ports.append(("output", 1, f"o_{r.name.lower()}_wr", f"{r.name} write strobe"))
        if r.rd_strobe:
            ports.append(("output", 1, f"o_{r.name.lower()}_rd", f"{r.name} read strobe"))
    if spec.has_irq():
        ports.append(("output", 1, "o_irq", "any enabled W1C flag"))
    return ports


# ============================================================================
# Instantiation templates
# ============================================================================
def gen_inst(spec: ModuleSpec):
    ports = _user_ports(spec)
    L = [f"// Instantiation template for {spec.module} (generated by axilgen.py)",
         f"{spec.module} #(", "    .C_AXI_ADDR_WIDTH (32)", f") u_{spec.module} ("]
    axi = ["S_AXI_ACLK", "S_AXI_ARESETN", "S_AXI_AWVALID", "S_AXI_AWREADY", "S_AXI_AWADDR",
           "S_AXI_AWPROT", "S_AXI_WVALID", "S_AXI_WREADY", "S_AXI_WDATA", "S_AXI_WSTRB",
           "S_AXI_BVALID", "S_AXI_BREADY", "S_AXI_BRESP", "S_AXI_ARVALID", "S_AXI_ARREADY",
           "S_AXI_ARADDR", "S_AXI_ARPROT", "S_AXI_RVALID", "S_AXI_RREADY", "S_AXI_RDATA",
           "S_AXI_RRESP"]
    names = axi + [p[2] for p in ports]
    for i, n in enumerate(names):
        L.append(f"    .{n:<24}({n}){',' if i < len(names) - 1 else ''}")
    L.append(");")
    return "\n".join(L) + "\n"


# ============================================================================
# C header and Markdown
# ============================================================================
def gen_header(spec: ModuleSpec):
    m = spec.module.upper()
    L = [f"/* {spec.module} register map (generated by axilgen.py) */",
         f"#ifndef {m}_REGS_H", f"#define {m}_REGS_H", "",
         "/* Slave base address depends on where the IP is connected; override before including. */",
         f"#ifndef {m}_BASE", f"#define {m}_BASE  0x{spec.base:08X}u", "#endif",
         f'#define {m}_IP_VERSION  "{spec.version}"', ""]
    for r in spec.regs:
        rn = f"{m}_{r.name.upper()}"
        L.append(f"#define {rn}_OFFSET  0x{r.offset:02X}u   /* {r.access} {single_line(r.desc).replace('*/', '')} */")
        for f in sorted(r.fields, key=lambda f: f.lsb):
            fn = rn if f.name.upper() == r.name.upper() else f"{rn}_{f.name.upper()}"
            L.append(f"#define {fn}_SHIFT  {f.lsb}u")
            L.append(f"#define {fn}_MASK   0x{f.mask:08X}u   /* {f.kind} */")
        L.append("")
    L.append(f"#endif /* {m}_REGS_H */")
    return "\n".join(L) + "\n"


def _md(text):
    return str(text).strip().replace("|", "\\|").replace("\n", "<br>")


def gen_markdown(spec: ModuleSpec):
    L = [f"# {spec.module}", "", f"IP version: `{spec.version}`", "", spec.desc or "AXI4-Lite register wrapper.", "",
         "Addresses below are offsets from the slave base address set on the interconnect.  ",
         f"Decoded address bits: `[{spec.reg_aw + 1}:2]` (block repeats every 0x{spec.span:X} bytes)", "",
         "## Registers", "", "| Offset | Register | Access | Reset | Description |",
         "|---|---|---|---|---|"]
    for r in spec.regs:
        L.append(f"| 0x{r.offset:02X} | {r.name} | {r.access} | 0x{r.reset_value:08X} | {_md(single_line(r.desc))} |")
    for r in spec.regs:
        L += ["", f"### 0x{r.offset:02X} {r.name}", "", r.desc or "", "",
              "| Bits | Field | Kind | Reset | Port | Description |", "|---|---|---|---|---|---|"]
        for f in sorted(r.fields, key=lambda f: -f.lsb):
            port = ", ".join(port_names(r, f)) or "—"
            kind = f.kind + (" (irq)" if f.irq else "") + (" (sync)" if f.sync else "")
            reset = f"0x{f.reset:X}" if f.kind in ("RW", "CONST") else "0"
            L.append(f"| {f.bits} | {f.name} | {kind} | {reset} | `{port}` | {_md(f.desc)} |")
        enum_fields = [f for f in sorted(r.fields, key=lambda f: -f.lsb) if f.values]
        for f in enum_fields:
            L += ["", f"**{f.name}** {f.bits} values:", "", "| Value | When read | When written |",
                  "|---|---|---|"]
            for e in sorted(f.values, key=lambda e: e.value):
                L.append(f"| 0x{e.value:X} | {_md(e.read) or '—'} | {_md(e.write) or '—'} |")
        strobes = [s for s, on in (("o_%s_wr" % r.name.lower(), r.wr_strobe),
                                   ("o_%s_rd" % r.name.lower(), r.rd_strobe)) if on]
        if strobes:
            L.append("")
            L.append("Strobes: " + ", ".join(f"`{s}`" for s in strobes))
    L += ["", "## Field kinds", ""] + [f"- **{k}**: {v}" for k, v in KIND_HELP.items()]
    return "\n".join(L) + "\n"


# ============================================================================
# Debug application project
# ============================================================================
def to_ipblock(spec: ModuleSpec, base=None, with_spec=False):
    """IPBlock (model.py) of the register map; base overrides spec.base. with_spec: the IP also
    keeps the full spec (project IP definitions), so the HDL can be generated from it again."""
    from .model import Field, IPBlock, Register
    regs = []
    for r in spec.regs:
        fields = [Field(f.name, f.lsb, f.width, f.desc or KIND_HELP[f.kind], kind=f.kind,
                        values=[EnumValue(e.value, e.read, e.write) for e in f.values], fmt=f.fmt)
                  for f in sorted(r.fields, key=lambda f: f.lsb)]
        regs.append(Register(r.name, r.offset, r.access, r.reset_value, r.desc, fields))
    return IPBlock(spec.module, spec.base if base is None else base, spec.desc, regs, version=spec.version,
                   regspec=spec.to_dict() if with_spec else None)


# register access -> field kind for register maps written without kinds (e.g. hand-made projects)
_ACCESS_KIND = {"RW": "RW", "RO": "RO", "WO": "PULSE"}


def spec_from_ipblock(ip):
    """ModuleSpec of an IP: its stored spec when it has one, otherwise built from the register map
    (fields without a kind take it from the register's access type)."""
    if getattr(ip, "regspec", None):
        spec = ModuleSpec.from_dict(ip.regspec)
        spec.module, spec.version = ip.name, ip.version or spec.version
        return spec
    regs = []
    for r in sorted(ip.registers, key=lambda r: r.offset):
        fields = []
        for f in r.fields:
            kind = f.kind or _ACCESS_KIND.get(r.access, "RW")
            reset = f.extract(r.reset) if kind in ("RW", "CONST") else 0
            fields.append(FieldSpec(f.name, f.lsb, f.width, kind, reset, f.desc,
                                    values=[EnumValue(e.value, e.read, e.write) for e in f.values], fmt=f.fmt))
        if not fields:
            fields.append(FieldSpec(r.name, 0, 32, _ACCESS_KIND.get(r.access, "RW"), r.reset if r.access == "RW" else 0))
        regs.append(RegSpec(r.name, r.offset, r.desc, fields=fields))
    return ModuleSpec(ip.name, 0, ip.desc, regs, True, ip.version or "1.0.0")


def add_to_project(spec: ModuleSpec, path, replace=True):
    """Add (or replace) the IP definition in an existing .axproj.json, or create one. A new IP is
    also placed in the address map at spec.base."""
    import os
    from .model import IPInstance, Project
    if os.path.exists(path):
        proj = Project.load(path)
    else:
        proj = Project(os.path.basename(path).split(".")[0], [], path)
    ip = to_ipblock(spec, 0, with_spec=True)
    old = proj.get_ip(ip.name)
    if old and replace:
        proj.ips[proj.ips.index(old)] = ip
    elif old:
        raise SpecError(f"Projede '{ip.name}' adında bir IP zaten var")
    else:
        proj.ips.append(ip)
    if not proj.instances_of(ip.name) and not proj.get_instance(ip.name):
        proj.instances.append(IPInstance(ip.name, ip.name, spec.base, 0, True))
    proj.save(path)
    return proj


# ============================================================================
# Self-checking testbench
# ============================================================================
def gen_testbench(spec: ModuleSpec, seed=1):
    rnd = random.Random(seed)
    m, regs = spec.module, spec.regs
    ports = _user_ports(spec)
    L = []
    a = L.append
    a("// Self-checking testbench for " + m + " (generated by axilgen.py)")
    a("// iverilog -g2012 -o tb " + m + "_tb.v " + m + ".v && vvp tb")
    a("`default_nettype none")
    a("module tb;")
    a("    reg clk = 0, rstn = 0;")
    a("    always #5 clk = ~clk;")
    a("    reg         awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;")
    a("    reg  [31:0] awaddr = 0, araddr = 0, wdata = 0;")
    a("    reg  [3:0]  wstrb = 4'hF;")
    a("    wire        awready, wready, bvalid, arready, rvalid;")
    a("    wire [1:0]  bresp, rresp;")
    a("    wire [31:0] rdata;")
    for d, w, n, _ in ports:
        if d == "input":
            a(f"    reg  {_vw(w)} {n} = 0;")
        else:
            a(f"    wire {_vw(w)} {n};")
    a("")
    a(f"    {m} #(.C_AXI_ADDR_WIDTH(32)) dut (")
    a("        .S_AXI_ACLK(clk), .S_AXI_ARESETN(rstn),")
    a("        .S_AXI_AWVALID(awvalid), .S_AXI_AWREADY(awready), .S_AXI_AWADDR(awaddr), .S_AXI_AWPROT(3'd0),")
    a("        .S_AXI_WVALID(wvalid), .S_AXI_WREADY(wready), .S_AXI_WDATA(wdata), .S_AXI_WSTRB(wstrb),")
    a("        .S_AXI_BVALID(bvalid), .S_AXI_BREADY(bready), .S_AXI_BRESP(bresp),")
    a("        .S_AXI_ARVALID(arvalid), .S_AXI_ARREADY(arready), .S_AXI_ARADDR(araddr), .S_AXI_ARPROT(3'd0),")
    a("        .S_AXI_RVALID(rvalid), .S_AXI_RREADY(rready), .S_AXI_RDATA(rdata), .S_AXI_RRESP(rresp)"
      + ("," if ports else ""))
    for i, (_, _, n, _) in enumerate(ports):
        a(f"        .{n}({n})" + ("," if i < len(ports) - 1 else ""))
    a("    );")
    a("")
    # pulse / strobe counters
    counters = [n for d, w, n, c in ports if d == "output" and (" PULSE" in c or c.endswith("strobe"))]
    for n in counters:
        a(f"    integer cnt_{n} = 0;")
        a(f"    reg  [31:0] last_{n} = 0;")
        a(f"    always @(posedge clk) if (|{n}) begin cnt_{n} = cnt_{n} + 1; last_{n} = {n}; end")
    a("")
    a("    integer errors = 0, checks = 0;")
    a("    task check(input [8*48-1:0] name, input [31:0] got, input [31:0] exp);")
    a("        begin")
    a("            checks = checks + 1;")
    a("            if (got !== exp) begin")
    a("                errors = errors + 1;")
    a("                $display(\"FAIL %0s: got %h expected %h\", name, got, exp);")
    a("            end")
    a("        end")
    a("    endtask")
    a("    task axi_write(input [31:0] adr, input [31:0] dat, input [3:0] stb);")
    a("        begin")
    a("            @(posedge clk); awaddr <= adr; wdata <= dat; wstrb <= stb;")
    a("            awvalid <= 1; wvalid <= 1; bready <= 1;")
    a("            @(posedge clk); while (!awready) @(posedge clk);")
    a("            awvalid <= 0; wvalid <= 0;")
    a("            while (!bvalid) @(posedge clk);")
    a("            @(posedge clk); bready <= 0;")
    a("        end")
    a("    endtask")
    a("    task axi_read(input [31:0] adr, output [31:0] dat);")
    a("        begin")
    a("            @(posedge clk); araddr <= adr; arvalid <= 1; rready <= 1;")
    a("            @(posedge clk); while (!arready) @(posedge clk);")
    a("            arvalid <= 0;")
    a("            while (!rvalid) @(posedge clk);")
    a("            dat = rdata; @(posedge clk); rready <= 0;")
    a("        end")
    a("    endtask")
    a("")
    a("    reg [31:0] v;")
    a("    initial begin")
    a("        repeat (4) @(posedge clk); rstn <= 1; repeat (2) @(posedge clk);")
    a("")
    # full interconnect address: the IP must ignore the upper bits
    base_addr = (spec.base or 0x40000000) & ~(spec.span - 1) & 0xFFFFFFFF
    state = {}              # per-register model: RW and W1C values
    inputs = {}             # current value driven on each RO input

    def expect(r):
        v = 0
        for f in r.fields:
            if f.kind == "RW":
                v |= (state[(r.name, f.name)] << f.lsb) & f.mask
            elif f.kind == "W1C":
                v |= (state[(r.name, f.name)] << f.lsb) & f.mask
            elif f.kind == "CONST":
                v |= (f.reset << f.lsb) & f.mask
            elif f.kind == "RO":
                v |= (inputs.get((r.name, f.name), 0) << f.lsb) & f.mask
        return v

    for r in regs:
        for f in r.fields:
            if f.kind in ("RW",):
                state[(r.name, f.name)] = f.reset
            if f.kind == "W1C":
                state[(r.name, f.name)] = 0

    a("        // 1) Reset values")
    for r in regs:
        a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name} reset\", v, 32'h{expect(r):08X});")
    a("")
    for r in regs:
        n = r.name.lower()
        rw = [f for f in r.fields if f.kind == "RW"]
        a(f"        // ---- {r.name}")
        # RO inputs
        for f in r.fields:
            if f.kind == "RO":
                val = rnd.getrandbits(f.width) or 1
                inputs[(r.name, f.name)] = val
                a(f"        i_{fbase(r, f)} = {_hex(f.width, val)};")
        if any(f.kind == "RO" and f.sync for f in r.fields):
            a("        repeat (3) @(posedge clk);")
        if any(f.kind == "RO" for f in r.fields):
            a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name} RO inputs\", v, 32'h{expect(r):08X});")
        # RW full write
        if rw:
            val = 0
            for f in rw:
                fv = rnd.getrandbits(f.width) or 1
                val |= fv << f.lsb
            a(f"        axi_write(32'h{base_addr + r.offset:08X}, 32'h{val:08X}, 4'hF);")
            for f in rw:
                state[(r.name, f.name)] = (val & f.mask) >> f.lsb
            a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name} RW write\", v, 32'h{expect(r):08X});")
            for f in rw:
                a(f"        check(\"o_{fbase(r, f)}\", o_{fbase(r, f)}, 32'h{state[(r.name, f.name)]:X});")
            # byte-lane write: only lane 0
            val2 = 0
            for f in rw:
                val2 |= (rnd.getrandbits(f.width)) << f.lsb
            a(f"        axi_write(32'h{base_addr + r.offset:08X}, 32'h{val2:08X}, 4'h1);")
            for f in rw:
                old = state[(r.name, f.name)] << f.lsb
                new = (old & ~0xFF) | (val2 & 0xFF)
                state[(r.name, f.name)] = (new & f.mask) >> f.lsb
            a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name} WSTRB lane 0\", v, 32'h{expect(r):08X});")
        # PULSE
        for f in r.fields:
            if f.kind == "PULSE":
                p = f"o_{fbase(r, f)}"
                val = (1 << f.width) - 1
                a(f"        cnt_{p} = 0;")
                a(f"        axi_write(32'h{base_addr + r.offset:08X}, 32'h{(val << f.lsb) | _rw_bits(r, state):08X}, 4'hF);")
                a("        repeat (3) @(posedge clk);")
                a(f"        check(\"{p} one pulse\", cnt_{p}, 1);")
                a(f"        check(\"{p} value\", last_{p}, 32'h{val:X});")
                a(f"        check(\"{p} idle\", {p}, 0);")
        # W1C
        for f in r.fields:
            if f.kind == "W1C":
                s = f"i_{fbase(r, f)}"
                val = (1 << f.width) - 1
                a(f"        @(posedge clk) {s} <= {_hex(f.width, val)}; @(posedge clk) {s} <= 0;")
                state[(r.name, f.name)] = val
                a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name}.{f.name} set\", v, 32'h{expect(r):08X});")
                if f.irq:
                    a(f"        check(\"o_irq set by {f.name}\", o_irq, 1);")
                a(f"        axi_write(32'h{base_addr + r.offset:08X}, 32'h{(val << f.lsb) | _rw_bits(r, state):08X}, 4'hF);")
                state[(r.name, f.name)] = 0
                a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name}.{f.name} W1C\", v, 32'h{expect(r):08X});")
                if f.irq:
                    a(f"        check(\"o_irq cleared\", o_irq, 0);")
        # strobes
        if r.wr_strobe:
            a(f"        cnt_o_{n}_wr = 0;")
            a(f"        axi_write(32'h{base_addr + r.offset:08X}, 32'h{_rw_bits(r, state):08X}, 4'hF);")
            a("        repeat (2) @(posedge clk);")
            a(f"        check(\"o_{n}_wr strobe\", cnt_o_{n}_wr, 1);")
        if r.rd_strobe:
            a(f"        cnt_o_{n}_rd = 0;")
            a(f"        axi_read(32'h{base_addr + r.offset:08X}, v);")
            a("        repeat (2) @(posedge clk);")
            a(f"        check(\"o_{n}_rd strobe\", cnt_o_{n}_rd, 1);")
        a("")
    # unused offset
    used = {r.index for r in regs}
    hole = next((i for i in range(1 << spec.reg_aw) if i not in used), None)
    if hole is not None:
        a("        // Unused offset reads 0")
        a(f"        axi_read(32'h{base_addr + hole * 4:08X}, v); check(\"unused offset 0x{hole * 4:02X}\", v, 0);")
    a("        // Final read-back of every register")
    for r in regs:
        a(f"        axi_read(32'h{base_addr + r.offset:08X}, v); check(\"{r.name} final\", v, 32'h{expect(r):08X});")
    a("")
    a("        $display(\"%0d checks, %0d errors: %0s\", checks, errors, errors ? \"FAILED\" : \"PASSED\");")
    a("        $finish;")
    a("    end")
    a("    initial begin #2000000; $display(\"TIMEOUT\"); $finish; end")
    a("endmodule")
    a("`default_nettype wire")
    return "\n".join(L) + "\n"


def _rw_bits(reg, state):
    """Current RW field values, so writes aimed at other fields keep them."""
    v = 0
    for f in reg.fields:
        if f.kind == "RW":
            v |= (state[(reg.name, f.name)] << f.lsb) & f.mask
    return v


# ============================================================================
def output_texts(spec: ModuleSpec, only=None):
    """{file name: text} of the generated text files, in output order; `only`: just that file.
    Verilog: the IP, the HDL Designer block (spec.packed), instantiation templates, testbench."""
    m = spec.module
    from . import axilpack
    files = {f"{m}.v": lambda: gen_verilog(spec)}      # the IP itself: plain S_AXI_* ports
    if spec.packed:                                     # HDL Designer block around the plain IP
        files[f"{m}_pk.v"] = lambda: hdl_designer_files(axilpack.parse_module(gen_verilog(spec), m))[f"{m}_pk.v"]
    files[f"{m}_inst.v"] = lambda: gen_inst(spec)
    if spec.packed:
        files[f"{m}_pk_inst.v"] = \
            lambda: hdl_designer_files(axilpack.parse_module(gen_verilog(spec), m))[f"{m}_pk_inst.v"]
    files[f"{m}_tb.v"] = lambda: gen_testbench(spec)
    files[f"{m}_regs.h"] = lambda: gen_header(spec)
    files[f"{m}_regs.md"] = lambda: gen_markdown(spec)
    if only is not None:
        return files[only]()
    return {name: make() for name, make in files.items()}


def verilog_files(spec: ModuleSpec):
    """Names of the generated Verilog files (for previews)."""
    m = spec.module
    names = [f"{m}.v"] + ([f"{m}_pk.v"] if spec.packed else []) + [f"{m}_inst.v"]
    return names + ([f"{m}_pk_inst.v"] if spec.packed else []) + [f"{m}_tb.v"]


def generate_all(spec: ModuleSpec, outdir):
    """Write every output file; returns the list of paths."""
    import os
    os.makedirs(outdir, exist_ok=True)
    m = spec.module
    paths = []
    for name, text in output_texts(spec).items():
        p = os.path.join(outdir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        paths.append(p)
    spec_path = os.path.join(outdir, f"{m}.regspec.json")
    spec.save(spec_path)
    paths.append(spec_path)
    proj_path = os.path.join(outdir, f"{m}.axproj.json")
    from .model import IPInstance, Project
    Project(m, [to_ipblock(spec, 0, with_spec=True)], proj_path,
            instances=[IPInstance(m, m, spec.base, 0, True)]).save(proj_path)
    paths.append(proj_path)
    from . import regmap_xlsx
    if regmap_xlsx.available():
        paths.append(export_xlsx(spec, os.path.join(outdir, f"{m}_regmap.xlsx")))
    return paths


def hdl_designer_files(info):
    """<name>_pk.v (SLAVE/MASTER_AXI_REQ/RSP wrapper around the plain module) and its
    instantiation template."""
    from . import axilpack
    name = f"{info['name']}_pk"
    pk_v = axilpack.gen_pack_wrapper(info, name)
    pk_info = axilpack.parse_module(pk_v, name)
    return {f"{name}.v": pk_v, f"{name}_inst.v": axilpack.gen_inst(pk_info)}


def core_spec(spec: ModuleSpec):
    """Register core of a packed IP: same registers, module name <module>_core."""
    import copy
    core = copy.deepcopy(spec)
    core.module = f"{spec.module}_core"
    core.packed = False
    return core


def hdl_files(spec: ModuleSpec):
    """Synthesis files of the IP, top first (the HDL Designer wrapper when it is generated)."""
    return [f"{spec.module}_pk.v", f"{spec.module}.v"] if spec.packed else [f"{spec.module}.v"]


def export_xlsx(spec: ModuleSpec, path, base=None):
    """Register map workbook (see regmap_xlsx.py); addresses follow the base cell in the sheet."""
    from . import regmap_xlsx
    return regmap_xlsx.export_xlsx([to_ipblock(spec, base)], path, f"{spec.module} kütük haritası")


def example_spec():
    F, R, V = FieldSpec, RegSpec, EnumValue
    return ModuleSpec("example_ip_axil", 0x80002000, "Example IP register wrapper", [
        R("ID", 0x00, "IP identification", fields=[
            F("VERSION", 0, 16, "CONST", 0x0102, "Version 1.2"),
            F("MAGIC", 16, 16, "CONST", 0xA5A5, "Constant pattern")]),
        R("CTRL", 0x04, "Control", wr_strobe=True, fields=[
            F("ENABLE", 0, 1, "RW", 0, "IP enable", values=[
                V(0, "IP disabled", "Disable the IP (outputs idle)"),
                V(1, "IP enabled", "Enable the IP")]),
            F("MODE", 1, 3, "RW", 2, "Operating mode\nOther codes are reserved.", values=[
                V(0, "Idle", "Stop"), V(1, "Single shot", "Run one transfer per START"),
                V(2, "Continuous", "Run continuously"), V(3, "Loopback", "Internal loopback test")]),
            F("START", 8, 1, "PULSE", desc="Start a transfer", values=[
                V(1, "", "Starts one transfer (ignored while BUSY=1)")]),
            F("SOFT_RESET", 9, 1, "PULSE", desc="Reset the IP"),
            F("DIVIDER", 16, 16, "RW", 0x1B2, "Clock divider")]),
        R("STATUS", 0x08, "Status", fields=[
            F("BUSY", 0, 1, "RO", desc="IP busy", values=[
                V(0, "Idle, a new START is accepted"), V(1, "Transfer in progress")]),
            F("LEVEL", 4, 8, "RO", desc="FIFO level"),
            F("EXT_PIN", 16, 2, "RO", desc="Asynchronous inputs", sync=True)]),
        R("IRQ", 0x0C, "Event flags", fields=[
            F("DONE", 0, 1, "W1C", desc="Transfer finished", irq=True, values=[
                V(0, "No transfer finished since last clear", "No effect"),
                V(1, "A transfer finished", "Clear the flag")]),
            F("ERROR", 1, 1, "W1C", desc="Transfer error", irq=True),
            F("OVERRUN", 4, 4, "W1C", desc="Overrun per channel")]),
        R("DATA", 0x14, "Data FIFO", rd_strobe=True, wr_strobe=True, fields=[
            F("RXDATA", 0, 16, "RO", desc="Head of RX FIFO (read pops)"),
            F("TXDATA", 16, 16, "RW", desc="TX data (write pushes)")]),
    ])


def main():
    import argparse
    ap = argparse.ArgumentParser(description="easyaxil-based AXI4-Lite wrapper generator")
    ap.add_argument("spec", nargs="?", help=".regspec.json file (omit with --example)")
    ap.add_argument("-o", "--outdir", default="axilgen_out")
    ap.add_argument("--example", action="store_true", help="use the built-in example spec")
    ap.add_argument("--project", help="also add/replace the IP in this .axproj.json")
    ap.add_argument("--pack", metavar="VFILE", help="write <module>_pk.v (packed REQ/RSP AXI ports) for a Verilog module")
    ap.add_argument("--module", help="module name for --pack when the file has several")
    ap.add_argument("--unpacked", action="store_true", help="--xbar: same as --xbar-style signals")
    ap.add_argument("--xbar-style", choices=("packed", "signals"), default="packed",
                    help="--xbar: signals = <name>.v with separate AXI ports only; "
                         "packed = also <name>_pk.v (HDL Designer, REQ/RSP per port)")
    ap.add_argument("--signals", action="store_true", help="IP: do not write the HDL Designer wrapper <module>_pk.v")
    ap.add_argument("--xbar", metavar="AXPROJ", help="generate an axilxbar wrapper for the IPs of a debug project")
    ap.add_argument("--nm", type=int, default=1, help="master ports for --xbar (extra ports disabled)")
    ap.add_argument("--ns", type=int, default=0, help="slave ports for --xbar (extra ports disabled)")
    ap.add_argument("--xbar-name", default="axil_xbar", help="module name for --xbar")
    a = ap.parse_args()
    if a.pack:
        from . import axilpack
        name, text = axilpack.pack_file(a.pack, a.module)
        os.makedirs(a.outdir, exist_ok=True)
        out = os.path.join(a.outdir, f"{name}_pk.v")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(out)
        return
    if a.xbar:
        from . import xbargen
        from .model import Project
        if a.xbar.endswith(".xbar.json"):
            xs = xbargen.XbarSpec.load(a.xbar)
        else:
            xs = xbargen.XbarSpec(a.xbar_name, [xbargen.XbarMaster("hexbus")],
                                  xbargen.slaves_from_project(Project.load(a.xbar)))
            xs.resize(nm=a.nm, ns=max(a.ns, len(xs.slaves)))
            xs.style = "signals" if a.unpacked else a.xbar_style
        errs = xs.validate()
        if errs:
            raise SystemExit("\n".join("error: " + e for e in errs))
        for p in xbargen.write_xbar(xs, a.outdir):
            print(p)
        return
    if not (a.spec or a.example):
        ap.error("spec file or --example required")
    spec = example_spec() if a.example else ModuleSpec.load(a.spec)
    if a.signals:
        spec.packed = False
    errs = spec.validate()
    if errs:
        raise SystemExit("\n".join("error: " + e for e in errs))
    for p in generate_all(spec, a.outdir):
        print(p)
    if a.project:
        add_to_project(spec, a.project, replace=True)
        print("added to", a.project)


if __name__ == "__main__":
    main()
