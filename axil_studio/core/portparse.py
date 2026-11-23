"""Port lists of a hardware block, read from Verilog/SystemVerilog or VHDL text.

The Register Map Editor uses it to turn the ports of the IP core that sits behind the register
file into bit fields. Accepted input: a whole module / entity / component, only its port list,
or just port declaration lines, e.g.

    Verilog:  input wire [7:0] a, b,      output reg [WIDTH-1:0] q;     inout c
    VHDL:     a, b : in std_logic_vector(7 downto 0);  q : out unsigned(W-1 downto 0)

Widths may use parameters / generics declared in the same text, simple arithmetic and
$clog2; a width that cannot be worked out is reported (Port.error) and can be typed in.
"""

import ast
import math
import re
from dataclasses import dataclass
from typing import Optional
from axil_studio.i18n import tr

DIRECTIONS = ("in", "out", "inout")
FIELD_WIDTH = 32


@dataclass
class Port:
    name: str
    direction: str              # "in" | "out" | "inout" (seen from the block)
    width: Optional[int]        # None when it could not be worked out
    lsb: int = 0                # index of the lowest bit (e.g. 0 for [7:0], 8 for [15:8])
    type_text: str = ""         # range / type as written
    error: str = ""


# ============================================================================ expressions
def _clog2(v):
    v = int(v)
    return 0 if v <= 1 else (v - 1).bit_length()


def _verilog_number(m):
    base, digits = m.group(2).lower(), m.group(3).replace("_", "")
    return str(int(digits, {"b": 2, "o": 8, "d": 10, "h": 16}[base]))


def evaluate(expr, params, depth=0):
    """Integer value of a width expression (Verilog or VHDL) or None."""
    if expr is None or depth > 20:
        return None
    e = str(expr).strip()
    try:
        e = re.sub(r"(\d*)\s*'[sS]?([bBoOdDhH])\s*([0-9a-fA-F_]+)", _verilog_number, e)   # 8'd10 -> 10
        e = re.sub(r"\b16#([0-9a-fA-F_]+)#", lambda m: str(int(m.group(1).replace("_", ""), 16)), e)
    except ValueError:
        return None
    e = e.replace("$clog2", "clog2")
    e = re.sub(r"\*\*", "^^", e)            # VHDL / SV power, kept apart from *

    def sub(m):
        name = m.group(0)
        if name.lower() in ("clog2", "abs", "max", "min"):
            return name.lower()
        key = next((k for k in params if k.lower() == name.lower()), None)
        if key is None:
            raise KeyError(name)
        v = evaluate(params[key], params, depth + 1)
        if v is None:
            raise KeyError(name)
        return f"({v})"
    try:
        e = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\b", sub, e)
    except KeyError:
        return None
    e = e.replace("^^", "**").replace("/", "//")
    if not re.fullmatch(r"[\d\s+\-*/%()<>,a-z]*", e):
        return None
    try:
        v = _calc(ast.parse(e, mode="eval").body)
    except Exception:  # noqa: BLE001 - not a usable expression (or one too large to be a width)
        return None
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else None


_LIMIT = 1 << 64            # no port width / index comes near this; larger results are refused
_FUNCS = {"clog2": lambda x: _clog2(x), "abs": abs, "max": max, "min": min}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
           ast.Pow: lambda a, b: a ** b, ast.LShift: lambda a, b: a << b, ast.RShift: lambda a, b: a >> b}


def _calc(node):
    """Integer arithmetic of a width expression, without eval: numbers, + - * // % ** << >>,
    unary +/- and clog2/abs/max/min; any value beyond 64 bits is refused (no runaway powers)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        v = node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _calc(node.operand)
        v = -v if isinstance(node.op, ast.USub) else v
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a, b = _calc(node.left), _calc(node.right)
        if isinstance(node.op, (ast.Pow, ast.LShift)) and not 0 <= b <= 64:
            raise ValueError(tr("exponent out of range"))
        v = _BINOPS[type(node.op)](a, b)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS \
            and not node.keywords:
        v = _FUNCS[node.func.id](*[_calc(x) for x in node.args])
    else:
        raise ValueError(tr("not an integer expression"))
    if not -_LIMIT <= v <= _LIMIT:
        raise ValueError(tr("value out of range"))
    return v


def _range_width(hi, lo, params):
    a, b = evaluate(hi, params), evaluate(lo, params)
    if a is None or b is None:
        return None, 0
    return abs(a - b) + 1, min(a, b)


# ============================================================================ helpers
def _split_top(text, sep):
    """Split at `sep` outside (), [] and {}."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _matching_paren(text, i):
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return len(text)


def detect_language(text):
    t = re.sub(r"--[^\n]*", "", text)
    if re.search(r"\b(entity|component)\s+\w+\s+is\b", t, re.I) or \
            re.search(r"\b\w+\s*(,\s*\w+\s*)*:\s*(in|out|inout|buffer)\s+\w", t, re.I):
        return "vhdl"
    return "verilog"


# ============================================================================ Verilog
_V_DIR = re.compile(r"\b(input|output|inout)\b")
_V_TYPES = {"wire", "reg", "logic", "var", "signed", "unsigned", "tri", "integer", "bit", "byte", "shortint", "int",
            "longint", "uwire", "wand", "wor", "supply0", "supply1", "tri0", "tri1", "interconnect"}
_V_FIXED = {"integer": 32, "int": 32, "byte": 8, "shortint": 16, "longint": 64}


def _verilog(text):
    t = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    t = re.sub(r"//[^\n]*", " ", t)
    t = re.sub(r"\(\*.*?\*\)", " ", t, flags=re.S)        # attributes
    # the input/output declarations of functions and tasks are not ports of the module
    t = re.sub(r"\bfunction\b.*?\bendfunction\b", " ", t, flags=re.S)
    t = re.sub(r"\btask\b.*?\bendtask\b", " ", t, flags=re.S)
    name = re.search(r"\bmodule\s+(\w+)", t)
    params = {}
    for m in re.finditer(r"\b(?:parameter|localparam)\b([^;]*?)(?=;|\)\s*\(|\bparameter\b|\blocalparam\b|$)", t, re.S):
        for item in _split_top(m.group(1), ","):
            pm = re.match(r"\s*(?:\w+\s+)*?(?:\[[^\]]*\]\s*)?([A-Za-z_]\w*)\s*=\s*(.+?)\s*$", item, re.S)
            if pm:
                params[pm.group(1)] = pm.group(2)
    ports = []
    hits = list(_V_DIR.finditer(t))
    for k, m in enumerate(hits):
        end = hits[k + 1].start() if k + 1 < len(hits) else len(t)
        seg = t[m.end():end]
        depth, cut = 0, len(seg)
        for i, ch in enumerate(seg):          # the declaration ends at ';' or the closing ')' of the list
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0 and ch == ")":
                    cut = i
                    break
                depth -= 1
            elif ch == ";" and depth == 0:
                cut = i
                break
        seg = seg[:cut].strip().rstrip(",")
        direction = {"input": "in", "output": "out", "inout": "inout"}[m.group(1)]
        words, packed = [], []
        while True:                           # net / data type keywords
            wm = re.match(r"\s*([A-Za-z_]\w*)\b", seg)
            if not wm or wm.group(1) not in _V_TYPES:
                break
            words.append(wm.group(1))
            seg = seg[wm.end():]
        while True:                           # packed dimensions
            rm = re.match(r"\s*\[([^\]]*)\]", seg)
            if not rm:
                break
            packed.append(rm.group(1))
            seg = seg[rm.end():]
        items = _split_top(seg, ",")
        if not all(re.fullmatch(r"\s*[A-Za-z_]\w*\s*(\[[^\]]*\]\s*)*(=.*)?\s*", it, re.S) for it in items):
            continue                          # an interface, a user type ...: not a plain port
        width, lsb, err = 1, 0, ""
        fixed = next((_V_FIXED[w] for w in words if w in _V_FIXED), None)
        if fixed:
            width = fixed
        for k, dim in enumerate(packed):
            if ":" not in dim:
                width, err = None, tr("cannot resolve width: [{0}]").format(dim.strip())
                break
            hi, lo = dim.split(":", 1)
            w, low = _range_width(hi, lo, params)
            if w is None:
                width, err = None, tr("cannot resolve width: [{0}]").format(dim.strip())
                break
            width = w if k == 0 and not fixed else width * w
            lsb = low if len(packed) == 1 else 0
        type_text = " ".join(words + [f"[{d.strip()}]" for d in packed])
        for item in items:
            nm = re.match(r"\s*([A-Za-z_]\w*)", item)
            if not nm:
                continue
            rest = item[nm.end():].split("=", 1)[0]
            unpacked = re.findall(r"\[([^\]]*)\]", rest)
            iw, ilsb, ierr = width, lsb, err
            itype = type_text + (" " + " ".join(f"[{u.strip()}]" for u in unpacked) if unpacked else "")
            if unpacked and ierr == "":
                ilsb = 0
                for u in unpacked:
                    if ":" in u:
                        hi, lo = u.split(":", 1)
                        uw, _ = _range_width(hi, lo, params)
                    else:
                        uw = evaluate(u, params)
                        if uw is not None and uw <= 0:
                            uw = None
                    if uw is None or iw is None:
                        iw, ierr = None, tr("cannot resolve width: [{0}]").format(u.strip())
                        break
                    iw *= uw
            ports.append(Port(nm.group(1), direction, iw, ilsb, itype.strip(), ierr))
    return (name.group(1) if name else ""), ports


# ============================================================================ VHDL
def _vhdl_type_width(typ, params):
    t = typ.strip()
    tl = t.lower()
    if re.fullmatch(r"(ieee\.)?(std_logic_1164\.)?(std_u?logic|bit|boolean)", tl):
        return 1, 0, ""
    m = re.fullmatch(r"[\w.]+\s*\(\s*(.+?)\s+(downto|to)\s+(.+?)\s*\)", t, re.I | re.S)
    if m:
        w, lo = _range_width(m.group(1), m.group(3), params)
        return (w, lo, "") if w is not None else (None, 0, tr("cannot resolve width: {0}").format(t))
    m = re.fullmatch(r"(integer|natural|positive)\s+range\s+(.+?)\s+(to|downto)\s+(.+)", t, re.I | re.S)
    if m:
        a, b = evaluate(m.group(2), params), evaluate(m.group(4), params)
        if a is None or b is None:
            return None, 0, tr("cannot resolve range: {0}").format(t)
        lo, hi = min(a, b), max(a, b)
        if lo < 0:
            neg_bits = (-lo - 1).bit_length()
            pos_bits = hi.bit_length() if hi >= 0 else 0
            bits = max(neg_bits, pos_bits) + 1
        else:
            bits = max(1, hi.bit_length())
        return bits, 0, ""
    if tl in ("integer", "natural", "positive"):
        return 32, 0, ""
    return None, 0, tr("unknown type: {0}").format(t)


def _vhdl(text):
    t = re.sub(r"--[^\n]*", " ", text)
    name = re.search(r"\b(?:entity|component)\s+(\w+)", t, re.I)
    params = {}
    gm = re.search(r"\bgeneric\s*\(", t, re.I)
    if gm:
        body = t[gm.end():_matching_paren(t, gm.end() - 1)]
        for item in _split_top(body, ";"):
            m = re.match(r"\s*([\w\s,]+?)\s*:\s*[^:=]+?(?::=\s*(.+))?\s*$", item, re.S)
            if m and m.group(2):
                for n in m.group(1).split(","):
                    params[n.strip()] = m.group(2).strip()
    for m in re.finditer(r"\bconstant\s+(\w+)\s*:\s*[^:;]+:=\s*([^;]+);", t, re.I):
        params[m.group(1)] = m.group(2)
    pm = re.search(r"\bport\s*\(", t, re.I)
    body = t[pm.end():_matching_paren(t, pm.end() - 1)] if pm else t
    ports = []
    for item in _split_top(body, ";"):
        m = re.match(r"\s*([A-Za-z]\w*(?:\s*,\s*[A-Za-z]\w*)*)\s*:\s*(in|out|inout|buffer)\s+(.+?)\s*(?::=.*)?$",
                     item, re.I | re.S)
        if not m:
            continue
        direction = {"in": "in", "out": "out", "buffer": "out", "inout": "inout"}[m.group(2).lower()]
        width, lsb, err = _vhdl_type_width(m.group(3), params)
        for n in m.group(1).split(","):
            ports.append(Port(n.strip(), direction, width, lsb, " ".join(m.group(3).split()), err))
    return (name.group(1) if name else ""), ports


# ============================================================================ API
def parse_ports(text, language=None):
    """(block name, [Port]) of Verilog/SystemVerilog or VHDL text; language: "verilog" | "vhdl" |
    None (detected)."""
    language = language or detect_language(text)
    return _vhdl(text) if language == "vhdl" else _verilog(text)


CLOCK_RESET = re.compile(r"^(.*_)?(a?clk|clock|clk_?i|i_?clk|a?rst_?n?|reset_?n?|aresetn|a?resetn?)(_.*)?$", re.I)


def is_clock_or_reset(name):
    return bool(CLOCK_RESET.match(name))


def field_name(text):
    """A valid field name from a signal name: letters, digits and single underscores, starting with
    a letter, upper case."""
    n = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
    n = re.sub(r"_+", "_", n).upper()
    if not n or not n[0].isalpha():
        n = "F_" + n if n else "FIELD"
    return n


def split_width(width, lsb=0, size=FIELD_WIDTH):
    """[(piece index, low bit, width)] of a signal cut into pieces of at most `size` bits."""
    if not width:
        return []
    n = math.ceil(width / size)
    return [(k, lsb + k * size, min(size, width - k * size)) for k in range(n)]
