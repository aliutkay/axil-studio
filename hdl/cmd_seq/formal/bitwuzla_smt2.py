#!/usr/bin/env python3
"""Minimal SMT-LIB2 front end for the bitwuzla Python package (pip install bitwuzla).

yosys-smtbmc talks to its solver over stdin / stdout. When no solver program is
installed, run_formal.py puts this script on PATH as "bitwuzla".
"""
import sys
import bitwuzla as bw

opts = bw.Options()
opts.set(bw.Option.PRODUCE_MODELS, True)
tm = bw.TermManager()
parser = bw.Parser(tm, opts)
buf, depth, in_str = [], 0, False
for line in sys.stdin:
    for ch in line:
        if ch == '"' or ch == '|':
            in_str = not in_str
        if not in_str:
            if ch == ';':
                break
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
        buf.append(ch)
    if depth == 0 and ''.join(buf).strip():
        cmd = ''.join(buf)
        buf = []
        if cmd.strip().startswith('(exit'):
            break
        parser.parse(cmd, False, False)
        sys.stdout.flush()
