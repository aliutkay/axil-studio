"""Project address map: where each IP instance (and external slave) sits on the bus.

The address map is a tree of model.IPInstance entries. The top list is the top interconnect's
slave ports in order (SLAVE0, SLAVE1 …). A Sub-bus Bridge (inst.bridge) is one slave of its
bus; its window starts with `self_window` bytes of its own, the rest is forwarded to the
interconnect behind it, whose slave ports are inst.children (in port order, any depth).

Addresses are global: every bus decodes the whole address and a bridge forwards it unchanged,
so the children of a bridge always lie inside the bridge's window.

An entry is either automatic (consecutive, window-aligned, in list order: from the start
address on the top bus, after the bridge's own window inside a bridge) or fixed (base typed by
hand). A bridge's window grows with its contents unless its size was chosen by hand (then
contents that do not fit are an error). The same IP definition can be placed any number of
times. The Address Map Editor edits the tree, the Interconnect Configurator turns every level
into one crossbar, the Debug Bridge reads registers through it.
"""

from . import xbargen as X
from .model import IPInstance, hex32, parse_int

MIN_WINDOW = 0x1000          # default slave window: 4 KB, larger when the IP needs it (changeable per project)
DEFAULT_START = 0x00000000
BRIDGE_STEM = "SBB"          # Sub-bus Bridge names: SBB_0, SBB_1 …
BRIDGE_LABEL = "Sub-bus Bridge"


def need_window(span, min_window=MIN_WINDOW):
    """Smallest power-of-two window (at least min_window) holding `span` bytes."""
    w = max(4, min_window)
    while w < span:
        w <<= 1
    return w


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


def start_of(project):
    d = project.address_map or {}
    try:
        return parse_int(d.get("start", DEFAULT_START)) & 0xFFFFFFFF
    except ValueError:
        return DEFAULT_START


def set_start(project, start):
    project.address_map = dict(project.address_map or {}, start=f"0x{start & 0xFFFFFFFF:08X}")


# ============================================================================ sizes
def _mask(w):
    return (~(w - 1)) & 0xFFFFFFFF


def _layout(project, items, start, ref_base):
    """Relative bases (to ref_base) of a bus's entries: fixed ones keep base - ref_base,
    automatic ones are placed from `start`. Returns the XbarSlave list (relative bases)."""
    sl = [X.XbarSlave(i.name, (i.base - ref_base) if i.fixed else 0, _mask(window_of(project, i)),
                      0, i.external, i.fixed) for i in items]
    X.auto_address(X.XbarSpec(slaves=sl), start)
    return sl


def content_end(project, bridge):
    """Bytes of a bridge's window in use: its own window and everything behind it."""
    try:
        sl = _layout(project, bridge.children, bridge.self_window, bridge.base)
    except ValueError:
        return 1 << 32
    return max([bridge.self_window] + [s.base + s.window for s in sl])


def span_of(project, inst):
    """Bytes used by an instance: its IP's register map, the size given for an external slave,
    or a bridge's contents."""
    if inst.bridge:
        return content_end(project, inst)
    if inst.external:
        return inst.span or 4
    ip = project.get_ip(inst.ip)
    return ip.span if ip else 4


def auto_window(project, inst):
    """Window an entry gets when its size is not set by hand."""
    if inst.bridge:
        return need_window(content_end(project, inst), default_window(project))
    return max(default_window(project), need_window(span_of(project, inst), 4))


def window_of(project, inst):
    """Address window of an entry. IPs / external slaves: their own size when it was set by hand
    (inst.window, kept when the default changes), otherwise the project default, grown to fit the
    IP. Bridges: the size set by hand, otherwise the smallest one holding their contents."""
    if inst.bridge:
        return inst.window or auto_window(project, inst)
    need = need_window(span_of(project, inst), 4)
    return max(inst.window or default_window(project), need)


def unique_name(project, stem):
    """Instance name for IP `stem`: stem_0, stem_1 … (the first number not taken at any level)."""
    names = {i.name for i in project.all_instances()}
    k = 0
    while f"{stem}_{k}" in names:
        k += 1
    return f"{stem}_{k}"


# ============================================================================ levels
def path_of(project, bridge):
    """Bridges from the top down to `bridge` (inclusive)."""
    out = []
    while bridge is not None:
        out.insert(0, bridge)
        bridge = project.parent_of(bridge)
    return out


def level_name(project, bridge=None):
    return "Üst bus" if bridge is None else bridge.name


def xbar_slaves(project, bridge=None):
    """Slave ports of the top interconnect (bridge None) or of the one behind `bridge`
    (port j = entry j of that level; a bridge is one port with its whole window)."""
    out = []
    for inst in project.level(bridge):
        w = window_of(project, inst)
        out.append(X.XbarSlave(inst.name, inst.base, _mask(w), span_of(project, inst), inst.external, inst.fixed))
    return out


def xbar_masters(project, bridge=None):
    """Masters of a level's interconnect: the top's come from the Interconnect Configurator
    settings, a bridge's interconnect has the bridge as MASTER0 plus its extra masters."""
    if bridge is None:
        ms = (project.interconnect or {}).get("masters") or [{"name": "hexbus"}]
        return [X.XbarMaster(m.get("name", "master")) for m in ms]
    return [X.XbarMaster(bridge.name)] + [X.XbarMaster(n) for n in bridge.xbar_masters]


def xbar_module(project, bridge=None):
    if bridge is None:
        return (project.interconnect or {}).get("module") or "axil_xbar"
    return bridge.xbar_module


def xbar_spec(project, bridge=None, style=None):
    style = style or (project.interconnect or {}).get("style") or "packed"
    return X.XbarSpec(xbar_module(project, bridge), xbar_masters(project, bridge),
                      xbar_slaves(project, bridge), style)


def levels(project):
    """Every interconnect of the project, top first, then depth first: [None, bridge, …]."""
    return [None] + project.bridges()


# ============================================================================ addressing
def readdress(project):
    """Give automatic entries consecutive aligned bases in list order at every level (fixed ones
    stay; the fixed ones inside a bridge move with the bridge). Returns True when a base changed;
    raises ValueError when the map does not fit in 32 bits (the map is then left as it was)."""
    keep = _bases(project)
    try:
        return _place(project, project.instances, start_of(project), 0, 0)
    except ValueError:
        _restore(project, keep)
        raise


def _bases(project):
    """(base, fixed) of every entry, to put the map back after a refused edit."""
    return [(i, i.base, i.fixed) for i, _ in project.walk()]


def _restore(project, keep):
    for inst, base, fixed in keep:
        inst.base, inst.fixed = base, fixed


def set_base(project, inst, base, fixed=True):
    """Give an entry a base typed by hand. A bridge's contents move with it: its fixed children
    keep their place inside its window (automatic ones are placed again by readdress)."""
    delta = (base & 0xFFFFFFFF) - inst.base
    if inst.bridge and delta:
        for c, _ in project.walk(inst.children):
            c.base = (c.base + delta) & 0xFFFFFFFF
    inst.base, inst.fixed = base & 0xFFFFFFFF, fixed


def _place(project, items, start, new_ref, old_ref):
    sl = _layout(project, items, start, old_ref)
    changed = False
    olds = [i.base for i in items]
    for inst, s in zip(items, sl):
        nb = (new_ref + s.base) & 0xFFFFFFFF
        if inst.base != nb:
            inst.base = nb
            changed = True
    if new_ref + max([0] + [s.base + s.window for s in sl]) > 1 << 32:
        bad = next(i for i, s in zip(items, sl) if new_ref + s.base + s.window > 1 << 32)
        raise ValueError(f"{bad.name} 32 bit adres alanına sığmıyor")
    for inst, old in zip(items, olds):
        if inst.bridge:
            changed |= _place(project, inst.children, inst.self_window, inst.base, old)
    return changed


def overlapping(project, bridge=None):
    """Indexes (in the level) of entries whose windows overlap another one."""
    sl = xbar_slaves(project, bridge)
    bad = set()
    for i, a in enumerate(sl):
        for j in range(i):
            b = sl[j]
            if not ((a.base ^ b.base) & a.mask & b.mask):
                bad.update((i, j))
    return bad


def outside(project, bridge):
    """Indexes of a bridge's children that are not inside the forwarded part of its window."""
    w = window_of(project, bridge)
    lo, hi = bridge.base + bridge.self_window, bridge.base + w
    return {j for j, s in enumerate(xbar_slaves(project, bridge)) if s.base < lo or s.base + s.window > hi}


def _fill(project):
    return {b.name: content_end(project, b) for b in project.bridges()}


def _grew(project, err, before):
    """A fit error that is new or got worse since `before` (see _fill)."""
    b = project.get_instance(err.split(":", 1)[0])
    return b is not None and content_end(project, b) > before.get(b.name, 0)


def check_fits(project, before):
    """After an edit: the first hand-sized bridge that the edit made (more) too small, or None."""
    return next((e for e in fit_errors(project) if _grew(project, e, before)), None)


def fit_errors(project):
    """Bridges whose hand-set window is too small for their contents."""
    errs = []
    for b in project.bridges():
        if b.window and content_end(project, b) > b.window:
            errs.append(f"{b.name}: içerik ({content_end(project, b):#x} byte) {BRIDGE_LABEL} penceresine "
                        f"({b.window:#x}) sığmıyor")
    return errs


def validate(project):
    """Error strings of the address map (empty when it is usable)."""
    if not project.instances:
        return ["Adres haritası boş: üst bus'a en az bir slave ekle"]
    errs = []
    seen = set()
    for bridge in levels(project):
        items = project.level(bridge)
        sl = xbar_slaves(project, bridge)
        where = "" if bridge is None else f"{bridge.name}/"
        if bridge is not None and bridge.self_window & (bridge.self_window - 1):
            errs.append(f"{bridge.name}: kendi penceresi 2'nin kuvveti olmalı")
        out = outside(project, bridge) if bridge is not None else set()
        for j, (inst, s) in enumerate(zip(items, sl)):
            tag = f"{where}SLAVE{j} ({inst.name})"
            if not X.IDENT.match(inst.name or ""):
                errs.append(f"{tag}: ad harfle başlamalı, sadece harf/rakam/_ içermeli")
            if inst.name in seen:
                errs.append(f"{tag}: aynı ad iki kez kullanılıyor")
            seen.add(inst.name)
            if not inst.external and not inst.bridge and project.get_ip(inst.ip) is None:
                errs.append(f"{tag}: IP tanımı “{inst.ip}” projede yok")
            if s.base & (s.window - 1):
                errs.append(f"{tag}: base {hex32(s.base)} pencereye ({s.window:#x}) hizalı değil")
            if s.base + s.window > 1 << 32:
                errs.append(f"{tag}: 32 bit adres alanına sığmıyor")
            if j in out:
                errs.append(f"{tag}: {bridge.name} penceresinin iletilen kısmında değil "
                            f"({hex32(bridge.base + bridge.self_window)} – "
                            f"{hex32(bridge.base + window_of(project, bridge) - 1)})")
            for k in range(j):
                o = sl[k]
                if not ((s.base ^ o.base) & s.mask & o.mask):
                    errs.append(f"{tag} ile {where}SLAVE{k} ({o.name}) adres aralıkları çakışıyor")
        if bridge is not None:
            if not items:
                errs.append(f"{bridge.name}: {BRIDGE_LABEL} boş; arkasındaki interconnect'e en az bir slave ekle")
            if not X.IDENT.match(bridge.xbar_module):
                errs.append(f"{bridge.name}: interconnect modül adı geçerli değil")
            if not 1 <= 1 + len(bridge.xbar_masters) <= 16:
                errs.append(f"{bridge.name}: interconnect'te en fazla 16 master olabilir")
            if len(items) > 64:
                errs.append(f"{bridge.name}: interconnect'te en fazla 64 slave olabilir")
    errs += fit_errors(project)
    errs += module_clashes(project)
    return errs


def module_clashes(project):
    """Verilog module names that would be defined twice in the design: the interconnects
    (and their _pk wrappers), the IPs (and theirs) and the ZipCPU modules they need."""
    from .axilgen import RESERVED_MODULES
    errs = []
    style = (project.interconnect or {}).get("style") or "packed"
    owners = {}                                   # lower-case name -> [who defines it]
    for b in levels(project):
        m = xbar_module(project, b)
        what = "üst interconnect" if b is None else f"{b.name} interconnect'i"
        names = [m] + ([f"{m}_pk"] if style == "packed" else [])
        for n in names:
            owners.setdefault(n.lower(), []).append((n, what))
    for ip in project.ips:
        for n in (ip.name, f"{ip.name}_pk"):
            owners.setdefault(n.lower(), []).append((n, f"{ip.name} IP'si"))
    told = set()
    for key, who in sorted(owners.items()):
        if key.endswith("_pk") and key[:-3] in told:
            continue                              # follows from the clash of the module it wraps
        if key in RESERVED_MODULES or len(who) > 1:
            told.add(key)
        if key in RESERVED_MODULES:
            errs.append(f"“{who[0][0]}” modül adı ({who[0][1]}) ZipCPU {key}.v ile çakışıyor")
        elif len(who) > 1:
            errs.append(f"“{who[0][0]}” modül adı birden çok yerde tanımlanıyor: "
                        + ", ".join(sorted({w for _, w in who})))
    return errs


# ============================================================================ editing
def _settle(project):
    try:
        readdress(project)
    except ValueError:
        pass


def add_instance(project, ip_name, name=None, parent=None):
    """Place IP definition `ip_name` once more at the end of a bus (the top one, or the one behind
    bridge `parent`; automatic address); named `name` when given and free, else <ip>_0, <ip>_1 …"""
    taken = {i.name for i in project.all_instances()}
    inst = IPInstance(name if name and name not in taken else unique_name(project, ip_name), ip_name)
    project.level(parent).append(inst)
    _settle(project)
    return inst


def add_bridge(project, parent=None, self_window=0, window=0, name=None):
    """A new Sub-bus Bridge at the end of a bus."""
    inst = IPInstance(name or unique_name(project, BRIDGE_STEM), bridge=True, self_window=self_window,
                      window=window, xbar={"module": "", "masters": []})
    project.level(parent).append(inst)
    _settle(project)
    return inst


def is_inside(project, inst, bridge):
    """True when `bridge` is `inst` or lies somewhere below it."""
    while bridge is not None:
        if bridge is inst:
            return True
        bridge = project.parent_of(bridge)
    return False


def move(project, inst, parent, index=None):
    """Move an entry to bus `parent` (None: top) at `index` (None: the end). Moving to another bus
    makes its address automatic again (a bridge's fixed children keep their place in it).
    Returns a message about the address, or raises ValueError when the move is not possible."""
    if inst.bridge and is_inside(project, inst, parent):
        raise ValueError(f"{inst.name} kendi içine taşınamaz")
    before = _fill(project)
    keep = _bases(project)
    old_parent = project.parent_of(inst)
    src = project.level(old_parent)
    pos = src.index(inst)
    src.remove(inst)
    dst = project.level(parent)
    if index is None:
        index = len(dst)
    elif old_parent is parent and index > pos:
        index -= 1                              # `index` counted the entry itself
    dst.insert(min(index, len(dst)), inst)
    note = ""
    if old_parent is not parent and inst.fixed:
        inst.fixed = False
        note = f"{inst.name} başka bir bus'a taşındı: adresi otomatiğe döndü"
    _settle(project)
    new = [e for e in fit_errors(project) if _grew(project, e, before)]
    if new:
        dst.remove(inst)
        src.insert(pos, inst)
        _restore(project, keep)           # every address (and lock) exactly as before the move
        raise ValueError(new[0])
    return note
