"""What the touch bar tests share: the checkout on sys.path, the GDX1505's axes,
events in device units, a Splitter fed whole frames, a config home, fake and
real app connections, the extension's plain modules run under node, an
app's page for the apps surface, and Fn+F6's GNOME setting, panel and
hotkeys device."""

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from urllib.request import pathname2url

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

from screenpad_touchbar.codes import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TRACKING_ID,
    BTN_LEFT,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    SYN_REPORT,
)
from screenpad_touchbar.common import HEADER_H, PANEL_H
from screenpad_touchbar.config import parse_config
from screenpad_touchbar.fnkey import Hotkey, Panel
from screenpad_touchbar.splitter import BAR, PAD, Axes, Splitter

# The GDX1505 as it reports itself: 3840x2160 units, 32 units/mm, 5 slots.
AXES = Axes(0, 3840, 0, 2160)


SYN = (EV_SYN, SYN_REPORT, 0)


def config(text=""):
    return parse_config(text)


def touch(slot, id, x, y):
    """Events that put a contact down (or move it) in a slot, in device units."""
    ev = [(EV_ABS, ABS_MT_SLOT, slot)]
    if id is not None:
        ev.append((EV_ABS, ABS_MT_TRACKING_ID, id))
    return ev + [(EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]


def lift(slot):
    return [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]


class Split:
    """A Splitter with a 160 px bar, fed whole frames."""

    def __init__(self, height=160):
        self.s = Splitter(AXES, 5, lambda px, py: BAR if py < height else PAD)

    def frame(self, *groups, left=None):
        events = [e for g in groups for e in g]
        if left is not None:
            events.append((EV_KEY, BTN_LEFT, int(left)))
        out = None
        for e in events + [SYN]:
            out = self.s.feed(*e)
            if e != SYN:
                self.assertNone(out)
        return out

    @staticmethod
    def assertNone(out):
        assert out is None, out


def codes(pad, type, code):
    return [v for ty, c, v in pad if (ty, c) == (type, code)]


class ConfigHome:
    """Points XDG_CONFIG_HOME and XDG_RUNTIME_DIR at a directory."""

    def __init__(self, d):
        self.d = d

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_RUNTIME_DIR")}
        os.environ["XDG_CONFIG_HOME"] = self.d
        os.environ["XDG_RUNTIME_DIR"] = os.path.join(self.d, "run")
        return os.path.join(self.d, "screenpad", "touchbar.toml")

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeClient:
    """An app as the daemon sees it, without a socket."""

    def __init__(self):
        self.sent = []
        self.name = None
        self.protocol = None

    def send(self, msg):
        self.sent.append(msg)

    def last(self, op):
        found = [m for m in self.sent if m.get("op") == op]
        return found[-1] if found else None


TILE_CONFIG = """
[[bar]]
icon = "audio-input-microphone-symbolic"
label = "PTT"
mode = "hold"
key = "F14"
[[bar]]
label = "Agents"
tile = "agents"
[[bar]]
app = "files.desktop"
"""


def pump(server, rounds=1):
    for _ in range(rounds):
        ready, _, _ = select.select(server.rfds(), server.wfds(), [], 0.01)
        for fd in ready:
            if server.owns(fd):
                server.readable(fd)
        server.flush()


class Conn:
    """An app's end of the socket, driven together with the server."""

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""

    def send(self, msg):
        self.s.sendall((json.dumps(msg) + "\n").encode())

    def recv(self, server):
        for _ in range(200):
            if b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                return json.loads(line)
            pump(server)
            self.s.settimeout(0.01)
            try:
                data = self.s.recv(65536)
            except TimeoutError:
                continue
            if not data:
                return None
            self.buf += data
        raise AssertionError("no reply")


# The spots at x 0, 272, 544, 856, 1128, 1400 (PTT and files in the first
# two); the page button at 1672; the grip at 1816. The bar is 160 px high
# (device rows 0..320); a card makes it 456 (0..912).
CONFIG = """
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"
[[bar]]
app = "files.desktop"
"""


EXT = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad")


def read(name):
    with open(os.path.join(EXT, name)) as f:
        return f.read()


def js(module, expr, **values):
    """The JSON value of expr, evaluated by node with the extension's module
    (contract.js, scene.js or cards.js) imported as m and values as v.

    Skips the test when node is missing, except in CI, where it must be
    there: those modules are the extension's logic the tests run.
    """
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            raise AssertionError("node is needed to test the GNOME Shell extension's modules")
        raise unittest.SkipTest("node is not installed")
    url = "file:" + pathname2url(os.path.abspath(os.path.join(EXT, module)))
    code = "import * as m from %s;\nconst v = %s;\nprocess.stdout.write(JSON.stringify(%s) ?? 'null');\n" % (
        json.dumps(url),
        json.dumps(values),
        expr,
    )
    run = subprocess.run([node, "--input-type=module", "-e", code], capture_output=True, text=True, timeout=30)
    if run.returncode != 0:
        raise AssertionError("node: %s" % run.stderr.strip())
    return json.loads(run.stdout)


# The bar tests' config. Spots at x 0, 272, 544, 856, 1128, 1400 (the files
# and web apps in the fourth and fifth); the page button at 1672; the grip
# at 1816.
BAR_CONFIG = """
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"
[[bar]]
label = "Mute"
key = "micmute"
[[bar]]
label = "Term"
mode = "hold"
command = "a"
release_command = "b"
[[bar]]
app = "files.desktop"
[[bar]]
app = "web.desktop"
"""


def notify(**kw):
    """A notify message as the extension sends it, with kw changed."""
    msg = {
        "op": "notify",
        "id": "n1",
        "app": "org.gnome.Calendar.desktop",
        "source": "Calendar",
        "title": "Standup",
        "text": "Starts in 5 minutes",
        "urgency": "normal",
        "actions": ["Join", "Snooze"],
        "banners": True,
        "dnd": False,
        "acknowledged": False,
    }
    msg.update(kw)
    return msg


# The apps surface's tests: a bar of TOP rows, so the page's grid starts
# at GRID_Y and is GRID_H rows tall.
TOP = 160  # the bar's own height in these tests
GRID_Y = TOP + HEADER_H
GRID_H = PANEL_H - GRID_Y


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def page_msg(**kw):
    """A page message: Allow (tap or long press), Deny and the prompt's
    lines, in a 3 by 2 grid; kw changes fields."""
    msg = {
        "op": "page",
        "page": "agents",
        "title": "Agents",
        "text": "1 blocked",
        "tone": "warning",
        "columns": 3,
        "rows": 2,
        "widgets": [
            {"id": "prompt", "col": 0, "row": 0, "cols": 3, "lines": ["Run npm test?", "1. Yes"], "touch": "none"},
            {"id": "allow", "col": 0, "row": 1, "label": "Allow", "tone": "accept", "touch": "long"},
            {"id": "deny", "col": 2, "row": 1, "label": "Deny", "tone": "reject"},
        ],
    }
    msg.update(kw)
    return msg


def hello(apps, name="fleet"):
    client = FakeClient()
    apps.connect(client)
    apps.handle(client, {"op": "hello", "app": name, "tiles": []})
    return client


class FakeSetting:
    """send-events as GNOME keeps it. flip() is what gsd-media-keys does on a
    press: the change shows only when the monitor's line is read."""

    def __init__(self, value="enabled"):
        self.value = value
        self.written = []
        self.r, self.w = os.pipe()
        self.lines = []

    def start(self, now):
        pass

    def fd(self):
        return self.r

    def flip(self):
        current = self.lines[-1] if self.lines else self.value
        self.change("disabled" if current == "enabled" else "enabled")

    def change(self, value):
        self.lines.append(value)
        os.write(self.w, b".")

    def readable(self):
        os.read(self.r, 64)
        changes = 0
        for value in self.lines:
            if value != self.value:
                self.value = value
                changes += 1
        self.lines = []
        return changes

    def set(self, value):
        self.written.append(value)
        self.value = value

    def stop(self):
        os.close(self.r)
        os.close(self.w)


class FakePanel(Panel):
    """The panel, with 'screenpad light' and 'dark' done at once."""

    def __init__(self, lit=True):
        super().__init__(power="/nonexistent", command=["false"])
        self.is_lit = lit
        self.ran = []

    def lit(self):
        return self.is_lit

    def spawn(self, on):
        self.ran.append("light" if on else "dark")
        self.is_lit = on


def hotkey(test, toggle):
    """A hotkeys device for toggle, a fifo; returns its writing end."""
    path = os.path.join(test.enterContext(tempfile.TemporaryDirectory()), "event13")
    os.mkfifo(path)
    toggle.hotkey = Hotkey(path)
    w = os.open(path, os.O_WRONLY)
    test.addCleanup(os.close, w)
    return w
