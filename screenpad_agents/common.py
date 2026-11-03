"""Names, paths, timings and the errors of the agent app.

The touch bar's own limits, its socket's path and clip() are the client
library's (screenpad_app), as for any app.
"""

import os

import screenpad_app

# Who the app is on the touch bar's socket, and what it gives it.
APP = "agents"
FLEET_TILE = "agents"
CALLS_TILE = "calls"
PAGE = "agents"

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

# The Agents page's grid: COLUMNS wide, MIN_ROWS rows or more as agents
# need, up to the touch bar's own limit (screenpad_app.MAX_ROWS).
COLUMNS = 4
MIN_ROWS = 2
# firstmate's interrupt (bin/fm-control.sh <task> interrupt) waits for the
# harness to acknowledge, about 5 s; past this it is reported failed.
CONTROL_TIMEOUT = 60.0
# Finding and opening the decisions board: fm-bearings-board.sh path, the
# Lavish session listing (node, about 0.5 s), and the URL handler.
BOARD_TIMEOUT = 15.0
LAVISH_TIMEOUT = 15.0
OPEN_TIMEOUT = 15.0
# The card that says no board is open stays this long, unless dismissed.
BOARD_CARD_TIMEOUT = 30


class AgentsError(Exception):
    """A problem the app reports in one line and exits on (code 2)."""


class ConfigError(AgentsError):
    pass


def socket_path():
    """The touch bar's socket, found the way any app finds it."""
    try:
        return screenpad_app.socket_path()
    except screenpad_app.TouchbarError as e:
        raise AgentsError(str(e))


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, *CONFIG)
