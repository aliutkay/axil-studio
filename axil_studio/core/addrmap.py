"""Project address map: where each IP instance (and external slave) sits on the bus.

The address map is a tree of model.IPInstance entries. The top list is the top interconnect's
slave ports in order (SLAVE0, SLAVE1 …). A subsystem (inst.bridge) is one slave of its bus that
groups the entries behind it (inst.children, in port order, any depth); its window starts with
`self_window` bytes of its own, the rest is forwarded unchanged to its contents.

A subsystem gets an interconnect of its own only when it needs one (has_xbar): more than one
slave behind it, or extra masters. With a single slave and no extra master, the upper bus port
connects straight to that slave and no crossbar is generated.

Addresses are global: every bus decodes the whole address and a subsystem forwards it
unchanged, so its contents always lie inside its window.

An entry is either automatic (consecutive, window-aligned, in list order: from the start
address on the top bus, after the subsystem's own window inside a subsystem) or fixed (base
typed by hand). A subsystem's window grows with its contents unless its size was chosen by hand
(then contents that do not fit are an error). The same IP definition can be placed any number of
times. The Address Map Editor edits the tree, the Interconnect Configurator turns every bus that
needs one into a crossbar, the Debug Bridge reads registers through it.
"""

from . import xbargen as X
from .model import IPInstance, hex32, parse_int
from axil_studio.i18n import tr

MIN_WINDOW = 0x1000          # default slave window: 4 KB, larger when the IP needs it (changeable per project)
DEFAULT_START = 0x00000000
BRIDGE_STEM = "SUB"          # subsystem names: SUB_0, SUB_1 …
BRIDGE_LABEL = tr("Subsystem")


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
    return tr("Top bus") if bridge is None else bridge.name


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
    """Every bus of the project, top first, then depth first: [None, subsystem, …]."""
    return [None] + project.bridges()


def has_xbar(project, bridge=None):
    """True when a bus is a crossbar: always the top bus; a subsystem only with more than one
    slave behind it or extra masters (otherwise its port connects straight to its one slave)."""
    if bridge is None:
        return True
    return len(bridge.children) > 1 or bool(bridge.xbar_masters)


def xbar_levels(project):
    """The buses that are generated as crossbars, in levels() order."""
    return [b for b in levels(project) if has_xbar(project, b)]


def xbar_of(project, inst):
    """The crossbar whose slave port `inst` is reached through, and that port:
    (bus, j) with bus None for the top one. Subsystems without a crossbar are passed through."""
    while True:
        parent = project.parent_of(inst)
        if has_xbar(project, parent):
            return parent, project.level(parent).index(inst)
        inst = parent


def endpoint(project, inst):
    """What a slave port leads to: `inst`, or for a subsystem without a crossbar the entry behind
    it (followed down to an IP, an external slave or a subsystem with a crossbar)."""
    while inst.bridge and not has_xbar(project, inst) and inst.children:
        inst = inst.children[0]
    return inst


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
        raise ValueError(tr("{0} does not fit in the 32-bit address space").format(bad.name))
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
            errs.append(tr("{0}: contents ({1:#x} bytes) do not fit in the {2} window ({3:#x})").format(b.name, content_end(project, b), BRIDGE_LABEL, b.window))
    return errs


def validate(project):
    """Error strings of the address map (empty when it is usable)."""
    if not project.instances:
        return [tr("Address map is empty: add at least one slave to the top bus")]
    errs = []
    seen = set()
    for bridge in levels(project):
        items = project.level(bridge)
        sl = xbar_slaves(project, bridge)
        where = "" if bridge is None else f"{bridge.name}/"
        if bridge is not None and bridge.self_window & (bridge.self_window - 1):
            errs.append(tr("{0}: own window must be a power of two").format(bridge.name))
        out = outside(project, bridge) if bridge is not None else set()
        for j, (inst, s) in enumerate(zip(items, sl)):
            tag = f"{where}SLAVE{j} ({inst.name})"
            if not X.IDENT.match(inst.name or ""):
                errs.append(tr("{0}: name must start with a letter and contain only letters, digits and _").format(tag))
            if inst.name in seen:
                errs.append(tr("{0}: the same name is used twice").format(tag))
            seen.add(inst.name)
            if not inst.external and not inst.bridge and project.get_ip(inst.ip) is None:
                errs.append(tr("{0}: IP definition “{1}” is not in the project").format(tag, inst.ip))
            if s.base & (s.window - 1):
                errs.append(tr("{0}: base {1} is not aligned to its window ({2:#x})").format(tag, hex32(s.base), s.window))
            if s.base + s.window > 1 << 32:
                errs.append(tr("{0}: does not fit in the 32-bit address space").format(tag))
            if j in out:
                errs.append(tr("{0}: not inside the forwarded part of the {1} window ({2} – {3})").format(tag, bridge.name, hex32(bridge.base + bridge.self_window), hex32(bridge.base + window_of(project, bridge) - 1)))
            for k in range(j):
                o = sl[k]
                if not ((s.base ^ o.base) & s.mask & o.mask):
                    errs.append(tr("{0} overlaps {1}SLAVE{2} ({3})").format(tag, where, k, o.name))
        if bridge is not None and not items:
            errs.append(tr("{0}: the subsystem is empty; add at least one slave to it").format(bridge.name))
        if bridge is not None and has_xbar(project, bridge):
            if not X.IDENT.match(bridge.xbar_module):
                errs.append(tr("{0}: interconnect module name is not valid").format(bridge.name))
            if not 1 <= 1 + len(bridge.xbar_masters) <= 16:
                errs.append(tr("{0}: an interconnect can have at most 16 masters").format(bridge.name))
            if len(items) > 64:
                errs.append(tr("{0}: an interconnect can have at most 64 slaves").format(bridge.name))
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
    for b in xbar_levels(project):
        m = xbar_module(project, b)
        what = tr("top interconnect") if b is None else tr("{0} interconnect").format(b.name)
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
            errs.append(tr("Module name “{0}” ({1}) clashes with ZipCPU {2}.v").format(who[0][0], who[0][1], key))
        elif len(who) > 1:
            errs.append(tr("Module name “{0}” is defined in more than one place:").format(who[0][0])
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
    """A new subsystem at the end of a bus."""
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
        raise ValueError(tr("{0} cannot be moved into itself").format(inst.name))
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
        note = tr("{0} moved to another bus: its address is automatic again").format(inst.name)
    _settle(project)
    new = [e for e in fit_errors(project) if _grew(project, e, before)]
    if new:
        dst.remove(inst)
        src.insert(pos, inst)
        _restore(project, keep)           # every address (and lock) exactly as before the move
        raise ValueError(new[0])
    return note
