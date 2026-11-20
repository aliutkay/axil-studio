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
    or an external slave (ip == "") that has no register map in the project."""
    name: str
    ip: str = ""            # IP definition name; "" = external slave
    base: int = 0
    window: int = 0         # address window in bytes; 0 = smallest that fits (min 4 KB)
    fixed: bool = False     # keeps its base when the map is auto-addressed
    span: int = 0           # external slaves: bytes used (IPs: from the register map)

    @property
    def external(self):
        return not self.ip

    def to_dict(self):
        d = {"name": self.name, "ip": self.ip, "base": hex32(self.base)}
        if self.window:
            d["window"] = f"0x{self.window:X}"
        if self.fixed:
            d["fixed"] = True
        if self.external:
            d["span"] = f"0x{self.span:X}"
        return d

    @staticmethod
    def from_dict(d):
        return IPInstance(d["name"], d.get("ip", ""), parse_int(d.get("base", 0)), parse_int(d.get("window", 0)),
                          bool(d.get("fixed", False)), parse_int(d.get("span", 0)))


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
    address_map: Optional[dict] = None    # Address Map Editor settings ({"start": "0x40000000"})

    def get_ip(self, name) -> Optional[IPBlock]:
        """IP definition by name."""
        return next((i for i in self.ips if i.name == name), None)

    def get_instance(self, name) -> Optional[IPInstance]:
        return next((i for i in self.instances if i.name == name), None)

    def instances_of(self, ip_name):
        return [i for i in self.instances if i.ip == ip_name]

    def address_ips(self) -> List[IPBlock]:
        """One IPBlock per address-map instance of an IP: instance name, instance base and the
        definition's registers (shared, not copied). External slaves are left out."""
        out = []
        for inst in self.instances:
            d = self.get_ip(inst.ip) if inst.ip else None
            if d is not None:
                out.append(IPBlock(inst.name, inst.base, d.desc, d.registers, d.source, d.version, ref=d.name))
        return out

    def get_address_ip(self, name) -> Optional[IPBlock]:
        return next((i for i in self.address_ips() if i.name == name), None)

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


def gpio_example_ip(base=0x60000000):
    """Register map of ZipCPU axilgpio (wb2axip/rtl/axilgpio.v)."""
    outs = [Field("OUT0", 0, 1, "Output bit 0"), Field("OUT1", 1, 1, "Output bit 1")]
    ins = [Field(f"IN{i}", i, 1, f"Input bit {i}") for i in range(5)]
    return IPBlock("GPIO0", base, "ZipCPU axilgpio", [
        Register("LOAD", 0x00, "RW", 0, "Output data (overwrite)", list(outs)),
        Register("SET", 0x04, "WO", 0, "Set output bits where 1 is written", list(outs)),
        Register("CLEAR", 0x08, "WO", 0, "Clear output bits where 1 is written", list(outs)),
        Register("TOGGLE", 0x0C, "WO", 0, "Toggle output bits where 1 is written", list(outs)),
        Register("INPUT", 0x10, "RO", 0, "Input data (2-FF synchronized)", list(ins)),
        Register("CHANGED", 0x14, "RW", 0, "Input toggle flags, write 1 to clear", list(ins)),
        Register("INT_MASK", 0x18, "RW", 0, "1 = toggle does not raise interrupt", list(ins)),
        Register("INT_ACTIVE", 0x1C, "RO", 0, "CHANGED & ~INT_MASK", list(ins)),
    ])


def uart_phy_example_ip(base=0x60001000):
    """Register map of axiluart_phy (ZipCPU axiluart_v2 + uart_phy_adapter)."""
    F, R = Field, Register
    return IPBlock("UART0", base, "axiluart_phy (axiluart_v2 + uart_phy_adapter)", [
        R("SETUP", 0x00, "RW", 0x400001B2, "Line format and baud divider (write resets RX/TX)", [
            F("BAUD_DIV", 0, 24, "ACLK / baud"), F("PAR_SEL", 24, 1, "Even(1)/odd(0), or mark/space if fixed"),
            F("PAR_FIXED", 25, 1, "Fixed parity bit"), F("PAR_EN", 26, 1, "Parity enable"),
            F("STOP2", 27, 1, "Two stop bits"), F("DATA_BITS", 28, 2, "0:8 1:7 2:6 3:5 bits"),
            F("NO_FLOW", 30, 1, "1: ignore RTS/CTS")]),
        R("FIFO", 0x04, "RO", 0, "RX [15:0] and TX [31:16] FIFO status", [
            F("RX_AVAIL", 0, 1, "RX data available"), F("RX_HALF", 1, 1, "RX FIFO half full"),
            F("RX_FILL", 2, 10, "Bytes waiting in RX FIFO"), F("RX_LGLEN", 12, 4, "log2 RX FIFO depth"),
            F("TX_SPACE", 16, 1, "TX FIFO can accept data"), F("TX_HALF", 17, 1, "TX FIFO half empty"),
            F("TX_FREE", 18, 10, "Free TX FIFO entries"), F("TX_LGLEN", 28, 4, "log2 TX FIFO depth")]),
        R("RXREG", 0x08, "RO", 0x100, "Received byte and flags (reading pops the RX FIFO)", [
            F("DATA", 0, 8, "Received byte"), F("EMPTY", 8, 1, "1: no data, DATA invalid"),
            F("PERR", 9, 1, "Parity error (sticky)"), F("FERR", 10, 1, "Frame error (sticky)"),
            F("BREAK", 11, 1, "Break condition on the line"), F("OVFL", 12, 1, "RX FIFO overflow (sticky)"),
            F("BYTE_PERR", 13, 1, "This byte had a parity error"),
            F("BYTE_FERR", 14, 1, "This byte had a frame error")]),
        R("RX_CLEAR", 0x08, "WO", 0, "Write-only view of RXREG for clearing flags", [
            F("PERR_CLR", 9, 1, "Write 1 to clear PERR"), F("FERR_CLR", 10, 1, "Write 1 to clear FERR"),
            F("RX_RESET", 12, 1, "Write 1 to reset receiver and RX FIFO (clears OVFL)")]),
        R("TXREG", 0x0C, "WO", 0, "Write a byte to the TX FIFO", [
            F("DATA", 0, 8, "Byte to send"), F("BREAK", 9, 1, "1: hold line in break"),
            F("TX_RESET", 12, 1, "1: reset transmitter and TX FIFO")]),
        R("PHY_CTRL", 0x10, "RW", 0xC2, "Line interface control (applied when TX idle)", [
            F("DE_MODE", 0, 2, "0: DE low 1: DE high 2: DE while transmitting"),
            F("HALF", 2, 1, "Half duplex: RX disabled while driving"),
            F("ECHO", 3, 1, "Keep RX enabled while driving (HALF only)"),
            F("TX_INV", 4, 1, "Invert TX line"), F("RX_INV", 5, 1, "Invert RX line"),
            F("TX_EN", 6, 1, "Transmitter enable"), F("RX_EN", 7, 1, "Receiver enable"),
            F("APPLY_NOW", 31, 1, "Write 1 to apply even if TX is busy")]),
        R("PHY_TIMING", 0x14, "RW", 0x00FA0032, "Driver enable timing in ACLK cycles", [
            F("LEAD", 0, 16, "DE to start bit"), F("TAIL", 16, 16, "Stop bit end to DE release")]),
        R("PHY_STATUS", 0x18, "RO", 0x0F, "Capabilities and live line state", [
            F("HAS_TX", 0, 1, ""), F("HAS_RX", 1, 1, ""), F("HAS_DE", 2, 1, ""), F("HAS_RE", 3, 1, ""),
            F("HAS_FLOW", 4, 1, ""), F("DRIVING", 8, 1, "DE sequence active"), F("DE", 9, 1, "DE pin"),
            F("RE_N", 10, 1, "/RE pin"), F("PENDING", 11, 1, "New settings waiting for TX idle"),
            F("CFG_ERR", 12, 1, "Unsupported setting was written")]),
        R("PHY_STATUS_CLR", 0x18, "WO", 0, "Write-only view of PHY_STATUS", [
            F("CFG_ERR_CLR", 12, 1, "Write 1 to clear CFG_ERR")]),
    ])
