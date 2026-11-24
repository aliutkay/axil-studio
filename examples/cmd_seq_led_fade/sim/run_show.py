#!/usr/bin/env python3
"""Simulate led_show.s on cmd_seq + the real axilgpio and check every frame.

    python3 examples/cmd_seq_led_fade/sim/run_show.py

The program runs with a short time unit and one frame per step. Every write to the GPIO LOAD
register is logged; four writes make a frame (bit planes 0..3), which gives the brightness 0..15 of
the 8 LEDs. The frames must be those of a Python model of the program (the playlist: 3 comets,
wave, sparkle, breathe), and the planes must last exactly 1, 2 and 4 units (plane 3: at least 8).
Needs Python 3.8+ and Icarus Verilog. Exit code 0 = passed.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_sim  # noqa: E402

U = 40
OVERRIDES = {"U": U, "U2": 2 * U, "U4": 4 * U, "U8": 8 * U, "DEF_SPEED": 1}
TAB = [0, 1, 2, 4, 6, 9, 12, 15, 15, 12, 9, 6, 4, 2, 1, 0]


def model(nframes, mode=0):
    """Frames (8 levels each) of led_show.s, one frame per step; mode = D[MODE]."""
    fb, frames = [0] * 8, []
    st = {"r6": 0, "r9": 1, "r7": 0xACE1}

    def hold():
        frames.append(list(fb))

    def fade():
        for i in range(8):
            fb[i] >>= 1

    def rand():
        lsb = st["r7"] & 1
        st["r7"] >>= 1
        if lsb:
            st["r7"] ^= 0xB400
        return st["r7"] & 7

    def comet():
        for _ in range(14):
            fade()
            fb[st["r6"]] = 15
            hold()
            if st["r6"] >= 7:
                st["r9"] = -1
            if st["r6"] == 0:
                st["r9"] = 1
            st["r6"] += st["r9"]

    def wave():
        for _ in range(64):
            for i in range(8):
                fb[i] = TAB[(2 * i + st["r6"]) & 15]
            hold()
            st["r6"] += 1
        st["r6"] &= 7

    def sparkle():
        for _ in range(48):
            fade()
            fb[rand()] = 15
            hold()

    def breathe():
        for _ in range(32):
            v = TAB[st["r6"] & 15]
            for i in range(8):
                fb[i] = v
            hold()
            st["r6"] += 1
        st["r6"] &= 7

    while len(frames) < nframes:
        if mode:
            (comet, wave, sparkle, breathe)[mode - 1]()
        else:
            comet(); comet(); comet(); wave(); sparkle(); breathe()
    return frames[:nframes]


def main():
    errors = run(0) + run(2) + run(3)
    print("led_show:", "PASSED" if not errors else "FAILED")
    return 1 if errors else 0


def run(mode):
    """mode 0: the playlist; mode k: D[MODE] = k, as if the PC had written it (the program
    stores it before its main loop)."""
    import shutil
    import subprocess
    import tempfile
    src = open(os.path.join(run_sim.EX, "led_show.s"), encoding="utf-8").read()
    for name, value in OVERRIDES.items():
        src, n = re.subn(rf"^(\.equ\s+{name}\s+)\S+", rf"\g<1>{value}", src, flags=re.M)
        assert n == 1, name
    if mode:
        src = src.replace("\nmain:", f"\n        LDI   R1, {mode}\n        STD   D[MODE], R1\nmain:", 1)
    prog, _ = run_sim.seqasm.assemble(src)
    words = [w for _, w, _ in prog]
    total = sum((w & 0xFFFFFFFF) + (w >> 32) for w in words) & 0xFFFFFFFF
    cycles = 260000
    work = tempfile.mkdtemp(prefix="led_show_")
    try:
        hexf = os.path.join(work, "show.hex")
        open(hexf, "w").write(run_sim.seqasm.hex_text(prog))
        exe = os.path.join(work, "tb")
        rtl = os.path.join(run_sim.ROOT, "hdl", "cmd_seq", "rtl")
        v = os.path.join(run_sim.ROOT, "hdl", "v2005")
        subprocess.run(["iverilog", "-g2012", "-DLOGW", "-o", exe,
                        f"-Ptb_led_fade.CYCLES={cycles}", f'-Ptb_led_fade.PROG="{hexf}"',
                        f"-Ptb_led_fade.NPROG={len(words)}", f"-Ptb_led_fade.SUM=32'h{total:08X}",
                        os.path.join(HERE, "tb_led_fade.v"), os.path.join(rtl, "cmd_seq.v"),
                        os.path.join(rtl, "cmd_seq_core.v"), os.path.join(v, "gpio", "axilgpio_a32.v"),
                        os.path.join(v, "gpio", "axilgpio.v"), os.path.join(v, "common", "skidbuffer.v")],
                       check=True)
        out = subprocess.run(["vvp", "-n", exe], check=True, capture_output=True, text=True).stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)
    errors = check(out, len(words), mode)
    for e in errors[:20]:
        print("  FAIL", e)
    return errors


def check(out, nwords, mode=0):
    errors = []
    status = int(re.search(r"STATUS (\w+)", out).group(1), 16)
    if "BUSERR 0" not in out or not status & 1 or status & 4:
        errors.append(f"program not running cleanly (STATUS 0x{status:08X})")
    w = [(int(t), int(d, 16)) for t, d in re.findall(r"^W (\d+) ([0-9a-f]+)", out, re.M)]
    nf = len(w) // 4
    frames, worst = [], 0
    for f in range(nf):
        planes = w[4 * f:4 * f + 4]
        t = [p[0] for p in planes]
        for k, want in ((0, U), (1, 2 * U), (2, 4 * U)):
            if t[k + 1] - t[k] != want:
                errors.append(f"frame {f}: plane {k} lasts {t[k + 1] - t[k]} cycles, expected {want}")
        if f + 1 < nf and w[4 * f + 4][0] - t[3] < 8 * U:
            errors.append(f"frame {f}: plane 3 lasts {w[4 * f + 4][0] - t[3]} cycles, expected >= {8 * U}")
        if f + 1 < nf:
            worst = max(worst, w[4 * f + 4][0] - t[3] - 8 * U)
        frames.append([sum(((planes[k][1] >> i) & 1) << k for k in range(4)) for i in range(8)])
    exp = model(nf, mode)
    for f, (got, want) in enumerate(zip(frames, exp)):
        if got != want:
            errors.append(f"frame {f}: levels {got}, expected {want}")
    if nf < 186 + 14:
        errors.append(f"only {nf} frames: the playlist (186 steps) did not run through")
    print(f"  D[MODE] = {mode}: {nwords} instructions, {nf} frames checked (playlist = 186), plane 3 is at most "
          f"{worst} cycles longer (the next frame is computed then), {len(errors)} errors")
    return errors


if __name__ == "__main__":
    import shutil
    if not (shutil.which("iverilog") and shutil.which("vvp")):
        print("iverilog / vvp not found")
        sys.exit(2)
    sys.exit(main())
