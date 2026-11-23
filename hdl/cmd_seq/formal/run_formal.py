#!/usr/bin/env python3
"""Formal verification of cmd_seq with Yosys + yosys-smtbmc (the engine SymbiYosys
uses; cmd_seq.sby runs the same tasks for SymbiYosys users).

  python3 run_formal.py               # bmc + prove + cover, both configurations
  python3 run_formal.py prove --depth 24 --solver yices

Configurations
  core  cmd_seq_core alone: free instruction stream (every program), done only
        answers a pending request (what "top" proves about the block)
  top   cmd_seq with formal/cmd_seq_core_abs.v in place of the core: free bus
        requests that follow the request behaviour proven in "core"
Modes
  bmc    no assertion can fail within <depth> cycles from reset
  prove  k-induction: the assertions hold in every reachable state (unbounded)
  cover  every cover() statement is reachable (the checks are not vacuous)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RTL = os.path.join(os.path.dirname(HERE), "rtl")
PARAMS = {"IMEM_AW": 3, "DMEM_AW": 2, "PT_TIMEOUT": 4, "BUS_TIMEOUT": 4}
CONFIGS = {
    # the core alone, every instruction stream, environment as inside cmd_seq
    "core": {"top": "cmd_seq_core", "defines": ["F_CORE_TOP"],
             "files": ["cmd_seq_core.v"]},
    # the block with the core replaced by its proven request behaviour
    "top": {"top": "cmd_seq", "defines": ["F_ABSTRACT_CORE"],
            "files": ["cmd_seq.v", "cmd_seq_core_abs.v", "faxil_check.v"]},
}
DEPTH = {"bmc": 30, "prove": 20, "cover": 40}


def python_bitwuzla(workdir):
    """Use the bitwuzla Python package as the solver program (bitwuzla_smt2.py)."""
    try:
        import bitwuzla  # noqa: F401
    except ImportError:
        return False
    bindir = os.path.join(workdir, "bin")
    os.makedirs(bindir, exist_ok=True)
    shim = os.path.join(bindir, "bitwuzla")
    with open(shim, "w") as fh:
        fh.write(f"#!/bin/sh\nexec \"{sys.executable}\" \"{os.path.join(HERE, 'bitwuzla_smt2.py')}\"\n")
    os.chmod(shim, 0o755)
    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    return True


def find_solver(pref, workdir):
    if pref:
        exe = {"yices": "yices-smt2"}.get(pref, pref)
        if shutil.which(exe) or (pref == "bitwuzla" and python_bitwuzla(workdir)):
            return pref
        sys.exit(f"solver {pref} not found")
    for s in ("yices", "bitwuzla"):
        if shutil.which({"yices": "yices-smt2"}.get(s, s)):
            return s
    if python_bitwuzla(workdir):
        return "bitwuzla"
    # last resort; boolector 1.x and z3 4.8 (old Linux packages) do not work well
    for s in ("boolector", "z3"):
        if shutil.which(s):
            return s
    sys.exit("no SMT solver found: install yices or bitwuzla (OSS CAD Suite), "
             "or 'pip install bitwuzla'")


def build(cfg, workdir):
    smt2 = os.path.join(workdir, f"cmd_seq_{cfg}.smt2")
    c = CONFIGS[cfg]
    defines = " ".join(f"-D{d}" for d in c["defines"])
    files = " ".join(os.path.join(RTL if os.path.exists(os.path.join(RTL, f)) else HERE, f)
                     for f in c["files"])
    params = {k: v for k, v in PARAMS.items() if c["top"] == "cmd_seq" or k != "PT_TIMEOUT"}
    chparam = " ".join(f"-set {k} {v}" for k, v in params.items())
    script = (f"read_verilog -formal {defines} -I{HERE} {files}; "
              f"chparam {chparam} {c['top']}; prep -top {c['top']}; memory_map; opt -fast; "
              f"async2sync; dffunmap; write_smt2 -wires {smt2}")
    log = os.path.join(workdir, f"yosys_{cfg}.log")
    with open(log, "w") as fh:
        r = subprocess.run(["yosys", "-p", script], stdout=fh, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f"yosys failed, see {log}")
    return smt2


def run(mode, cfg, smt2, solver, depth, workdir):
    vcd = os.path.join(workdir, f"{mode}_{cfg}.vcd")
    cmd = ["yosys-smtbmc", "-s", solver, "--presat", "-t", str(depth)]
    if mode == "prove":
        cmd.append("-i")
    elif mode == "cover":
        cmd.append("-c")
    cmd += ["--dump-vcd", vcd, smt2]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and "PASSED" in out
    status = "PASS" if ok else "FAIL"
    print(f"  {mode:5s} {cfg:8s} depth {depth:3d}: {status}  ({time.time() - t0:5.1f} s)")
    if not ok:
        lines = [ln for ln in out.splitlines() if "failed" in ln.lower() or "Assert" in ln or
                 "Unreached" in ln or "error" in ln.lower()]
        for ln in lines[:12]:
            print("      " + ln.strip())
        print(f"      counterexample / trace: {vcd}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modes", nargs="*", help="bmc, prove, cover (default: all)")
    ap.add_argument("--config", choices=list(CONFIGS), action="append")
    ap.add_argument("--depth", type=int)
    ap.add_argument("--solver")
    ap.add_argument("--keep", metavar="DIR", help="work folder (kept)")
    a = ap.parse_args(argv)
    a.modes = a.modes or ["bmc", "prove", "cover"]
    for m in a.modes:
        if m not in DEPTH:
            ap.error(f"unknown mode {m}")
    for tool in ("yosys", "yosys-smtbmc"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found")
    workdir = a.keep or tempfile.mkdtemp(prefix="cmdseq_formal_")
    os.makedirs(workdir, exist_ok=True)
    solver = find_solver(a.solver, workdir)
    print(f"formal: solver {solver}, parameters {PARAMS}, work {workdir}")
    ok = True
    for cfg in a.config or list(CONFIGS):
        smt2 = build(cfg, workdir)
        for mode in a.modes:
            ok &= run(mode, cfg, smt2, solver, a.depth or DEPTH[mode], workdir)
    print("formal: " + ("PASSED" if ok else "FAILED"))
    if ok and not a.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
