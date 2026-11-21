"""Part 2: HDL spec -> Verilog. Every test starts from an HDL spec file (tests/data/hdl), the only
input of the generators (core/hdlspec.py): the files must match the golden ones and work."""

import copy
import os
import subprocess
import sys

import pytest

from axil_studio.core import axilpack as PK
from axil_studio.core import hdlspec as H
from tests.conftest import DATA, HDL, ROOT, check_golden, needs_iverilog
from tests.helpers import hier_project, tiny_spec

HDLSPECS = sorted(os.listdir(os.path.join(DATA, "hdl")))
IPS = [n for n in HDLSPECS if H.load(os.path.join(DATA, "hdl", n))["format"] == H.IP_FORMAT]
XBARS = [n for n in HDLSPECS if n not in IPS]
CORE = [os.path.join(HDL, f) for f in ("axilxbar.v", "addrdecode.v", "skidbuffer.v")]


def load(name):
    return H.load(os.path.join(DATA, "hdl", name))


@pytest.mark.parametrize("name", HDLSPECS)
def test_verilog_matches_golden(name, update_golden):
    d = load(name)
    check_golden(H.verilog(d), d["module"], update_golden)


@pytest.mark.parametrize("name", HDLSPECS)
def test_command_line_script_writes_the_same_files(name, tmp_path):
    """python -m axil_studio.core.hdlspec <file> -o <dir>: the Verilog of the file, nothing else."""
    src = os.path.join(DATA, "hdl", name)
    subprocess.run([sys.executable, "-m", "axil_studio.core.hdlspec", src, "-o", str(tmp_path)], check=True,
                   capture_output=True, cwd=ROOT)
    d = load(name)
    assert sorted(os.listdir(tmp_path)) == sorted(H.verilog_names(d))
    for n, text in H.verilog(d).items():
        assert (tmp_path / n).read_text() == text


@pytest.mark.parametrize("name", HDLSPECS)
def test_output_depends_on_the_hdl_spec_only(name):
    d = load(name)
    assert H.verilog(copy.deepcopy(d)) == H.verilog(d)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["slaves"][0].update(mask="0xFFFF0F00"), "bitişik"),
    (lambda d: d["slaves"][1].update(addr=d["slaves"][0]["addr"]), "çakışıyor"),
    (lambda d: d["slaves"][0].update(addr="0x40000010"), "hizalı"),
    (lambda d: d.update(masters=0), "Master"),
    (lambda d: d.update(masters=17), "Master"),
    (lambda d: d.update(module="1bad"), "Modül adı"),
    (lambda d: d["parameters"].update(EXTRA=1), "parametre"),
])
def test_invalid_xbar_hdl_spec_is_refused(change, message):
    d = load("xb_3x4.hdlspec.json")
    change(d)
    errs = H.validate(d)
    assert any(message in e for e in errs), errs
    with pytest.raises(H.HdlSpecError):
        H.verilog(d)


@pytest.mark.parametrize("change", [
    lambda d: d["registers"][0]["fields"][0].update(lsb=40),
    lambda d: d["registers"][0]["fields"][0].update(kind="XX"),
    lambda d: d["registers"][1].update(offset=d["registers"][0]["offset"]),
    lambda d: d.update(module="skidbuffer"),
    lambda d: d.update(module="always"),
    lambda d: d["registers"][0].update(fields=[]),
])
def test_invalid_ip_hdl_spec_is_refused(change):
    d = load("example_ip_axil.hdlspec.json")
    change(d)
    assert H.validate(d)
    with pytest.raises(H.HdlSpecError):
        H.verilog(d)


def test_xbar_parameters_reach_axilxbar():
    d = load("xb_min.hdlspec.json")
    d["parameters"] = {"OPT_LOWPOWER": 0, "OPT_LINGER": 7, "LGMAXBURST": 3}
    v = H.verilog(d, "xb_min.v")
    assert "OPT_LOWPOWER = 1'b0" in v and "OPT_LINGER   = 7" in v and "LGMAXBURST   = 3" in v


@needs_iverilog
@pytest.mark.parametrize("name", IPS)
def test_ip_testbench_passes_in_simulation(name, tmp_path):
    """The generated self-checking testbench: reset values, RW / WSTRB, PULSE, W1C, IRQ, strobes,
    RO inputs (with synchronizers), unused offsets, final read-back."""
    d = load(name)
    H.generate(d, str(tmp_path))
    m = d["module"]
    hdl = [f"{m}_pk.v", f"{m}.v"] if d["hdl_designer_wrapper"] else [f"{m}.v"]
    srcs = [str(tmp_path / f) for f in [f"{m}_tb.v"] + hdl] + [os.path.join(HDL, "skidbuffer.v")]
    subprocess.run(["iverilog", "-g2012", "-o", str(tmp_path / "tb"), *srcs], check=True, capture_output=True)
    out = subprocess.run(["vvp", "-n", str(tmp_path / "tb")], capture_output=True, text=True, timeout=120).stdout
    assert "PASSED" in out and "FAIL" not in out, out[-2000:]


@needs_iverilog
@pytest.mark.parametrize("name", HDLSPECS)
def test_verilog_compiles(name, tmp_path):
    d = load(name)
    H.generate(d, str(tmp_path), with_spec=False)
    m = d["module"]
    tops = [m] + ([f"{m}_pk"] if d["hdl_designer_wrapper"] else [])
    srcs = [str(tmp_path / f) for f in (f"{m}.v", f"{m}_pk.v") if (tmp_path / f).exists()]
    srcs += CORE if d["format"] == H.XBAR_FORMAT else [os.path.join(HDL, "skidbuffer.v")]
    for top in tops:
        subprocess.run(["iverilog", "-g2012", "-s", top, "-o", str(tmp_path / "x"), *srcs], check=True,
                       capture_output=True)


# ------------------------------------------------------------------ cascade simulation
def _pack(fields, prefix):
    return "{ " + ", ".join(f"{prefix}{n.lower()}" for n, _ in fields) + " }"


def cascade_testbench(project, xspecs, ip_module):
    """A testbench for the whole hierarchy: one master on the top crossbar; every IP instance is a
    tst_reg slave; every bridge connects its port on the upper crossbar to MASTER0 of its own."""
    from axil_studio.core import addrmap as A
    lv = A.levels(project)
    spec_of = {(b.name if b else ""): s for b, s in zip(lv, xspecs)}
    L = ["`timescale 1ns/1ps", "`default_nettype none", "module tb;",
         "    reg clk = 0, rstn = 0;", "    always #5 clk = ~clk;"]
    for n, w in PK.REQ_FIELDS:
        L.append(f"    reg  [{w - 1}:0] m_{n.lower()} = 0;")
    for n, w in PK.RSP_FIELDS:
        L.append(f"    wire [{w - 1}:0] m_{n.lower()};")
    L.append(f"    wire [{PK.REQ_W - 1}:0] m_req = {_pack(PK.REQ_FIELDS, 'm_')};")
    L.append(f"    wire [{PK.RSP_W - 1}:0] m_rsp;")
    L.append(f"    assign {_pack(PK.RSP_FIELDS, 'm_')} = m_rsp;")
    wires = []
    for key, s in spec_of.items():
        for j, sl in enumerate(s.slaves):
            wires.append(f"    wire [{PK.REQ_W - 1}:0] {s.module}_s{j}_req;")
            wires.append(f"    wire [{PK.RSP_W - 1}:0] {s.module}_s{j}_rsp;")
        for i in range(1, s.nm):
            wires.append(f"    wire [{PK.RSP_W - 1}:0] {s.module}_m{i}_rsp;")
    L += wires
    L += [f"    wire [31:0] val_{i.name};      // the IP's VAL register, seen from outside the bus"
          for i in project.leaf_instances()]
    for key, s in spec_of.items():
        conns = [".ACLK(clk)", ".ARESETN(rstn)"]
        if key == "":
            conns += [".MASTER0_AXI_REQ(m_req)", ".MASTER0_AXI_RSP(m_rsp)"]
        else:
            parent = project.parent_of(project.get_instance(key))
            ps = spec_of[parent.name if parent else ""]
            j = [x.name for x in ps.slaves].index(key)
            conns += [f".MASTER0_AXI_REQ({ps.module}_s{j}_req)", f".MASTER0_AXI_RSP({ps.module}_s{j}_rsp)"]
        for i in range(1, s.nm):                       # extra masters stay idle
            conns += [f".MASTER{i}_AXI_REQ({PK.REQ_W}'b0)", f".MASTER{i}_AXI_RSP({s.module}_m{i}_rsp)"]
        for j in range(s.ns):
            conns += [f".SLAVE{j}_AXI_REQ({s.module}_s{j}_req)", f".SLAVE{j}_AXI_RSP({s.module}_s{j}_rsp)"]
        L.append(f"    {s.module}_pk u_{s.module} ({', '.join(conns)});")
    for key, s in spec_of.items():
        for j, sl in enumerate(s.slaves):
            inst = project.get_instance(sl.name)
            if inst.bridge:
                continue
            L.append(f"    {ip_module}_pk u_{sl.name} (.S_AXI_ACLK(clk), .S_AXI_ARESETN(rstn), "
                     f".SLAVE_AXI_REQ({s.module}_s{j}_req), .SLAVE_AXI_RSP({s.module}_s{j}_rsp), .o_val(val_{sl.name}));")
    L += ["    integer errors = 0;",
          "    task wr(input [31:0] a, input [31:0] d, input [1:0] exp);",
          "        begin",
          "            @(negedge clk); m_awaddr = a; m_wdata = d; m_wstrb = 4'hF; m_awvalid = 1; m_wvalid = 1;"
          " m_bready = 1;",
          "            while (m_awvalid || m_wvalid) begin @(posedge clk); #1;"
          " if (m_awready) m_awvalid = 0; if (m_wready) m_wvalid = 0; end",
          "            while (!m_bvalid) begin @(posedge clk); #1; end",
          "            if (m_bresp !== exp) begin errors = errors + 1;"
          " $display(\"FAIL write %h bresp %b expected %b\", a, m_bresp, exp); end",
          "            @(posedge clk); #1; m_bready = 0;",
          "        end",
          "    endtask",
          "    task rd(input [31:0] a, input [31:0] exp, input [1:0] expr);",
          "        begin",
          "            @(negedge clk); m_araddr = a; m_arvalid = 1; m_rready = 1;",
          "            while (m_arvalid) begin @(posedge clk); #1; if (m_arready) m_arvalid = 0; end",
          "            while (!m_rvalid) begin @(posedge clk); #1; end",
          "            if (m_rresp !== expr || (expr == 0 && m_rdata !== exp)) begin errors = errors + 1;",
          "                $display(\"FAIL read %h = %h resp %b, expected %h resp %b\", a, m_rdata, m_rresp, exp, expr);"
          " end",
          "            @(posedge clk); #1; m_rready = 0;",
          "        end",
          "    endtask",
          "    initial begin",
          "        repeat (5) @(posedge clk); rstn = 1; repeat (2) @(posedge clk);"]
    leaves = [i for i in project.leaf_instances()]
    for k, inst in enumerate(leaves):                   # a different value in every IP ...
        L.append(f"        wr(32'h{inst.base:08X}, 32'h{0xC0DE0000 + k:08X}, 2'b00);")
    for k, inst in enumerate(leaves):                   # ... it landed in the right IP (not just any one)
        L.append(f"        if (val_{inst.name} !== 32'h{0xC0DE0000 + k:08X}) begin errors = errors + 1;"
                 f" $display(\"FAIL {inst.name} holds %h\", val_{inst.name}); end")
    for k, inst in enumerate(leaves):                   # ... and is read back through the bus
        L.append(f"        rd(32'h{inst.base:08X}, 32'h{0xC0DE0000 + k:08X}, 2'b00);")
        L.append(f"        rd(32'h{inst.base + 4:08X}, 32'h0000001D, 2'b00);")
    for hole in unmapped(project):
        L.append(f"        rd(32'h{hole:08X}, 32'h0, 2'b11);   // not mapped: DECERR")
        L.append(f"        wr(32'h{hole:08X}, 32'h1, 2'b11);")
    L += ["        $display(\"%0d errors: %0s\", errors, errors ? \"FAILED\" : \"PASSED\");",
          "        $finish;", "    end",
          "    initial begin #2000000; $display(\"TIMEOUT\"); $finish; end",
          "endmodule"]
    return "\n".join(L) + "\n"


def unmapped(project):
    """Addresses no slave answers: past the top bus, and in each bridge's unused room."""
    from axil_studio.core import addrmap as A
    out = [0xF0000000]
    for b in project.bridges():
        end = A.content_end(project, b)
        if end < A.window_of(project, b):
            out.append(b.base + end)                    # inside the bridge window, behind every child
    return out


@needs_iverilog
def test_cascaded_crossbars_route_global_addresses(tmp_path):
    """top: ip_0, SBB_0 (ip_1, ip_2, SBB_1 (ip_3)), ip_4: writes and reads reach the right IP through
    up to three crossbars; addresses nobody decodes get DECERR at the bus that has no slave for them."""
    spec = tiny_spec()
    p = hier_project(spec)
    b0 = p.get_instance("SBB_0")
    b0.window = 0x10000                                  # room behind the children: an unmapped hole
    from axil_studio.core import addrmap as A
    A.readdress(p)
    assert A.validate(p) == []
    xspecs = H.project_xbar_specs(p, "packed")
    srcs = []
    for s in xspecs:                                     # every crossbar from its HDL spec alone
        srcs += [q for q in H.generate(H.xbar_hdlspec(s), str(tmp_path), with_spec=False)]
    H.generate(H.ip_hdlspec(spec), str(tmp_path / "ip"), with_spec=False)
    srcs = [q for q in srcs if q.endswith(".v")]
    srcs += [str(tmp_path / "ip" / f) for f in (f"{spec.module}_pk.v", f"{spec.module}.v")]
    tb = tmp_path / "tb.v"
    tb.write_text(cascade_testbench(p, xspecs, spec.module))
    subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", str(tmp_path / "sim"), str(tb), *srcs, *CORE],
                   check=True, capture_output=True, text=True)
    out = subprocess.run(["vvp", "-n", str(tmp_path / "sim")], capture_output=True, text=True, timeout=120).stdout
    assert "PASSED" in out and "FAIL" not in out, out[-3000:]
