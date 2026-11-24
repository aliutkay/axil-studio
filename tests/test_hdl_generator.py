"""HDL generators (core/generators.py, generators/*) and the HDL Generator tool."""

import json
import os
import shutil
import subprocess

import pytest

from axil_studio.core import generators as GN
from axil_studio.core.model import Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS485 = os.path.join(ROOT, "examples", "cmd_seq_rs485", "rs485_terminal.axproj.json")
V = os.path.join(ROOT, "hdl", "v2005")
IVERILOG = shutil.which("iverilog")


def _gens():
    gens, errors = GN.discover()
    assert not errors
    return gens


def _req(name, gid, settings):
    return {"name": name, "ip": gid, "base": 0, "settings": settings}


def test_discovery():
    gens = _gens()
    assert {"axiluart_phy", "hexbus_axil_top", "axilgpio"} <= set(gens)
    for g in gens.values():
        assert os.path.isfile(g.source), g.source


def test_uart_values():
    g = _gens()["axiluart_phy"]
    r = GN.run_batch(g, _req("U", "axiluart_phy", {}))
    # the defaults give the reset values of the hardware (library SETUP reset, DEF_TIMING default)
    assert r["params"]["INITIAL_SETUP"] == "31'h400001B2" and r["params"]["DEF_TIMING"] == "32'h00000000"
    r = GN.run_batch(g, _req("U", "axiluart_phy", {"clock_mhz": 50, "baud": 9600, "parity": "even",
                                                   "stop_bits": 2, "line": "rs485_2w"}))
    assert r["params"]["INITIAL_SETUP"] == "31'h4D001458"          # div 5208, PAR_EN, even, 2 stop, no flow
    assert r["params"]["DEF_CTRL"] == "32'h000000C6" and r["params"]["DEF_TIMING"] == "32'h00FA0032"
    r = GN.run_batch(g, _req("U", "axiluart_phy", {"data_bits": 7, "parity": "mark", "flow": True}))
    setup = int(r["params"]["INITIAL_SETUP"].split("h")[1], 16)
    assert setup >> 28 & 3 == 1 and setup >> 24 & 7 == 0b111 and not setup >> 30 & 1
    with pytest.raises(GN.GeneratorError):
        GN.run_batch(g, _req("U", "axiluart_phy", {"clock_mhz": 1, "baud": 1_000_000}))
    with pytest.raises(GN.GeneratorError):
        GN.run_batch(g, _req("U", "axiluart_phy", {"line": "rs485_2w", "has_de": False}))


def test_hexbus_and_gpio_values():
    gens = _gens()
    r = GN.run_batch(gens["hexbus_axil_top"], _req("H", "hexbus_axil_top", {"clock_mhz": 100, "baud": 921600}))
    assert r["params"] == {"CLK_FREQ_HZ": 100000000, "BAUD_RATE": 921600, "C_AXI_ADDR_WIDTH": 32}
    r = GN.run_batch(gens["axilgpio"], _req("G", "axilgpio", {"outputs": 4, "inputs": 2, "default_hex": "5"}))
    assert r["params"]["NOUT"] == 4 and r["params"]["DEFAULT_OUTPUT"] == "4'h5"
    with pytest.raises(GN.GeneratorError):
        GN.run_batch(gens["axilgpio"], _req("G", "axilgpio", {"outputs": 2, "default_hex": "F"}))


@pytest.mark.skipif(not IVERILOG, reason="needs Icarus Verilog")
def test_blocks_compile(tmp_path):
    gens = _gens()
    files = {
        "axiluart_phy": [f"{V}/uart_phy/{f}.v" for f in ("axiluart_phy", "axiluart_v2", "txuart_v2", "uart_phy_adapter")]
        + [f"{V}/wbuart32/rxuart.v", f"{V}/wbuart32/ufifo.v", f"{V}/common/skidbuffer.v"],
        "axilgpio": [f"{V}/gpio/axilgpio.v", f"{V}/common/skidbuffer.v"],
        "hexbus_axil_top": [f"{V}/hexbus/{f}" for f in os.listdir(f"{V}/hexbus") if f.startswith(("hexbus_axil_top.", "hb"))]
        + [f"{V}/wbuart32/rxuartlite.v", f"{V}/wbuart32/txuartlite.v"],
    }
    for name, gid, st in (("UART1", "axiluart_phy", {"baud": 9600, "fifo": 64}),
                          ("LEDS", "axilgpio", {"outputs": 4, "inputs": 2}),
                          ("DBG", "hexbus_axil_top", {"clock_mhz": 100})):
        r = GN.run_batch(gens[gid], _req(name, gid, st))
        p = GN.write_block(gens[gid], name, r["params"], str(tmp_path), r["summary"])
        text = open(p).read()
        assert os.path.basename(p) == f"{name.lower()}_pk.v" and f"module {name.lower()}_pk (" in text
        res = subprocess.run(["iverilog", "-g2005", "-o", os.devnull, "-s", f"{name.lower()}_pk", p] + files[gid],
                             capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
    assert "output wire [3:0] o_gpio" in open(tmp_path / "leds_pk.v").read()


@pytest.mark.skipif(not shutil.which("yosys"), reason="needs Yosys")
def test_uart_defaults_equal_the_ready_block(tmp_path):
    """The UART generator with its defaults gives the same logic as hdl/v2005/uart_phy/axiluart_phy_pk.v
    except for the documented reset values (it writes DEF_CTRL / DEF_TIMING of a TTL line)."""
    g = _gens()["axiluart_phy"]
    r = GN.run_batch(g, _req("U", "axiluart_phy", {"line": "rs485_4w"}))
    assert r["params"]["DEF_CTRL"] == "32'h000000C2" and r["params"]["DEF_TIMING"] == "32'h00FA0032"
    p = GN.write_block(g, "axiluart_phy", r["params"], str(tmp_path))
    cores = " ".join(f"{V}/uart_phy/{f}.v" for f in ("axiluart_phy", "axiluart_v2", "txuart_v2", "uart_phy_adapter")) \
        + f" {V}/wbuart32/rxuart.v {V}/wbuart32/ufifo.v {V}/common/skidbuffer.v"
    script = (f"read_verilog {V}/uart_phy/axiluart_phy_pk.v {cores}; hierarchy -top axiluart_phy_pk; proc; flatten; "
              f"memory; opt_clean; rename axiluart_phy_pk gold; design -stash gold; read_verilog {p} {cores}; "
              "hierarchy -top axiluart_phy_pk; proc; flatten; memory; opt_clean; rename axiluart_phy_pk gate; "
              "design -stash gate; design -copy-from gold -as gold gold; design -copy-from gate -as gate gate; "
              "equiv_make gold gate equiv; hierarchy -top equiv; async2sync; equiv_simple -seq 5; "
              "equiv_induct -seq 5; equiv_status -assert")
    res = subprocess.run(["yosys", "-q", "-p", script], capture_output=True, text=True, timeout=600)
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]


def test_rows_store_and_generate_all(tmp_path):
    p = Project.load(RS485)
    assert [(n, g) for n, g, _ in GN.rows(p)] == [("GPIO", "axilgpio"), ("UART1", "axiluart_phy"), ("UART2", "axiluart_phy")]
    GN.blocks(p)["DBG"] = {"generator": "hexbus_axil_top", "settings": {"clock_mhz": 50}, "free": True}
    GN.blocks(p)["UART1"] = {"generator": "axiluart_phy", "settings": {"line": "rs485_4w", "baud": 57600}}
    paths, errors = GN.generate_all(p, _gens(), str(tmp_path))
    assert not errors and sorted(os.path.basename(x) for x in paths) == ["dbg_pk.v", "gpio_pk.v", "uart1_pk.v", "uart2_pk.v"]
    e = p.hdl_gen["blocks"]["UART1"]
    assert e["summary"].startswith("57600 8N1") and e["params"]["DEF_CTRL"] == "32'h000000C2"
    again = Project.from_data(p.to_data())               # kept in the project file
    assert again.hdl_gen["blocks"]["DBG"]["free"] and again.hdl_gen["blocks"]["UART1"]["settings"]["baud"] == 57600


def test_tool_page(qapp, no_dialogs, monkeypatch, tmp_path):
    from axil_studio.project import ProjectContext
    from axil_studio.tools.hdl_generator import HdlGeneratorPage
    ctx = ProjectContext(qapp)
    ctx.project = Project.load(RS485)
    page = HdlGeneratorPage(ctx)
    names = [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]
    assert names == ["GPIO", "UART1", "UART2"] and page.tree.topLevelItem(1).text(1) == "uart1_pk.v"
    # configure: the generator's window is replaced by its batch run with chosen settings
    gens = page.gens

    def fake_configure(name):
        row = next(r for r in GN.rows(ctx.project) if r[0] == name)
        res = GN.run_batch(gens[row[1]], _req(name, row[1], {"baud": 230400, "line": "rs485_2w"}))
        page.apply_result(name, row[1], res, row[2] == "block")
    monkeypatch.setattr(page, "configure", fake_configure)
    assert page.select("UART2")
    page.configure_selected()
    assert page.tree.currentItem().text(3).startswith("230400 8N1, RS-485 2-wire")
    # a block added by hand, then generate all into the chosen folder
    monkeypatch.setattr("axil_studio.tools.hdl_generator.QInputDialog.getItem",
                        lambda *a, **k: (next(x for x in a[3] if "hexbus_axil_top" in x), True))
    monkeypatch.setattr("axil_studio.tools.hdl_generator.QInputDialog.getText", lambda *a, **k: ("DBG", True))
    page.add_block()
    assert page.select("DBG") and page.rm_btn.isEnabled()
    monkeypatch.setattr("axil_studio.tools.hdl_generator.QFileDialog.getExistingDirectory", lambda *a, **k: str(tmp_path))
    paths, errors = page.generate_all()
    assert not errors and len(paths) == 4 and (tmp_path / "uart2_pk.v").exists()
    assert "230400" in (tmp_path / "uart2_pk.v").read_text()
    page.remove_block()
    assert not page.select("DBG")
    page.shutdown()


def test_window_runs(qapp, tmp_path):
    """The SDK window opens, computes and writes OUT on OK (driven without a user)."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "generators", "sdk"))
    sys.path.insert(0, os.path.join(ROOT, "generators", "axilgpio"))
    import axil_gen
    import gen as gpio_gen
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QSpinBox
    fin, fout = tmp_path / "in.json", tmp_path / "out.json"
    fin.write_text(json.dumps(_req("LEDS", "axilgpio", {"outputs": 6})))

    def press_ok():
        dlg = next(w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible())
        spin = dlg.findChildren(QSpinBox)[0]
        spin.setValue(12)
        dlg.accept()
    QTimer.singleShot(200, press_ok)
    rc = axil_gen.run({"en": "GPIO generator"}, gpio_gen.FIELDS, gpio_gen.compute,
                      ["--in", str(fin), "--out", str(fout), "--lang", "tr"])
    assert rc == 0 and json.loads(fout.read_text())["params"]["NOUT"] == 12
    sys.path.remove(os.path.join(ROOT, "generators", "axilgpio"))
    sys.modules.pop("gen", None)
