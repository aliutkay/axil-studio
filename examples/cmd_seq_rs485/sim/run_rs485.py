#!/usr/bin/env python3
"""System simulation of the RS-485 terminal example (three sequencers, GPIO, two UART PHYs).

    python3 examples/cmd_seq_rs485/sim/run_rs485.py               # UARTs at 16 clocks per bit (~5 s)
    python3 examples/cmd_seq_rs485/sim/run_rs485.py --real-baud   # hdl/v2005 axiluart_phy_pk: 434 (~1 min)

Builds the design from what AXIL Studio generates for rs485_terminal.axproj.json (the packed
interconnects and the per-sequencer HDL Designer blocks) and hdl/v2005, loads the three programs,
starts SEQ_MAIN and checks:
  - a byte on UART1 gets "OK: <byte>" CR LF back on UART2, and o_gpio[0] blinks ('3' -> 3 times,
    'A' -> once; CR gets no answer and no blink); UART1 never drives DE, UART2 does;
  - after the PC swaps D[0] / D[1] of SEQ_UART (through the running SEQ_MAIN) a byte on UART2 is
    answered on UART1 ('2' -> 2 blinks), and now UART1 drives DE;
  - the counters (SEQ_UART COUNT / EVENTS / ERRORS, SEQ_MAIN FWD, SEQ_GPIO DONE), no bus error,
    the three sequencers still running.
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
from axil_studio.core import addrmap as A, cmdseq, xbargen as X  # noqa: E402
from axil_studio.core.model import Project  # noqa: E402

REPLY = [0x4F, 0x4B, 0x3A, 0x20]          # "OK: "


def build(work, bit=16):
    """Generated Verilog and program hex files in `work`; returns (sources, -P options). `bit`:
    clocks per UART bit; other than 434 the UART block is generated with that INITIAL_SETUP."""
    p = Project.load(os.path.join(EX, "rs485_terminal.axproj.json"))
    srcs = []
    for b in A.xbar_levels(p):
        if b is None:
            continue                      # the top one has SEQ_MAIN only: the TB master is wired to it
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
        opts += [f'-Ptb_rs485.P{key}="{hexf}"', f"-Ptb_rs485.N{key}={len(prog)}"]
    v = os.path.join(ROOT, "hdl", "v2005")
    uart_pk = os.path.join(v, "uart_phy", "axiluart_phy_pk.v")
    if bit != 434:
        from axil_studio.core import axilpack
        _, text = axilpack.pack_file(os.path.join(v, "uart_phy", "axiluart_phy.v"),
                                     values={"INITIAL_SETUP": f"31'd{bit}"})
        uart_pk = os.path.join(work, "axiluart_phy_pk.v")
        open(uart_pk, "w").write(text)
    opts.append(f"-Ptb_rs485.BIT={bit}")
    rtl = [os.path.join(ROOT, "hdl", "cmd_seq", "rtl", f) for f in ("cmd_seq.v", "cmd_seq_core.v")]
    rtl += [os.path.join(v, "interconnect", "axilxbar.v"), os.path.join(v, "interconnect", "addrdecode.v"),
            os.path.join(v, "common", "skidbuffer.v"), os.path.join(v, "gpio", "axilgpio_pk.v"),
            os.path.join(v, "gpio", "axilgpio.v"), uart_pk,
            os.path.join(v, "uart_phy", "axiluart_phy.v"), os.path.join(v, "uart_phy", "axiluart_v2.v"),
            os.path.join(v, "uart_phy", "txuart_v2.v"), os.path.join(v, "uart_phy", "uart_phy_adapter.v"),
            os.path.join(v, "wbuart32", "rxuart.v"), os.path.join(v, "wbuart32", "ufifo.v")]
    return srcs + rtl, opts


def run(bit=16):
    work = tempfile.mkdtemp(prefix="rs485_")
    try:
        srcs, opts = build(work, bit)
        exe = os.path.join(work, "tb")
        subprocess.run(["iverilog", "-g2012", "-o", exe] + opts + [os.path.join(HERE, "tb_rs485.v")] + srcs,
                       check=True)
        return subprocess.run(["vvp", "-n", exe], check=True, capture_output=True, text=True, timeout=900).stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(out):
    errors = []
    phases = out.split("PHASE ")
    if len(phases) != 3:
        return ["the testbench did not reach both phases"]
    p1, p2 = phases[1], phases[2]

    def tx(text, line):
        return [int(x, 16) for x in re.findall(rf"^TX{line} ([0-9a-f]+)", text, re.M)]

    def leds(text):
        return len(re.findall(r"^LED \d+", text, re.M))

    def de(text):
        return [tuple(map(int, m)) for m in re.findall(r"^DE (\d+) (\d+)", text, re.M)]

    def reply(c):
        return REPLY + [c, 0x0D, 0x0A]

    for base, n in re.findall(r"^LOADED ([0-9a-f]+) (\d+)", out, re.M):
        if int(n) == 0:
            errors.append(f"program at 0x{base} not loaded")
    st = dict((int(a, 16), int(d, 16)) for a, d in re.findall(r"^R ([0-9a-f]+) ([0-9a-f]+)", phases[0], re.M))
    for a in (0x0002000C, 0x0004000C):
        if not st.get(a, 0) & 1:
            errors.append(f"sequencer at 0x{a - 0xC:08X} not started by SEQ_MAIN (STATUS 0x{st.get(a, 0):08X})")
    if tx(p1, 2) != reply(0x33) + reply(0x41):
        errors.append(f"phase 1: UART2 sent {[hex(x) for x in tx(p1, 2)]}, expected 'OK: 3' and 'OK: A' replies")
    if tx(p1, 1):
        errors.append(f"phase 1: UART1 sent {tx(p1, 1)}")
    if leds(p1) != 4:
        errors.append(f"phase 1: {leds(p1)} blinks, expected 3 + 1")
    d1 = de(p1)
    if not d1 or d1[0][0] != 0 or d1[0][1] < 2:
        errors.append(f"phase 1: DE pulses (UART1, UART2) = {d1}, expected UART1 0 and UART2 >= 2")
    if tx(p2, 1) != reply(0x32):
        errors.append(f"phase 2: UART1 sent {[hex(x) for x in tx(p2, 1)]}, expected 'OK: 2'")
    if tx(p2, 2):
        errors.append(f"phase 2: UART2 sent {tx(p2, 2)} after the swap")
    if leds(p2) != 2:
        errors.append(f"phase 2: {leds(p2)} blinks, expected 2")
    d2 = de(p2)
    if not d1 or not d2 or d2[0][0] < 1 or d2[0][1] != d1[0][1]:
        errors.append(f"phase 2: DE pulses {d2} (after phase 1: {d1}): UART1 must drive, UART2 not")
    regs = dict((int(a, 16), int(d, 16)) for a, d in re.findall(r"^R ([0-9a-f]+) ([0-9a-f]+)", p2, re.M))
    want = {0x00040410: 3, 0x0004041C: 3, 0x00040418: 0, 0x00000400: 3, 0x00020414: 3}
    for a, w in want.items():
        if regs.get(a) != w:
            errors.append(f"0x{a:08X} = {regs.get(a)}, expected {w}")
    for a in (0x0000000C, 0x0002000C, 0x0004000C):
        s = regs.get(a, 0)
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
    errors = check(out)
    for e in errors:
        print("  FAIL", e)
    print("rs485 terminal:", "PASSED" if not errors else "FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
