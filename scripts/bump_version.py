#!/usr/bin/env python3
"""Raise AXIL Studio's version by 0.0.1 (axil_studio/__init__.py, APP_VERSION).

    python scripts/bump_version.py           # 0.1.1 -> 0.1.2
    python scripts/bump_version.py --hook    # from .githooks/pre-commit: skipped when the commit
                                             # already changes APP_VERSION; the file is staged
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "axil_studio", "__init__.py")
PATTERN = re.compile(r'^APP_VERSION = "(\d+)\.(\d+)\.(\d+)"', re.M)


def staged_version_change():
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--", INIT], cwd=ROOT,
                          capture_output=True, text=True).stdout
    return any(line.startswith("+APP_VERSION") for line in diff.splitlines())


def main():
    hook = "--hook" in sys.argv
    if hook and staged_version_change():
        return 0
    with open(INIT, encoding="utf-8") as fh:
        text = fh.read()
    m = PATTERN.search(text)
    if not m:
        print("APP_VERSION bulunamadı", file=sys.stderr)
        return 1
    major, minor, patch = (int(x) for x in m.groups())
    new = f"{major}.{minor}.{patch + 1}"
    text = text[:m.start()] + f'APP_VERSION = "{new}"' + text[m.end():]
    with open(INIT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    if hook:
        subprocess.run(["git", "add", "--", INIT], cwd=ROOT, check=True)
    print(f"AXIL Studio sürümü: {'.'.join(m.groups())} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
