#!/usr/bin/env python3
"""System simulation of the UART loopback example (three sequencers, GPIO, two UART PHYs with a
crossed cable modelled in the testbench).

    python3 examples/cmd_seq_loopback/sim/run_loopback.py              # UARTs at 16 clocks per bit
    python3 examples/cmd_seq_loopback/sim/run_loopback.py --real-baud  # 434 (115200 at 50 MHz)

The hardware is that of examples/cmd_seq_rs485 (same project, same generated blocks); only the
programs are this folder's. Checks:
  - lamp test at the start (both LEDs on);
  - cable fine: rounds pass, OK LED (o_gpio[0]) on, FAIL LED (o_gpio[1]) off, every byte good;
  - UART1 -> UART2 wire cut: RESULT "bytes missing", FAIL LED, first failure on path A, nothing came;
  - wire back: OK LED again;
  - HOLD = 1 and one wrong bit on path B: FAIL LED; it stays on while later rounds pass, until
    the PC writes HELD = 0;
  - no bus error, the three sequencers still running.
Needs Python 3.8+ and Icarus Verilog. Exit code 0 = passed.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(EX))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "hdl", "cmd_seq", "sw"))
import seqasm  # noqa: E402
from axil_studio.core import addrmap as A, axilpack, cmdseq, xbargen as X  # noqa: E402
from axil_studio.core.model import Project  # noqa: E402

PROJECT = os.path.join(ROOT, "examples", "cmd_seq_rs485", "rs485_terminal.axproj.json")
LEN = 32                                   # bytes per path (SEQ_UART default)


def build(work, bit=16):
    p = Project.load(PROJECT)
    srcs = []
    for b in A.xbar_levels(p):
        if b is None:
            continue
        spec = A.xbar_spec(p, b, "packed")
        for name, text in ((f"{spec.module}.v", X.gen_xbar(spec)), (f"{spec.module}_pk.v", X.gen_xbar_pk(spec))):
            path = os.path.join(work, name)
            open(path, "w").write(text)
            srcs.append(path)
    for n in ("SEQ_MAIN", "SEQ_GPIO", "SEQ_UART"):
        inst = p.get_instance(n)
        path = os.path.join(work, cmdseq.pk_name(inst) + ".v")
        open(path, "w").write(cmdseq.pk_wrapper(inst))
        srcs.append(path)
    opts = []
    for key, f in (("MAIN", "seq_main"), ("GPIO", "seq_gpio"), ("UART", "seq_uart")):
        prog, _ = seqasm.assemble(open(os.path.join(EX, f + ".s"), encoding="utf-8").read())
        hexf = os.path.join(work, f + ".hex")
        open(hexf, "w").write(seqasm.hex_text(prog))
        opts += [f'-Ptb_loopback.P{key}="{hexf}"', f"-Ptb_loopback.N{key}={len(prog)}"]
    v = os.path.join(ROOT, "hdl", "v2005")
    uart_pk = os.path.join(v, "uart_phy", "axiluart_phy_pk.v")
    if bit != 434:
        _, text = axilpack.pack_file(os.path.join(v, "uart_phy", "axiluart_phy.v"),
                                     values={"INITIAL_SETUP": f"31'd{bit}"})
        uart_pk = os.path.join(work, "axiluart_phy_pk.v")
        open(uart_pk, "w").write(text)
    opts.append(f"-Ptb_loopback.BIT={bit}")
    rtl = [os.path.join(ROOT, "hdl", "cmd_seq", "rtl", f) for f in ("cmd_seq.v", "cmd_seq_core.v")]
    rtl += [os.path.join(v, "interconnect", "axilxbar.v"), os.path.join(v, "interconnect", "addrdecode.v"),
            os.path.join(v, "common", "skidbuffer.v"), os.path.join(v, "gpio", "axilgpio_pk.v"),
            os.path.join(v, "gpio", "axilgpio.v"), uart_pk,
            os.path.join(v, "uart_phy", "axiluart_phy.v"), os.path.join(v, "uart_phy", "axiluart_v2.v"),
            os.path.join(v, "uart_phy", "txuart_v2.v"), os.path.join(v, "uart_phy", "uart_phy_adapter.v"),
            os.path.join(v, "wbuart32", "rxuart.v"), os.path.join(v, "wbuart32", "ufifo.v")]
    return srcs + rtl, opts


def run(bit=16):
    work = tempfile.mkdtemp(prefix="loopback_")
    try:
        srcs, opts = build(work, bit)
        exe = os.path.join(work, "tb")
        subprocess.run(["iverilog", "-g2012", "-o", exe] + opts + [os.path.join(HERE, "tb_loopback.v")] + srcs,
                       check=True)
        return subprocess.run(["vvp", "-n", exe], check=True, capture_output=True, text=True, timeout=1800).stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(out):
    errors = []
    parts = out.split("PHASE ")
    if len(parts) != 7:
        return [f"the testbench reached {len(parts) - 1} of 6 phases"]

    def regs(text):
        return dict((int(a, 16), int(d, 16)) for a, d in re.findall(r"^R ([0-9a-f]+) ([0-9a-f]+)", text, re.M))

    def leds(text):
        m = re.search(r"^LEDS (\d) (\d)", text, re.M)
        return (int(m.group(1)), int(m.group(2))) if m else None

    def expect(phase, cond, what):
        if not cond:
            errors.append(f"phase {phase}: {what}")

    for base, n in re.findall(r"^LOADED ([0-9a-f]+) (\d+)", out, re.M):
        if int(n) == 0:
            errors.append(f"program at 0x{base} not loaded")
    r0 = regs(parts[0])
    for a in (0x0002000C, 0x0004000C):
        if not r0.get(a, 0) & 1:
            errors.append(f"sequencer at 0x{a - 0xC:08X} not started by SEQ_MAIN")
    if "LAMP 1 1" not in parts[0]:
        errors.append("no lamp test (both LEDs on at the start)")

    p = [None] + [regs(t) for t in parts[1:]]
    ROUNDS, LAST, FAILS = 0x400, 0x404, 0x408
    GOOD, BAD, LOST, LINE = 0x40438, 0x4043C, 0x40440, 0x40444
    FPATH, FIDX, FGOT, HELD = 0x4044C, 0x40450, 0x40458, 0x20414
    r = p[1]
    expect(1, leds(parts[1]) == (1, 0), f"LEDs {leds(parts[1])}, expected OK on, FAIL off")
    expect(1, r.get(LAST) == 0 and r.get(FAILS) == 0, f"LAST {r.get(LAST)}, FAILS {r.get(FAILS)}")
    expect(1, r.get(GOOD) == r.get(ROUNDS, 0) * 2 * LEN and r.get(ROUNDS, 0) >= 3,
           f"GOOD {r.get(GOOD)} after {r.get(ROUNDS)} rounds, expected {2 * LEN} per round")
    expect(1, r.get(BAD) == r.get(LOST) == r.get(LINE) == 0, "BAD / LOST / LINE not 0")
    r = p[2]
    expect(2, leds(parts[2]) == (0, 1), f"LEDs {leds(parts[2])}, expected OK off, FAIL on")
    expect(2, r.get(LAST) == 0x02 and r.get(FAILS, 0) >= 2, f"LAST 0x{r.get(LAST, 0):X}, FAILS {r.get(FAILS)}")
    expect(2, r.get(FPATH) == 1 and r.get(FGOT) == 0x100 and r.get(FIDX) == 0,
           f"first failure: path {r.get(FPATH)}, index {r.get(FIDX)}, got 0x{r.get(FGOT, 0):X}")
    expect(2, r.get(LOST, 0) >= 2 * LEN, f"LOST {r.get(LOST)}")
    r = p[3]
    expect(3, leds(parts[3]) == (1, 0), f"LEDs {leds(parts[3])}, expected OK again")
    expect(3, r.get(LAST) == 0, f"LAST 0x{r.get(LAST, 0):X}")
    r = p[4]
    expect(4, leds(parts[4]) == (0, 1), f"LEDs {leds(parts[4])}, expected FAIL")
    expect(4, r.get(HELD) == 1, f"HELD {r.get(HELD)}")
    expect(4, r.get(FPATH) == 2, f"first failure on path {r.get(FPATH)}, expected B (2)")
    expect(4, r.get(BAD, 0) + r.get(LINE, 0) >= 1, "neither a wrong byte nor a line error counted")
    r = p[5]
    expect(5, r.get(LAST) == 0, f"LAST 0x{r.get(LAST, 0):X}: the round after the bit flip should pass")
    expect(5, leds(parts[5]) == (0, 1) and r.get(HELD) == 1, f"LEDs {leds(parts[5])}: the failure must be held")
    r = p[6]
    expect(6, leds(parts[6]) == (1, 0), f"LEDs {leds(parts[6])}: OK after HELD = 0")
    for a in (0x0000000C, 0x0002000C, 0x0004000C):
        s = r.get(a, 0)
        if not s & 1 or s & 4:
            errors.append(f"STATUS 0x{a:08X} = 0x{s:08X}: not running or error")
    if "BUSERR 0" not in out:
        errors.append("bus error on the upstream port: " + ", ".join(re.findall(r"^BUSERR [RW] \w+", out, re.M)))
    return errors


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not (shutil.which("iverilog") and shutil.which("vvp")):
        print("iverilog / vvp not found")
        return 2
    out = run(434 if "--real-baud" in argv else 16)
    if "-v" in argv:
        print(out)
    errors = check(out)
    for e in errors:
        print("  FAIL", e)
    print("uart loopback:", "PASSED" if not errors else "FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
