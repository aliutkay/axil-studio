"""Check the interface translations: every tr("...") text of the code against each catalog.

    python scripts/i18n_check.py            report missing / unused texts and placeholder mismatches
    python scripts/i18n_check.py --prune    also drop catalog entries the code no longer uses

Exit status 1 when a catalog misses texts or has placeholder mismatches.
"""

import argparse
import ast
import glob
import json
import os
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from axil_studio import i18n  # noqa: E402


def code_texts():
    """{text: [file:line, ...]} of every tr() call with a literal argument."""
    out = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "axil_studio", "**", "*.py"), recursive=True)):
        tree = ast.parse(open(f, encoding="utf-8").read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args \
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.setdefault(n.args[0].value, []).append(f"{os.path.relpath(f, ROOT)}:{n.lineno}")
    return out


def fields(text):
    try:
        return sorted((name, spec, conv) for _, name, spec, conv in string.Formatter().parse(text) if name is not None)
    except ValueError as exc:
        return [("invalid", str(exc), "")]


def check(prune=False):
    texts = code_texts()
    problems = 0
    for code in i18n.LANGUAGES:
        if code == "en":
            continue
        path = i18n.catalog_path(code)
        cat = json.load(open(path, encoding="utf-8"))
        missing = sorted(t for t in texts if t not in cat)
        unused = sorted(t for t in cat if t not in texts)
        bad = sorted(t for t in texts if t in cat and fields(t) != fields(cat[t]))
        print(f"{code}: {len(texts)} texts, {len(missing)} missing, {len(unused)} unused, {len(bad)} placeholder mismatches")
        for t in missing:
            print(f"  missing  {t!r}  ({texts[t][0]})")
        for t in bad:
            print(f"  mismatch {t!r} -> {cat[t]!r}")
        problems += len(missing) + len(bad)
        if prune and unused:
            for t in unused:
                del cat[t]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(dict(sorted(cat.items())), fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            print(f"  {len(unused)} unused entries removed")
    return problems


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prune", action="store_true")
    sys.exit(1 if check(ap.parse_args().prune) else 0)
