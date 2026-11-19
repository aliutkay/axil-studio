"""AXIL Studio: hub for the AXI4-Lite tools.

  Slave Interface Wrapper                  register spec -> AXI4-Lite slave HDL
  Address Map & Interconnect Configurator  project address map -> axilxbar wrapper
  UART-to-AXI Debug Bridge                 live register access over hexbus/UART

All three share one project (.axproj.json, see model.py) through ProjectContext,
and each can also run on its own:  python -m axil_studio --tool debug
"""

import os
import sys

APP_NAME = "AXIL Studio"
RELEASE_STAGE = "early-alpha"     # early-alpha → alpha → beta → release
APP_VERSION = "0.0.1"
ORG_NAME = "AXIL"


def version_text(version=APP_VERSION, stage=RELEASE_STAGE):
    """"early-alpha 0.0.1" style label."""
    return f"{stage} {version}"

# The tool cores (model, hexbus, axilgen, xbargen, ...) live in the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
