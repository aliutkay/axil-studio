#!/usr/bin/env python3
"""Write Verilog files without `localparam`, for tools that reject it (HDL Designer, Verilog-2001
/ 2005 modes of Libero / Synplify).

    python3 no_localparam.py IN.v [IN2.v ...] -o OUTDIR     # converted copies in OUTDIR
    python3 no_localparam.py --check FILE.v [...]           # list the localparams left (exit 1 if any)

Rules (the values never change, so the logic is the same):
  - `localparam` at the start of a declaration becomes `parameter`. In the module parameter list
    `#( ... )` this is the only Verilog-2005 form; in the module body a `parameter` of a module with
    a parameter list is local anyway (IEEE 1364-2005 12.2).
  - A localparam inside a generate block cannot be a `parameter`; it becomes a constant `wire` of
    the same width (only used for such constants under `ifdef FORMAL` in the ZipCPU files).
  - Comments are not touched. Each converted file gets a note in its header.
Do not override the former localparams (DW, AW, LCLLGFLEN, ...) when instantiating a module.
"""
import argparse
import os
import re
import sys

NOTE = ("// AXIL Studio (hdl/v2005): `localparam` written as `parameter` (same values) for Verilog-2001 /\n"
        "// 2005 tools. Do not override the former localparams.\n")


def _code(line):
    return line.split("//")[0]


def convert(text):
    """Returns (new_text, count, notes)."""
    out, gen, n, notes = [], 0, 0, []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        code = _code(line)
        gen += len(re.findall(r"\bgenerate\b", code)) - len(re.findall(r"\bendgenerate\b", code))
        m = re.match(r"^(\s*)localparam\b(.*)$", line)
        if m and gen > 0:
            # `localparam [range] NAME = value;` inside generate -> constant wire
            g = re.match(r"^(\s*)localparam\s*(\[[^\]]*\])?\s*(\w+)\s*=\s*(.*?);(.*)$", line)
            if not g:
                raise ValueError(f"line {i + 1}: localparam in a generate block needs a manual change")
            ind, rng, name, val, rest = g.groups()
            line = f"{ind}wire\t{rng + ' ' if rng else ''}{name} = {val};{rest}"
            notes.append(f"line {i + 1}: {name} in a generate block -> wire")
            n += 1
        elif m:
            line = f"{m.group(1)}parameter{m.group(2)}"
            n += 1
        out.append(line)
        i += 1
    new = "\n".join(out)
    if n:
        if re.search(r"(?m)^// Filename:.*\n", new):
            new = re.sub(r"(?m)^(// Filename:.*\n)", lambda mm: mm.group(1) + "//\n" + NOTE, new, count=1)
        else:
            new = NOTE + new
    return new, n, notes


def left(text):
    """Lines that still declare a localparam (comments ignored)."""
    return [i + 1 for i, l in enumerate(text.split("\n")) if re.search(r"\blocalparam\b", _code(l))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--outdir")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    bad = 0
    for p in a.files:
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        if a.check:
            ls = left(text)
            if ls:
                bad += 1
                print(f"{p}: localparam on line(s) {', '.join(map(str, ls))}")
            continue
        if not a.outdir:
            ap.error("-o OUTDIR is needed (or --check)")
        new, n, notes = convert(text)
        os.makedirs(a.outdir, exist_ok=True)
        dst = os.path.join(a.outdir, os.path.basename(p))
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(new)
        print(f"{os.path.basename(p):24s} {n:3d} localparam -> parameter" + "".join(f"\n    {x}" for x in notes))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
