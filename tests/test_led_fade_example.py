"""examples/cmd_seq_led_fade: two LEDs on axilgpio cross-faded by cmd_seq (project, library,
program, and a simulation on the real RTL)."""

import os
import shutil
import sys

import pytest

from axil_studio.core import addrmap as A
from axil_studio.core.library import IPLibrary
from axil_studio.core.model import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "cmd_seq_led_fade")
sys.path.insert(0, os.path.join(EX, "sim"))
sys.path.insert(1, os.path.join(ROOT, "interfaces", "cmd_seq"))
import run_sim  # noqa: E402


def test_project_and_library():
    p = Project.load(os.path.join(EX, "led_fade.axproj.json"))
    assert not A.validate(p)
    ips = {i.name: i for i in p.address_ips()}
    assert ips["SEQ0"].base == 0x03000000 and ips["GPIO"].base == 0x03010000
    assert [r.offset for r in ips["GPIO"].registers] == list(range(0, 0x20, 4))
    lib = IPLibrary.load(os.path.join(EX, "axilgpio.axlib.json")).get("axilgpio").ip
    assert [r.to_dict() for r in lib.registers] == [r.to_dict() for r in p.definition_of(p.get_instance("GPIO")).registers]


def test_program_assembles_with_the_project_names():
    import seq_core as S
    with open(os.path.join(EX, "led_fade.s"), encoding="utf-8") as fh:
        src = fh.read()
    p = Project.load(os.path.join(EX, "led_fade.axproj.json"))
    entries = [{"name": i.name, "base": i.base,
                "registers": [r.to_dict() for r in (p.definition_of(i).registers if p.definition_of(i) else [])]}
               for i, _ in p.walk()]
    syms = S.symbols(entries)
    assert syms["GPIO_BASE"] == 0x03010000 and syms["GPIO_TOGGLE"] == 0x0301000C
    a, _, _ = S.assemble(src, 9, 8, syms)           # the Command sequencer window
    b, _ = run_sim.seqasm.assemble(src)             # the command-line assembler
    assert [w for _, w, _ in a] == [w for _, w, _ in b] and len(b) < 512


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_led_waveform_on_the_rtl():
    assert run_sim.run({"PERIOD": 200, "STEP": 50, "REP": 3, "HOLD": 4}, 2 * 32 * 200 + 3000) == []


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_led_waveform_with_the_axi4_wrappers():
    """cmd_seq_axi4 -> axilgpio_axi4 (Libero bus definition AXI4): same waveform, single beats,
    the request IDs come back in BID / RID."""
    assert run_sim.run({"PERIOD": 200, "STEP": 50, "REP": 3, "HOLD": 4}, 2 * 32 * 200 + 3000, axi4=True) == []
