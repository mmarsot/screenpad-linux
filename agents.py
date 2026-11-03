#!/usr/bin/python3 -I
"""Runs the screenpad-agents command from the screenpad_agents package.

Installed as /usr/local/lib/screenpad/agents.py next to the package, with
/usr/local/bin/screenpad-agents a symlink to it; run from the checkout as
./agents.py. Isolated mode (-I) puts neither the script's directory nor the
user's site-packages on sys.path, so this adds the directory the script
really lives in, and only that.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.dont_write_bytecode = True

from screenpad_agents.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
