"""Project address map: where each IP instance (and external slave) sits on the bus.

The address map is the list of model.IPInstance entries of a project, in slave port order
(SLAVE0, SLAVE1 …). An instance is either automatic (consecutive, window-aligned, in list order
from the start address) or fixed (base typed by hand). The same IP definition can be placed
any number of times. The Address Map Editor edits it, the Interconnect Configurator turns it
into the crossbar's slave ports and the Debug Bridge reads registers through it.
"""

from . import xbargen as X
from .model import IPInstance, hex32, parse_int

MIN_WINDOW = 0x1000          # default slave window: 4 KB, larger when the IP needs it (changeable per project)
DEFAULT_START = 0x40000000


def need_window(span, min_window=MIN_WINDOW):
    """Smallest power-of-two window (at least min_window) holding `span` bytes."""
    w = max(4, min_window)
    while w < span:
        w <<= 1
    return w


def span_of(project, inst):
    """Bytes used by an instance: its IP's register map, or the size given for an external slave."""
    if inst.external:
        return inst.span or 4
    ip = project.get_ip(inst.ip)
    return ip.span if ip else 4


def default_window(project):
    """Window of every instance whose size was not set by hand (project setting, 4 KB by default)."""
    d = project.address_map or {}
    try:
        w = parse_int(d.get("window", MIN_WINDOW))
    except ValueError:
        return MIN_WINDOW
    return w if w >= 4 and not w & (w - 1) else MIN_WINDOW


def set_default_window(project, window):
    project.address_map = dict(project.address_map or {}, window=f"0x{window:X}")


def window_of(project, inst):
    """Address window of an instance: its own size when it was set by hand (inst.window, kept
    when the default changes), otherwise the project default; grown to fit the IP when needed."""
    need = need_window(span_of(project, inst), 4)
    return max(inst.window or default_window(project), need)


def start_of(project):
    d = project.address_map or {}
    try:
        return parse_int(d.get("start", DEFAULT_START)) & 0xFFFFFFFF
    except ValueError:
        return DEFAULT_START


def set_start(project, start):
    project.address_map = dict(project.address_map or {}, start=f"0x{start & 0xFFFFFFFF:08X}")


def unique_name(project, stem):
    """Instance name for IP `stem`: stem_0, stem_1 … (the first number not taken)."""
    names = {i.name for i in project.instances}
    k = 0
    while f"{stem}_{k}" in names:
        k += 1
    return f"{stem}_{k}"


def xbar_slaves(project):
    """The address map as interconnect slave ports (port j = instance j)."""
    out = []
    for inst in project.instances:
        w = window_of(project, inst)
        out.append(X.XbarSlave(inst.name, inst.base, (~(w - 1)) & 0xFFFFFFFF, span_of(project, inst),
                               inst.external, inst.fixed))
    return out


def readdress(project):
    """Give automatic instances consecutive aligned bases in list order (fixed ones stay).
    Returns True when a base changed; raises ValueError when the map does not fit in 32 bits."""
    spec = X.XbarSpec(slaves=xbar_slaves(project))
    X.auto_address(spec, start_of(project))
    changed = False
    for inst, s in zip(project.instances, spec.slaves):
        if inst.base != s.base:
            inst.base = s.base
            changed = True
    return changed


def overlapping(project):
    """Indexes of instances whose windows overlap another one."""
    sl = xbar_slaves(project)
    bad = set()
    for i, a in enumerate(sl):
        for j in range(i):
            b = sl[j]
            if not ((a.base ^ b.base) & a.mask & b.mask):
                bad.update((i, j))
    return bad


def validate(project):
    """Error strings of the address map (empty when it is usable)."""
    errs = []
    seen = set()
    sl = xbar_slaves(project)
    for j, (inst, s) in enumerate(zip(project.instances, sl)):
        tag = f"SLAVE{j} ({inst.name})"
        if not X.IDENT.match(inst.name or ""):
            errs.append(f"{tag}: ad harfle başlamalı, sadece harf/rakam/_ içermeli")
        if inst.name in seen:
            errs.append(f"{tag}: aynı ad iki kez kullanılıyor")
        seen.add(inst.name)
        if not inst.external and project.get_ip(inst.ip) is None:
            errs.append(f"{tag}: IP tanımı “{inst.ip}” projede yok")
        if s.base & (s.window - 1):
            errs.append(f"{tag}: base {hex32(s.base)} pencereye ({s.window:#x}) hizalı değil")
        if s.base + s.window > 1 << 32:
            errs.append(f"{tag}: 32 bit adres alanına sığmıyor")
        for k in range(j):
            o = sl[k]
            if not ((s.base ^ o.base) & s.mask & o.mask):
                errs.append(f"{tag} ile SLAVE{k} ({o.name}) adres aralıkları çakışıyor")
    return errs


def add_instance(project, ip_name, name=None):
    """Place IP definition `ip_name` in the address map once more (at the end, automatic address);
    named `name` when given and free, otherwise <ip>_0, <ip>_1 …"""
    taken = {i.name for i in project.instances}
    inst = IPInstance(name if name and name not in taken else unique_name(project, ip_name), ip_name)
    project.instances.append(inst)
    try:
        readdress(project)
    except ValueError:
        pass
    return inst
