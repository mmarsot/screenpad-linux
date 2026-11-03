"""Names, paths, timings and the errors of the agent app."""

import os
import re

# Who the app is on the touch bar's socket, and what it gives it.
APP = "agents"
FLEET_TILE = "agents"
CALLS_TILE = "calls"
PAGE = "agents"

# The touch bar's socket, as docs/app-api.md gives it. The app finds it the
# way any app does; it imports nothing of the daemon.
SOCKET = ("screenpad", "touchbar.sock")
CONFIG = ("screenpad", "agents.toml")

# How often Herdr is asked (seconds), and the bounds a config may set.
HERDR_POLL = 2.0
HERDR_POLL_RANGE = (1.0, 60.0)
HERDR_TIMEOUT = 5.0
# firstmate's reader takes about 3 s; it runs at most once a minute, plus on
# demand when the Captain's Call tile or widget is tapped.
FIRSTMATE_POLL = 60.0
FIRSTMATE_POLL_RANGE = (60.0, 3600.0)
FIRSTMATE_TIMEOUT = 30.0
BEARINGS_SCHEMA = "fm-bearings.v1"
# Waiting for the touch bar's daemon when it is not running.
RECONNECT = 5.0

# Herdr's agent states. Herdr is authoritative; anything else is "unknown".
STATES = ("working", "blocked", "idle", "done", "unknown")
STATE_TONES = {"working": "accept", "blocked": "warning"}

# The touch bar's limits this app writes within (docs/app-api.md).
MAX_LABEL = 32
MAX_TEXT = 200
MAX_TILE_TEXT = 48
MAX_BADGE = 4
WIDGET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
COLUMNS = 4
MIN_ROWS, MAX_ROWS = 2, 4
# A widget's fixed-width lines: at most 16, each at most 120 characters.
MAX_LINES = 16
MAX_LINE_CHARS = 120
# firstmate's interrupt (bin/fm-control.sh <task> interrupt) waits for the
# harness to acknowledge, about 5 s; past this it is reported failed.
CONTROL_TIMEOUT = 60.0


class AgentsError(Exception):
    """A problem the app reports in one line and exits on (code 2)."""


class ConfigError(AgentsError):
    pass


def runtime_dir():
    path = os.environ.get("XDG_RUNTIME_DIR")
    if not path:
        raise AgentsError("XDG_RUNTIME_DIR is not set; run it in your graphical session")
    return path


def socket_path():
    return os.path.join(runtime_dir(), *SOCKET)


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, *CONFIG)


def clip(text, limit):
    """text cut to limit characters, with an ellipsis when it was longer."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
