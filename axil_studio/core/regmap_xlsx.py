"""
Register map export to Excel (.xlsx).

Works on model.IPBlock objects, so both the debug application (project IPs)
and the wrapper generator (axilgen.to_ipblock) use the same exporter.

Sheets
  Özet           slave base address input per IP, IP list and register summary
                 (every address in the workbook is a formula on that base cell)
  Kütük Haritası register / field rows, reserved bits, behaviour, data type and value
                 descriptions (every value as hex | binary | value in the field's data type)
  Bit Görünümü   32-bit layout of every register, coloured by field type
"""

import datetime

from . import numfmt as N
from .model import KIND_READS, KIND_WRITES, hex32
from axil_studio.i18n import tr

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    HAVE_OPENPYXL = True
except ImportError:          # pragma: no cover - reported to the user by the callers
    HAVE_OPENPYXL = False

KIND_TEXT = {
    "RW": ("RW", tr("The written value is kept and read back.")),
    "RO": ("RO", tr("Reads the hardware state. Writing has no effect.")),
    "PULSE": (tr("WO (pulse)"), tr("Writing 1 to a bit gives a one-clock pulse. Always reads 0.")),
    "W1C": ("RW1C", tr("Set to 1 by an event and stays 1. Writing 1 clears it, writing 0 has no effect.")),
    "CONST": (tr("RO (constant)"), tr("Constant value. Writing has no effect.")),
}
ACCESS_TEXT = {"RW": tr("Read and written."), "RO": tr("Read only."), "WO": tr("Write only; the value read has no meaning.")}
KIND_FILL = {"RW": "DCE8FD", "RO": "D7F0DD", "PULSE": "FEF0C7", "W1C": "FADBD8", "CONST": "E8E8E8",
             "": "EDE7F6", "RSVD": "F5F5F5"}

HEAD_FILL = "1F3864"
REG_FILL = "D9E1F2"
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top", wrap_text=True)


def available():
    return HAVE_OPENPYXL


def _bits(msb, lsb):
    return f"[{lsb}]" if msb == lsb else f"[{msb}:{lsb}]"


def _field_access(reg, f):
    if f.kind and f.kind in KIND_TEXT:
        return KIND_TEXT[f.kind]
    return reg.access, ACCESS_TEXT.get(reg.access, "")


def _value_text(f, v):
    """hex | binary | value in the field's data type (the last only when it adds something)."""
    return " | ".join(N.triple(v, f.width, getattr(f, "fmt", "")))


def _meanings(f, which):
    """'0x1 | 0b001 | 1 → text' lines for read or write meanings."""
    lines = []
    for e in sorted(f.values, key=lambda e: e.value):
        text = e.read if which == "read" else e.write
        if text:
            lines.append(f"{_value_text(f, e.value)} → {text}")
    return "\n".join(lines)


def _rows_of(reg):
    """Fields from MSB to LSB with reserved gaps as None entries: (msb, lsb, field|None)."""
    out, bit = [], 31
    for f in sorted(reg.fields, key=lambda f: -(f.lsb + f.width - 1)):
        lsb = max(0, min(31, f.lsb))
        msb = max(lsb, min(31, f.lsb + max(1, f.width) - 1))
        if msb > bit:
            msb = bit
        if msb < lsb or bit < 0:
            continue
        if msb < bit:
            out.append((bit, msb + 1, None))
        out.append((msb, lsb, f))
        bit = lsb - 1
    if reg.fields and bit >= 0:
        out.append((bit, 0, None))
    return out


def _height(*texts, base=15):
    return base * max(1, *(str(t).count("\n") + 1 for t in texts if t is not None))


def _autofit(ws, row, min_lines=1):
    """Row height from wrapped text length and column widths (Excel does not do it on load)."""
    lines = min_lines
    for cell in ws[row]:
        if cell.value is None or not isinstance(cell.value, str) or cell.value.startswith("="):
            continue   # formulas (addresses) render as one short line
        width = ws.column_dimensions[cell.column_letter].width or 10
        chars = max(4, int(width * 1.15))
        n = sum(max(1, -(-len(part) // chars)) for part in cell.value.split("\n"))
        lines = max(lines, n)
    ws.row_dimensions[row].height = 15 * lines + 2


def _header(ws, row, titles, widths):
    for c, (t, w) in enumerate(zip(titles, widths), 1):
        cell = ws.cell(row=row, column=c, value=t)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEAD_FILL)
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(c)].width = w


def _addr_formula(base_ref, offset):
    """Excel formula: "0x" + 8 hex digits of (base cell + offset). The base cell may be typed
    as 0x80002000, 80002000 or 8000_2000."""
    clean = f'SUBSTITUTE(SUBSTITUTE(UPPER(TRIM({base_ref})),"0X",""),"_","")'
    return f'="0x"&DEC2HEX(HEX2DEC({clean})+{offset},8)'


INPUT_FILL = "FFF2CC"


def _put(ws, row, values, fill=None, bold=False, italic=False, color=None, align=None):
    for c, v in enumerate(values, 1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.border = BORDER
        cell.alignment = (align or {}).get(c, WRAP)
        if fill:
            cell.fill = PatternFill("solid", fgColor=fill)
        if bold or italic or color:
            cell.font = Font(bold=bold, italic=italic, color=color)


def export_xlsx(ips, path, title=None, bus_of=None):
    """Write the register map of one or more IPBlocks to an .xlsx file. bus_of: {IP name: bus name}
    (the subsystem an instance sits in), written next to its base address."""
    if not HAVE_OPENPYXL:
        raise RuntimeError(tr("Excel output needs openpyxl: pip install openpyxl"))
    ips = list(ips)
    wb = Workbook()
    summary = wb.active
    summary.title = tr("Summary")
    regmap = wb.create_sheet(tr("Register Map"))
    layout = wb.create_sheet(tr("Bit View"))

    # ------------------------------------------------------------ base address inputs
    s = summary
    s["A1"] = title or (tr("Register map:").rstrip() + " " + ", ".join(ip.name for ip in ips))
    s["A1"].font = Font(bold=True, size=14)
    s["A2"] = tr("Created: {0:%Y-%m-%d %H:%M}").format(datetime.datetime.now())
    s["A2"].font = Font(italic=True, color="808080")
    _header(s, 4, ("IP", tr("Slave base address"), tr("Note")), (14, 18, 18))
    base_ref = {}
    for i, ip in enumerate(ips):
        r = 5 + i
        s.cell(row=r, column=1, value=ip.name).font = Font(bold=True)
        s.cell(row=r, column=1).border = BORDER
        c = s.cell(row=r, column=2, value=hex32(ip.base))
        c.fill = PatternFill("solid", fgColor=INPUT_FILL)
        c.font = Font(bold=True)
        c.border = BORDER
        c.alignment = CENTER
        bus = (bus_of or {}).get(ip.name)
        s.cell(row=r, column=3, value=(tr("bus: {0} · ").format(bus) if bus else "")
               + tr("← enter the base address set on the interconnect; every address follows"))
        s.cell(row=r, column=3).font = Font(italic=True, color="808080")
        base_ref[ip.name] = tr("'Summary'!$B${0}").format(r)
    summary_start = 6 + len(ips)

    # ------------------------------------------------------------ register map
    cols = ("IP", tr("Address"), "Offset", "Register", tr("Bits"), tr("Bit field"), tr("Access"), tr("Data type"), "Reset",
            tr("Description"), tr("Behavior"), tr("When read (hex | binary | type → meaning)"), tr("When written (hex | binary | type → effect)"))
    _header(regmap, 1, cols, (16, 13, 8, 16, 9, 18, 11, 12, 44, 40, 34, 50, 50))
    mid = {c: CENTER for c in (2, 3, 5, 7, 8, 9)}
    row, reg_row = 2, {}
    for ip in ips:
        for reg in sorted(ip.registers, key=lambda r: r.offset):
            reg_row[(ip.name, reg.name)] = row
            _put(regmap, row, (ip.name, _addr_formula(base_ref[ip.name], reg.offset), f"0x{reg.offset:02X}",
                               reg.name, "[31:0]", "",
                               reg.access, "", hex32(reg.reset), reg.desc, ACCESS_TEXT.get(reg.access, ""), "", ""),
                 fill=REG_FILL, bold=True, align=mid)
            _autofit(regmap, row)
            row += 1
            for msb, lsb, f in _rows_of(reg):
                if f is None:
                    _put(regmap, row, ("", "", "", "", _bits(msb, lsb), tr("Reserved"), "—", "", "0",
                                       tr("Not used."), tr("Reads 0. Writing has no effect; writing 0 is recommended."),
                                       "", ""), fill=KIND_FILL["RSVD"], italic=True, color="808080", align=mid)
                    _autofit(regmap, row)
                else:
                    acc, behaviour = _field_access(reg, f)
                    rd = _meanings(f, "read") if KIND_READS.get(f.kind, True) else ""
                    wr = _meanings(f, "write") if KIND_WRITES.get(f.kind, True) else ""
                    reset = "—" if f.kind == "RO" else _value_text(f, f.extract(reg.reset))
                    ftype = N.label(getattr(f, "fmt", ""), f.width).replace(tr(" (auto)"), "")
                    _put(regmap, row, ("", "", "", "", f.bits, f.name, acc, ftype, reset, f.desc, behaviour, rd, wr),
                         align=mid)
                    regmap.cell(row=row, column=7).fill = PatternFill("solid", fgColor=KIND_FILL.get(f.kind, KIND_FILL[""]))
                    _autofit(regmap, row)
                row += 1
    regmap.freeze_panes = "E2"
    regmap.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{max(row - 1, 1)}"

    # ------------------------------------------------------------ bit layout
    layout.column_dimensions["A"].width = 26
    for b in range(32):
        layout.column_dimensions[get_column_letter(2 + b)].width = 4.2
    for b in range(32):
        c = layout.cell(row=1, column=2 + b, value=31 - b)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEAD_FILL)
        c.alignment = Alignment(horizontal="center")
    layout.cell(row=1, column=1, value="Register").font = Font(bold=True, color="FFFFFF")
    layout.cell(row=1, column=1).fill = PatternFill("solid", fgColor=HEAD_FILL)
    lrow = 2
    for ip in ips:
        for reg in sorted(ip.registers, key=lambda r: r.offset):
            addr = _addr_formula(base_ref[ip.name], reg.offset)[1:]
            safe_lbl = f"{ip.name}.{reg.name}".replace('"', '""')
            name = layout.cell(row=lrow, column=1, value=f'="{safe_lbl}"&CHAR(10)&{addr}')
            name.alignment = WRAP
            name.font = Font(bold=True)
            segments = _rows_of(reg) or [(31, 0, None)]
            for msb, lsb, f in segments:
                c0, c1 = 2 + (31 - msb), 2 + (31 - lsb)
                kind = "RSVD" if f is None else f.kind
                label = "" if f is None else f.name
                if c1 > c0:
                    layout.merge_cells(start_row=lrow, start_column=c0, end_row=lrow, end_column=c1)
                cell = layout.cell(row=lrow, column=c0, value=label)
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True,
                                           text_rotation=90 if c1 == c0 and len(label) > 3 else 0)
                cell.font = Font(size=8, bold=f is not None)
                fill = PatternFill("solid", fgColor=KIND_FILL.get(kind, KIND_FILL[""]))
                for c in range(c0, c1 + 1):
                    layout.cell(row=lrow, column=c).fill = fill
                    layout.cell(row=lrow, column=c).border = BORDER
            layout.row_dimensions[lrow].height = 48
            lrow += 1
    lrow += 1
    layout.cell(row=lrow, column=1, value=tr("Colors")).font = Font(bold=True)
    for i, (k, text) in enumerate((("RW", "RW"), ("RO", "RO"), ("PULSE", tr("PULSE (pulse)")),
                                   ("W1C", tr("W1C (write 1 to clear)")), ("CONST", tr("Constant")), ("", tr("Untyped field")),
                                   ("RSVD", tr("Reserved")))):
        c = layout.cell(row=lrow + 1 + i, column=1, value=text)
        c.fill = PatternFill("solid", fgColor=KIND_FILL[k])
        c.border = BORDER
    layout.freeze_panes = "B2"

    # ------------------------------------------------------------ summary
    s.column_dimensions["A"].width = 14
    s.column_dimensions["B"].width = 18
    s.column_dimensions["C"].width = 18
    s.column_dimensions["D"].width = 10
    s.column_dimensions["E"].width = 13
    s.column_dimensions["F"].width = 70
    r = summary_start + 1
    for ip in ips:
        s.cell(row=r, column=1, value=ip.name).font = Font(bold=True, size=12)
        r += 1
        if ip.desc:
            s.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            c = s.cell(row=r, column=1, value=ip.desc)
            c.alignment = WRAP
            s.row_dimensions[r].height = _height(ip.desc) + 4
            r += 1
        _header(s, r, ("Offset", tr("Address"), "Register", tr("Access"), "Reset", tr("Description")), (14, 18, 18, 10, 13, 70))
        r += 1
        for reg in sorted(ip.registers, key=lambda x: x.offset):
            _put(s, r, (f"0x{reg.offset:02X}", _addr_formula(base_ref[ip.name], reg.offset), reg.name, reg.access,
                        hex32(reg.reset), reg.desc), align={c: CENTER for c in (1, 2, 4, 5)})
            link = s.cell(row=r, column=3)
            link.hyperlink = tr("#'Register Map'!D{0}").format(reg_row[(ip.name, reg.name)])
            link.font = Font(color="0563C1", underline="single")
            _autofit(s, r)
            r += 1
        r += 1
    s.cell(row=r, column=1, value=tr("Access types")).font = Font(bold=True)
    for i, (k, (acc, text)) in enumerate(KIND_TEXT.items()):
        s.cell(row=r + 1 + i, column=1, value=acc).fill = PatternFill("solid", fgColor=KIND_FILL[k])
        s.cell(row=r + 1 + i, column=3, value=text)
    for ws in (summary, regmap, layout):
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_options.horizontalCentered = True
    regmap.print_title_rows = "1:1"
    wb.save(path)
    return path
