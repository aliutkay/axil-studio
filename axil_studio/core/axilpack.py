"""
Packed AXI4-Lite ports.

Block-diagram tools connect one net per port, so an AXI4-Lite interface
costs 19 wires. Here an interface is carried on two vectors instead:

    *_AXI_REQ [110:0]  master -> slave (AW, W, B-ready, AR, R-ready)
    *_AXI_RSP  [40:0]  slave  -> master (AW/W-ready, B, AR-ready, R)

The packed bus is always 32-bit address / 32-bit data. Narrower module
ports are truncated / zero-extended inside the wrapper.

gen_pack_wrapper() reads a Verilog-2001/2005 ANSI module header and writes
<module>_pk, which exposes SLAVE_AXI_REQ/RSP (for the module's S_AXI_*
slave port) and/or MASTER_AXI_REQ/RSP (for its M_AXI_* master port) and passes every other port
through unchanged (same name, direction and width).

<module>_pk has no parameters (no generics in HDL Designer): a VHDL top turns Verilog parameters into
`integer` generics, which overflow for 32-bit values such as address masks. The parameter values are
written as numbers in the port widths and in the instantiation of the module: its defaults, or the
values given to gen_pack_wrapper(values=...) / `axilgen --pack FILE --set NAME=VALUE`.
"""

import re
from axil_studio.i18n import tr

# MSB first. (name, width)
REQ_FIELDS = (("AWADDR", 32), ("AWPROT", 3), ("AWVALID", 1), ("WDATA", 32), ("WSTRB", 4), ("WVALID", 1),
              ("BREADY", 1), ("ARADDR", 32), ("ARPROT", 3), ("ARVALID", 1), ("RREADY", 1))
RSP_FIELDS = (("AWREADY", 1), ("WREADY", 1), ("BRESP", 2), ("BVALID", 1), ("ARREADY", 1), ("RDATA", 32),
              ("RRESP", 2), ("RVALID", 1))
REQ_W = sum(w for _, w in REQ_FIELDS)     # 111
RSP_W = sum(w for _, w in RSP_FIELDS)     # 41
REQ_SIGS = {n for n, _ in REQ_FIELDS}
# packed port name per AXI group of a module
PACKED_NAME = {"S": "SLAVE_AXI", "M": "MASTER_AXI"}
RSP_SIGS = {n for n, _ in RSP_FIELDS}


def layout_lines(prefix="//   "):
    """Bit positions of the packed fields, for file headers."""
    out = []
    for title, fields, total in (("REQ", REQ_FIELDS, REQ_W), ("RSP", RSP_FIELDS, RSP_W)):
        pos, parts = total, []
        for n, w in fields:
            msb, lsb = pos - 1, pos - w
            parts.append(f"{n}[{msb}:{lsb}]" if w > 1 else f"{n}[{msb}]")
            pos -= w
        line = f"{prefix}{title} [{total - 1}:0] = "
        cur = line
        for p in parts:
            if len(cur) + len(p) > 96:
                out.append(cur.rstrip())
                cur = prefix + " " * (len(line) - len(prefix))
            cur += p + " "
        out.append(cur.rstrip())
    return out


# =============================================================================
# Minimal ANSI header parser
# =============================================================================
class ParseError(ValueError):
    pass


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _matching(text, i):
    """Index of the ')' matching the '(' at text[i]."""
    depth = 0
    for k in range(i, len(text)):
        c = text[k]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return k
    raise ParseError(tr("Unbalanced parentheses"))


def _split_top(text):
    items, depth, cur = [], 0, ""
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            items.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        items.append(cur.strip())
    return items


def parse_module(text, name=None):
    """Return {'name', 'params': [(kind, decl, pname)], 'ports': [(dir, range, pname)]}."""
    src = _strip_comments(text)
    pat = r"\bmodule\s+(" + (re.escape(name) if name else r"[A-Za-z_]\w*") + r")\b"
    m = re.search(pat, src)
    if not m:
        raise ParseError(tr("Module not found") + (f": {name}" if name else ""))
    mod, i = m.group(1), m.end()
    while i < len(src) and src[i].isspace():
        i += 1
    params = []
    if i < len(src) and src[i] == "#":
        j = src.find("(", i)
        if j < 0:
            raise ParseError(tr("Port list not found"))
        k = _matching(src, j)
        for item in _split_top(src[j + 1:k]):
            pm = re.match(r"(parameter|localparam)\b(.*?)([A-Za-z_]\w*)\s*=\s*(.+)$", item, re.S)
            if not pm:
                raise ParseError(tr("Cannot read parameter: {0}").format(item[:60]))
            params.append((pm.group(1), " ".join(item.split()), pm.group(3)))
        i = k + 1
    while i < len(src) and src[i].isspace():
        i += 1
    if i >= len(src) or src[i] != "(":
        raise ParseError(tr("Port list not found"))
    k = _matching(src, i)
    ports = []
    last_dir, last_rng = None, ""
    for item in _split_top(src[i + 1:k]):
        clean = " ".join(item.split())
        pm = re.match(r"(input|output|inout)\s+(?:(?:wire|reg|logic|signed|unsigned|var)\s+)*"
                      r"(\[[^\]]+\])?\s*([A-Za-z_]\w*)$", clean)
        if pm:
            last_dir, last_rng = pm.group(1), pm.group(2) or ""
            ports.append((last_dir, last_rng, pm.group(3)))
        elif last_dir is not None and re.match(r"^[A-Za-z_]\w*$", clean):
            ports.append((last_dir, last_rng, clean))
        else:
            raise ParseError(tr("Cannot read port (only ANSI headers are supported): {0}").format(item[:60]))
    if not ports:
        raise ParseError(tr("The module has no ports"))
    return {"name": mod, "params": params, "ports": ports}


def axi_groups(info):
    """Prefixes ('S' and/or 'M') that have a usable AXI4-Lite port group."""
    names = {p[2] for p in info["ports"]}
    out = []
    for pre, need in (("S", "S_AXI_AWVALID"), ("M", "M_AXI_AWVALID")):
        if need in names and f"{pre}_AXI_ARVALID" in names:
            out.append(pre)
    return out


# =============================================================================
# Constant expressions (parameter defaults, port ranges)
# =============================================================================
_TOK = re.compile(r"\s*(?:(?P<num>(?:\d[\d_]*)?\s*'[sS]?[bBoOdDhH]\s*[0-9a-fA-F_]+|\d[\d_]*)"
                  r"|(?P<id>\$?[A-Za-z_]\w*)|(?P<op>\*\*|<<<|>>>|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%<>!~&|^?:(),]))")
_BIN = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, "<=": 7, ">": 7, ">=": 7,
        "<<": 8, ">>": 8, "<<<": 8, ">>>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "**": 11}


def _num(text):
    t = text.replace("_", "").replace(" ", "")
    if "'" not in t:
        return int(t)
    base = t.split("'", 1)[1].lstrip("sS")
    radix = {"b": 2, "o": 8, "d": 10, "h": 16}[base[0].lower()]
    return int(base[1:], radix)


def evaluate(expr, env):
    """Value of a Verilog constant expression; `env` gives parameter values. ParseError when it
    uses something else (an unknown name, a function other than $clog2)."""
    toks = []
    pos, text = 0, expr.strip()
    while pos < len(text):
        m = _TOK.match(text, pos)
        if not m or m.end() == pos:
            raise ParseError(tr("Cannot evaluate: {0}").format(expr))
        toks.append((m.lastgroup, m.group(m.lastgroup)))
        pos = m.end()
    toks.append(("end", ""))
    k = [0]

    def peek():
        return toks[k[0]]

    def take(want=None):
        t = toks[k[0]]
        if want is not None and t[1] != want:
            raise ParseError(tr("Cannot evaluate: {0}").format(expr))
        k[0] += 1
        return t

    def unary():
        kind, v = take()
        if kind == "num":
            return _num(v)
        if kind == "id":
            if v == "$clog2":
                take("(")
                x = cond()
                take(")")
                return max(0, (x - 1).bit_length())
            if v not in env:
                raise ParseError(tr("Cannot evaluate {0}: unknown name {1}").format(expr, v))
            return env[v]
        if v == "(":
            x = cond()
            take(")")
            return x
        if v in ("-", "+", "!", "~"):
            x = unary()
            return {"-": -x, "+": x, "!": int(not x), "~": ~x}[v]
        raise ParseError(tr("Cannot evaluate: {0}").format(expr))

    def binary(level):
        x = unary()
        while True:
            op = peek()[1]
            prec = _BIN.get(op) if peek()[0] == "op" else None
            if prec is None or prec < level:
                return x
            take()
            y = binary(prec + (0 if op == "**" else 1))
            x = {"||": lambda: int(bool(x) or bool(y)), "&&": lambda: int(bool(x) and bool(y)),
                 "|": lambda: x | y, "^": lambda: x ^ y, "&": lambda: x & y, "==": lambda: int(x == y),
                 "!=": lambda: int(x != y), "<": lambda: int(x < y), "<=": lambda: int(x <= y),
                 ">": lambda: int(x > y), ">=": lambda: int(x >= y), "<<": lambda: x << y,
                 ">>": lambda: x >> y, "<<<": lambda: x << y, ">>>": lambda: x >> y, "+": lambda: x + y,
                 "-": lambda: x - y, "*": lambda: x * y, "/": lambda: int(x / y) if y else 0,
                 "%": lambda: x % y if y else 0, "**": lambda: x ** y}[op]()

    def cond():
        c = binary(1)
        if peek()[1] == "?":
            take()
            a = cond()
            take(":")
            b = cond()
            return a if c else b
        return c

    v = cond()
    if peek()[0] != "end":
        raise ParseError(tr("Cannot evaluate: {0}").format(expr))
    return v


def _range(rng, env):
    """'[NOUT-1:0]' -> '[29:0]' (ParseError when it cannot be evaluated)."""
    if not rng:
        return ""
    inner = rng.strip()[1:-1]
    depth, q, cut = 0, 0, None
    for i, c in enumerate(inner):           # the ':' of the range, not one of a ?: inside it
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c == "?":
            q += 1
        elif depth == 0 and c == ":":
            if q:
                q -= 1
            else:
                cut = i
                break
    if cut is None:
        raise ParseError(tr("Cannot read range {0}").format(rng))
    return f"[{evaluate(inner[:cut], env)}:{evaluate(inner[cut + 1:], env)}]"


def param_values(info, values=None):
    """[(kind, name, width_or_None, value)] of the module's parameters in order, with `values`
    (name -> text or int) replacing the defaults of real parameters."""
    values = values or {}
    env, out = {}, []
    for kind, decl, pname in info["params"]:
        m = re.match(r"(?:parameter|localparam)\s+(?:integer\s+|signed\s+|unsigned\s+)*(\[[^\]]*\])?\s*"
                     + re.escape(pname) + r"\s*=\s*(.+)$", decl)
        if not m:
            raise ParseError(tr("Cannot read parameter: {0}").format(decl[:60]))
        rng, text = m.group(1), m.group(2)
        if kind == "parameter" and pname in values:
            text = values[pname]
        val = text if isinstance(text, int) else evaluate(str(text), env)
        width = None
        if rng:
            r = _range(rng, env)[1:-1].split(":")
            width = abs(int(r[0]) - int(r[1])) + 1
            val &= (1 << width) - 1
        env[pname] = val
        out.append((kind, pname, width, val))
    unknown = set(values) - {p for k, p, _, _ in out if k == "parameter"}
    if unknown:
        raise ParseError(tr("Unknown parameter(s): {0}").format(", ".join(sorted(unknown))))
    return out


def _literal(width, val):
    """A Verilog literal for a parameter value: sized hex for a ranged parameter or a value that
    does not fit a 32-bit signed integer, decimal otherwise."""
    if width:
        return f"{width}'h{val:X}"
    if val < 0 or val < 1 << 31:
        return str(val)
    return f"{max(32, val.bit_length())}'h{val:X}"


# =============================================================================
# Wrapper generation
# =============================================================================
def gen_pack_wrapper(info, wrapper=None, values=None):
    """<module>_pk: packed AXI ports, every other port unchanged, no parameters (the values are
    numbers in the port widths and the instantiation: the defaults or `values`)."""
    mod = info["name"]
    wrapper = wrapper or f"{mod}_pk"
    groups = axi_groups(info)
    if not groups:
        raise ParseError(tr("No S_AXI_* or M_AXI_* AXI4-Lite port group found"))
    axi = {f"{g}_AXI_{s}" for g in groups for s in REQ_SIGS | RSP_SIGS}
    ports = {p[2]: p for p in info["ports"]}

    L = ["////////////////////////////////////////////////////////////////////////////////",
         f"// Module: {wrapper}",
         "//",
         f"// HDL Designer block: {mod} with packed AXI4-Lite ports (one request and one",
         "// response vector per interface). The plain module is instantiated unchanged inside.",
         "// All other ports are passed through unchanged.",
         "//",
         "// No parameters (no generics for a VHDL top, where 32-bit values would overflow an",
         "// integer). The parameter values of the module are fixed here (regenerate with",
         "// axilgen --pack FILE --set NAME=VALUE, or the AXIL Studio dialog, to change them):"]
    pv = param_values(info, values)
    env = {p: v for _, p, _, v in pv}
    for kind, p, w, v in pv:
        if kind == "parameter":
            L.append(f"//   {p} = {_literal(w, v)}" + (f"  ({v})" if w and v > 9 else ""))
    if not any(k == "parameter" for k, _, _, _ in pv):
        L.append("//   (the module has none)")
    L.append("//")
    for g in groups:
        role = "slave" if g == "S" else "master"
        L.append(f"//   {PACKED_NAME[g]}_REQ / {PACKED_NAME[g]}_RSP : {role} interface ({g}_AXI_*), 32-bit")
    L += ["//", "// Packed layout:"] + layout_lines() + [
        "////////////////////////////////////////////////////////////////////////////////",
        "", "`default_nettype none", ""]

    L.append(f"module {wrapper} (")
    out_ports = []
    for d, rng, pname in info["ports"]:
        if pname in axi:
            continue
        r = _range(rng, env)
        out_ports.append(f"    {d:<6} wire {r + ' ' if r else ''}{pname}")
    for g in groups:
        n = PACKED_NAME[g]
        if g == "S":
            out_ports.append(f"    input  wire [{REQ_W - 1}:0] {n}_REQ")
            out_ports.append(f"    output wire [{RSP_W - 1}:0] {n}_RSP")
        else:
            out_ports.append(f"    output wire [{REQ_W - 1}:0] {n}_REQ")
            out_ports.append(f"    input  wire [{RSP_W - 1}:0] {n}_RSP")
    L.append(",\n".join(out_ports))
    L.append(");")
    L.append("")

    widths = dict(REQ_FIELDS + RSP_FIELDS)
    for g in groups:
        L.append(f"    // {PACKED_NAME[g]}: packed <-> core {g}_AXI_*")
        # 32-bit view of every field
        for n, w in REQ_FIELDS + RSP_FIELDS:
            L.append(f"    wire {f'[{w - 1}:0] ' if w > 1 else ''}{g.lower()}_{n.lower()};")
        req = ", ".join(f"{g.lower()}_{n.lower()}" for n, _ in REQ_FIELDS)
        rsp = ", ".join(f"{g.lower()}_{n.lower()}" for n, _ in RSP_FIELDS)
        if g == "S":
            L.append(f"    assign {{{req}}} = SLAVE_AXI_REQ;")
            L.append(f"    assign SLAVE_AXI_RSP = {{{rsp}}};")
            core_in, core_out = REQ_SIGS, RSP_SIGS
        else:
            L.append(f"    assign MASTER_AXI_REQ = {{{req}}};")
            L.append(f"    assign {{{rsp}}} = MASTER_AXI_RSP;")
            core_in, core_out = RSP_SIGS, REQ_SIGS
        # core-width wires named like the core ports
        for n, _ in REQ_FIELDS + RSP_FIELDS:
            pname = f"{g}_AXI_{n}"
            if pname in ports:
                rng = _range(ports[pname][1], env)
                L.append(f"    wire {rng + ' ' if rng else ''}{pname};")
                if n in core_in:
                    L.append(f"    assign {pname} = {g.lower()}_{n.lower()};")
                else:
                    L.append(f"    assign {g.lower()}_{n.lower()} = {pname};")
            elif n in core_out:
                # field the core does not drive
                default = {"BRESP": "2'b00", "RRESP": "2'b00", "WSTRB": "4'hF"}.get(n, f"{widths[n]}'d0")
                L.append(f"    assign {g.lower()}_{n.lower()} = {default};")
        L.append("")

    inst_params = [(p, _literal(w, v)) for k, p, w, v in pv if k == "parameter"]
    if inst_params:
        L.append(f"    {mod} #(")
        L.append(",\n".join(f"        .{p}({v})" for p, v in inst_params))
        L.append(f"    ) u_core (")
    else:
        L.append(f"    {mod} u_core (")
    L.append(",\n".join(f"        .{p}({p})" for _, _, p in info["ports"]))
    L += ["    );", "", "endmodule", "", "`default_nettype wire", ""]
    return "\n".join(L)


def pack_file(path, module=None, wrapper=None, values=None):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    info = parse_module(text, module)
    return info["name"], gen_pack_wrapper(info, wrapper, values)


# =============================================================================
# Templates from a parsed header
# =============================================================================
def gen_inst(info, inst=None):
    mod = info["name"]
    params = [(p, d.split("=", 1)[1].strip()) for k, d, p in info["params"] if k == "parameter"]
    L = [f"// Instantiation template for {mod}"]
    if params:
        L.append(f"{mod} #(")
        for k, (p, val) in enumerate(params):
            L.append(f"    .{p:<20}({val})" + ("," if k < len(params) - 1 else ""))
        L.append(f") {inst or 'u_' + mod} (")
    else:
        L.append(f"{mod} {inst or 'u_' + mod} (")
    ports = info["ports"]
    for k, (d, r, p) in enumerate(ports):
        sep = "," if k < len(ports) - 1 else " "
        L.append(f"    .{p:<20}(){sep}  // {d} {r}".rstrip())
    L.append(");")
    return "\n".join(L) + "\n"
