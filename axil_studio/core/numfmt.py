"""Number formats ("data types") for register fields.

A field can say how its value is meant to be read: hex, unsigned, signed (two's
complement), binary, octal, boolean, ASCII characters or fixed point. The wrapper
generator stores the choice in the spec and the debug bridge uses it as the default
view and for parsing what the user types.

Format strings:  hex  udec  sdec  bin  oct  bool  ascii  ufix<N>  sfix<N>  enum
(N = fraction bits, e.g. "sfix8" is Q(width-8).8). An empty string means "auto":
bool for 1-bit fields, hex otherwise.
"""

import re

BASE_FORMATS = [
    ("hex", "Hex"),
    ("udec", "Unsigned decimal"),
    ("sdec", "Signed decimal (2's complement)"),
    ("bin", "Binary"),
    ("oct", "Octal"),
    ("bool", "Boolean"),
    ("ascii", "ASCII karakter"),
    ("ufix", "Unsigned fixed point (Q)"),
    ("sfix", "Signed fixed point (Q)"),
    ("enum", "Değer anlamı (enum)"),
]
LABELS = dict(BASE_FORMATS)
SHORT = {"hex": "hex", "udec": "uint", "sdec": "int", "bin": "bin", "oct": "oct", "bool": "bool",
         "ascii": "ascii", "ufix": "ufix", "sfix": "sfix", "enum": "enum"}

_FMT = re.compile(r"^(hex|udec|sdec|bin|oct|bool|ascii|ufix|sfix|enum)(\d+)?$")


def split(fmt, width=32):
    """("sfix", 8) from "sfix8"; resolves "" (auto)."""
    fmt = (fmt or "").strip().lower()
    if not fmt:
        return ("bool" if width == 1 else "hex"), 0
    m = _FMT.match(fmt)
    if not m:
        return "hex", 0
    return m.group(1), int(m.group(2) or 0)


def join(base, frac=0):
    return f"{base}{frac}" if base in ("ufix", "sfix") else base


def label(fmt, width=32):
    base, frac = split(fmt, width)
    if base in ("ufix", "sfix"):
        return f"{SHORT[base]} Q{width - frac}.{frac}" if base == "ufix" else f"{SHORT[base]} Q{width - frac - 1}.{frac}"
    return SHORT[base] + ("" if fmt else " (oto)")


def to_signed(value, width):
    value &= (1 << width) - 1
    return value - (1 << width) if width and value >> (width - 1) else value


def format_value(value, width, fmt="", meanings=None):
    """Text for `value` (an unsigned int of `width` bits). meanings: {value: text} for enum."""
    if value is None:
        return ""
    width = max(1, width)
    value &= (1 << width) - 1
    base, frac = split(fmt, width)
    if base == "hex":
        return f"0x{value:0{(width + 3) // 4}X}"
    if base == "udec":
        return str(value)
    if base == "sdec":
        return str(to_signed(value, width))
    if base == "bin":
        digits = f"{value:0{width}b}"
        groups = [digits[max(0, i - 4):i] for i in range(len(digits), 0, -4)][::-1]
        return "0b" + "_".join(groups)
    if base == "oct":
        return f"0o{value:o}"
    if base == "bool":
        return "true" if value else "false"
    if base == "ascii":
        nbytes = (width + 7) // 8
        out = []
        for i in reversed(range(nbytes)):
            b = (value >> (8 * i)) & 0xFF
            out.append(chr(b) if 32 <= b < 127 and chr(b) not in "'\\" else f"\\x{b:02X}")
        return "'" + "".join(out) + "'"
    if base in ("ufix", "sfix"):
        raw = to_signed(value, width) if base == "sfix" else value
        return _fix_text(raw / (1 << frac)) if frac else str(raw)
    if base == "enum":
        text = (meanings or {}).get(value)
        return f"{text} ({value})" if text else f"0x{value:0{(width + 3) // 4}X}"
    return f"0x{value:X}"


def _fix_text(x):
    text = f"{x:.6f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def parse_value(text, width, fmt="", meanings=None):
    """Inverse of format_value. Prefixed numbers (0x 0b 0o) are always accepted; a plain
    number is read in the field's own format. Raises ValueError with a readable message."""
    width = max(1, width)
    base, frac = split(fmt, width)
    s = str(text).strip().replace("_", "").replace(" ", "")
    if not s:
        raise ValueError("Boş değer")
    mask = (1 << width) - 1
    low = s.lower()

    if meanings:
        for v, meaning in meanings.items():
            if meaning and s.lower() == meaning.strip().lower():
                return v
    if low in ("true", "false", "on", "off", "yes", "no", "evet", "hayır", "açık", "kapalı"):
        return 1 if low in ("true", "on", "yes", "evet", "açık") else 0
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return _check(_parse_ascii(s[1:-1]), width)
    if re.match(r"^-?0[xbo]", low):
        neg = low.startswith("-")
        v = int(low.lstrip("-"), 0)
        return _check(-v if neg else v, width)

    if base in ("ufix", "sfix"):
        try:
            x = float(s)
        except ValueError:
            raise ValueError(f"'{text}' bir sayı değil") from None
        return _check(round(x * (1 << frac)), width, signed=base == "sfix")
    radix = {"hex": 16, "bin": 2, "oct": 8}.get(base, 10)
    try:
        v = int(s, radix)
    except ValueError:
        raise ValueError(f"'{text}' {LABELS.get(base, base)} olarak okunamadı") from None
    if v < 0 or base == "sdec":
        return _check(v, width, signed=True)
    return v if v <= mask else _check(v, width)


def _check(v, width, signed=False):
    if v < 0 or signed:
        lo = -(1 << (width - 1))
        if not lo <= v < (1 << width):
            raise ValueError(f"{v}, {width} bitlik signed aralığa sığmıyor ({lo}..{(1 << (width - 1)) - 1})")
        return v & ((1 << width) - 1)
    if v >> width:
        raise ValueError(f"{v} ({v:#x}), {width} bite sığmıyor (en fazla {(1 << width) - 1})")
    return v


def _parse_ascii(body):
    data = body.encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1")
    v = 0
    for b in data:
        v = (v << 8) | b
    return v


def all_views(value, width, frac=0):
    """[(label, text)] of the value in every format, for tooltips and the debug panel."""
    rows = [("Hex", format_value(value, width, "hex")), ("Unsigned", format_value(value, width, "udec")),
            ("Signed", format_value(value, width, "sdec")), ("Binary", format_value(value, width, "bin")),
            ("Octal", format_value(value, width, "oct"))]
    if width % 8 == 0:
        rows.append(("ASCII", format_value(value, width, "ascii")))
    if frac:
        rows.append((f"Q{width - frac}.{frac}", format_value(value, width, f"ufix{frac}")))
    return rows


def typed_extra(fmt, width):
    """Format of the third column next to hex and binary, or None when it would repeat them
    (hex / binary types). enum falls back to unsigned decimal."""
    base, frac = split(fmt, width)
    if base in ("hex", "bin"):
        return None
    return "udec" if base == "enum" else join(base, frac)


def triple(value, width, fmt=""):
    """[hex, binary] plus the value in the field's own type when that adds something."""
    out = [format_value(value, width, "hex"), format_value(value, width, "bin")]
    extra = typed_extra(fmt, width)
    if extra:
        out.append(format_value(value, width, extra))
    return out
