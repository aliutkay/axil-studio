"""axiluart_phy RTL checks (RTL: hdl/v2005/uart_phy and hdl/v2005/wbuart32; testbenches and patches:
hdl/uart_phy)."""

import os
import shutil
import subprocess

import pytest

from tests.conftest import needs_iverilog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "hdl", "uart_phy")
RTL = os.path.join(ROOT, "hdl", "v2005", "uart_phy")


@needs_iverilog
def test_adapter_reset_ctrl_follows_the_hardware(tmp_path):
    sim = str(tmp_path / "sim")
    subprocess.run(["iverilog", "-g2012", "-o", sim, os.path.join(UP, "tb", "tb_adapter_defaults.v"),
                    os.path.join(RTL, "uart_phy_adapter.v")], check=True)
    out = subprocess.run(["vvp", "-n", sim], capture_output=True, text=True).stdout
    assert "0 errors: PASSED" in out, out


def test_patch_gives_axiluart_v2(tmp_path):
    """Reversing axiluart_v2.patch on axiluart_v2.v and applying it again gives the same file, and the
    patch switches the transmitter to txuart_v2."""
    if not shutil.which("patch"):
        return
    cur = os.path.join(RTL, "axiluart_v2.v")
    p = os.path.join(UP, "patches", "axiluart_v2.patch")
    work = tmp_path / "axiluart.v"
    shutil.copy(cur, work)
    subprocess.run(["patch", "-R", "-s", str(work), p], check=True)
    orig = work.read_text()
    assert "txuart_v2" not in orig and "\ttxuart\t#(" in orig
    subprocess.run(["patch", "-s", str(work), p], check=True)
    with open(cur, encoding="utf-8") as fh:
        assert work.read_text() == fh.read()


@needs_iverilog
@pytest.mark.parametrize("tb, ok", [("tb_axiluart_phy", "PASSED: 0 error(s)"),
                                    ("tb_formats_loopback", "PASSED"),
                                    ("tb_tx_parity", "nonstandard parity bits 0/6")])
def test_axiluart_phy_testbenches(tmp_path, tb, ok):
    """The full IP with the wbuart32 files of hdl/v2005 (Verilog-2005, no localparam)."""
    v = os.path.join(ROOT, "hdl", "v2005")
    srcs = [os.path.join(UP, "tb", f"{tb}.v")] + [os.path.join(RTL, f) for f in (
        "axiluart_phy.v", "axiluart_v2.v", "txuart_v2.v", "uart_phy_adapter.v")] + [
        os.path.join(v, "wbuart32", "rxuart.v"), os.path.join(v, "wbuart32", "ufifo.v"),
        os.path.join(v, "common", "skidbuffer.v")]
    sim = str(tmp_path / "sim")
    subprocess.run(["iverilog", "-g2012", "-o", sim] + srcs, check=True)
    out = subprocess.run(["vvp", "-n", sim], capture_output=True, text=True, timeout=600).stdout
    assert ok in out and "FAIL" not in out, out[-2000:]
    if tb == "tb_tx_parity":
        assert out.count("nonstandard parity bits 0/") == 4, out
