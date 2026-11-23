"""C header of the address map (core/cheader.py)."""

import os
import shutil
import subprocess

import pytest

from axil_studio.core import addrmap as A
from axil_studio.core.cheader import gen_system_header, header_name
from axil_studio.core.model import IPInstance, Project

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _project():
    p = Project.load(os.path.join(ROOT, "examples", "uart_phy", "uart_phy_demo.axproj.json"))
    A.add_instance(p, "axiluart_phy", "UART2")
    p.instances.append(IPInstance("DDR", "", 0x70000000, 0x100000, True, span=0x100000))
    return p


def test_header_has_every_instance_and_register():
    p = _project()
    h = gen_system_header(p)
    assert header_name(p) == "uart_phy_demo_map.h" and "#ifndef UART_PHY_DEMO_MAP_H" in h
    assert h.count("#define AXILUART_PHY_SETUP_OFFSET  0x00u") == 1          # one block per IP definition
    assert "#define AXILUART_PHY_PHY_CTRL_DE_MODE_MASK   0x00000003u" in h
    assert "#define UART0_BASE  0x60001000u" in h
    assert "#define UART0_PHY_CTRL  (UART0_BASE + AXILUART_PHY_PHY_CTRL_OFFSET)   /* 0x60001010 */" in h
    u1 = p.get_instance("UART2")
    assert f"#define UART2_BASE  0x{u1.base:08X}u" in h and "#define UART2_SETUP  (UART2_BASE" in h
    assert "#define DDR_BASE  0x70000000u" in h and "#define DDR_SIZE  0x00100000u" in h


@pytest.mark.skipif(not (shutil.which("gcc") or shutil.which("cc")), reason="no C compiler")
def test_header_compiles(tmp_path):
    hdr = tmp_path / "map.h"
    hdr.write_text(gen_system_header(_project()))
    src = tmp_path / "t.c"
    src.write_text('#include "map.h"\n_Static_assert(UART0_PHY_CTRL == 0x60001010u, "addr");\n'
                   'unsigned f(void) { return UART0_SETUP + AXILUART_PHY_SETUP_DATA_BITS_MASK; }\n')
    cc = shutil.which("gcc") or shutil.which("cc")
    r = subprocess.run([cc, "-std=c11", "-Wall", "-Werror", "-fsyntax-only", str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_command_line(tmp_path):
    from axil_studio.core import cheader
    out = tmp_path / "x.h"
    assert cheader.main([os.path.join(ROOT, "examples", "gpio_demo.axproj.json"), "-o", str(out)]) == 0
    assert "#define GPIO_0_BASE" in out.read_text()
