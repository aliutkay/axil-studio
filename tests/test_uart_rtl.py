"""axiluart_phy RTL checks that need no ZipCPU files (hdl/uart_phy)."""

import os
import shutil
import subprocess

from tests.conftest import needs_iverilog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP = os.path.join(ROOT, "hdl", "uart_phy")


@needs_iverilog
def test_adapter_reset_ctrl_follows_the_hardware(tmp_path):
    sim = str(tmp_path / "sim")
    subprocess.run(["iverilog", "-g2012", "-o", sim, os.path.join(UP, "tb", "tb_adapter_defaults.v"),
                    os.path.join(UP, "rtl", "uart_phy_adapter.v")], check=True)
    out = subprocess.run(["vvp", "-n", sim], capture_output=True, text=True).stdout
    assert "0 errors: PASSED" in out, out


def test_patch_gives_axiluart_v2(tmp_path):
    """Reversing axiluart_v2.patch on axiluart_v2.v and applying it again gives the same file, and the
    patch switches the transmitter to txuart_v2."""
    if not shutil.which("patch"):
        return
    cur = os.path.join(UP, "rtl", "axiluart_v2.v")
    p = os.path.join(UP, "patches", "axiluart_v2.patch")
    work = tmp_path / "axiluart.v"
    shutil.copy(cur, work)
    subprocess.run(["patch", "-R", "-s", str(work), p], check=True)
    orig = work.read_text()
    assert "txuart_v2" not in orig and "\ttxuart\t#(" in orig
    subprocess.run(["patch", "-s", str(work), p], check=True)
    with open(cur, encoding="utf-8") as fh:
        assert work.read_text() == fh.read()
