"""examples/cmd_seq_rs485: three sequencers (SEQ_MAIN, SEQ_GPIO, SEQ_UART), an RS-485 terminal that
answers and blinks an LED."""

import os
import shutil
import sys

import pytest

from axil_studio.core import addrmap as A
from axil_studio.core import cmdseq
from axil_studio.core import xbargen as X
from axil_studio.core.model import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(ROOT, "examples", "cmd_seq_rs485")
sys.path.insert(0, os.path.join(EX, "sim"))
sys.path.insert(1, os.path.join(ROOT, "interfaces", "cmd_seq"))
PROGS = ("seq_main.s", "seq_gpio.s", "seq_uart.s")


def _project():
    return Project.load(os.path.join(EX, "rs485_terminal.axproj.json"))


def test_project():
    p = _project()
    assert not A.validate(p)
    ids = {n: cmdseq.self_id(p.get_instance(n)) for n in ("SEQ_MAIN", "SEQ_GPIO", "SEQ_UART")}
    assert ids == {"SEQ_MAIN": 0x00, "SEQ_GPIO": 0x02, "SEQ_UART": 0x04}
    bases = {i.name: i.base for i in p.address_ips()}
    assert bases == {"SEQ_MAIN": 0, "SEQ_GPIO": 0x20000, "GPIO": 0x30000, "SEQ_UART": 0x40000,
                     "UART1": 0x50000, "UART2": 0x51000}


def test_programs_assemble_in_the_window():
    import seq_core as S
    import seqasm
    p = _project()
    entries = [{"name": i.name, "base": i.base,
                "registers": [r.to_dict() for r in (p.definition_of(i).registers if p.definition_of(i) else [])]}
               for i, _ in p.walk()]
    syms = S.symbols(entries)
    for f in PROGS:
        src = open(os.path.join(EX, f), encoding="utf-8").read()
        a, _, _ = S.assemble(src, 9, 8, syms)
        b, _ = seqasm.assemble(src)
        assert [w for _, w, _ in a] == [w for _, w, _ in b] and len(b) < 512, f


def test_generated_hdl_is_current():
    """examples/cmd_seq_rs485/hdl = what the Interconnect Configurator writes for the project."""
    p = _project()
    want = {}
    for b in A.xbar_levels(p):
        if b is None:
            continue
        sp = A.xbar_spec(p, b, "packed")
        want[f"{sp.module}.v"] = X.gen_xbar(sp)
        want[f"{sp.module}_pk.v"] = X.gen_xbar_pk(sp)
    for n in ("SEQ_MAIN", "SEQ_GPIO", "SEQ_UART"):
        inst = p.get_instance(n)
        want[f"{cmdseq.pk_name(inst)}.v"] = cmdseq.pk_wrapper(inst)
    d = os.path.join(EX, "hdl")
    assert sorted(os.listdir(d)) == sorted(want)
    for name, text in want.items():
        with open(os.path.join(d, name), encoding="utf-8") as fh:
            assert fh.read() == text, name


def test_write_templates_writes_the_blocks(tmp_path):
    paths = cmdseq.write_templates(_project(), str(tmp_path))
    names = sorted(os.path.basename(x) for x in paths)
    assert names == sorted(f"seq_{n}_cmd_seq_{k}" for n in ("main", "gpio", "uart") for k in ("pk.v", "inst.v"))
    pk = (tmp_path / "seq_uart_cmd_seq_pk.v").read_text()
    assert "module seq_uart_cmd_seq_pk (" in pk and ".SELF_ID(8'h4)" in pk


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_system_simulation():
    """Terminal answers, LED blinks, RX / TX swapped from the PC while running (see sim/run_sim.py)."""
    import run_sim
    out = run_sim.run(16)
    assert run_sim.check(out) == []
