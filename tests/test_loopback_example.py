"""examples/cmd_seq_loopback: UART loopback test on the hardware of examples/cmd_seq_rs485 (three
sequencers, GPIO, two UART PHYs); OK / FAIL LEDs."""

import os
import shutil
import sys

import pytest

from axil_studio.core.model import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "cmd_seq_loopback")
sys.path.insert(0, os.path.join(EX, "sim"))
sys.path.insert(1, os.path.join(ROOT, "interfaces", "cmd_seq"))
PROGS = ("seq_main.s", "seq_gpio.s", "seq_uart.s")


def test_programs_assemble_in_the_window():
    """Same words from the sequencer window (address-map symbols of the RS-485 project) and seqasm."""
    import seq_core as S
    import seqasm
    p = Project.load(os.path.join(ROOT, "examples", "cmd_seq_rs485", "rs485_terminal.axproj.json"))
    entries = [{"name": i.name, "base": i.base,
                "registers": [r.to_dict() for r in (p.definition_of(i).registers if p.definition_of(i) else [])]}
               for i, _ in p.walk()]
    syms = S.symbols(entries)
    for f in PROGS:
        src = open(os.path.join(EX, f), encoding="utf-8").read()
        a, _, _ = S.assemble(src, 9, 8, syms)
        b, _ = seqasm.assemble(src)
        assert [w for _, w, _ in a] == [w for _, w, _ in b] and len(b) < 512, f


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_system_simulation():
    """Cable fine / wire cut / bit flipped, OK and FAIL LEDs, HOLD (see sim/run_loopback.py)."""
    import run_loopback
    out = run_loopback.run(16)
    assert run_loopback.check(out) == []
