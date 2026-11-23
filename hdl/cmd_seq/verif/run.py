#!/usr/bin/env python3
"""Run the cmd_seq verification: directed tests, random co-simulation and formal.

  python3 run.py                 # everything
  python3 run.py directed random -n 500
  python3 run.py formal

Needs Python 3.8+, Icarus Verilog (iverilog, vvp) and for formal Yosys with
yosys-smtbmc and a solver (yices / bitwuzla, or 'pip install bitwuzla').
Works on Linux, macOS and Windows (formal: Linux / macOS).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "formal"))
import seqasm  # noqa: E402

RTL = [os.path.join(ROOT, "rtl", "cmd_seq.v"), os.path.join(ROOT, "rtl", "cmd_seq_core.v")]


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        print(f"  skipped: {', '.join(missing)} not found")
        return False
    return True


def directed(workdir):
    print("directed tests")
    if not need("iverilog", "vvp"):
        return None
    progs = os.path.join(HERE, "progs")
    for name in sorted(os.listdir(progs)):
        if name.endswith(".s"):
            with open(os.path.join(progs, name), encoding="utf-8") as fh:
                prog, _ = seqasm.assemble(fh.read())
            with open(os.path.join(workdir, name[:-2] + ".hex"), "w") as fh:
                fh.write(seqasm.hex_text(prog))
    exe = os.path.join(workdir, "tb_directed.vvp")
    subprocess.run(["iverilog", "-g2012", "-I", HERE, "-o", exe, os.path.join(HERE, "tb_directed.v"),
                    os.path.join(HERE, "axil_slave_model.v")] + RTL, check=True)
    r = subprocess.run(["vvp", "-n", exe, f"+progs={workdir}"], capture_output=True, text=True)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith(("FAIL", "tb_directed"))]
    for ln in lines:
        print("  " + ln)
    return any("PASSED" in ln for ln in lines) and not any(ln.startswith("FAIL") for ln in lines)


def random_tests(n, seed):
    print("random co-simulation against the reference model")
    if not need("iverilog", "vvp"):
        return None
    import random_test
    ok = True
    for maxd in (0, 3):                 # always-ready slave and random stalls
        print(f"  slave delay 0..{maxd}: ", end="", flush=True)
        ok &= random_test.main(["-n", str(n), "--seed", str(seed + maxd), "--maxd", str(maxd)]) == 0
    return ok


def formal():
    print("formal (yosys-smtbmc)")
    if not need("yosys", "yosys-smtbmc"):
        return None
    import run_formal
    return run_formal.main([]) == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parts", nargs="*", help="directed, random, formal (default: all)")
    ap.add_argument("-n", type=int, default=300, help="random programs per slave setting")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    parts = a.parts or ["directed", "random", "formal"]
    workdir = tempfile.mkdtemp(prefix="cmdseq_")
    results = {}
    try:
        for p in parts:
            if p == "directed":
                results[p] = directed(workdir)
            elif p == "random":
                results[p] = random_tests(a.n, a.seed)
            elif p == "formal":
                results[p] = formal()
            else:
                ap.error(f"unknown part {p}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print()
    for p, r in results.items():
        print(f"{p:9s} {'PASSED' if r else 'SKIPPED' if r is None else 'FAILED'}")
    return 1 if any(r is False for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
