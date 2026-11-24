#!/usr/bin/env python3
"""Simulate led_fade.s on cmd_seq + the real axilgpio and check the LED waveform.

    python3 examples/cmd_seq_led_fade/sim/run_sim.py            # short constants, 2 full cycles
    python3 examples/cmd_seq_led_fade/sim/run_sim.py --real     # the program's own PERIOD / STEP,
                                                                # REP = HOLD = 1 (one cycle)

Needs Python 3.8+ and Icarus Verilog (iverilog, vvp). Exit code 0 = passed.

What is checked, period by period (each period window starts at the first B-on edge, so it is
aligned with the program's own periods: the period starts are exact TIMER multiples):
  - B is on for D cycles and A for PERIOD - D, with D following the program's sequence
    (hold at 0, up in steps, hold at PERIOD, down in steps, again);
  - the periods do not drift (the windows stay aligned to the end);
  - A and B are never both on / both off for more than a few cycles at an edge;
  - no bus error, the program keeps running.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(EX))
sys.path.insert(0, os.path.join(ROOT, "hdl", "cmd_seq", "sw"))
import seqasm  # noqa: E402

TOL = 8          # cycles: bus latency difference between the on and the off write


def source(overrides):
    with open(os.path.join(EX, "led_fade.s"), encoding="utf-8") as fh:
        src = fh.read()
    for name, value in overrides.items():
        src, n = re.subn(rf"^(\.equ\s+{name}\s+)\S+", rf"\g<1>{value}", src, flags=re.M)
        assert n == 1, name
    return src


def equ(src, name):
    return seqasm.Assembler().value(re.search(rf"^\.equ\s+{name}\s+(\S+)", src, re.M).group(1), "")


def expected(P, S, rep, hold, n):
    """D of every period from the start of the program, n periods."""
    out = []
    while len(out) < n:
        out += [0] * hold
        out += [d for d in range(S, P + 1, S) for _ in range(rep)]
        out += [P] * hold
        out += [d for d in range(P - S, -1, -S) for _ in range(rep)]
    return out[:n]


def run(overrides, cycles):
    src = source(overrides)
    P, S, rep, hold = (equ(src, k) for k in ("PERIOD", "STEP", "REP", "HOLD"))
    prog, _ = seqasm.assemble(src)
    words = [w for _, w, _ in prog]
    total = sum((w & 0xFFFFFFFF) + (w >> 32) for w in words) & 0xFFFFFFFF
    work = tempfile.mkdtemp(prefix="led_fade_")
    try:
        hexf = os.path.join(work, "led_fade.hex")
        with open(hexf, "w") as fh:
            fh.write(seqasm.hex_text(prog))
        exe = os.path.join(work, "tb")
        rtl = os.path.join(ROOT, "hdl", "cmd_seq", "rtl")
        subprocess.run(["iverilog", "-g2012", "-o", exe,
                        f"-Ptb_led_fade.CYCLES={cycles}", f'-Ptb_led_fade.PROG="{hexf}"',
                        f"-Ptb_led_fade.NPROG={len(words)}", f"-Ptb_led_fade.SUM=32'h{total:08X}",
                        os.path.join(HERE, "tb_led_fade.v"), os.path.join(rtl, "cmd_seq.v"),
                        os.path.join(rtl, "cmd_seq_core.v"), os.path.join(ROOT, "hdl", "axilgpio.v"),
                        os.path.join(ROOT, "hdl", "skidbuffer.v")], check=True)
        out = subprocess.run(["vvp", "-n", exe], check=True, capture_output=True, text=True).stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return check(out, P, S, rep, hold, cycles)


def check(out, P, S, rep, hold, cycles):
    errors = []
    if "FAIL" in out:
        return [line for line in out.splitlines() if "FAIL" in line]
    ev = [tuple(map(int, line.split()[1:])) for line in out.splitlines() if line.startswith("G ")]
    status = int(re.search(r"STATUS (\w+)", out).group(1), 16)
    if "BUSERR 0" not in out:
        errors.append("bus error on the upstream port")
    if not status & 1 or status & 4:
        errors.append(f"program not running at the end (STATUS = 0x{status:08X})")
    # per-cycle waveform
    b = [0] * cycles
    a = [0] * cycles
    for k, (t, vb, va) in enumerate(ev):
        end = ev[k + 1][0] if k + 1 < len(ev) else cycles
        for c in range(t, min(end, cycles)):
            b[c], a[c] = vb, va
    rises = [t for (t, vb, _), prev in zip(ev[1:], ev) if vb and not prev[1]]
    if not rises:
        return errors + ["LED B never turned on"]
    r0 = rises[0]
    n = (cycles - r0) // P
    exp = expected(P, S, rep, hold, hold + n)[hold:]
    worst = 0
    for k in range(n):
        lo = r0 + k * P
        db, da = sum(b[lo:lo + P]), sum(a[lo:lo + P])
        e = exp[k]
        err = max(abs(db - e), abs(da - (P - e)))
        worst = max(worst, err)
        if err > TOL:
            errors.append(f"period {k}: B on {db}, A on {da}, expected {e} / {P - e}")
    # A and B at the same level only for a few cycles at an edge
    run_len, longest = 0, 0
    for c in range(ev[0][0] + P, cycles):
        run_len = run_len + 1 if a[c] == b[c] else 0
        longest = max(longest, run_len)
    if longest > TOL:
        errors.append(f"A and B at the same level for {longest} cycles")
    print(f"  PERIOD={P} STEP={S} REP={rep} HOLD={hold}: {n} periods checked, worst error "
          f"{worst} cycles, longest overlap {longest} cycles, {len(errors)} errors")
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--real", action="store_true", help="the program's PERIOD and STEP")
    args = ap.parse_args(argv)
    if not (shutil.which("iverilog") and shutil.which("vvp")):
        print("iverilog / vvp not found")
        return 2
    if args.real:
        src = source({})
        P, S = equ(src, "PERIOD"), equ(src, "STEP")
        cycles = (2 + 2 * P // S + 2) * P + 2000
        errors = run({"REP": 1, "HOLD": 1}, cycles)
    else:
        errors = run({"PERIOD": 200, "STEP": 50, "REP": 3, "HOLD": 4}, 2 * 32 * 200 + 3000)
    for e in errors[:20]:
        print("  FAIL", e)
    print("led_fade:", "PASSED" if not errors else "FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
