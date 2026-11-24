"""hdl/v2005: the localparam-free Verilog-2005 set (hexbus + interconnect + UART PHY + axilgpio)."""

import glob
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = os.path.join(ROOT, "hdl", "v2005")
sys.path.insert(0, V)
import no_localparam  # noqa: E402

RTL = sorted(p for p in glob.glob(os.path.join(V, "**", "*.v"), recursive=True)
             if not os.path.basename(p).startswith("tb_"))
IVERILOG = shutil.which("iverilog") and shutil.which("vvp")


def test_no_localparam_left():
    assert RTL and no_localparam.main(["--check"] + RTL) == 0


def test_one_copy_of_every_module():
    names = [os.path.basename(p) for p in RTL]
    assert len(names) == len(set(names))


def test_converter():
    src = ("// Filename: x.v\nmodule m #(\n\tparameter A = 1,\n\tlocalparam B = A + 1\n) (input wire c);\n"
           "\tlocalparam [3:0] S = 4'h2;\n\t// localparam in a comment stays\n"
           "\tgenerate if (1) begin : g\n\t\tlocalparam [1:0] T = 2'd3;\n\tend endgenerate\nendmodule\n")
    out, n, notes = no_localparam.convert(src)
    assert n == 3 and no_localparam.left(out) == [] and "// localparam in a comment stays" in out
    assert "\tparameter B = A + 1" in out and "wire\t[1:0] T = 2'd3;" in out and len(notes) == 1


@pytest.mark.skipif(not IVERILOG, reason="needs Icarus Verilog")
@pytest.mark.parametrize("std", ["2001", "2005"])
def test_tops_compile_as_plain_verilog(std):
    for top in ("hexbus_axil_top_pk", "axiluart_phy_pk", "axilxbar", "axilgpio_pk", "axilgpio_a32",
                "axilgpio_axi4", "system_top"):
        r = subprocess.run(["iverilog", f"-g{std}", "-o", os.devnull, "-s", top] + RTL,
                           capture_output=True, text=True)
        assert r.returncode == 0, (top, r.stderr)


@pytest.mark.skipif(not IVERILOG, reason="needs Icarus Verilog")
def test_system_simulation(tmp_path):
    exe = str(tmp_path / "sim")
    subprocess.run(["iverilog", "-g2005", "-o", exe, os.path.join(V, "example", "tb_system.v")] + RTL, check=True)
    out = subprocess.run(["vvp", "-n", exe], capture_output=True, text=True, check=True, timeout=300).stdout
    assert "A03010001K00000000" in out and "GPIO 5a" in out      # GPIO write through the xbar
    assert "R0000005a" in out                                    # read back
    assert "A03020015R00fa0032" in out                           # UART PHY_TIMING reset value
    assert "TXD 41" in out                                       # the UART PHY sent 'A'
    assert "A05000001E" in out and "DONE" in out                 # no slave there: DECERR


@pytest.mark.skipif(not IVERILOG, reason="needs Icarus Verilog")
def test_generated_interconnect_has_no_localparam(tmp_path):
    """The Interconnect Configurator's wrappers (signals and packed) need no localparam and compile
    as Verilog-2005 with the cores of hdl/v2005."""
    from axil_studio.core import xbargen as X
    spec = X.XbarSpec(module="ic", masters=[X.XbarMaster("m0"), X.XbarMaster("m1")],
                      slaves=[X.XbarSlave("a", base=0x0, mask=0xFFFF0000), X.XbarSlave("b", base=0x10000, mask=0xFFFF0000)])
    files = {"ic.v": X.gen_xbar(spec), "ic_pk.v": X.gen_xbar_pk(spec)}
    paths = []
    for name, text in files.items():
        assert no_localparam.left(text) == [], name
        (tmp_path / name).write_text(text)
        paths.append(str(tmp_path / name))
    core = [os.path.join(V, "interconnect", "axilxbar.v"), os.path.join(V, "interconnect", "addrdecode.v"),
            os.path.join(V, "common", "skidbuffer.v")]
    r = subprocess.run(["iverilog", "-g2005", "-o", os.devnull, "-s", "ic_pk"] + paths + core,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


PK = [os.path.join(V, "gpio", "axilgpio_pk.v"), os.path.join(V, "uart_phy", "axiluart_phy_pk.v"),
      os.path.join(V, "hexbus", "hexbus_axil_top_pk.v"), os.path.join(ROOT, "hdl", "cmd_seq_pk.v")]


def _header(text):
    import re
    m = re.search(r"\bmodule\s+\w+\s*(#\s*\()?", text)
    return m.group(1)


def test_hdl_designer_blocks_have_no_generics():
    """A VHDL top turns Verilog parameters into integer generics (32-bit values overflow): the _pk
    blocks, the ready ones and the generated ones, have no parameter list."""
    from axil_studio.core import axilpack as P
    from axil_studio.core import xbargen as X
    for p in PK:
        with open(p, encoding="utf-8") as fh:
            assert _header(fh.read()) is None, p
    spec = X.XbarSpec(module="ic", slaves=[X.XbarSlave("a", base=0x80000000, mask=0xFFFF0000)])
    pk = X.gen_xbar_pk(spec)
    assert _header(pk) is None and ".SLAVE0_ADDR      (32'h8000_0000)" in pk and ".SLAVE0_MASK      (32'hFFFF_0000)" in pk
    info = P.parse_module(open(os.path.join(V, "gpio", "axilgpio.v")).read(), "axilgpio")
    assert _header(P.gen_pack_wrapper(info)) is None


def test_pack_values():
    """--set / the dialog: values fixed in the _pk block, port widths follow them."""
    from axil_studio.core import axilpack as P
    info = P.parse_module(open(os.path.join(V, "gpio", "axilgpio.v")).read(), "axilgpio")
    t = P.gen_pack_wrapper(info, values={"NOUT": "8", "NIN": 2, "DEFAULT_OUTPUT": "8'h5a"})
    assert "output wire [7:0] o_gpio" in t and "input  wire [1:0] i_gpio" in t
    assert ".NOUT(8)" in t and ".DEFAULT_OUTPUT(8'h5A)" in t and ".C_AXI_DATA_WIDTH(32)" in t
    info = P.parse_module(open(os.path.join(V, "hexbus", "hexbus_axil_top.v")).read(), "hexbus_axil_top")
    t = P.gen_pack_wrapper(info, values={"CLK_FREQ_HZ": "100_000_000"})
    assert ".CLK_FREQ_HZ(100000000)" in t
    with pytest.raises(P.ParseError):
        P.gen_pack_wrapper(info, values={"NOPE": 1})
    with pytest.raises(P.ParseError):
        P.gen_pack_wrapper(info, values={"BAUD_RATE": "FOO+1"})
    assert P.evaluate("((NIN>0) ? (NIN-1):0)", {"NIN": 5}) == 4 and P.evaluate("$clog2(32)-3", {}) == 2
    assert P._literal(None, 0xFFFF0000) == "32'hFFFF0000" and P._literal(None, 434) == "434"


@pytest.mark.skipif(not IVERILOG, reason="needs Icarus Verilog")
def test_pk_blocks_compile(tmp_path):
    cmd = [os.path.join(ROOT, "hdl", "cmd_seq", "rtl", f) for f in ("cmd_seq.v", "cmd_seq_core.v")]
    for p in PK:
        top = os.path.splitext(os.path.basename(p))[0]
        others = [r for r in RTL if os.path.basename(r) != os.path.basename(p)]
        r = subprocess.run(["iverilog", "-g2005", "-o", os.devnull, "-s", top, p] + others + cmd,
                           capture_output=True, text=True)
        assert r.returncode == 0, (top, r.stderr)
