"""Project, IP, register and bit-field data model with JSON persistence."""

import json
from dataclasses import dataclass, field
from typing import List, Optional

ACCESS_TYPES = ("RW", "RO", "WO")
PROJECT_FORMAT = "axil-uart-project"
PROJECT_VERSION = 2
CONFIG_FORMAT = "axil-uart-config"


def parse_int(text):
    """Parse decimal, 0x-hex or 0b-binary text into an int."""
    text = str(text).strip().replace("_", "")
    if not text:
        raise ValueError("empty value")
    return int(text, 0)


def hex32(value):
    return f"0x{value & 0xFFFFFFFF:08X}"


FIELD_KINDS = ("", "RW", "RO", "PULSE", "W1C", "CONST")

# Which value-meaning columns apply to a field kind ("" = plain register access)
KIND_READS = {"": True, "RW": True, "RO": True, "PULSE": False, "W1C": True, "CONST": True}
KIND_WRITES = {"": True, "RW": True, "RO": False, "PULSE": True, "W1C": True, "CONST": False}


def single_line(text):
    """First line of a (possibly multi-line) description."""
    return str(text).strip().splitlines()[0].strip() if str(text).strip() else ""


@dataclass
class EnumValue:
    """Meaning of one field value: what it means when read / what it does when written."""
    value: int
    read: str = ""
    write: str = ""

    def to_dict(self):
        d = {"value": f"0x{self.value:X}"}
        if self.read:
            d["read"] = self.read
        if self.write:
            d["write"] = self.write
        return d

    @staticmethod
    def from_dict(d):
        return EnumValue(parse_int(d["value"]), d.get("read", ""), d.get("write", ""))


@dataclass
class Field:
    name: str
    lsb: int
    width: int = 1
    desc: str = ""
    kind: str = ""      # optional: RW, RO, PULSE, W1C, CONST (from axilgen)
    values: List[EnumValue] = field(default_factory=list)
    fmt: str = ""       # data type / display format (numfmt.py); "" = auto

    def meanings(self):
        return {e.value: e.read for e in self.values if e.read}

    def value_info(self, v) -> Optional[EnumValue]:
        return next((e for e in self.values if e.value == v), None)

    def read_meaning(self, v):
        e = self.value_info(v)
        return e.read if e else ""

    def write_choices(self):
        return [e for e in self.values if e.write]

    @property
    def mask(self):
        return ((1 << self.width) - 1) << self.lsb

    @property
    def bits(self):
        msb = self.lsb + self.width - 1
        return f"[{self.lsb}]" if self.width == 1 else f"[{msb}:{self.lsb}]"

    def extract(self, value):
        return (value & self.mask) >> self.lsb

    def insert(self, value, field_value):
        return (value & ~self.mask) | ((field_value << self.lsb) & self.mask)

    def to_dict(self):
        d = {"name": self.name, "lsb": self.lsb, "width": self.width, "desc": self.desc}
        if self.kind:
            d["kind"] = self.kind
        if self.values:
            d["values"] = [e.to_dict() for e in sorted(self.values, key=lambda e: e.value)]
        if self.fmt:
            d["fmt"] = self.fmt
        return d

    @staticmethod
    def from_dict(d):
        return Field(d["name"], int(d["lsb"]), int(d.get("width", 1)), d.get("desc", ""),
                     d.get("kind", ""), [EnumValue.from_dict(e) for e in d.get("values", [])],
                     d.get("fmt", ""))


@dataclass
class Register:
    name: str
    offset: int
    access: str = "RW"
    reset: int = 0
    desc: str = ""
    fields: List[Field] = field(default_factory=list)

    @property
    def readable(self):
        return self.access in ("RW", "RO")

    @property
    def writable(self):
        return self.access in ("RW", "WO")

    def get_field(self, name) -> Optional[Field]:
        return next((f for f in self.fields if f.name == name), None)

    def mask_of(self, *kinds):
        m = 0
        for f in self.fields:
            if f.kind in kinds:
                m |= f.mask
        return m

    def to_dict(self):
        return {"name": self.name, "offset": hex32(self.offset), "access": self.access,
                "reset": hex32(self.reset), "desc": self.desc,
                "fields": [f.to_dict() for f in self.fields]}

    @staticmethod
    def from_dict(d):
        return Register(d["name"], parse_int(d["offset"]), d.get("access", "RW"),
                        parse_int(d.get("reset", 0)), d.get("desc", ""),
                        [Field.from_dict(f) for f in d.get("fields", [])])


@dataclass
class IPBlock:
    """Register map of an IP. In a project this is the IP *definition* (base unused); the
    address map places it with IPInstance entries, so one IP can be used several times.
    Project.address_ips() gives one IPBlock per instance with its name and base."""
    name: str
    base: int = 0
    desc: str = ""
    registers: List[Register] = field(default_factory=list)
    source: str = ""        # library the IP was taken from ("" = made in this project)
    version: str = ""       # IP version (from the register map / library)
    regspec: Optional[dict] = None   # full register spec (axilgen.ModuleSpec.to_dict()) when known
    ref: str = ""           # address-map view only: name of the IP definition (not saved)

    def get_register(self, name) -> Optional[Register]:
        return next((r for r in self.registers if r.name == name), None)

    def address(self, reg: Register):
        return self.base + reg.offset

    @property
    def span(self):
        """Bytes used by the register map (last register + 4)."""
        return max((r.offset for r in self.registers), default=0) + 4

    def to_dict(self, with_base=True):
        d = {"name": self.name}
        if with_base:
            d["base"] = hex32(self.base)
        d.update({"desc": self.desc, "registers": [r.to_dict() for r in self.registers]})
        if self.source:
            d["source"] = self.source
        if self.version:
            d["version"] = self.version
        if self.regspec:
            d["regspec"] = self.regspec
        return d

    @staticmethod
    def from_dict(d):
        return IPBlock(d["name"], parse_int(d.get("base", 0)), d.get("desc", ""),
                       [Register.from_dict(r) for r in d.get("registers", [])], d.get("source", ""),
                       str(d.get("version", "")), d.get("regspec"))


@dataclass
class IPInstance:
    """One entry of the project's address map: an IP definition placed at a base address,
    an external slave (ip == "") that has no register map in the project, or a Sub-bus Bridge.

    A Sub-bus Bridge (bridge=True) takes one window on its bus like a slave; the first
    `self_window` bytes of it belong to the bridge itself, the rest is forwarded unchanged
    (global addresses) to the interconnect behind it, whose slaves are `children` (IP instances,
    external slaves and further Sub-bus Bridges). `xbar` holds that interconnect's settings:
    {"module": "" (= axil_xbar_<name>), "masters": [extra master names]}; the bridge itself is
    always its MASTER0."""
    name: str
    ip: str = ""            # IP definition name; "" = external slave (or a bridge)
    base: int = 0           # global address
    window: int = 0         # address window set by hand (bytes); 0 = the project's default window
                            # (bridges: 0 = grows with its contents)
    fixed: bool = False     # keeps its base when the map is auto-addressed
    span: int = 0           # external slaves: bytes used (IPs: from the register map)
    bridge: bool = False
    self_window: int = 0    # bridges: bytes reserved for the bridge itself at the start of its window
    children: list = field(default_factory=list)
    xbar: Optional[dict] = None

    @property
    def external(self):
        return not self.ip and not self.bridge

    @property
    def xbar_module(self):
        return (self.xbar or {}).get("module") or f"axil_xbar_{self.name}"

    @property
    def xbar_masters(self):
        return list((self.xbar or {}).get("masters", []))

    def to_dict(self):
        d = {"name": self.name, "ip": self.ip, "base": hex32(self.base)}
        if self.bridge:
            d = {"name": self.name, "kind": "bridge", "base": hex32(self.base)}
        if self.window:
            d["window"] = f"0x{self.window:X}"
        if self.fixed:
            d["fixed"] = True
        if self.external:
            d["span"] = f"0x{self.span:X}"
        if self.bridge:
            if self.self_window:
                d["self_window"] = f"0x{self.self_window:X}"
            if self.xbar and (self.xbar.get("module") or self.xbar.get("masters")):
                d["xbar"] = self.xbar
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @staticmethod
    def from_dict(d):
        bridge = d.get("kind") == "bridge"
        return IPInstance(d["name"], "" if bridge else d.get("ip", ""), parse_int(d.get("base", 0)),
                          parse_int(d.get("window", 0)), bool(d.get("fixed", False)), parse_int(d.get("span", 0)),
                          bridge, parse_int(d.get("self_window", 0)),
                          [IPInstance.from_dict(c) for c in d.get("children", [])] if bridge else [],
                          d.get("xbar") if bridge else None)


@dataclass
class Project:
    name: str = "Untitled"
    ips: List[IPBlock] = field(default_factory=list)          # IP definitions (register maps)
    path: Optional[str] = None
    interconnect: Optional[dict] = None   # Interconnect Configurator settings (module, style, masters)
    debug: Optional[dict] = None          # UART-to-AXI Debug Bridge settings (extra IPs ...)
    libraries: List[str] = field(default_factory=list)   # IP library files used (relative to the project)
    generator: str = ""                   # program and version that wrote the file
    instances: List[IPInstance] = field(default_factory=list)   # address map, in slave port order
    address_map: Optional[dict] = None    # Address Map Editor settings ({"start": "0x00000000"})

    def get_ip(self, name) -> Optional[IPBlock]:
        """IP definition by name."""
        return next((i for i in self.ips if i.name == name), None)

    def walk(self, items=None, parent=None):
        """(instance, parent bridge or None) for every entry of the address map, depth first,
        in port order."""
        for inst in self.instances if items is None else items:
            yield inst, parent
            if inst.bridge:
                yield from self.walk(inst.children, inst)

    def all_instances(self):
        return [i for i, _ in self.walk()]

    def leaf_instances(self):
        """IP instances and external slaves (no bridges), at every level."""
        return [i for i, _ in self.walk() if not i.bridge]

    def bridges(self):
        return [i for i, _ in self.walk() if i.bridge]

    def parent_of(self, inst):
        return next((p for i, p in self.walk() if i is inst), None)

    def level(self, bridge=None):
        """Slave list of the top interconnect (None) or of the one behind `bridge`."""
        return bridge.children if bridge is not None else self.instances

    def remove_instances(self, pred, items=None):
        """Remove every instance (at any level) for which pred(inst) is true."""
        items = self.instances if items is None else items
        items[:] = [i for i in items if not pred(i)]
        for i in items:
            if i.bridge:
                self.remove_instances(pred, i.children)

    def get_instance(self, name) -> Optional[IPInstance]:
        return next((i for i, _ in self.walk() if i.name == name), None)

    def instances_of(self, ip_name):
        return [i for i in self.leaf_instances() if i.ip == ip_name]

    def address_ips(self) -> List[IPBlock]:
        """One IPBlock per address-map instance of an IP: instance name, instance base and the
        definition's registers (shared, not copied). External slaves and bridges are left out."""
        out = []
        for inst in self.leaf_instances():
            d = self.get_ip(inst.ip) if inst.ip else None
            if d is not None:
                out.append(IPBlock(inst.name, inst.base, d.desc, d.registers, d.source, d.version, ref=d.name))
        return out

    def save(self, path=None):
        self.path = path or self.path
        data = {"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "name": self.name,
                "ips": [i.to_dict(with_base=False) for i in self.ips],
                "instances": [i.to_dict() for i in self.instances]}
        if self.address_map:
            data["address_map"] = self.address_map
        if self.generator:
            data["generator"] = self.generator
        if self.interconnect:
            data["interconnect"] = self.interconnect
        if self.debug:
            data["debug"] = self.debug
        if self.libraries:
            data["libraries"] = list(self.libraries)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format") != PROJECT_FORMAT:
            raise ValueError("Not a project file")
        proj = Project(data.get("name", "Untitled"),
                       [IPBlock.from_dict(i) for i in data.get("ips", [])], path,
                       interconnect=data.get("interconnect"), debug=data.get("debug"),
                       libraries=list(data.get("libraries", [])), generator=data.get("generator", ""),
                       instances=[IPInstance.from_dict(i) for i in data.get("instances", [])],
                       address_map=data.get("address_map"))
        if "instances" not in data:
            _upgrade_v1(proj, data)
        for ip in proj.ips:
            ip.base = 0
        return proj


def _upgrade_v1(proj, data):
    """Version 1 files kept one IP per address (base in the IP) and the slave list in
    "interconnect": turn them into IP definitions + address-map instances."""
    ic = dict(data.get("interconnect") or {})
    by_name = {ip.name: ip for ip in proj.ips}
    done = set()
    for s in ic.get("slaves", []):
        if not s.get("enabled", True):
            continue
        name = s.get("name", "")
        window = ((~parse_int(s.get("mask", "0xFFFFF000"))) & 0xFFFFFFFF) + 1
        if s.get("external"):
            proj.instances.append(IPInstance(name, "", parse_int(s.get("base", 0)), window, True,
                                             parse_int(s.get("span", 0))))
        elif name in by_name:
            proj.instances.append(IPInstance(name, name, by_name[name].base, window, bool(s.get("fixed"))))
            done.add(name)
    excluded = set(ic.get("excluded", []))
    for ip in proj.ips:
        if ip.name not in done and ip.name not in excluded:
            proj.instances.append(IPInstance(ip.name, ip.name, ip.base, 0, True))
    if "auto" in ic:
        proj.address_map = {"start": ic["auto"].get("start", "0x40000000")}
    if ic:
        proj.interconnect = {k: ic[k] for k in ("module", "style", "masters") if k in ic} or None


def save_config(path, writes, description=""):
    """writes: list of (ip_name, register_name, value)."""
    data = {"format": CONFIG_FORMAT, "version": 1, "description": description,
            "writes": [{"ip": ip, "register": reg, "value": hex32(val)}
                       for ip, reg, val in writes]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != CONFIG_FORMAT:
        raise ValueError("Not a config file")
    return [(w["ip"], w["register"], parse_int(w["value"])) for w in data.get("writes", [])]
