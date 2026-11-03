"""What the touch bar tests share: the checkout on sys.path, the GDX1505's axes,
events in device units, a Splitter fed whole frames, a config home, fake and
real app connections, and the extension's plain modules run under node."""

import json
import os
import select
import shutil
import socket
import subprocess
import sys
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
from screenpad_touchbar.config import parse_config
from screenpad_touchbar.splitter import Axes, Splitter

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
        self.s = Splitter(AXES, 5, lambda px, py: py < height)

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

    def send(self, msg):
        self.sent.append(msg)

    def last(self, op):
        found = [m for m in self.sent if m.get("op") == op]
        return found[-1] if found else None


TILE_CONFIG = """
[[button]]
icon = "audio-input-microphone-symbolic"
label = "PTT"
mode = "hold"
key = "F14"
[[button]]
label = "Agents"
tile = "agents"
[[shortcut]]
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


# Buttons at x 0, 296, 592; shortcuts at 928, 1224, 1520; grip 1816. The
# bar is 160 px high (device rows 0..320); a card makes it 456 (0..912).
CONFIG = """
[[button]]
label = "PTT"
mode = "hold"
key = "F14"
[[shortcut]]
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


# The bar tests' config. Spots: buttons at x 0, 296, 592; shortcuts at 928,
# 1224, 1520; grip 1816.
BAR_CONFIG = """
[[button]]
label = "PTT"
mode = "hold"
key = "F14"
[[button]]
label = "Mute"
key = "micmute"
[[button]]
label = "Term"
mode = "hold"
command = "a"
release_command = "b"
[[shortcut]]
app = "files.desktop"
[[shortcut]]
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
