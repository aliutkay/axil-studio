#!/usr/bin/env python3
"""Hardware validation of axiluart_phy from the command line.

The tests live in axil_studio/core/uart_validate.py (the UART interface's Validation tab runs the
same code); this script only starts them:

  python scripts/uart_validate.py --hexbus COM5 --line COM7 --line-type rs485-4w --lead-us 10
  python scripts/uart_validate.py --config examples/uart_phy/bench_rs485_4w.json
"""

import os
import sys

# run as a script: the package lives in the repository root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from axil_studio.core.uart_validate import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
