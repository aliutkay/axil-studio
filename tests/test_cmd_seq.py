"""cmd_seq (programmable command sequencer, hdl/cmd_seq): assembler, reference model,
directed and random simulation, formal proof."""

import importlib
import importlib.util
import os
import shutil
import sys

import pytest

from tests.conftest import needs_iverilog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CS = os.path.join(ROOT, "hdl", "cmd_seq")
for sub in ("sw", "verif", "formal"):
    sys.path.insert(0, os.path.join(CS, sub))

seqasm = importlib.import_module("seqasm")
seqmodel = importlib.import_module("seqmodel")


def run_model(src, **kw):
    prog, entry = seqasm.assemble(src)
    m = seqmodel.Seq()
    m.load(prog)
    m.run(entry, **kw)
    return m


# ---------------------------------------------------------------- assembler
def test_assembler_round_trip():
    progs = os.path.join(CS, "verif", "progs")
    src = "".join(open(os.path.join(progs, n), encoding="utf-8").read()
                  for n in sorted(os.listdir(progs)) if n.endswith(".s") and n != "errors.s")
    src += "KILL 0x42\nSEV 7 @9\nWAITT R3\nTMR R4\nRD D[3], [R2-4]\nMOV R1, 0x8000_0000\n"
    prog, _ = seqasm.assemble(src)
    text = "\n".join(seqasm.disasm(w) for _, w, _ in prog)
    again, _ = seqasm.assemble(text.replace("-0x", "+0x"))
    assert [w for _, w, _ in again] == [w for _, w, _ in prog]


@pytest.mark.parametrize("line, msg", [
    ("KILL 5", "0x10..0xFF"),
    ("LOOP R0, 0", "other than R0"),
    ("BR XX, 0", "unknown condition"),
    ("JMP 512", "outside I-mem"),
    ("NOP @256", "0..255"),
    ("SEV 8", "0..7"),
    ("LDD R1, D[256]", "out of range"),
])
def test_assembler_rejects(line, msg):
    with pytest.raises(seqasm.AsmError, match=msg):
        seqasm.assemble(line + "\n")


def test_axi_list_has_count_and_sum():
    prog, entry = seqasm.assemble("LDI R1, 1\nEND\n")
    text = seqasm.axi_text(prog, entry, 0x0300 << 16, run=True)
    assert f"IMEM_SUM=0x{seqasm.imem_sum(prog):08X}" in text
    assert "W2 0x03000024" in text and text.strip().endswith("# RUN + ERR_HALT")


# ---------------------------------------------------------------- reference model
def test_model_conditions():
    m = run_model(open(os.path.join(CS, "verif", "progs", "cond.s"), encoding="utf-8").read())
    assert (m.R[1], m.R[4], m.R[6]) == (0x155, 0x293, 0x18D)


def test_model_ldd_and_errors():
    m = run_model(open(os.path.join(CS, "verif", "progs", "ldd.s"), encoding="utf-8").read())
    assert m.R[2:5] == [0x22, 0x11, 0x22] and m.state == "DONE"
    m = run_model("RET\n")
    assert (m.state, m.err_code, m.err_pc) == ("ERROR", seqasm.E_STACK, 0)
    m = run_model("LDI R11, 0x000E0000\nRD R1, [R11]\nBR ERR, x\nEND\nx: LDI R2, 7\nEND\n", err_halt=0)
    assert m.state == "DONE" and m.R[2] == 7 and m.err_addr == 0x000E0000


# ---------------------------------------------------------------- simulation
@needs_iverilog
def test_directed(tmp_path):
    run = importlib.import_module("run")
    assert run.directed(str(tmp_path)) is True


@needs_iverilog
@pytest.mark.parametrize("maxd", [0, 3])
def test_random_against_model(maxd):
    random_test = importlib.import_module("random_test")
    assert random_test.main(["-n", "60", "--seed", str(11 + maxd), "--maxd", str(maxd)]) == 0


# ---------------------------------------------------------------- formal
def _have_solver():
    if any(shutil.which(s) for s in ("yices-smt2", "bitwuzla")):
        return True
    # find_spec only: importing bitwuzla next to Qt in the test process crashes at exit
    return importlib.util.find_spec("bitwuzla") is not None


@pytest.mark.skipif(not (shutil.which("yosys") and shutil.which("yosys-smtbmc") and _have_solver()),
                    reason="yosys / yosys-smtbmc / yices or bitwuzla not installed")
def test_formal_prove_and_cover():
    run_formal = importlib.import_module("run_formal")
    assert run_formal.main(["prove", "cover"]) == 0


def test_examples_assemble():
    ex = os.path.join(CS, "sw", "examples")
    names = sorted(n for n in os.listdir(ex) if n.endswith(".s"))
    assert len(names) == 4
    for n in names:
        prog, _ = seqasm.assemble(open(os.path.join(ex, n), encoding="utf-8").read())
        assert prog
