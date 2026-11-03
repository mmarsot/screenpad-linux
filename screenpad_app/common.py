"""The app protocol version, the socket's path, the bar's limits and the errors.

The limits are the ones docs/app-api.md gives; the daemon refuses a message
past them, with an "error" the app gets back.
"""

import os
import re

# The app protocol version this library speaks (docs/app-api.md#the-protocol-
# version). Version 1 is the protocol from before it was versioned, whose
# hello says no version; version 2 added the version to hello and welcome.
PROTOCOL = 2
MIN_PROTOCOL = 1

# $XDG_RUNTIME_DIR/screenpad/touchbar.sock
SOCKET = ("screenpad", "touchbar.sock")

# Bytes in one line, each way.
MAX_LINE = 16384
# Names: an app, a tile, a page (lower case), and the ids of questions,
# answers and widgets.
NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
MAX_TILES = 8
# A tile: its label, second line and badge.
MAX_TILE_LABEL = 32
MAX_TILE_TEXT = 48
MAX_BADGE = 4
# A question.
MAX_TITLE = 80
MAX_ASK_TEXT = 200
MAX_OPTIONS = 3
MAX_OPTION_LABEL = 24
MAX_ASK_TIMEOUT = 3600
# A page of widgets, and a widget.
MAX_PAGES = 4
MAX_COLUMNS = 6
MAX_ROWS = 4
MAX_WIDGETS = 24
MAX_LABEL = 32
MAX_TEXT = 200
MAX_LINES = 16
MAX_LINE_CHARS = 120
TONES = ("neutral", "accept", "reject", "warning")


class TouchbarError(Exception):
    """The connection to the touch bar cannot be used (any reason below)."""


class TouchbarGone(TouchbarError):
    """The daemon is not running, closed the connection, or sent garbage."""


class HelloRefused(TouchbarError):
    """The daemon refused the app's hello: str(e) says why."""


class ProtocolMismatch(HelloRefused):
    """The daemon does not speak the app protocol version the app does.

    speaks is the app's version; supported is (min, max) as the daemon gave
    them, or None for a daemon from before the protocol was versioned
    (which speaks only version 1).
    """

    def __init__(self, speaks, supported):
        self.speaks, self.supported = speaks, supported
        if supported is None:
            text = "the touch bar is too old for app protocol %d: it speaks only version 1; update screenpad-linux"
            super().__init__(text % speaks)
            return
        low, high = supported
        advice = "update screenpad-linux" if speaks > high else "update the app"
        super().__init__("the touch bar speaks app protocol %d to %d, the app %d; %s" % (low, high, speaks, advice))


def socket_path(environ=None):
    """The socket's path; TouchbarGone when XDG_RUNTIME_DIR is not set."""
    runtime = (os.environ if environ is None else environ).get("XDG_RUNTIME_DIR")
    if not runtime:
        raise TouchbarGone("XDG_RUNTIME_DIR is not set; run it in your graphical session")
    return os.path.join(runtime, *SOCKET)


def clip(text, limit):
    """text on one line, cut to limit characters with an ellipsis when it was longer."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
