#!/usr/bin/env python3
"""AXIL Studio launcher.

    python studio.py                      # hub
    python studio.py --tool debug         # one tool on its own (wrapper | interconnect | debug)
    python studio.py examples/gpio_demo.axproj.json
"""

import sys

from axil_studio.hub import main

if __name__ == "__main__":
    sys.exit(main())
