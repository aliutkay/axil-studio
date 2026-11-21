"""Part 2: register spec (+ base address) -> IP files. Only the spec goes in."""

import os
import subprocess

import pytest

from axil_studio.core import axilgen as G
from tests.conftest import DATA, HDL, check_golden, needs_iverilog

SPECS = sorted(f for f in os.listdir(os.path.join(DATA, "ip")) if f.endswith(".regspec.json"))
BASE = 0x40002000           # a generation input: C header default and testbench bus address


def load(name):
    return G.ModuleSpec.load(os.path.join(DATA, "ip", name))


@pytest.mark.parametrize("name", SPECS)
def test_files_match_golden(name, update_golden):
    spec = load(name)
    check_golden(G.output_texts(spec, base=BASE), f"ip/{spec.module}", update_golden)


@pytest.mark.parametrize("name", SPECS)
def test_generation_is_deterministic_and_spec_only(name, tmp_path):
    spec = load(name)
    a = G.output_texts(spec, base=BASE)
    b = G.output_texts(G.ModuleSpec.from_dict(spec.to_dict()), base=BASE)
    assert a == b
    paths = G.generate_all(spec, str(tmp_path), BASE)
    assert [os.path.basename(p) for p in paths] == G.output_texts_names(spec)
    assert not any(p.endswith(".axproj.json") for p in paths)     # no project / address map is written
    assert G.ModuleSpec.load(str(tmp_path / f"{spec.module}.regspec.json")).to_dict() == spec.to_dict()


def test_base_only_changes_the_address_users():
    spec = load("example_ip_axil.regspec.json")
    a, b = G.output_texts(spec, base=0), G.output_texts(spec, base=0x80000000)
    changed = {n for n in a if a[n] != b[n]}
    assert changed == {f"{spec.module}_tb.v", f"{spec.module}_regs.h"}


@pytest.mark.parametrize("module, ok", [("good_ip", True), ("skidbuffer", False), ("easyaxil_pk", False),
                                        ("module", False), ("bad__name", False), ("1abc", False)])
def test_module_name_rules(module, ok):
    spec = load("far.regspec.json")
    spec.module = module
    assert (spec.validate() == []) == ok


def test_invalid_spec_is_refused(tmp_path):
    spec = load("far.regspec.json")
    spec.registers[0].fields[0].lsb = 40
    with pytest.raises(G.SpecError):
        G.generate_all(spec, str(tmp_path))
    assert not os.listdir(tmp_path)


@needs_iverilog
@pytest.mark.parametrize("name", SPECS)
def test_testbench_passes_in_simulation(name, tmp_path):
    """The generated self-checking testbench: reset values, RW / WSTRB, PULSE, W1C, IRQ, strobes,
    RO inputs (with synchronizers), unused offsets, final read-back."""
    spec = load(name)
    G.generate_all(spec, str(tmp_path), BASE)
    m = spec.module
    srcs = [str(tmp_path / f) for f in [f"{m}_tb.v"] + G.hdl_files(spec)] + [os.path.join(HDL, "skidbuffer.v")]
    subprocess.run(["iverilog", "-g2012", "-o", str(tmp_path / "tb"), *srcs], check=True, capture_output=True)
    out = subprocess.run(["vvp", "-n", str(tmp_path / "tb")], capture_output=True, text=True, timeout=120).stdout
    assert "PASSED" in out and "FAIL" not in out, out[-2000:]


@needs_iverilog
@pytest.mark.parametrize("name", SPECS)
def test_hdl_designer_wrapper_compiles(name, tmp_path):
    spec = load(name)
    if not spec.packed:
        pytest.skip("no _pk wrapper for this spec")
    G.generate_all(spec, str(tmp_path), BASE)
    m = spec.module
    srcs = [str(tmp_path / f"{m}_pk.v"), str(tmp_path / f"{m}.v"), os.path.join(HDL, "skidbuffer.v")]
    subprocess.run(["iverilog", "-g2012", "-s", f"{m}_pk", "-o", str(tmp_path / "pk"), *srcs], check=True,
                   capture_output=True)
