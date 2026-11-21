"""Part 1: user data -> HDL specs. The HDL spec is the only input of the Verilog generators
(core/hdlspec.py); getting it right, and the same on every path (tools, command line, saved
project), is all part 1 must do. tests/data/hdl holds the expected HDL specs."""

import copy
import json
import os
import subprocess
import sys

import pytest

from axil_studio.core import axilgen as G
from axil_studio.core import hdlspec as H
from axil_studio.core import xbargen as X
from axil_studio.core.model import EnumValue, Project
from tests.conftest import DATA, ROOT
from tests.helpers import hier_project, tiny_spec

REGSPECS = sorted(os.listdir(os.path.join(DATA, "ip")))
XBARS = sorted(os.listdir(os.path.join(DATA, "xbar")))


def expected(module):
    return H.load(os.path.join(DATA, "hdl", f"{module}.hdlspec.json"))


def cli(*args):
    return subprocess.run([sys.executable, "-m", "axil_studio.core.axilgen", *args], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("name", REGSPECS)
def test_register_map_gives_the_expected_hdl_spec(name):
    spec = G.ModuleSpec.load(os.path.join(DATA, "ip", name))
    assert H.ip_hdlspec(spec) == expected(spec.module)


@pytest.mark.parametrize("name", XBARS)
def test_crossbar_gives_the_expected_hdl_spec(name):
    spec = X.XbarSpec.load(os.path.join(DATA, "xbar", name))
    assert H.xbar_hdlspec(spec) == expected(spec.module)


def test_hierarchy_gives_the_expected_hdl_specs():
    got = [H.xbar_hdlspec(s) for s in H.project_xbar_specs(hier_project())]
    assert got == [expected(m) for m in ("axil_xbar", "axil_xbar_SBB_0", "axil_xbar_SBB_1")]


def test_only_code_data_is_in_the_ip_hdl_spec():
    """Descriptions, value meanings, formats, version, unplaced fields do not change the HDL spec."""
    spec = G.ModuleSpec.load(os.path.join(DATA, "ip", "example_ip_axil.regspec.json"))
    other = copy.deepcopy(spec)
    other.desc, other.version = "another description", "9.9.9"
    other.unplaced = [G.FieldSpec("SPARE", 0, 4)]
    for r in other.registers:
        r.desc = "x"
        for f in r.fields:
            f.desc, f.fmt, f.values = "y", "hex", [EnumValue(0, "zero", "clear")]
    assert H.ip_hdlspec(other) == H.ip_hdlspec(spec)
    changed = copy.deepcopy(spec)
    changed.registers[1].fields[0].reset ^= 1                   # a reset value changes the code
    assert H.ip_hdlspec(changed) != H.ip_hdlspec(spec)


def test_only_code_data_is_in_the_xbar_hdl_spec():
    spec = X.XbarSpec.load(os.path.join(DATA, "xbar", "xb_3x4.xbar.json"))
    other = copy.deepcopy(spec)
    for m in other.masters:
        m.name = "renamed"
    for s in other.slaves:
        s.name, s.span, s.fixed, s.external = "renamed", 0, not s.fixed, not s.external
    assert H.xbar_hdlspec(other) == H.xbar_hdlspec(spec)
    other.slaves[0].mask = 0xFFFFE000
    assert H.xbar_hdlspec(other) != H.xbar_hdlspec(spec)


def test_hierarchy_ports_and_addresses():
    p = hier_project()
    p.interconnect = {"module": "top_xbar", "masters": [{"name": "cpu"}, {"name": "dbg"}], "style": "signals"}
    p.get_instance("SBB_0").xbar = {"module": "", "masters": ["dma"]}
    top, s0, s1 = [H.xbar_hdlspec(s) for s in H.project_xbar_specs(p)]
    assert (top["module"], s0["module"], s1["module"]) == ("top_xbar", "axil_xbar_SBB_0", "axil_xbar_SBB_1")
    assert (top["masters"], s0["masters"], s1["masters"]) == (2, 2, 1)      # a bridge's MASTER0 + extras
    assert (len(top["slaves"]), len(s0["slaves"]), len(s1["slaves"])) == (3, 3, 1)
    assert not top["hdl_designer_wrapper"]
    win = lambda s: (int(s["addr"], 16), ((~int(s["mask"], 16)) & 0xFFFFFFFF) + 1)
    lo, size = win(top["slaves"][1])                                          # SBB_0's window on the top bus
    for s in s0["slaves"]:                                                    # global addresses inside it
        a, w = win(s)
        assert lo <= a and a + w <= lo + size


def test_regspec_survives_the_project():
    """Register Map Editor -> project IP definition -> Slave Interface Wrapper: the same HDL spec."""
    for name in REGSPECS:
        spec = G.ModuleSpec.load(os.path.join(DATA, "ip", name))
        ip = G.to_ipblock(copy.deepcopy(spec), 0, with_spec=True)
        assert H.ip_hdlspec(G.spec_from_ipblock(ip)) == H.ip_hdlspec(spec)


def test_saved_project_gives_the_same_hdl_specs(tmp_path):
    p = hier_project()
    before = H.project_hdlspecs(p)
    path = str(tmp_path / "h.axproj.json")
    p.save(path)
    assert H.project_hdlspecs(Project.load(path)) == before


def test_command_line_dump_matches(tmp_path):
    p = hier_project()
    path = str(tmp_path / "h.axproj.json")
    p.save(path)
    assert json.loads(cli("--dump-spec", path)) == H.project_hdlspecs(Project.load(path))
    rs = os.path.join(DATA, "ip", "far.regspec.json")
    assert json.loads(cli("--dump-spec", rs)) == expected("far")
    xb = os.path.join(DATA, "xbar", "xb_3x4.xbar.json")
    assert json.loads(cli("--dump-spec", xb)) == expected("xb_3x4")


def test_generated_folders_hold_their_hdl_spec(tmp_path):
    """Every generation writes the HDL spec next to the Verilog it was made from."""
    spec = tiny_spec()
    G.generate_all(spec, str(tmp_path / "ip"), 0x1000)
    assert H.load(str(tmp_path / "ip" / "tst_reg.hdlspec.json")) == H.ip_hdlspec(spec)
    p = hier_project()
    path = str(tmp_path / "h.axproj.json")
    p.save(path)
    cli("--xbar", path, "-o", str(tmp_path / "x"))
    for s in H.project_xbar_specs(p):
        assert H.load(str(tmp_path / "x" / f"{s.module}.hdlspec.json")) == H.xbar_hdlspec(s)
