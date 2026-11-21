"""Part 1: project -> specs. The spec is the whole input of part 2 (core/specs.py), so getting it
right (and the same on every path: tools, command line, saved project) is all part 1 must do."""

import copy
import json
import os
import subprocess
import sys

from axil_studio.core import addrmap as A
from axil_studio.core import axilgen as G
from axil_studio.core import specs
from axil_studio.core import xbargen as X
from axil_studio.core.model import Project
from tests.conftest import DATA, ROOT
from tests.helpers import hier_project, tiny_spec


def cli(*args):
    out = subprocess.run([sys.executable, "-m", "axil_studio.core.axilgen", *args], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return out.stdout


def test_regspec_holds_no_address():
    view = specs.ip_view(tiny_spec(), 0x40000000)
    text = json.dumps(view["regspec"])
    assert "base" not in view["regspec"] and "40000000" not in text
    assert view["generation"]["base"] == "0x40000000"


def test_regspec_survives_the_project():
    """Register Map Editor -> project IP definition -> Slave Interface Wrapper: the same spec."""
    for name in sorted(os.listdir(os.path.join(DATA, "ip"))):
        spec = G.ModuleSpec.load(os.path.join(DATA, "ip", name))
        ip = G.to_ipblock(copy.deepcopy(spec), 0, with_spec=True)
        assert G.spec_from_ipblock(ip).to_dict() == spec.to_dict(), name


def test_hierarchy_gives_one_xbar_spec_per_bus():
    p = hier_project()
    p.interconnect = {"module": "top_xbar", "masters": [{"name": "cpu"}, {"name": "dbg"}], "style": "signals"}
    p.get_instance("SBB_0").xbar = {"module": "", "masters": ["dma"]}
    top, s0, s1 = specs.project_xbar_specs(p)
    assert (top.module, s0.module, s1.module) == ("top_xbar", "axil_xbar_SBB_0", "axil_xbar_SBB_1")
    assert [m.name for m in top.masters] == ["cpu", "dbg"]
    assert [m.name for m in s0.masters] == ["SBB_0", "dma"]           # the bridge is MASTER0
    assert [s.name for s in top.slaves] == ["ip_0", "SBB_0", "ip_4"]
    assert [s.name for s in s0.slaves] == ["ip_1", "ip_2", "SBB_1"]
    assert {top.style, s0.style, s1.style} == {"signals"}
    b0 = p.get_instance("SBB_0")
    sbb_port = top.slaves[1]
    assert (sbb_port.base, sbb_port.window) == (b0.base, A.window_of(p, b0))
    for s in s0.slaves:                                                 # global addresses, inside the bridge
        assert sbb_port.base <= s.base and s.base + s.window <= sbb_port.base + sbb_port.window


def test_ip_generation_address_is_its_first_place():
    p = hier_project()
    view = specs.project_view(p)
    assert view["ips"][0]["generation"]["base"] == "0x%08X" % p.get_instance("ip_0").base
    assert [i["name"] for i in view["ips"][0]["instances"]] == ["ip_0", "ip_1", "ip_2", "ip_3", "ip_4"]
    assert view["errors"] == []


def test_saved_project_gives_the_same_specs(tmp_path):
    p = hier_project()
    before = specs.project_view(p)
    path = tmp_path / "h.axproj.json"
    p.save(str(path))
    assert specs.project_view(Project.load(str(path))) == before


def test_command_line_dump_matches(tmp_path):
    p = hier_project()
    path = tmp_path / "h.axproj.json"
    p.save(str(path))
    assert json.loads(cli("--dump-spec", str(path))) == specs.project_view(Project.load(str(path)))
    rs = os.path.join(DATA, "ip", "far.regspec.json")
    assert json.loads(cli("--dump-spec", rs, "--base", "0x1000")) == specs.ip_view(G.ModuleSpec.load(rs), 0x1000)
    xb = os.path.join(DATA, "xbar", "xb_3x4.xbar.json")
    assert json.loads(cli("--dump-spec", xb)) == specs.xbar_view(X.XbarSpec.load(xb))


def test_command_line_generates_what_the_specs_say(tmp_path):
    """axilgen --xbar <project>: one wrapper set per interconnect, as in the project's specs."""
    p = hier_project()
    path = tmp_path / "h.axproj.json"
    p.save(str(path))
    cli("--xbar", str(path), "-o", str(tmp_path / "out"))
    want = sorted(n for s in specs.project_xbar_specs(p) for n in X.output_names(s))
    assert sorted(os.listdir(tmp_path / "out")) == want
    for s in specs.project_xbar_specs(p):
        assert (tmp_path / "out" / f"{s.module}.v").read_text() == X.gen_xbar(s)
