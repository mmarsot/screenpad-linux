#!/usr/bin/python3 -I
"""Runs the screenpad-touchbar command from the screenpad_touchbar package.

Installed as /usr/local/lib/screenpad/touchbar.py next to the package, with
/usr/local/bin/screenpad-touchbar a symlink to it; run from the checkout as
./touchbar.py. Isolated mode (-I) puts neither the script's directory nor the
user's site-packages on sys.path, so this adds the directory the script
really lives in, and only that.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
# Keep root from leaving root-owned __pycache__ directories in the checkout.
sys.dont_write_bytecode = True

from screenpad_touchbar.cli import main

if __name__ == "__main__":
    sys.exit(main())
