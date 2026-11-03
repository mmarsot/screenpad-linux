#!/usr/bin/python3 -I
"""ScreenPad touch bar: buttons along the top of the ScreenPad, touchpad below.

The ScreenPad digitizer (GDX1505) only has a touchpad HID collection. This
daemon grabs its evdev node (EVIOCGRAB), so the desktop no longer reads it,
and splits every contact by where it starts:

- in the bar (the top strip, by default 160 of the panel's 1080 rows): the
  contact is ours for its whole life, even if it slides out. It never reaches
  the desktop; it acts on the spot under it: one of three buttons (a key
  combo or a command), one of three app shortcuts (tap opens, a long press
  then lift removes), or the grip at the right end (drag to resize);
- anywhere else: the contact goes, unchanged, to a uinput clone of the
  touchpad (same multitouch axes, resolution, BUTTONPAD property, bus and
  ids), so libinput treats it as the real touchpad and the desktop's touchpad
  settings apply.

It grabs only while the ScreenPad is on ('screenpad on' creates
/run/screenpad/active, 'screenpad off' removes it). When it stops, or dies,
the kernel drops the grab and the uinput devices, and the plain touchpad works
again. It publishes the bar as a scene of tiles in
$XDG_RUNTIME_DIR/screenpad/touchbar.json; the GNOME Shell extension draws
whatever tiles it finds there, on the ScreenPad, and takes apps dropped on it.

Other programs use the bar through a socket next to that file
(touchbar.sock): an app draws and handles the button spots the config gives
it ('tile' in a [[button]] block), and any program can ask the user a
question with up to three answers ('screenpad-touchbar ask' does it from a
shell). The extension forwards GNOME's notifications there too, and those
with buttons (or urgent ones) become questions whose answers press the
notification's own buttons. A question is an attention card: the bar grows
to CARD_HEIGHT for it and shrinks back once it is answered, dismissed or
timed out. Contacts already down keep the kind they started with.

Runs as the desktop user (group input, plus access to /dev/uinput).
Commands: run, on, off, status, check, probe, shortcut, ask.
"""

import argparse
import ast
import errno
import fcntl
import hashlib
import json
import os
import re
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import time
import tomllib

# The panel as you see it with the laptop open: landscape, (0, 0) at the top
# left, the top edge nearest the screen hinge. The digitizer's own axes run
# the same way (y grows towards you), which is why the touchpad works.
PANEL_W = 1920
PANEL_H = 1080

SOURCE_VENDOR = 0x27C6
SOURCE_PRODUCT = 0x01F1
SOURCE_SUFFIX = " Touchpad"
PAD_NAME = "ScreenPad Touchpad"
KEYS_NAME = "ScreenPad Touch Bar Keys"
UUID = "screenpad-touchbar@screenpad"
UNIT = "screenpad-touchbar.service"
# Opening a shortcut: through the GNOME Shell extension, which raises the
# app if it is already open, else with gtk-launch.
DBUS_PATH = "/org/screenpad/Touchbar"
DBUS_IFACE = "org.screenpad.Touchbar"
LAUNCH = [
    "/bin/sh",
    "-c",
    'gdbus call --session --dest org.gnome.Shell --object-path %s --method %s.Launch "$1"'
    ' >/dev/null 2>&1 || exec gtk-launch "$1"' % (DBUS_PATH, DBUS_IFACE),
    "sh",
]
ACTIVE_FLAG = "/run/screenpad/active"
DEFAULT_CONFIG = "/usr/local/share/screenpad/touchbar.toml"
# The EDID 'screenpad on' hands the kernel for the ScreenPad's connector.
SAVED_EDID = "/var/lib/screenpad/edid.bin"
# What mutter reports for that EDID (the UX580 Toshiba bridge dump), used
# when the saved file cannot be read.
MONITOR_ID = ("TSB", "no-audio-HD", "0x88888800")
SYS_INPUT = "/sys/class/input"

DEFAULT_HEIGHT = 160
# How far, in panel pixels, a tap may drift outside its button and still fire.
TAP_SLOP = 60
# How long a shortcut is held before lifting removes it, in seconds.
LONG_PRESS = 0.8
# How long to wait for fingers to lift before grabbing or letting go.
LIFT_WAIT = 2.0
POLL = 1.0

# The scene in touchbar.json. Version 2 is a list of tiles the extension
# draws without knowing what they are; version 1 was the fixed spots.
SCENE_VERSION = 2
# While it is enabled, the extension writes renderer.json with this protocol
# and whether the bar is on screen ("drawn"). Questions (asks) show only
# while both say so: an extension loaded before this version draws the
# version 1 spots and could not show them, a bar not drawn (no ScreenPad
# monitor found) shows nothing, and a touch must never answer a question
# nobody can see.
RENDERER_PROTOCOL = 2
RENDERER_NAME = "renderer.json"
SOCKET_NAME = "touchbar.sock"
MAX_APPS = 16
# Bytes: a longer line, or a client that does not read its replies, is cut off.
MAX_LINE = 16384
MAX_OUT = 262144
# A question ignores touches that start this soon after it appears, so a tap
# already on its way lands on nothing rather than on an answer.
ASK_ARM = 0.3
# The bar's height while a question shows (about 29 mm, which leaves about
# 39 mm of touchpad). It grows only when no touchpad contact lies in the
# band it is about to cover, or after waiting GROW_WAIT for one to leave.
CARD_HEIGHT = 456
GROW_WAIT = 0.3
ASK_TIMEOUT = 60
# GNOME notifications: how long a card shows one, and how many one sender
# (the extension) may have waiting at once.
NOTIFY_TIMEOUT = 20
MAX_NOTIFY_WAITING = 8
URGENCIES = ("low", "normal", "high", "critical")
MAX_ASK_TIMEOUT = 3600
MAX_OPTIONS = 3
TONES = ("neutral", "accept", "reject", "warning")
TILE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
OPTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0x00, 0x01, 0x03, 0x04
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39
ABS_CNT = 0x40
MSC_TIMESTAMP = 0x05
BTN_LEFT = 0x110
BTN_TOOL_FINGER = 0x145
BTN_TOOL_QUINTTAP = 0x148
BTN_TOUCH = 0x14A
BTN_TOOL_DOUBLETAP = 0x14D
BTN_TOOL_TRIPLETAP = 0x14E
BTN_TOOL_QUADTAP = 0x14F
KEY_CNT = 0x300
MT_TOOL_PALM = 2
BUS_VIRTUAL = 0x06
# BTN_TOOL_* for 1..5 fingers, as input_mt_report_finger_count() sends them.
FINGER_TOOLS = (BTN_TOOL_FINGER, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP, BTN_TOOL_QUINTTAP)
# The key device can send every code below the BTN_* range.
MAX_KEY = 0xFF

INPUT_EVENT = struct.Struct("llHHi")
ABSINFO = struct.Struct("6i")
INPUT_ID = struct.Struct("4H")
UINPUT_SETUP = struct.Struct("4H80sI")
UINPUT_ABS_SETUP = struct.Struct("H2x6i")


def _ioc(direction, nr, size, kind="E"):
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def EVIOCGBIT(ev, size):
    return _ioc(2, 0x20 + ev, size)


def EVIOCGABS(code):
    return _ioc(2, 0x40 + code, ABSINFO.size)


EVIOCGID = _ioc(2, 0x02, INPUT_ID.size)
EVIOCGPROP = _ioc(2, 0x09, 4)
EVIOCGKEY = _ioc(2, 0x18, KEY_CNT // 8)
EVIOCGRAB = _ioc(1, 0x90, 4)
UI_DEV_CREATE = _ioc(0, 1, 0, "U")
UI_DEV_DESTROY = _ioc(0, 2, 0, "U")
UI_DEV_SETUP = _ioc(1, 3, UINPUT_SETUP.size, "U")
UI_ABS_SETUP = _ioc(1, 4, UINPUT_ABS_SETUP.size, "U")
UI_SET_EVBIT = _ioc(1, 100, 4, "U")
UI_SET_KEYBIT = _ioc(1, 101, 4, "U")
UI_SET_ABSBIT = _ioc(1, 103, 4, "U")
UI_SET_MSCBIT = _ioc(1, 104, 4, "U")
UI_SET_PROPBIT = _ioc(1, 110, 4, "U")


def EVIOCGMTSLOTS(nslots):
    return _ioc(2, 0x0A, 4 * (1 + nslots))


class TouchbarError(Exception):
    pass


class ConfigError(TouchbarError):
    pass


def log(msg):
    print("screenpad-touchbar: %s" % msg, file=sys.stderr, flush=True)


# --- keys --------------------------------------------------------------------


def _key_names():
    names = {}
    for row, first in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
        for i, c in enumerate(row):
            names[c] = first + i
    for i in range(1, 10):
        names[str(i)] = 1 + i
    names["0"] = 11
    for i in range(1, 11):
        names["f%d" % i] = 58 + i
    names["f11"], names["f12"] = 87, 88
    for i in range(13, 25):
        names["f%d" % i] = 170 + i
    names.update(
        {
            "esc": 1,
            "minus": 12,
            "equal": 13,
            "backspace": 14,
            "tab": 15,
            "leftbrace": 26,
            "rightbrace": 27,
            "enter": 28,
            "leftctrl": 29,
            "semicolon": 39,
            "apostrophe": 40,
            "grave": 41,
            "leftshift": 42,
            "backslash": 43,
            "comma": 51,
            "dot": 52,
            "slash": 53,
            "rightshift": 54,
            "leftalt": 56,
            "space": 57,
            "capslock": 58,
            "numlock": 69,
            "scrolllock": 70,
            "rightctrl": 97,
            "sysrq": 99,
            "rightalt": 100,
            "home": 102,
            "up": 103,
            "pageup": 104,
            "left": 105,
            "right": 106,
            "end": 107,
            "down": 108,
            "pagedown": 109,
            "insert": 110,
            "delete": 111,
            "mute": 113,
            "volumedown": 114,
            "volumeup": 115,
            "pause": 119,
            "leftmeta": 125,
            "rightmeta": 126,
            "compose": 127,
            "menu": 139,
            "calc": 140,
            "sleep": 142,
            "nextsong": 163,
            "playpause": 164,
            "previoussong": 165,
            "stopcd": 166,
            "record": 167,
            "print": 210,
            "camera": 212,
            "search": 217,
            "brightnessdown": 224,
            "brightnessup": 225,
            "micmute": 248,
        }
    )
    names.update(
        {
            "ctrl": 29,
            "control": 29,
            "shift": 42,
            "alt": 56,
            "altgr": 100,
            "super": 125,
            "meta": 125,
            "win": 125,
            "escape": 1,
            "return": 28,
            "del": 111,
            "pgup": 104,
            "pgdn": 109,
            "printscreen": 99,
            "playpause": 164,
            "next": 163,
            "previous": 165,
            "prev": 165,
        }
    )
    return names


KEY_NAMES = _key_names()


def parse_key(spec):
    """'ctrl+alt+t' -> (29, 56, 20). Names are evdev key names, KEY_ optional."""
    if not isinstance(spec, str) or not spec.strip():
        raise ConfigError('key must be a non-empty string like "alt+k" or "ctrl+alt+t"')
    codes = []
    for token in spec.split("+"):
        name = token.strip().lower()
        if name.startswith("key_"):
            name = name[4:]
        if name.isdigit():
            code = int(name)
            if not 0 < code <= MAX_KEY:
                raise ConfigError("key code %s is outside 1..%d" % (name, MAX_KEY))
        elif name in KEY_NAMES:
            code = KEY_NAMES[name]
        else:
            raise ConfigError("unknown key %r in %r" % (token.strip(), spec))
        if code in codes:
            raise ConfigError("key %r appears twice in %r" % (token.strip(), spec))
        codes.append(code)
    return tuple(codes)


class KeyEmitter:
    """Presses and releases key combos on the uinput key device.

    Counts presses per code, so two held buttons sharing a modifier do not
    release it under each other.
    """

    def __init__(self, write):
        self.write = write
        self.down = {}

    def _frame(self, changes):
        if changes:
            self.write([(EV_KEY, c, v) for c, v in changes] + [(EV_SYN, SYN_REPORT, 0)])

    def press(self, codes):
        changes = []
        for c in codes:
            self.down[c] = self.down.get(c, 0) + 1
            if self.down[c] == 1:
                changes.append((c, 1))
        self._frame(changes)

    def release(self, codes):
        changes = []
        for c in reversed(codes):
            n = self.down.get(c, 0)
            if n == 0:
                continue
            if n == 1:
                del self.down[c]
                changes.append((c, 0))
            else:
                self.down[c] = n - 1
        self._frame(changes)

    def tap(self, codes):
        self.press(codes)
        self.release(codes)

    def release_all(self):
        self._frame([(c, 0) for c in sorted(self.down)])
        self.down = {}


# --- config ------------------------------------------------------------------

# The bar, left to right: three button spots, a gap, three shortcut spots,
# and the resize grip at the right end, all as tall as the bar.
MAX_BUTTONS = 3
MAX_SHORTCUTS = 3
SPOT_W = 296
GROUP_GAP = 40
SHORTCUTS_X = MAX_BUTTONS * SPOT_W + GROUP_GAP
GRIP_X = SHORTCUTS_X + MAX_SHORTCUTS * SPOT_W
MIN_HEIGHT = 64
MAX_HEIGHT = PANEL_H // 2
# The grip changes the height in steps of this many pixels (half a mm).
HEIGHT_STEP = 8
BUTTON, SHORTCUT, GRIP = "button", "shortcut", "grip"


class Button:
    def __init__(self, label, mode, keys=None, command=None, release_command=None, key=None, icon=None, tile=None):
        self.label = label
        self.icon = icon
        self.key = key
        self.mode = mode
        self.keys = keys
        self.command = command
        self.release_command = release_command
        # The name of a tile an app draws and handles in this spot.
        self.tile = tile

    def action(self):
        return (self.mode, self.keys, self.command, self.release_command, self.tile)

    def public(self):
        return {"label": self.label, "icon": self.icon, "mode": self.mode}


class Spot:
    __slots__ = ("index", "item", "kind", "width", "x")

    def __init__(self, kind, index, x, width, item=None):
        self.kind, self.index, self.x, self.width, self.item = kind, index, x, width, item

    def public(self):
        out = {"kind": self.kind, "x": self.x, "width": self.width}
        if self.kind == BUTTON:
            out.update(self.item.public() if self.item else {"label": None, "icon": None, "mode": None})
        elif self.kind == SHORTCUT:
            out["app"] = self.item
        return out


class NotifyPolicy:
    """Which GNOME notifications the bar mirrors ([notifications])."""

    def __init__(self, mirror=True, allow=None, deny=(), timeout=NOTIFY_TIMEOUT):
        self.mirror = mirror
        self.allow = None if allow is None else list(allow)
        self.deny = list(deny)
        self.timeout = timeout

    def describe(self):
        if not self.mirror:
            return "not mirrored"
        out = "mirrored for %d s" % self.timeout
        if self.allow is not None:
            out += ", only %s" % (", ".join(self.allow) or "no app")
        if self.deny:
            out += ", never %s" % ", ".join(self.deny)
        return out


class Config:
    def __init__(self, height, buttons, shortcuts=(), notifications=None):
        self.height = height
        self.buttons = list(buttons)
        self.shortcuts = list(shortcuts)
        self.notifications = notifications or NotifyPolicy()
        self.spots = (
            [
                Spot(BUTTON, i, i * SPOT_W, SPOT_W, self.buttons[i] if i < len(self.buttons) else None)
                for i in range(MAX_BUTTONS)
            ]
            + [
                Spot(
                    SHORTCUT,
                    i,
                    SHORTCUTS_X + i * SPOT_W,
                    SPOT_W,
                    self.shortcuts[i] if i < len(self.shortcuts) else None,
                )
                for i in range(MAX_SHORTCUTS)
            ]
            + [Spot(GRIP, 0, GRIP_X, PANEL_W - GRIP_X)]
        )

    def spot_at(self, px, py, height=None):
        """The index in spots of what is under a bar touch, or None.

        height is the bar's height for touches now (taller while a card
        shows), else the config's."""
        if not 0 <= py < (height or self.height):
            return None
        for i, s in enumerate(self.spots):
            if s.x <= px < s.x + s.width:
                return i
        return None

    def near_spot(self, i, px, py):
        s = self.spots[i]
        return near(s.x, s.width, self.height, px, py)

    def tiles(self):
        """The tile names the config places, in spot order."""
        return [b.tile for b in self.buttons if b.tile]


def near(x, width, height, px, py):
    """Whether a touch at px, py is on, or within TAP_SLOP of, a bar rectangle."""
    return x - TAP_SLOP <= px < x + width + TAP_SLOP and -TAP_SLOP <= py < height + TAP_SLOP


# The push-to-talk button's block in the default config.
PTT_BLOCK = """\
# Push to talk. Alt+K, held while you hold the button: Claude Code's voice
# push-to-talk when ~/.claude/keybindings.json binds "meta+k" (Claude
# Code's name for Alt+K) to "voice:pushToTalk" in the "Chat" context.
# Terminals send Alt+K, unlike F13 and up. For another voice app, set key
# to the push-to-talk key it uses.
[[button]]
icon = "audio-input-microphone-symbolic"
label = "Push to talk"
mode = "hold"
key = "alt+k"
"""

# What versions before it wrote. Terminals send nothing for F14 (GNOME maps
# it to XF86Launch5), so it could not be Claude Code's push-to-talk key.
OLD_PTT_BLOCK = """\
# Push to talk. F14 is on no keyboard and GNOME binds nothing to it
# (it reaches apps as XF86Launch5); set your voice app's push-to-talk key to
# it by holding this button, or change key to what the app already uses.
[[button]]
icon = "audio-input-microphone-symbolic"
label = "Push to talk"
mode = "hold"
key = "F14"
"""

# How the default config documents 'tile', below 'command'.
COMMAND_DOC = """\
#   command          instead of key: a command to run, as a string for sh
#                    or a list of arguments. With mode "hold" it runs on
#                    touch, and release_command (optional) runs on release.
"""
TILE_DOC = """\
#   tile             instead of key or command: the name of a tile that an
#                    app draws and handles in this spot (an app that talks
#                    to the bar's socket; see app-api.md in the docs). The
#                    icon or label shows, dimmed, while no app provides it.
"""

EXAMPLE_CONFIG = """\
# ScreenPad touch bar. Edit and save: the daemon picks changes up within a
# second. 'screenpad-touchbar check' validates this file. The bar writes
# here too, when you resize it or add or remove a shortcut; it changes only
# the height line and the [[shortcut]] blocks and keeps the rest as it is.
#
# The bar, left to right: three button spots, three shortcut spots, and a
# grip at the right end. Units are ScreenPad pixels as you see the panel
# with the laptop open: 1920 wide, 1080 tall, the top being the edge
# nearest the screen hinge. 16 pixels are about 1 mm.

# Height of the bar at the top of the panel, %(min)d to %(max)d. Touches that
# start in it never move the pointer or click; everything below stays a
# touchpad. Drag the grip at the bar's right end down or up to change it.
height = %(height)d

# Up to three [[button]] blocks, for the three button spots in order.
#   icon             a symbolic icon name from the icon theme, such as
#                    "audio-input-microphone-symbolic" (the names are the
#                    files in /usr/share/icons/Adwaita/symbolic), or the
#                    path of an image file (SVG or PNG)
#   label            the button's name; drawn instead when there is no icon
#   mode             "hold": keys down on touch, up on release
#                    "tap":  one key press when you lift the finger on it
#   key              a key or combo: "alt+k", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional;
#                    "meta" and "super" are the Super key, not Alt)
#   command          instead of key: a command to run, as a string for sh
#                    or a list of arguments. With mode "hold" it runs on
#                    touch, and release_command (optional) runs on release.
%(tile)s
%(ptt)s
# Mutes and unmutes the microphone, like a keyboard's mic mute key.
[[button]]
icon = "microphone-sensitivity-muted-symbolic"
label = "Mute microphone"
mode = "tap"
key = "micmute"

# GNOME's screenshot tool, like the Print Screen key.
[[button]]
icon = "applets-screenshooter-symbolic"
label = "Screenshot"
mode = "tap"
key = "printscreen"

# Up to three [[shortcut]] blocks, for the shortcut spots in order: apps by
# their desktop file id. Drag an app from the dock or the app grid onto the
# bar to add it; tap it to open the app; hold it for a second, until it
# turns red, and lift to remove it. 'screenpad-touchbar shortcut' does the
# same from a terminal. For example:
# [[shortcut]]
# app = "org.gnome.Nautilus.desktop"

# GNOME notifications that have buttons (Join, Reply, ...), and urgent ones,
# show in the bar: it grows into a card with the notification's text and
# buttons, and shrinks back when you tap one, dismiss it or it times out.
# Tapping a button presses the notification's own button. Dismissing only
# closes the card; the notification stays in GNOME's list. Nothing shows
# while Do Not Disturb is on, except what GNOME itself shows then (critical
# notifications). To change that, a [notifications] table:
# [notifications]
# mirror = false                             # no cards for notifications
# deny = ["org.gnome.Evolution.desktop"]     # never these apps
# allow = ["org.gnome.Calendar.desktop"]     # only these apps
# timeout = %(notify_timeout)d                               # seconds a card shows
""" % {
    "height": DEFAULT_HEIGHT,
    "min": MIN_HEIGHT,
    "max": MAX_HEIGHT,
    "ptt": PTT_BLOCK,
    "tile": TILE_DOC,
    "notify_timeout": NOTIFY_TIMEOUT,
}

# sha256 of the defaults earlier versions wrote. A file still exactly one of
# them is replaced by the current default (see migrate_config()).
OLD_DEFAULTS = {
    "dfabfa5d73979ec2302e2a4d343426085da0c6fcd9093f31e3cee4067bc30c36",
    # Before tiles (tests/touchbar-v3-default.toml).
    "a764addfad0135d0272d89ed390b4013ed575819ca224fd3fa704508dde2a755",
    # Before notifications (tests/touchbar-v4-default.toml).
    "d6137e89e4ace61da259ef3accf7645b090baee1863076db4145bc08d54d8456",
}

# Default blocks earlier versions wrote, each with its current text. A config
# that still holds one word for word gets the new one in its place and keeps
# the rest (see migrate_config()).
OLD_BLOCKS = (
    (OLD_PTT_BLOCK, PTT_BLOCK),
    (
        """\
#   key              a key or combo: "F14", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional)
""",
        """\
#   key              a key or combo: "alt+k", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional;
#                    "meta" and "super" are the Super key, not Alt)
""",
    ),
    # The blank line after it marks the block without 'tile' yet, so this
    # applies once.
    (COMMAND_DOC + "\n", COMMAND_DOC + TILE_DOC + "\n"),
)

# x and width placed buttons in the first version; the spots do it now.
BUTTON_KEYS = {"label", "icon", "mode", "key", "command", "release_command", "tile", "x", "width"}
SHORTCUT_KEYS = {"app"}
APP_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.desktop")


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be a whole number of pixels" % what)
    return value


def _command(value, what):
    if isinstance(value, str) and value.strip():
        return ["/bin/sh", "-c", value]
    if isinstance(value, list) and value and all(isinstance(a, str) for a in value) and value[0]:
        return list(value)
    raise ConfigError("%s must be a string or a non-empty list of strings" % what)


def _icon(value):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("icon must be an icon name or the path of an image")
    value = value.strip()
    if value.startswith("~"):
        value = os.path.expanduser(value)
    if "/" in value and not value.startswith("/"):
        raise ConfigError("icon %r: an image path must start with / or ~" % value)
    return value


def app_id(value):
    """A desktop file id, "org.gnome.Nautilus" or "org.gnome.Nautilus.desktop"."""
    if not isinstance(value, str):
        raise ConfigError('app must be a desktop file id like "org.gnome.Nautilus.desktop"')
    value = value.strip()
    if not value.endswith(".desktop"):
        value += ".desktop"
    if not APP_ID.fullmatch(value):
        raise ConfigError('app %r is not a desktop file id like "org.gnome.Nautilus.desktop"' % value)
    return value


def _blocks(data, name, allowed, limit):
    raw = data.get(name, [])
    if not isinstance(raw, list):
        raise ConfigError("%ss go in [[%s]] blocks" % (name, name))
    if len(raw) > limit:
        raise ConfigError("at most %d [[%s]] blocks (got %d)" % (limit, name, len(raw)))
    for n, b in enumerate(raw, 1):
        where = "%s %d" % (name, n)
        if not isinstance(b, dict):
            raise ConfigError("%s: must be a [[%s]] block" % (where, name))
        if isinstance(b.get("label"), str) and b["label"]:
            where += " (%s)" % b["label"]
        unknown = set(b) - allowed
        if unknown:
            raise ConfigError("%s: unknown setting(s): %s" % (where, ", ".join(sorted(unknown))))
        yield where, b


def _button(b):
    label = b.get("label", "")
    if not isinstance(label, str):
        raise ConfigError("label must be a string")
    icon = _icon(b["icon"]) if "icon" in b else None
    if not icon and not label:
        raise ConfigError("needs an icon or a label")
    mode = b.get("mode", "tap")
    if mode not in ("hold", "tap"):
        raise ConfigError('mode must be "hold" or "tap"')
    if sum(k in b for k in ("key", "command", "tile")) != 1:
        raise ConfigError("needs exactly one of key, command or tile")
    keys = parse_key(b["key"]) if "key" in b else None
    command = _command(b["command"], "command") if "command" in b else None
    tile = None
    if "tile" in b:
        tile = b["tile"]
        if not isinstance(tile, str) or not TILE_NAME.fullmatch(tile):
            raise ConfigError(
                'tile must be a name like "agents" (lower case letters, digits, dot, dash or underscore, at most 32)'
            )
        if "mode" in b:
            raise ConfigError("mode does not apply to a tile: the app that draws it decides")
    release = None
    if "release_command" in b:
        if mode != "hold" or command is None:
            raise ConfigError('release_command only goes with a command in mode "hold"')
        release = _command(b["release_command"], "release_command")
    return Button(label, mode, keys, command, release, b.get("key"), icon, tile)


def parse_config(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(str(e))
    unknown = set(data) - {"height", "button", "shortcut", "notifications"}
    if unknown:
        raise ConfigError("unknown setting(s): %s" % ", ".join(sorted(unknown)))
    height = _int(data.get("height", DEFAULT_HEIGHT), "height")
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ConfigError("height must be between %d and %d" % (MIN_HEIGHT, MAX_HEIGHT))
    buttons: list[Button] = []
    for where, b in _blocks(data, "button", BUTTON_KEYS, MAX_BUTTONS):
        try:
            button = _button(b)
            if button.tile and button.tile in [o.tile for o in buttons]:
                raise ConfigError("tile %s is already in another spot" % button.tile)
            buttons.append(button)
        except ConfigError as e:
            raise ConfigError("%s: %s" % (where, e))
    shortcuts = []
    for where, s in _blocks(data, "shortcut", SHORTCUT_KEYS, MAX_SHORTCUTS):
        try:
            app = app_id(s.get("app"))
        except ConfigError as e:
            raise ConfigError("%s: %s" % (where, e))
        if app in shortcuts:
            raise ConfigError("%s: %s is already a shortcut" % (where, app))
        shortcuts.append(app)
    try:
        notifications = _notifications(data.get("notifications", {}))
    except ConfigError as e:
        raise ConfigError("notifications: %s" % e)
    return Config(height, buttons, shortcuts, notifications)


NOTIFY_KEYS = {"mirror", "allow", "deny", "timeout"}


def _apps(value, what):
    if not isinstance(value, list):
        raise ConfigError("%s must be a list of desktop file ids" % what)
    return list(dict.fromkeys(app_id(a) for a in value))


def _notifications(table):
    if not isinstance(table, dict):
        raise ConfigError("must be a [notifications] table")
    unknown = set(table) - NOTIFY_KEYS
    if unknown:
        raise ConfigError("unknown setting(s): %s" % ", ".join(sorted(unknown)))
    mirror = table.get("mirror", True)
    if not isinstance(mirror, bool):
        raise ConfigError("mirror must be true or false")
    allow = _apps(table["allow"], "allow") if "allow" in table else None
    deny = _apps(table.get("deny", []), "deny")
    timeout = table.get("timeout", NOTIFY_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_ASK_TIMEOUT:
        raise ConfigError("timeout must be a whole number of seconds, 1 to %d" % MAX_ASK_TIMEOUT)
    return NotifyPolicy(mirror, allow, deny, timeout)


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "screenpad", "touchbar.toml")


def load_config(path=None):
    """The user's config, else the installed default, else the built-in one."""
    for p in [path] if path else [config_path(), DEFAULT_CONFIG]:
        try:
            with open(p) as f:
                text = f.read()
        except FileNotFoundError:
            if path:
                raise
            continue
        try:
            return parse_config(text), p
        except ConfigError as e:
            raise ConfigError("%s: %s" % (p, e))
    return parse_config(EXAMPLE_CONFIG), None


# --- writing the config ----------------------------------------------------------
#
# The bar changes two things in the user's file: the height line and the
# [[shortcut]] blocks. It edits those lines and leaves every other line,
# comments included, as it was, then checks that the result parses to the
# old config with only that change before it replaces the file.

TABLE = re.compile(r"\s*\[\[?\s*([A-Za-z0-9_.\"' -]+?)\s*\]\]?\s*(#.*)?")
HEIGHT_LINE = re.compile(r"\s*height\s*=.*")


def with_height(text, height):
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if TABLE.fullmatch(line.rstrip("\n")):
            break
        if HEIGHT_LINE.fullmatch(line.rstrip("\n")):
            lines[i] = "height = %d\n" % height
            return "".join(lines)
    else:
        i = len(lines)
    lines.insert(i, "height = %d\n\n" % height)
    return "".join(lines)


def with_shortcuts(text, apps):
    """text with its [[shortcut]] blocks replaced by these, at the end."""
    lines = text.splitlines(keepends=True)
    out, block = [], None
    for line in lines + [None]:
        header = line is not None and TABLE.fullmatch(line.rstrip("\n"))
        if block is not None and (line is None or header):
            # A block's trailing comments and blank lines introduce what
            # comes next, so they stay.
            k = len(block)
            while k > 1 and (not block[k - 1].strip() or block[k - 1].lstrip().startswith("#")):
                k -= 1
            out += block[k:]
            block = None
        if line is None:
            break
        if header and header.group(1) == "shortcut":
            block = [line]
        elif block is not None:
            block.append(line)
        else:
            out.append(line)
    text = "".join(out).rstrip()
    if text:
        text += "\n"
    for app in apps:
        text += '\n[[shortcut]]\napp = "%s"\n' % app
    return text


def _lock():
    path = os.path.join(os.path.dirname(state_path()), "config.lock")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def edit_config(change, path=None):
    """Apply change(config) -> (height, shortcuts) to the user's file.

    Writes the default first if there is no file. Returns the new Config.
    """
    path = path or config_path()
    fd = _lock()
    try:
        try:
            with open(path) as f:
                text = f.read()
        except FileNotFoundError:
            text = EXAMPLE_CONFIG
        try:
            old = parse_config(text)
        except ConfigError as e:
            raise ConfigError("%s: fix this first: %s" % (path, e))
        height, shortcuts = change(old)
        new_text = text
        if height != old.height:
            new_text = with_height(new_text, height)
        if shortcuts != old.shortcuts:
            new_text = with_shortcuts(new_text, shortcuts)
        new = parse_config(new_text)
        if (
            (new.height, new.shortcuts) != (height, shortcuts)
            or [b.public() for b in new.buttons] != [b.public() for b in old.buttons]
            or [b.action() for b in new.buttons] != [b.action() for b in old.buttons]
        ):
            raise ConfigError("%s: could not edit it safely; change it by hand" % path)
        if new_text != text or not os.path.exists(path):
            _write(path, new_text)
        return new
    finally:
        os.close(fd)


def add_shortcut(shortcuts, app, spot=None):
    """shortcuts with app added: at spot (0-based), else at the end.

    On a full bar, an app dropped on a spot replaces the one there. An app
    already on the bar moves.
    """
    apps = [a for a in shortcuts if a != app]
    if spot is None:
        if len(apps) >= MAX_SHORTCUTS:
            raise ConfigError("the bar has no free shortcut spot; remove one first")
        return apps + [app]
    if not 0 <= spot < MAX_SHORTCUTS:
        raise ConfigError("spot must be 1 to %d" % MAX_SHORTCUTS)
    if len(apps) >= MAX_SHORTCUTS:
        apps[spot] = app
        return apps
    apps.insert(min(spot, len(apps)), app)
    return apps


def migrate_config(path=None):
    """Replace a config that is still an earlier version's default."""
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    if hashlib.sha256(data).hexdigest() in OLD_DEFAULTS:
        _write(path, EXAMPLE_CONFIG)
        log("%s was an earlier default; replaced it with the current one" % path)
        return True
    text = data.decode("utf-8", "replace")
    changed = False
    for old, new in OLD_BLOCKS:
        if text.count(old) == 1:
            text = text.replace(old, new)
            changed = True
    if not changed:
        return False
    # Updated block by block, an older default can become a later one.
    if hashlib.sha256(text.encode()).hexdigest() in OLD_DEFAULTS:
        text = EXAMPLE_CONFIG
    _write(path, text)
    log("%s had an earlier default button; updated it to the current one" % path)
    return True


def edid_identity(edid):
    """(vendor, product, serial) as mutter reports them for this EDID, or None.

    mutter takes the PNP vendor id, the display name descriptor (else the
    product code as 0x%04x) and the serial string descriptor (else the serial
    number as 0x%08x). The GNOME Shell extension draws the bar only on the
    monitor with exactly these three, never on another display.
    """
    if len(edid) < 128 or edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        return None
    v = edid[8] << 8 | edid[9]
    vendor = "".join(chr(ord("A") - 1 + (v >> s & 0x1F)) for s in (10, 5, 0))
    product = "0x%04x" % (edid[10] | edid[11] << 8)
    serial = "0x%08x" % int.from_bytes(edid[12:16], "little")
    for o in range(54, 126, 18):
        d = edid[o : o + 18]
        if d[0:3] != b"\0\0\0" or d[3] not in (0xFC, 0xFF):
            continue
        text = d[5:18].split(b"\n")[0].decode("cp437").strip()
        if not text:
            continue
        if d[3] == 0xFC:
            product = text
        else:
            serial = text
    return vendor, product, serial


def monitor_identity(path=SAVED_EDID):
    try:
        with open(path, "rb") as f:
            ident = edid_identity(f.read(256))
    except OSError:
        ident = None
    return list(ident or MONITOR_ID)


def state_path():
    base = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    return os.path.join(base, "screenpad", "touchbar.json")


def runtime_file(name):
    """A file next to touchbar.json: the app socket or the renderer handshake."""
    return os.path.join(os.path.dirname(state_path()), name)


# --- the scene ---------------------------------------------------------------
#
# What touchbar.json shows the extension: tiles, each a rectangle in the bar
# (x and width in panel pixels, as tall as the bar) and what to draw in it.
# The extension draws tiles without knowing what they are, so a new kind of
# tile needs no extension update (GNOME loads extension code only at login).
#
# Every tile has "id", "x", "width" and "style" (its CSS class is
# screenpad-touchbar-<style>). It may have "classes" (more CSS classes),
# "pressed", "glyph" ("grip": drawn by the extension's own code), "icon" (an
# icon name or an image path) and "icon_scale", "app" (a desktop file id,
# drawn as that app's icon; the extension opens only apps shown this way),
# "label", "title" and "text" (text lines), "badge" (a short text in a
# corner), "drop" (the shortcut spot number an app icon dropped there takes)
# and "empty". "divider" is the x of the line between the two groups.
# "height" is the bar's height drawn (CARD_HEIGHT while a card shows) and
# "strut" its own height, which windows keep clear of; the extension
# animates a growth of height over an unchanged strut.

TILE_STYLES = ("button", "shortcut", "grip", "ask-text", "option")
# Every CSS class a tile can carry besides its style. The stylesheet has a
# rule for each but "tap", which is the plain button (tests/test_touchbar.py
# checks it).
TILE_CLASSES = ("hold", "tap", "empty", "removing", "tile", "offline") + tuple("tone-" + tone for tone in TONES)
DIVIDER_X = SHORTCUTS_X - GROUP_GAP // 2
# A question (an attention card): its title and text over the first two
# button spots, Dismiss in the third, its answers in the three shortcut
# spots. The grip stays.
ASK_TEXT_W = DISMISS_X = 2 * SPOT_W
DISMISS = "dismiss"


def _looks(button):
    """A config button's icon and label, those it has."""
    return {k: v for k, v in (("icon", button.icon), ("label", button.label)) if v}


def spot_tiles(config, pressed=(), removing=(), provided=None):
    """The tiles of the config's spots: buttons, shortcuts and the grip.

    provided maps the names of the tiles apps provide now to what they draw.
    """
    provided = provided or {}
    tiles = []
    for i, s in enumerate(config.spots):
        tile = {"id": "%s-%d" % (s.kind, s.index), "x": s.x, "width": s.width, "style": s.kind}
        if i in pressed:
            tile["pressed"] = True
        if s.kind == GRIP:
            tile["id"], tile["glyph"] = GRIP, GRIP
        elif s.kind == SHORTCUT:
            tile["drop"] = s.index + 1
            if s.item is None:
                tile.update(classes=["empty"], icon="list-add-symbolic", icon_scale=0.6, empty=True)
            else:
                tile["app"] = s.item
                if i in removing:
                    tile.update(classes=["removing"], icon="edit-delete-symbolic")
        elif s.item is None:
            tile["classes"] = ["empty"]
        elif s.item.tile:
            look = provided.get(s.item.tile)
            if look is None:
                tile["classes"] = ["tile", "offline"]
                look = {"icon": s.item.icon, "label": s.item.label}
            else:
                tile["classes"] = ["tile", "tone-" + look.get("tone", "neutral")]
            for k in ("icon", "label", "text", "badge"):
                if look.get(k):
                    tile[k] = look[k]
            if "icon" not in tile and "label" not in tile:
                tile.update(_looks(s.item))
        else:
            tile["classes"] = [s.item.mode]
            tile.update(_looks(s.item))
        tiles.append(tile)
    return tiles


def ask_option_x(k):
    return SHORTCUTS_X + k * SPOT_W


def option_x(option):
    """Where an answer's tile (an index, or DISMISS) starts."""
    return DISMISS_X if option == DISMISS else ask_option_x(option)


def ask_option_at(ask, px):
    """The answer under a touch at px (an index, or DISMISS), or None."""
    if DISMISS_X <= px < DISMISS_X + SPOT_W:
        return DISMISS
    for k in range(len(ask.options)):
        if ask_option_x(k) <= px < ask_option_x(k) + SPOT_W:
            return k
    return None


def ask_tiles(ask, pressed=(), queued=0):
    """The tiles of a question: its text, Dismiss, then one tile per answer."""
    text = {"id": "ask-text", "x": 0, "width": ASK_TEXT_W, "style": "ask-text", "title": ask.title}
    if ask.text:
        text["text"] = ask.text
    if queued:
        text["badge"] = "+%d" % queued
    dismiss = {
        "id": "ask-dismiss",
        "x": DISMISS_X,
        "width": SPOT_W,
        "style": "option",
        "classes": ["tone-neutral"],
        "icon": "window-close-symbolic",
        "icon_scale": 0.6,
        "label": "Dismiss",
    }
    if DISMISS in pressed:
        dismiss["pressed"] = True
    tiles = [text, dismiss]
    for k, o in enumerate(ask.options):
        tile = {
            "id": "ask-option-%d" % k,
            "x": ask_option_x(k),
            "width": SPOT_W,
            "style": "option",
            "classes": ["tone-" + o["tone"]],
            "label": o["label"],
        }
        if k in pressed:
            tile["pressed"] = True
        tiles.append(tile)
    return tiles


# --- the split ---------------------------------------------------------------


class Axes:
    """Maps digitizer units to panel pixels."""

    def __init__(self, xmin, xmax, ymin, ymax):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    def panel(self, x, y):
        return (
            (x - self.xmin) * PANEL_W / (self.xmax - self.xmin),
            (y - self.ymin) * PANEL_H / (self.ymax - self.ymin),
        )


PAD, BAR, IGNORED = "pad", "bar", "ignored"
# What feed() returns for the SYN_REPORT that ends a SYN_DROPPED gap.
RESYNC = "resync"


class Slot:
    __slots__ = ("id", "tool", "x", "y")

    def __init__(self):
        self.id, self.x, self.y, self.tool = -1, 0, 0, 0


class Contact:
    __slots__ = ("id", "kind", "order", "tool", "x", "y")

    def __init__(self, id, kind, x, y, tool, order):
        self.id, self.kind, self.x, self.y, self.tool, self.order = id, kind, x, y, tool, order


class Splitter:
    """Splits the source touchpad's frames between the bar and the virtual pad.

    feed() takes the source's events one by one. At each SYN_REPORT it returns
    (pad, bar), or RESYNC after a SYN_DROPPED gap (then call resync()): the
    events for the virtual touchpad (whole frames, ending in SYN_REPORT, or
    nothing if the pad did not change) and the bar's contact events,
    ("begin"|"move"|"end"|"cancel", slot, px, py) in panel pixels.
    Contacts keep the kind they started with: a bar contact that slides down
    stays out of the pad, and a pad contact that slides up stays a pad contact,
    so the pointer never sees a touch appear or vanish mid-stroke.
    """

    def __init__(self, axes, nslots, in_bar, cur=0):
        self.axes = axes
        self.in_bar = in_bar  # in_bar(px, py) -> bool
        self.slots = [Slot() for _ in range(nslots)]
        # The kernel sends ABS_MT_SLOT only when it changes, so a reader
        # starts from the value EVIOCGABS gives at open.
        self.cur = cur
        self.contacts = {}  # slot -> Contact
        self.sent = {}  # slot -> (id, x, y, tool) on the virtual pad
        self.sent_tool = None
        self.sent_left = False
        self.src_left = False
        self.left_changed = False
        self.timestamp = None
        self.dropping = False
        self.order = 0

    def feed(self, type, code, value):
        if self.dropping:
            if type == EV_SYN and code == SYN_REPORT:
                self.dropping = False
                return RESYNC
            return None
        if type == EV_SYN:
            if code == SYN_REPORT:
                return self.frame()
            if code == SYN_DROPPED:
                self.dropping = True
            return None
        slot = self.slots[self.cur] if 0 <= self.cur < len(self.slots) else None
        if type == EV_ABS:
            if code == ABS_MT_SLOT:
                self.cur = value
            elif slot is None:
                pass
            elif code == ABS_MT_TRACKING_ID:
                slot.id = value
            elif code == ABS_MT_POSITION_X:
                slot.x = value
            elif code == ABS_MT_POSITION_Y:
                slot.y = value
            elif code == ABS_MT_TOOL_TYPE:
                slot.tool = value
        elif type == EV_KEY and code == BTN_LEFT:
            self.src_left = bool(value)
            self.left_changed = True
        elif type == EV_MSC and code == MSC_TIMESTAMP:
            self.timestamp = value
        return None

    def resync(self, ids, xs, ys, tools, left, cur):
        """Take the device state after a SYN_DROPPED, as EVIOCGMTSLOTS reads it."""
        for s, slot in enumerate(self.slots):
            slot.id, slot.x, slot.y, slot.tool = ids[s], xs[s], ys[s], tools[s]
        self.cur = cur
        if bool(left) != self.src_left:
            self.src_left = bool(left)
            self.left_changed = True
        return self.frame()

    def frame(self):
        bar = []
        for s, slot in enumerate(self.slots):
            c = self.contacts.get(s)
            if c and c.id != slot.id:
                del self.contacts[s]
                if c.kind == BAR:
                    bar.append(("end", s) + self.axes.panel(c.x, c.y))
                c = None
            if slot.id == -1:
                continue
            if c is None:
                px, py = self.axes.panel(slot.x, slot.y)
                if not self.in_bar(px, py):
                    kind = PAD
                elif slot.tool == MT_TOOL_PALM:
                    kind = IGNORED
                else:
                    kind = BAR
                    bar.append(("begin", s, px, py))
                self.order += 1
                self.contacts[s] = Contact(slot.id, kind, slot.x, slot.y, slot.tool, self.order)
                continue
            moved = (slot.x, slot.y) != (c.x, c.y)
            c.x, c.y = slot.x, slot.y
            if c.kind == BAR and slot.tool == MT_TOOL_PALM:
                c.kind = IGNORED
                bar.append(("cancel", s) + self.axes.panel(c.x, c.y))
            elif c.kind == BAR and moved:
                bar.append(("move", s) + self.axes.panel(c.x, c.y))
            c.tool = slot.tool
        return self.pad_frames(), bar

    def wanted(self):
        return {s: (c.id, c.x, c.y, c.tool) for s, c in self.contacts.items() if c.kind == PAD}

    def pad_in_band(self, top, bottom):
        """Whether a touchpad contact is now between rows top and bottom:
        the band a growing bar would cover. It would stay a touchpad
        contact, but the card would be drawn under the finger."""
        return any(top <= self.axes.panel(c.x, c.y)[1] < bottom for c in self.contacts.values() if c.kind == PAD)

    def pad_frames(self, wanted=None):
        wanted = self.wanted() if wanted is None else wanted
        events = []
        # A slot that changes from one contact to another ends in a frame of
        # its own: libinput counts touches by TRACKING_ID -1.
        replaced = [s for s in self.sent if s in wanted and wanted[s][0] != self.sent[s][0]]
        if replaced:
            interim = {s: v for s, v in wanted.items() if s not in replaced and s in self.sent}
            events += self._diff(interim, False)
        events += self._diff(wanted, True)
        return events

    def _diff(self, wanted, final):
        ev = []
        for s in sorted(set(self.sent) | set(wanted)):
            old, new = self.sent.get(s), wanted.get(s)
            if old == new:
                continue
            ev.append((EV_ABS, ABS_MT_SLOT, s))
            if new is None:
                ev.append((EV_ABS, ABS_MT_TRACKING_ID, -1))
                del self.sent[s]
                continue
            if old is None or old[0] != new[0]:
                ev += [
                    (EV_ABS, ABS_MT_TRACKING_ID, new[0]),
                    (EV_ABS, ABS_MT_TOOL_TYPE, new[3]),
                    (EV_ABS, ABS_MT_POSITION_X, new[1]),
                    (EV_ABS, ABS_MT_POSITION_Y, new[2]),
                ]
            else:
                for i, code in ((1, ABS_MT_POSITION_X), (2, ABS_MT_POSITION_Y), (3, ABS_MT_TOOL_TYPE)):
                    if old[i] != new[i]:
                        ev.append((EV_ABS, code, new[i]))
            self.sent[s] = new
        n = len(self.sent)
        tool = FINGER_TOOLS[min(n, len(FINGER_TOOLS)) - 1] if n else None
        if tool != self.sent_tool:
            if (self.sent_tool is None) != (tool is None):
                ev.append((EV_KEY, BTN_TOUCH, 1 if tool else 0))
            if self.sent_tool is not None:
                ev.append((EV_KEY, self.sent_tool, 0))
            if tool is not None:
                ev.append((EV_KEY, tool, 1))
            self.sent_tool = tool
        if ev and self.sent:
            # Pointer emulation follows the oldest contact, as input_mt does.
            s = min(self.sent, key=lambda s: self.contacts[s].order if s in self.contacts else 0)
            ev += [(EV_ABS, ABS_X, self.sent[s][1]), (EV_ABS, ABS_Y, self.sent[s][2])]
        if final:
            ev += self._left()
        if not ev:
            return ev
        if final and self.timestamp is not None:
            ev.append((EV_MSC, MSC_TIMESTAMP, self.timestamp))
        ev.append((EV_SYN, SYN_REPORT, 0))
        return ev

    def _left(self):
        # A click counts on the desktop only while a pad contact is down, and
        # its release goes out only if its press did.
        if not self.left_changed:
            return []
        self.left_changed = False
        if self.src_left and not self.sent_left and self.sent:
            self.sent_left = True
            return [(EV_KEY, BTN_LEFT, 1)]
        if not self.src_left and self.sent_left:
            self.sent_left = False
            return [(EV_KEY, BTN_LEFT, 0)]
        return []

    def release_all(self):
        """End everything: the pad's frames, and cancels for the bar's contacts."""
        bar = [("cancel", s) + self.axes.panel(c.x, c.y) for s, c in sorted(self.contacts.items()) if c.kind == BAR]
        for c in self.contacts.values():
            c.kind = IGNORED
        self.src_left = False
        self.left_changed = True
        return self.pad_frames({}), bar


class Touch:
    __slots__ = ("armed", "removing", "spot", "start", "start_height", "start_y")

    def __init__(self, spot, start, start_y=0, start_height=0):
        self.spot, self.armed, self.start, self.removing = spot, True, start, False
        self.start_y, self.start_height = start_y, start_height


class AskTouch:
    """A contact on a question: on one of its answers, or inert (option None)."""

    __slots__ = ("armed", "ask", "option")

    def __init__(self, ask, option):
        self.ask, self.option, self.armed = ask, option, option is not None


class Bar:
    """Turns bar contacts into button presses, shortcut taps and resizes.

    - button: "hold" sends its keys down on touch and up on lift; "tap" sends
      them when the finger lifts on (or near) the button; a "tile" button
      belongs to an app, which gets "down" on touch and "up" on lift, with
      whether the finger lifted on (or near) it;
    - shortcut: a tap opens the app; held for LONG_PRESS it turns to
      "remove", and lifting then removes it; sliding away cancels either;
    - grip: dragging it up or down resizes the bar, saved on lift;
    - while a question shows (asks.shown()), the bar is CARD_HEIGHT tall
      (touch_height()) and the question covers the button and shortcut
      spots: lifting on an answer chooses it, lifting on Dismiss closes
      it with no answer. Touches that start within ASK_ARM of it showing,
      or on its text, do nothing. The grip stays.
    A touch keeps what it started on: a button held when a question appears
    stays held, and a touch on a question that goes away does nothing.
    launch(app), remove(index) and resize(height) do the saving and
    launching; remove and resize return whether they did. tiles has
    online(name) and event(name, kind, inside); asks has shown(), the shown
    question's shown_at, choose(ask, option) and dismiss(ask).
    """

    def __init__(
        self, config, keys, run, launch=None, remove=None, resize=None, clock=time.monotonic, tiles=None, asks=None
    ):
        self.config = config
        self.keys = keys  # KeyEmitter
        self.run = run  # run(argv)
        self.launch = launch or (lambda app: None)
        self.remove = remove or (lambda index: False)
        self.resize = resize or (lambda height: False)
        self.clock = clock
        self.tiles = tiles
        self.asks = asks
        self.touch = {}  # slot -> Touch
        self.ask_touch = {}  # slot -> AskTouch
        self.preview = None  # the height while the grip moves

    def spot(self, t):
        return self.config.spots[t.spot]

    def handle(self, events):
        changed = False
        for kind, slot, px, py in events:
            if kind == "begin":
                changed |= self._begin(slot, px, py)
                continue
            if slot in self.ask_touch:
                changed |= self._ask_event(kind, slot, px, py)
                continue
            t = self.touch.get(slot)
            if t is None:
                continue
            s = self.spot(t)
            if kind == "move":
                if s.kind == GRIP:
                    h = t.start_height + (py - t.start_y)
                    h = round(h / HEIGHT_STEP) * HEIGHT_STEP
                    h = min(max(h, MIN_HEIGHT), MAX_HEIGHT)
                    if h != self.preview:
                        self.preview = h
                        changed = True
                    continue
                near = self.config.near_spot(t.spot, px, py)
                if (s.kind == SHORTCUT or s.item.mode == "tap") and near != t.armed:
                    t.armed = near
                    changed = True
                continue
            del self.touch[slot]
            changed = True
            lifted = kind == "end" and t.armed and self.config.near_spot(t.spot, px, py)
            if s.kind == GRIP:
                if not (kind == "end" and self.preview != self.config.height and self.resize(self.preview)):
                    self.preview = None
            elif s.kind == SHORTCUT:
                if lifted and t.removing:
                    self.remove(s.index)
                elif lifted:
                    self.launch(s.item)
            elif s.item.tile:
                self.tiles.event(s.item.tile, "up", bool(lifted))
            elif s.item.mode == "hold":
                self._fire(s.item, press=False)
            elif lifted:
                self._fire(s.item, press=True)
                self._fire(s.item, press=False)
        return changed

    def touch_height(self):
        """How far down touches belong to the bar now: the config's height,
        or CARD_HEIGHT while a question shows on a shorter bar."""
        if self.asks and self.asks.shown():
            return max(self.config.height, CARD_HEIGHT)
        return self.config.height

    def _begin(self, slot, px, py):
        ask = self.asks.shown() if self.asks else None
        if ask and 0 <= py < self.touch_height() and px < GRIP_X:
            # Too soon after the question appeared (with the bar grown
            # under it), or on its text: the contact is the question's,
            # and does nothing.
            fresh = self.clock() < ask.shown_at + ASK_ARM
            option = None if fresh else ask_option_at(ask, px)
            self.ask_touch[slot] = AskTouch(ask, option)
            return option is not None
        i = self.config.spot_at(px, py, self.touch_height())
        if i is None:
            return False
        s = self.config.spots[i]
        if s.item is None and s.kind != GRIP:
            return False
        if s.kind == GRIP:
            if any(self.spot(t).kind == GRIP for t in self.touch.values()):
                return False
            self.preview = self.config.height
            self.touch[slot] = Touch(i, self.clock(), py, self.config.height)
            return True
        if s.kind == BUTTON and s.item.tile:
            if not (self.tiles and self.tiles.online(s.item.tile)):
                return False
            self.touch[slot] = Touch(i, self.clock())
            self.tiles.event(s.item.tile, "down")
            return True
        self.touch[slot] = Touch(i, self.clock())
        if s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=True)
        return True

    def _ask_event(self, kind, slot, px, py):
        t = self.ask_touch[slot]
        if t.option is None:
            if kind != "move":
                del self.ask_touch[slot]
            return False
        here = near(option_x(t.option), SPOT_W, max(self.config.height, CARD_HEIGHT), px, py)
        if kind == "move":
            if here != t.armed:
                t.armed = here
                return True
            return False
        del self.ask_touch[slot]
        if kind == "end" and t.armed and here and self.asks.shown() is t.ask:
            if t.option == DISMISS:
                self.asks.dismiss(t.ask)
            else:
                self.asks.choose(t.ask, t.option)
        return True

    def tick(self):
        """Turn long-held shortcuts to "remove"; True if that changed anything."""
        now, changed = self.clock(), False
        for t in self.touch.values():
            if self.spot(t).kind == SHORTCUT and not t.removing and now >= t.start + LONG_PRESS:
                t.removing = True
                changed |= t.armed
        return changed

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        times = [t.start + LONG_PRESS for t in self.touch.values() if self.spot(t).kind == SHORTCUT and not t.removing]
        return min(times) if times else None

    def _fire(self, b, press):
        if b.keys:
            (self.keys.press if press else self.keys.release)(b.keys)
        elif press and b.command:
            self.run(b.command)
        elif not press and b.release_command:
            self.run(b.release_command)

    def base_height(self):
        """The bar's own height: the config's, or the grip's while it moves."""
        return self.config.height if self.preview is None else self.preview

    def height(self):
        """The height drawn: the bar's own, or CARD_HEIGHT while a question
        shows on a shorter bar."""
        if self.asks and self.asks.shown():
            return max(self.base_height(), CARD_HEIGHT)
        return self.base_height()

    def pressed(self):
        return sorted({t.spot for t in self.touch.values() if t.armed})

    def removing(self):
        return sorted({t.spot for t in self.touch.values() if t.armed and t.removing})

    def ask_pressed(self, ask):
        """The answers of this question a finger is on (DISMISS first)."""
        on = {t.option for t in self.ask_touch.values() if t.ask is ask and t.armed and t.option is not None}
        return ([DISMISS] if DISMISS in on else []) + sorted(o for o in on if o != DISMISS)

    def _end(self, slot):
        t = self.touch.pop(slot)
        s = self.spot(t)
        if s.kind == BUTTON and s.item.tile:
            self.tiles.event(s.item.tile, "up", False)
        elif s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=False)

    def set_config(self, config):
        """Take a reloaded config. Buttons held through it stay held if the
        same button is still in the same spot; every other touch ends."""
        for slot, t in list(self.touch.items()):
            s, n = self.spot(t), config.spots[t.spot]
            if not (s.kind == BUTTON and n.kind == BUTTON and n.item and n.item.action() == s.item.action()):
                self._end(slot)
        self.config = config
        self.preview = None

    def tile_gone(self, name):
        """An app stopped providing a tile: touches on it end, unannounced."""
        for slot, t in list(self.touch.items()):
            s = self.spot(t)
            if s.kind == BUTTON and s.item.tile == name:
                del self.touch[slot]

    def release_all(self):
        for slot in list(self.touch):
            self._end(slot)
        self.ask_touch.clear()
        self.preview = None
        self.keys.release_all()


# --- apps --------------------------------------------------------------------
#
# Programs use the bar through a Unix socket next to touchbar.json
# (SOCKET_NAME, in the user's own runtime directory), one JSON object per
# line each way. docs/app-api.md is the reference. In short, an app sends:
#
#   {"op": "hello", "app": NAME, "tiles": [TILE, ...]}   the tiles it draws
#   {"op": "set", "tile": TILE, "icon": .., "label": .., "text": ..,
#    "badge": .., "tone": ..}                              what a tile shows
#   {"op": "ask", "id": ID, "title": .., "text": .., "timeout": SECONDS,
#    "options": [{"id": .., "label": .., "tone": ..}, ...]}  a question
#   {"op": "cancel", "id": ID}  and  {"op": "ping"}
#   {"op": "notify", ...}   a GNOME notification (the extension; see below)
#
# and gets back "welcome" (which of its tiles have a spot), "event" (a touch
# on its tile: "down", then "up" with "inside" when the finger lifted on
# it), "answer" (the chosen option id, or null with the reason: "dismissed"
# when the user closed the card, "timeout", "unavailable" when the bar
# cannot show questions, "cancelled", or "ignored" with "why" for a
# notification the bar does not mirror), "pong" and "error".


class ProtocolError(Exception):
    pass


def _printable(value, what, limit, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise ProtocolError("%s must be printable text of 1 to %d characters" % (what, limit))
    return value.strip()


def _known(msg, allowed):
    unknown = set(msg) - allowed - {"op"}
    if unknown:
        raise ProtocolError("unknown field(s): %s" % ", ".join(sorted(unknown)))


def _tone(value):
    if value is None:
        return "neutral"
    if value not in TONES:
        raise ProtocolError("tone must be one of %s" % ", ".join(TONES))
    return value


class Ask:
    """A question from an app, with up to MAX_OPTIONS answers; or a GNOME
    notification shown the same way (notification True)."""

    __slots__ = ("client", "deadline", "id", "notification", "options", "shown_at", "text", "title")

    def __init__(self, client, id, title, text, options, deadline, notification=False):
        self.client, self.id, self.title, self.text = client, id, title, text
        self.options, self.deadline, self.shown_at = options, deadline, None
        self.notification = notification


def parse_ask(client, msg, now):
    """An Ask from an "ask" message; ProtocolError if it is not one."""
    _known(msg, {"id", "title", "text", "options", "timeout"})
    ask_id = msg.get("id")
    if not isinstance(ask_id, str) or not OPTION_ID.fullmatch(ask_id):
        raise ProtocolError('id must be a name like "q1" (letters, digits, . _ -, at most 32)')
    title = _printable(msg.get("title"), "title", 80)
    text = _printable(msg.get("text"), "text", 200, required=False)
    options = msg.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        raise ProtocolError("options must be a list of 1 to %d answers" % MAX_OPTIONS)
    clean: list[dict] = []
    for o in options:
        if not isinstance(o, dict):
            raise ProtocolError("each option is an object with id and label")
        _known(o, {"id", "label", "tone"})
        if not isinstance(o.get("id"), str) or not OPTION_ID.fullmatch(o["id"]):
            raise ProtocolError('option id must be a name like "allow"')
        if o["id"] in [c["id"] for c in clean]:
            raise ProtocolError("option id %s appears twice" % o["id"])
        clean.append(
            {"id": o["id"], "label": _printable(o.get("label"), "option label", 24), "tone": _tone(o.get("tone"))}
        )
    timeout = msg.get("timeout", ASK_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= MAX_ASK_TIMEOUT:
        raise ProtocolError("timeout must be 1 to %d seconds" % MAX_ASK_TIMEOUT)
    return Ask(client, ask_id, title, text, clean, now + timeout)


# --- notifications -----------------------------------------------------------
#
# The extension forwards each notification GNOME's message tray asks a banner
# for, normalized, as one "notify" message:
#
#   {"op": "notify", "id": ID, "app": "org.gnome.Calendar.desktop" | null,
#    "source": "Calendar", "title": .., "text": .., "urgency": "normal",
#    "actions": ["Join", "Snooze"], "banners": true, "dnd": false,
#    "acknowledged": false}
#
# "banners" is whether GNOME shows banners for that app (false under Do Not
# Disturb too), "dnd" whether Do Not Disturb is on. The rules of what the bar
# mirrors are here, not in the extension, so they are tested here.


def clip(value, limit):
    """value as one line of printable text, at most limit characters."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def mirror_refusal(card, policy):
    """Why the bar does not mirror this notification, or None if it does.

    Only notifications GNOME would show a banner for (not low urgency, not
    already seen, banners on for the app and Do Not Disturb off, except for
    critical ones, as GNOME does), and of those only the ones with buttons
    or that are urgent (high or critical). Then the config's say.
    """
    urgency = card["urgency"]
    if not policy.mirror:
        return "mirroring is off in the config"
    if card["app"] in policy.deny:
        return "the config denies %s" % card["app"]
    if policy.allow is not None and card["app"] not in policy.allow:
        return "%s is not in the config's allow list" % (card["app"] or "an app with no id")
    if card["acknowledged"]:
        return "already seen"
    if urgency == "low":
        return "low urgency"
    if urgency != "critical" and card["dnd"]:
        return "do not disturb"
    if urgency != "critical" and not card["banners"]:
        return "banners are off for the app"
    if not card["actions"] and urgency not in ("high", "critical"):
        return "no buttons and not urgent"
    return None


def parse_notify(msg):
    """The normalized notification of a "notify" message."""
    _known(msg, {"id", "app", "source", "title", "text", "urgency", "actions", "banners", "dnd", "acknowledged"})
    if not isinstance(msg.get("id"), str) or not OPTION_ID.fullmatch(msg["id"]):
        raise ProtocolError('id must be a name like "n1" (letters, digits, . _ -, at most 32)')
    app = msg.get("app")
    try:
        app = app_id(app) if app is not None else None
    except ConfigError:
        app = None
    if msg.get("urgency", "normal") not in URGENCIES:
        raise ProtocolError("urgency must be one of %s" % ", ".join(URGENCIES))
    actions = msg.get("actions", [])
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        raise ProtocolError("actions must be a list of button labels")
    flags = {}
    for key, default in (("banners", True), ("dnd", False), ("acknowledged", False)):
        flags[key] = msg.get(key, default)
        if not isinstance(flags[key], bool):
            raise ProtocolError("%s must be true or false" % key)
    return dict(
        flags,
        id=msg["id"],
        app=app,
        source=clip(msg.get("source"), 80),
        title=clip(msg.get("title"), 80),
        text=clip(msg.get("text"), 400),
        urgency=msg.get("urgency", "normal"),
        actions=[clip(a, 24) or "Action" for a in actions],
    )


def notification_ask(client, card, now, timeout):
    """The question that shows a notification: its first MAX_OPTIONS buttons
    as answers "action-0".., the title (else the app's name) and the text."""
    title = card["title"] or card["source"] or "Notification"
    text = " \u00b7 ".join(p for p in (card["source"] if card["source"] != title else "", card["text"]) if p)
    options = [
        {"id": "action-%d" % k, "label": label, "tone": "neutral"}
        for k, label in enumerate(card["actions"][:MAX_OPTIONS])
    ]
    return Ask(client, card["id"], title, clip(text, 200) or None, options, now + timeout, notification=True)


class Asks:
    """Questions waiting for an answer; the first is shown, the rest queue.

    Questions show only while the bar can show them (available): the daemon
    holds the touchpad and a renderer that knows questions is running. When
    it cannot, a new question is answered "unavailable" at once and waiting
    ones are too, so whoever asked can fall back to asking another way.

    The bar grows to CARD_HEIGHT for the first question (raised), once no
    touchpad contact lies in the band it would cover (band_clear(), which
    the daemon sets) or GROW_WAIT after it started waiting, whichever comes
    first. The question shows from then on, so its ASK_ARM counts from the
    moment the bar grew. It shrinks back when no question is left.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.queue = []
        self.available = False
        self.changed = False
        # Whether the bar can show questions now; the daemon sets it.
        self.can_show = None
        self.band_clear = lambda: True
        self.raised = False
        self.waiting_since = None

    def refresh(self):
        """Ask can_show again: a question arriving right after the bar came
        on screen shows, and waiting ones go when it left."""
        if self.can_show is not None:
            self.set_available(bool(self.can_show()))

    def find(self, client, ask_id):
        return next((a for a in self.queue if a.client is client and a.id == ask_id), None)

    def add(self, ask):
        if self.find(ask.client, ask.id):
            raise ProtocolError("question %s is already waiting" % ask.id)
        self.refresh()
        if not self.available:
            self._reply(ask, None, "unavailable")
            return
        self.queue.append(ask)
        self.settle()

    def replace(self, ask):
        """A newer version of a waiting question (an updated notification)
        takes its place; shown, it is armed anew. False if none waits."""
        old = self.find(ask.client, ask.id)
        if old is None:
            return False
        i = self.queue.index(old)
        self.queue[i] = ask
        if i == 0 and self.raised:
            ask.shown_at = self.clock()
        self.changed = True
        return True

    def shown(self):
        if self.available and self.queue and self.raised:
            return self.queue[0]
        return None

    def settle(self):
        """Grow the bar for the first question, or let it shrink back."""
        head = self.queue[0] if self.available and self.queue else None
        if head is None:
            if self.raised or self.waiting_since is not None:
                self.raised, self.waiting_since = False, None
                self.changed = True
            return
        if self.raised:
            return
        now = self.clock()
        if self.waiting_since is None:
            self.waiting_since = now
        if now >= self.waiting_since + GROW_WAIT or self.band_clear():
            self.raised, self.waiting_since = True, None
            head.shown_at = now
            self.changed = True

    def choose(self, ask, k):
        self._finish(ask, ask.options[k]["id"], "chosen")

    def dismiss(self, ask):
        """Closed with no answer: for a notification, the card only."""
        self._finish(ask, None, "dismissed")

    def cancel(self, client, ask_id):
        ask = self.find(client, ask_id)
        if ask is None:
            return False
        self._finish(ask, None, "cancelled")
        return True

    def drop(self, client):
        """The app went away: its questions go, unanswered."""
        for a in [a for a in self.queue if a.client is client]:
            self._finish(a, None, None)

    def set_available(self, ok):
        if ok == self.available:
            return
        self.available = ok
        if not ok:
            for a in list(self.queue):
                self._finish(a, None, "unavailable")
        self.changed = True
        self.settle()

    def tick(self):
        now = self.clock()
        for a in [a for a in self.queue if now >= a.deadline]:
            self._finish(a, None, "timeout")
        self.settle()

    def deadline(self):
        """When tick() next has something to do: a timeout, or the end of
        the wait for the band to clear."""
        times = [a.deadline for a in self.queue]
        if self.waiting_since is not None:
            times.append(self.waiting_since + GROW_WAIT)
        return min(times, default=None)

    def _finish(self, ask, option, reason):
        head = self.queue and self.queue[0] is ask
        self.queue.remove(ask)
        if head and self.queue and self.raised:
            # The next one shows in the bar already grown, armed anew.
            self.queue[0].shown_at = self.clock()
        self.changed = True
        if reason:
            self._reply(ask, option, reason)
        self.settle()

    @staticmethod
    def _reply(ask, option, reason):
        ask.client.send({"op": "answer", "id": ask.id, "option": option, "reason": reason})


class Apps:
    """The programs on the socket: who provides which tile, and their asks.

    A tile belongs to the first app that provides it, until that app goes.
    on_tile_gone(name) lets the bar end touches on a tile whose app left.
    """

    def __init__(self, clock=time.monotonic):
        self.clients = []
        self.owner = {}  # tile name -> client
        self.look = {}  # tile name -> what it shows
        self.placed = set()  # tile names the config gives a spot
        self.asks = Asks(clock)
        self.notify_policy = NotifyPolicy()
        self.on_tile_gone = lambda name: None
        self.dirty = False

    def take_changed(self):
        changed = self.dirty or self.asks.changed
        self.dirty = self.asks.changed = False
        return changed

    def connect(self, client):
        if len(self.clients) >= MAX_APPS:
            client.send({"op": "error", "message": "too many apps (%d)" % MAX_APPS})
            return False
        client.name = None
        self.clients.append(client)
        return True

    def disconnect(self, client):
        if client not in self.clients:
            return
        self.clients.remove(client)
        self.asks.drop(client)
        for name in [n for n, c in self.owner.items() if c is client]:
            del self.owner[name]
            self.look.pop(name, None)
            self.on_tile_gone(name)
            self.dirty = True

    def set_placed(self, names):
        self.placed = set(names)

    def online(self, name):
        return name in self.owner

    def provided(self):
        return {name: self.look.get(name, {}) for name in self.owner}

    def names(self):
        return sorted({c.name for c in self.clients if c.name})

    def event(self, name, kind, inside=None):
        client = self.owner.get(name)
        if client is None:
            return
        msg = {"op": "event", "tile": name, "type": kind}
        if inside is not None:
            msg["inside"] = inside
        client.send(msg)

    def handle(self, client, msg):
        """One message from an app; errors go back to it."""
        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                client.send({"op": "pong"})
            elif op == "hello":
                self._hello(client, msg)
            elif client.name is None:
                raise ProtocolError("say hello first")
            elif op == "set":
                self._set(client, msg)
            elif op == "ask":
                self.asks.add(parse_ask(client, msg, self.asks.clock()))
            elif op == "notify":
                self._notify(client, msg)
            elif op == "cancel":
                _known(msg, {"id"})
                if not self.asks.cancel(client, msg.get("id")):
                    raise ProtocolError("no waiting question %r" % msg.get("id"))
            else:
                raise ProtocolError("unknown op %r" % op)
        except ProtocolError as e:
            reply = {"op": "error", "request": op if isinstance(op, str) else None, "message": str(e)}
            if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                reply["id"] = msg["id"]
            client.send(reply)

    def _hello(self, client, msg):
        _known(msg, {"app", "tiles"})
        if client.name is not None:
            raise ProtocolError("hello was already said")
        name = msg.get("app")
        if not isinstance(name, str) or not TILE_NAME.fullmatch(name):
            raise ProtocolError('app must be a name like "agents" (lower case, at most 32)')
        tiles = msg.get("tiles", [])
        if (
            not isinstance(tiles, list)
            or len(tiles) > 8
            or not all(isinstance(t, str) and TILE_NAME.fullmatch(t) for t in tiles)
        ):
            raise ProtocolError("tiles must be a list of at most 8 tile names")
        client.name = name
        taken = []
        for tile in dict.fromkeys(tiles):
            if tile in self.owner:
                taken.append(tile)
                continue
            self.owner[tile] = client
            self.look[tile] = {}
            self.dirty = True
        mine = [t for t in dict.fromkeys(tiles) if t not in taken]
        client.send(
            {
                "op": "welcome",
                "app": name,
                "placed": [t for t in mine if t in self.placed],
                "unplaced": [t for t in mine if t not in self.placed],
                "taken": taken,
            }
        )

    def _notify(self, client, msg):
        """A GNOME notification, from the extension: a card if the rules
        say so, else answered "ignored" at once. One already waiting with
        the same id was updated, and the newer version replaces it."""
        card = parse_notify(msg)
        old = self.asks.find(client, card["id"])
        why = mirror_refusal(card, self.notify_policy)
        if (
            why is None
            and old is None
            and MAX_NOTIFY_WAITING <= sum(a.client is client and a.notification for a in self.asks.queue)
        ):
            why = "too many notifications waiting"
        if why is not None:
            if old is not None:
                self.asks.cancel(client, card["id"])
                return
            client.send({"op": "answer", "id": card["id"], "option": None, "reason": "ignored", "why": why})
            return
        ask = notification_ask(client, card, self.asks.clock(), self.notify_policy.timeout)
        if not self.asks.replace(ask):
            self.asks.add(ask)

    def _set(self, client, msg):
        _known(msg, {"tile", "icon", "label", "text", "badge", "tone"})
        tile = msg.get("tile")
        if self.owner.get(tile) is not client:
            raise ProtocolError("tile %r is not yours; provide it in hello" % tile)
        look = {"tone": _tone(msg.get("tone"))}
        icon = msg.get("icon")
        if icon is not None:
            if not isinstance(icon, str) or not (
                re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", icon)
                or (icon.startswith("/") and len(icon) <= 4096 and icon.isprintable())
            ):
                raise ProtocolError("icon must be an icon name or an absolute image path")
            look["icon"] = icon
        for key, limit in (("label", 32), ("text", 48), ("badge", 4)):
            value = _printable(msg.get(key), key, limit, required=False)
            if value:
                look[key] = value
        if look != self.look.get(tile):
            self.look[tile] = look
            self.dirty = True


class AppClient:
    """One connection to the app socket."""

    def __init__(self, sock):
        self.sock = sock
        self.rbuf = b""
        self.wbuf = b""
        self.name = None
        # Set when it must be cut off: a too long line, or replies piling up.
        self.broken = False

    def fileno(self):
        return self.sock.fileno()

    def send(self, msg):
        if self.broken:
            return
        self.wbuf += (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        if len(self.wbuf) > MAX_OUT:
            self.broken = True


def peer_uid(sock):
    """The uid of the process at the other end of a Unix socket, or None."""
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    return struct.unpack("3i", creds)[1]


class AppServer:
    """The listening socket and its connections; the daemon's loop drives it."""

    def __init__(self, path, apps):
        self.path, self.apps = path, apps
        self.clients = {}  # fd -> AppClient
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        if os.path.exists(path):
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            try:
                probe.connect(path)
            except OSError:
                os.unlink(path)  # left by a daemon that died
            else:
                raise TouchbarError("another daemon answers on %s" % path)
            finally:
                probe.close()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC)
        old = os.umask(0o177)
        try:
            self.sock.bind(path)
        finally:
            os.umask(old)
        self.sock.listen(8)

    def rfds(self):
        return [self.sock.fileno()] + list(self.clients)

    def wfds(self):
        return [fd for fd, c in self.clients.items() if c.wbuf]

    def owns(self, fd):
        return fd == self.sock.fileno() or fd in self.clients

    def readable(self, fd):
        if fd == self.sock.fileno():
            self._accept()
            return
        client = self.clients.get(fd)
        if client is None:
            return
        try:
            data = client.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._drop(client)
            return
        client.rbuf += data
        while b"\n" in client.rbuf:
            line, client.rbuf = client.rbuf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                client.send({"op": "error", "message": "not a JSON object"})
                continue
            self.apps.handle(client, msg)
        if len(client.rbuf) > MAX_LINE:
            client.broken = True

    def _accept(self):
        try:
            conn, _ = self.sock.accept()
        except BlockingIOError:
            return
        if peer_uid(conn) != os.getuid():
            conn.close()
            return
        conn.setblocking(False)
        client = AppClient(conn)
        if self.apps.connect(client):
            self.clients[conn.fileno()] = client
        else:
            self._write(client)
            conn.close()

    def flush(self):
        """Send what is waiting, and cut off broken clients."""
        for client in list(self.clients.values()):
            if client.broken:
                self._drop(client)
            elif client.wbuf:
                self._write(client)

    def _write(self, client):
        try:
            n = client.sock.send(client.wbuf)
        except BlockingIOError:
            return
        except OSError:
            self._drop(client)
            return
        client.wbuf = client.wbuf[n:]

    def _drop(self, client):
        self.clients.pop(client.fileno(), None)
        self.apps.disconnect(client)
        client.sock.close()

    def close(self):
        for client in list(self.clients.values()):
            self._drop(client)
        self.sock.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


# --- evdev and uinput ----------------------------------------------------------


def bits(fd, ev, count):
    buf = bytearray((count + 7) // 8)
    fcntl.ioctl(fd, EVIOCGBIT(ev, len(buf)), buf)
    return [i for i in range(count) if buf[i // 8] >> (i % 8) & 1]


def absinfo(fd, code):
    buf = bytearray(ABSINFO.size)
    fcntl.ioctl(fd, EVIOCGABS(code), buf)
    return ABSINFO.unpack(buf)


def read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def find_source(sys_input=SYS_INPUT):
    """/dev/input/eventN of the ScreenPad touchpad, or None. Skips virtual devices."""
    try:
        names = sorted(os.listdir(sys_input), key=lambda n: (len(n), n))
    except OSError:
        return None
    for name in names:
        if not name.startswith("event"):
            continue
        dev = os.path.join(sys_input, name, "device")
        if "/devices/virtual/" in os.path.realpath(dev):
            continue
        vendor = read_sys(os.path.join(dev, "id", "vendor"))
        product = read_sys(os.path.join(dev, "id", "product"))
        dev_name = read_sys(os.path.join(dev, "name")) or ""
        if (
            vendor
            and product
            and int(vendor, 16) == SOURCE_VENDOR
            and int(product, 16) == SOURCE_PRODUCT
            and dev_name.endswith(SOURCE_SUFFIX)
        ):
            return os.path.join("/dev/input", name)
    return None


def write_events(fd, events):
    if events:
        os.write(fd, b"".join(INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events))


class Source:
    """The grabbed ScreenPad touchpad."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            buf = bytearray(INPUT_ID.size)
            fcntl.ioctl(self.fd, EVIOCGID, buf)
            self.id = INPUT_ID.unpack(buf)
            name = bytearray(256)
            n = fcntl.ioctl(self.fd, _ioc(2, 0x06, len(name)), name)
            self.name = bytes(name[: max(n - 1, 0)]).decode(errors="replace")
            prop = bytearray(4)
            fcntl.ioctl(self.fd, EVIOCGPROP, prop)
            self.props = [i for i in range(32) if prop[i // 8] >> (i % 8) & 1]
            self.keys = bits(self.fd, EV_KEY, KEY_CNT)
            self.abs = {code: absinfo(self.fd, code) for code in bits(self.fd, EV_ABS, ABS_CNT)}
            self.msc = bits(self.fd, EV_MSC, 8)
            for code in (ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                if code not in self.abs:
                    raise TouchbarError("%s has no multitouch axis %#x" % (path, code))
        except BaseException:
            os.close(self.fd)
            raise
        self.grabbed = False

    @property
    def nslots(self):
        return self.abs[ABS_MT_SLOT][2] + 1

    def axes(self):
        x, y = self.abs[ABS_MT_POSITION_X], self.abs[ABS_MT_POSITION_Y]
        return Axes(x[1], x[2], y[1], y[2])

    def touching(self):
        buf = bytearray(KEY_CNT // 8)
        fcntl.ioctl(self.fd, EVIOCGKEY, buf)
        return bool(buf[BTN_TOUCH // 8] >> (BTN_TOUCH % 8) & 1)

    def mt_slots(self, code):
        buf = bytearray(4 * (1 + self.nslots))
        struct.pack_into("I", buf, 0, code)
        fcntl.ioctl(self.fd, EVIOCGMTSLOTS(self.nslots), buf)
        return list(struct.unpack_from("%di" % self.nslots, buf, 4))

    def state(self):
        """Arguments for Splitter.resync()."""
        ids = self.mt_slots(ABS_MT_TRACKING_ID)
        xs = self.mt_slots(ABS_MT_POSITION_X)
        ys = self.mt_slots(ABS_MT_POSITION_Y)
        tools = self.mt_slots(ABS_MT_TOOL_TYPE) if ABS_MT_TOOL_TYPE in self.abs else [0] * self.nslots
        buf = bytearray(KEY_CNT // 8)
        fcntl.ioctl(self.fd, EVIOCGKEY, buf)
        left = buf[BTN_LEFT // 8] >> (BTN_LEFT % 8) & 1
        return ids, xs, ys, tools, left, absinfo(self.fd, ABS_MT_SLOT)[0]

    def grab(self, on):
        fcntl.ioctl(self.fd, EVIOCGRAB, 1 if on else 0)
        self.grabbed = on

    def read(self):
        """The pending events, as (type, code, value)."""
        try:
            data = os.read(self.fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            return []
        if not data:
            raise OSError(errno.ENODEV, "device gone")
        return [
            INPUT_EVENT.unpack_from(data, o)[2:] for o in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size)
        ]

    def close(self):
        os.close(self.fd)


def uinput_open():
    try:
        return os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except PermissionError:
        raise TouchbarError(
            "cannot open /dev/uinput; 'sudo ./install.sh' installs a udev rule that gives the desktop user access"
        )


def uinput_create(fd, name, bus, vendor, product, version):
    setup = UINPUT_SETUP.pack(bus, vendor, product, version, name.encode()[:79], 0)
    fcntl.ioctl(fd, UI_DEV_SETUP, setup)
    fcntl.ioctl(fd, UI_DEV_CREATE)


def create_pad(source):
    """A uinput touchpad with the source's capabilities, ids and resolution."""
    fd = uinput_open()
    try:
        for ev in (EV_SYN, EV_KEY, EV_ABS) + ((EV_MSC,) if source.msc else ()):
            fcntl.ioctl(fd, UI_SET_EVBIT, ev)
        for code in source.keys:
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        for code in source.msc:
            fcntl.ioctl(fd, UI_SET_MSCBIT, code)
        for prop in source.props:
            fcntl.ioctl(fd, UI_SET_PROPBIT, prop)
        for code, info in sorted(source.abs.items()):
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP, UINPUT_ABS_SETUP.pack(code, *info))
        # Same bus and ids as the real pad: libinput then tags it internal, so
        # disable-while-typing pairs it with the built-in keyboard.
        uinput_create(fd, PAD_NAME, *source.id)
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_keys():
    fd = uinput_open()
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, MAX_KEY + 1):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        uinput_create(fd, KEYS_NAME, BUS_VIRTUAL, SOURCE_VENDOR, SOURCE_PRODUCT, 1)
    except BaseException:
        os.close(fd)
        raise
    return fd


def uinput_destroy(fd):
    try:
        fcntl.ioctl(fd, UI_DEV_DESTROY)
    finally:
        os.close(fd)


# --- daemon ------------------------------------------------------------------


class Daemon:
    def __init__(self, config, config_file, device=None, flag=ACTIVE_FLAG):
        self.config = config
        self.config_file = config_file
        self.config_mtime = self._mtime()
        self.device = device
        self.flag = flag
        self.source: Source | None = None
        self.pad: int | None = None
        self.keys_fd: int | None = None
        self.splitter: Splitter | None = None
        self.bar: Bar | None = None
        self.children = []
        self.stop = False
        self.reload = False
        self.published = None
        self.monitor = monitor_identity()
        self.wakeup: int | None = None
        self.apps = Apps()
        self.apps.set_placed(config.tiles())
        self.apps.on_tile_gone = self._tile_gone
        # Questions show only while the bar is on screen, drawn by an
        # extension that knows them; otherwise whoever asked hears
        # "unavailable".
        self.apps.asks.can_show = lambda: self.source is not None and self.source.grabbed and self.renderer_ready()
        # The bar grows for a card only when no touchpad finger is in the
        # band it would cover (or after GROW_WAIT).
        self.apps.asks.band_clear = lambda: (
            not (self.splitter and self.splitter.pad_in_band(self.config.height, CARD_HEIGHT))
        )
        self.apps.notify_policy = config.notifications
        self.server = None
        # renderer.json's mtime, and whether it is ready
        self.renderer: tuple[int | None, bool] = (None, False)

    def _mtime(self):
        p = self.config_file or config_path()
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    def _tile_gone(self, name):
        if self.bar:
            self.bar.tile_gone(name)

    def renderer_ready(self):
        """Whether the extension draws questions and has the bar on screen
        (renderer.json)."""
        path = runtime_file(RENDERER_NAME)
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            self.renderer = (None, False)
            return False
        if self.renderer[0] != mtime:
            try:
                with open(path) as f:
                    said = json.load(f)
                protocol = said.get("protocol")
                ready = (
                    isinstance(protocol, int)
                    and not isinstance(protocol, bool)
                    and protocol >= RENDERER_PROTOCOL
                    and said.get("drawn") is True
                )
            except (OSError, ValueError, AttributeError):
                ready = False
            self.renderer = (mtime, ready)
        return self.renderer[1]

    def scene(self):
        """The tiles to draw: a question over the spots while one shows."""
        pressed = self.bar.pressed() if self.bar else []
        removing = self.bar.removing() if self.bar else []
        tiles = spot_tiles(self.config, pressed, removing, self.apps.provided())
        ask = self.apps.asks.shown()
        if ask is not None:
            tiles = ask_tiles(ask, self.bar.ask_pressed(ask) if self.bar else (), len(self.apps.asks.queue) - 1) + [
                t for t in tiles if t["style"] == GRIP
            ]
        return tiles

    # The state file the GNOME Shell extension draws from.
    def publish(self):
        state = {
            "version": SCENE_VERSION,
            "pid": os.getpid(),
            "active": self.source is not None and self.source.grabbed,
            "monitor": self.monitor,
            "panel": [PANEL_W, PANEL_H],
            # Drawn: CARD_HEIGHT while a card shows. The strut (the work
            # area windows avoid) is the bar's own height: a card covers
            # windows for a moment rather than resizing them.
            "height": self.bar.height() if self.bar else self.config.height,
            "strut": self.bar.base_height() if self.bar else self.config.height,
            "tiles": self.scene(),
            "divider": DIVIDER_X,
            "apps": self.apps.names(),
            # Version 1: the extension loaded before tiles keeps drawing the
            # bar from these until the next login (GNOME loads new extension
            # code only then). It shows no app tiles and no questions.
            "spots": [s.public() for s in self.config.spots],
            "pressed": self.bar.pressed() if self.bar else [],
            "removing": self.bar.removing() if self.bar else [],
        }
        if state == self.published:
            return
        path = state_path()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        self.published = state

    def unpublish(self):
        try:
            os.unlink(state_path())
        except FileNotFoundError:
            pass

    def run_command(self, argv):
        try:
            self.children.append(subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True))
        except OSError as e:
            log("%s: %s" % (shlex.join(argv), e))

    def launch(self, app):
        self.run_command(LAUNCH + [app])

    def edit(self, change, what):
        try:
            edit_config(change, self.config_file)
        except (OSError, ConfigError) as e:
            log("could not %s: %s" % (what, e))
            return False
        self.reload = True
        return True

    def remove_shortcut(self, index):
        def change(c):
            return c.height, c.shortcuts[:index] + c.shortcuts[index + 1 :]

        return self.edit(change, "remove the shortcut")

    def resize(self, height):
        return self.edit(lambda c: (height, c.shortcuts), "resize the bar")

    def new_bar(self, keys):
        return Bar(
            self.config,
            keys,
            self.run_command,
            self.launch,
            self.remove_shortcut,
            self.resize,
            tiles=self.apps,
            asks=self.apps.asks,
        )

    def reap(self):
        self.children = [c for c in self.children if c.poll() is None]

    def want_active(self):
        return os.path.exists(self.flag)

    def activate(self):
        """True once grabbed; False while a finger is down; None if there is no pad."""
        path = self.device or find_source()
        if not path:
            return None
        source = Source(path)
        try:
            # Grab only between touches, so the desktop never keeps a touch
            # whose end it will not see.
            if source.touching():
                source.close()
                return False
            self.pad = create_pad(source)
            self.keys_fd = create_keys()
            source.grab(True)
        except BaseException:
            source.close()
            self.destroy_uinput()
            raise
        self.source = source
        x, y = source.abs[ABS_MT_POSITION_X], source.abs[ABS_MT_POSITION_Y]
        # New contacts take the bar's height now, taller while a card
        # shows; contacts already down keep their kind.
        self.splitter = Splitter(source.axes(), source.nslots, self._in_bar, source.abs[ABS_MT_SLOT][0])
        self.bar = self.new_bar(KeyEmitter(lambda ev: write_events(self.keys_fd, ev)))
        log(
            "grabbed %s (%s), %d slots, x 0..%d, y 0..%d, %d units/mm; bar %d px high, "
            "%d button(s), %d shortcut(s)"
            % (
                path,
                source.name,
                source.nslots,
                x[2],
                y[2],
                x[5],
                self.config.height,
                len(self.config.buttons),
                len(self.config.shortcuts),
            )
        )
        return True

    def _in_bar(self, px, py):
        assert self.bar is not None  # made right after the splitter, before any event
        return py < self.bar.touch_height()

    def destroy_uinput(self):
        for attr in ("pad", "keys_fd"):
            fd = getattr(self, attr)
            setattr(self, attr, None)
            if fd is not None:
                try:
                    uinput_destroy(fd)
                except OSError as e:
                    log("destroying a uinput device: %s" % e)

    def deactivate(self, why, wait=True):
        source = self.source
        if source is None:
            return
        assert self.splitter is not None and self.bar is not None
        try:
            pad, bar = self.splitter.release_all()
            self.bar.handle(bar)
            self.bar.release_all()
            write_events(self.pad, pad)
        except OSError as e:
            log("releasing: %s" % e)
        deadline = time.monotonic() + (LIFT_WAIT if wait else 0)
        try:
            while source.grabbed and source.touching() and time.monotonic() < deadline:
                time.sleep(0.05)
                source.read()
            source.grab(False)
        except OSError:
            pass
        source.close()
        self.source = self.splitter = self.bar = None
        self.destroy_uinput()
        log("released the touchpad: %s" % why)

    def process(self):
        assert self.source is not None and self.splitter is not None and self.bar is not None
        try:
            events = self.source.read()
        except OSError as e:
            self.deactivate("%s: %s" % (self.source.path, e), wait=False)
            return
        changed = False
        for ev in events:
            out = self.splitter.feed(*ev)
            if out is None:
                continue
            if out == RESYNC:
                out = self.splitter.resync(*self.source.state())
            pad, bar = out
            write_events(self.pad, pad)
            changed |= self.bar.handle(bar)
        if changed and self.bar:
            self.publish()

    def check_config(self):
        mtime = self._mtime()
        if not self.reload and mtime == self.config_mtime:
            return
        self.reload = False
        self.config_mtime = mtime
        try:
            config, path = load_config(self.config_file)
        except (OSError, ConfigError) as e:
            log("keeping the current bar: %s" % e)
            return
        self.config = config
        self.apps.set_placed(config.tiles())
        self.apps.notify_policy = config.notifications
        if self.bar:
            self.bar.set_config(config)
        log(
            "loaded %s: bar %d px high, %d button(s), %d shortcut(s)"
            % (path or "the built-in default", config.height, len(config.buttons), len(config.shortcuts))
        )
        self.publish()

    def loop(self):
        self.publish()
        retry_at = 0.0
        while not self.stop:
            self.check_config()
            self.reap()
            timeout = POLL
            if self.source is None and self.want_active() and time.monotonic() >= retry_at:
                try:
                    taken = self.activate()
                except OSError as e:
                    log("cannot take the touchpad: %s" % e)
                    retry_at = time.monotonic() + 5
                    taken = None
                if taken:
                    self.publish()
                elif taken is False:
                    timeout = 0.2  # a finger is down, or the pad is not there yet
            elif self.source is not None and not self.want_active():
                self.deactivate("%s is gone (screenpad off)" % self.flag)
                self.publish()
            self.apps.asks.refresh()
            self.apps.asks.tick()
            for deadline in (self.bar.deadline() if self.bar else None, self.apps.asks.deadline()):
                if deadline is not None:
                    timeout = min(timeout, max(deadline - time.monotonic(), 0))
            if self.apps.take_changed():
                self.publish()
            if self.server:
                self.server.flush()
            fds = (
                ([self.source.fd] if self.source else [])
                + ([self.wakeup] if self.wakeup is not None else [])
                + (self.server.rfds() if self.server else [])
            )
            ready, _, _ = select.select(fds, self.server.wfds() if self.server else [], [], timeout)
            if self.wakeup is not None and self.wakeup in ready:
                try:
                    os.read(self.wakeup, 64)
                except BlockingIOError:
                    pass
            if self.source and self.source.fd in ready:
                self.process()
                if self.source is None:
                    self.publish()
            for fd in ready:
                if self.server and self.server.owns(fd):
                    self.server.readable(fd)
            if self.bar and self.bar.tick():
                self.publish()
            if self.apps.take_changed():
                self.publish()
            if self.server:
                self.server.flush()

    def main(self):
        def on_term(signum, frame):
            self.stop = True

        def on_hup(signum, frame):
            self.reload = True

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        signal.signal(signal.SIGHUP, on_hup)
        # Signals wake the select() at once: a reload ('screenpad-touchbar
        # shortcut' sends SIGHUP) shows on the bar without waiting for POLL.
        r, w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        signal.set_wakeup_fd(w)
        self.wakeup = r
        try:
            self.server = AppServer(runtime_file(SOCKET_NAME), self.apps)
        except (OSError, TouchbarError) as e:
            log("apps cannot use the bar: %s" % e)
        try:
            self.loop()
        finally:
            self.deactivate("stopping")
            if self.server:
                self.server.close()
            self.unpublish()
        return 0


# --- commands ----------------------------------------------------------------


def cmd_run(args):
    try:
        migrate_config(args.config)
        config, path = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    log("config: %s" % (path or "built-in default"))
    return Daemon(config, args.config, args.device, args.flag).main()


def cmd_check(args):
    try:
        config, path = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    print("%s: ok" % (path or "built-in default"))
    print(
        "bar: %d of %d px high (%.1f mm); %d px while a card shows"
        % (config.height, PANEL_H, config.height / 16, max(config.height, CARD_HEIGHT))
    )
    print("notifications: %s" % config.notifications.describe())
    for s in config.spots:
        where = "  %-8s %d  x %4d..%-4d " % (s.kind, s.index + 1, s.x, s.x + s.width)
        if s.kind == GRIP:
            print(where + "drag to resize")
        elif s.item is None:
            print(where + "empty")
        elif s.kind == SHORTCUT:
            print(where + s.item)
        else:
            b = s.item
            if b.tile:
                print(where + "%s, icon %s, tile %s (an app draws it)" % (b.label or "-", b.icon or "none", b.tile))
                continue
            action = (
                "key %s (codes %s)" % (b.key, "+".join(str(k) for k in b.keys))
                if b.keys
                else "command %s" % shlex.join(b.command)
            )
            print(where + "%s, icon %s, %s %s" % (b.label or "-", b.icon or "none", b.mode, action))
    return 0


def systemctl(*argv):
    return subprocess.run(["systemctl", "--user"] + list(argv)).returncode


def enable_extension():
    """Enable the GNOME Shell extension; a new one loads at the next login."""
    if subprocess.run(["gnome-extensions", "enable", UUID], capture_output=True).returncode == 0:
        return True
    r = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"], capture_output=True, text=True)
    if r.returncode != 0:
        log("could not read the enabled GNOME Shell extensions: %s" % r.stderr.strip())
        return False
    value = r.stdout.strip()
    if value.startswith("@as "):
        value = value[4:]
    enabled = ast.literal_eval(value)
    if UUID not in enabled:
        enabled.append(UUID)
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(enabled)], check=True)
    log("enabled %s; GNOME Shell loads it at your next login (log out and in once)" % UUID)
    return False


def cmd_on(args):
    path = config_path()
    migrate_config(path)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(EXAMPLE_CONFIG)
        log("wrote the default bar to %s" % path)
    try:
        load_config()
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    enable_extension()
    return systemctl("enable", "--now", UNIT)


def cmd_off(args):
    return systemctl("disable", "--now", UNIT)


def cmd_status(args):
    try:
        config, path = load_config(args.config)
        print(
            "config: %s, bar %d px, %d button(s), shortcuts: %s"
            % (path or "built-in default", config.height, len(config.buttons), ", ".join(config.shortcuts) or "none")
        )
    except (OSError, ConfigError) as e:
        print("config: %s" % e)
    source = find_source()
    if source and not os.access(source, os.R_OK):
        source += " (not readable: add your user to group input and log in again)"
    print("touchpad: %s" % (source or "not found"))
    print(
        "screenpad: %s"
        % ("on (%s)" % ACTIVE_FLAG if os.path.exists(ACTIVE_FLAG) else "off; the bar waits for 'screenpad on'")
    )
    print("uinput: %s" % ("writable" if os.access("/dev/uinput", os.W_OK) else "not writable"))
    r = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
    print("unit: %s %s" % (UNIT, r.stdout.strip() or "unknown"))
    try:
        with open(state_path()) as f:
            state = json.load(f)
        print(
            "daemon: pid %d, %s, pressed %s"
            % (state["pid"], "touchpad grabbed" if state["active"] else "idle", state["pressed"])
        )
        print("apps: %s" % (", ".join(state.get("apps") or []) or "none"))
    except (OSError, ValueError, KeyError):
        print("daemon: not running")
    return 0


def cmd_probe(args):
    """Print where touches land, without grabbing; for checking the orientation."""
    path = args.device or find_source()
    if not path:
        log("ScreenPad touchpad not found")
        return 1
    try:
        config, _ = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    source = Source(path)
    splitter = Splitter(source.axes(), source.nslots, lambda px, py: py < config.height)
    others = [
        p
        for p in (os.path.join("/dev/input", n) for n in os.listdir(SYS_INPUT) if n.startswith("event"))
        if p != path and _same_hid(p, path)
    ]
    other_fds = {}
    for p in others:
        try:
            other_fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
        except OSError:
            pass
    print(
        "reading %s (%s), not grabbing; bar %d px high. Touch the panel; Ctrl+C stops."
        % (path, source.name, config.height)
    )
    seen: dict[int, tuple] = {}  # slot -> Contact as last printed
    try:
        while True:
            ready, _, _ = select.select([source.fd] + list(other_fds), [], [])
            for fd in ready:
                if fd in other_fds:
                    n = len(os.read(fd, 4096)) // INPUT_EVENT.size
                    print("  %s (same digitizer) sent %d event(s)" % (other_fds[fd], n))
                    continue
                for ev in source.read():
                    if splitter.feed(*ev) is None:
                        continue
                    now = splitter.contacts
                    for s, c in sorted(seen.items()):
                        if s not in now or now[s].id != c[0]:
                            print("slot %d lift   at panel %4.0f,%4.0f" % ((s,) + c[1]))
                            del seen[s]
                    for s, c in sorted(now.items()):
                        px, py = splitter.axes.panel(c.x, c.y)
                        if s not in seen:
                            i = config.spot_at(px, py) if c.kind == BAR else None
                            spot = config.spots[i] if i is not None else None
                            print(
                                "slot %d touch  at panel %4.0f,%4.0f (device %d,%d): %s%s"
                                % (
                                    s,
                                    px,
                                    py,
                                    c.x,
                                    c.y,
                                    c.kind,
                                    "" if spot is None else ", %s %d" % (spot.kind, spot.index + 1),
                                )
                            )
                        seen[s] = (c.id, (px, py))
    except KeyboardInterrupt:
        return 0
    finally:
        source.close()
        for fd in other_fds:
            os.close(fd)


def poke_daemon():
    """Make the running daemon reload its config now (else it does within POLL)."""
    try:
        with open(state_path()) as f:
            pid = json.load(f)["pid"]
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmdline = f.read().split(b"\0")
        if any(a.endswith((b"screenpad-touchbar", b"touchbar.py")) for a in cmdline):
            os.kill(pid, signal.SIGHUP)
    except (OSError, ValueError, KeyError, TypeError):
        pass


def cmd_shortcut(args):
    """List, add or remove the bar's shortcuts (the extension adds by drag and drop)."""
    try:
        migrate_config(args.config)
        if args.action == "list":
            config, _path = load_config(args.config)
            for i in range(MAX_SHORTCUTS):
                print("%d  %s" % (i + 1, config.shortcuts[i] if i < len(config.shortcuts) else "(empty)"))
            return 0
        if args.action == "add":
            app = app_id(args.app)
            spot = None if args.spot is None else args.spot - 1

            def change(c):
                return c.height, add_shortcut(c.shortcuts, app, spot)
        else:
            if not 1 <= args.spot <= MAX_SHORTCUTS:
                raise ConfigError("spot must be 1 to %d" % MAX_SHORTCUTS)

            def change(c):
                if args.spot > len(c.shortcuts):
                    raise ConfigError("shortcut spot %d is empty" % args.spot)
                return c.height, c.shortcuts[: args.spot - 1] + c.shortcuts[args.spot :]

        config = edit_config(change, args.config)
    except ConfigError as e:
        log(str(e))
        return 2
    poke_daemon()
    print("shortcuts: %s" % (", ".join(config.shortcuts) or "none"))
    return 0


def cmd_ask(args):
    """Ask a question in the bar; print the chosen answer's id."""
    options, tones = [], {}
    for spec in args.tone:
        key, sep, tone = spec.partition("=")
        if not sep:
            log("--tone takes ID=TONE, such as allow=accept")
            return 2
        tones[key] = tone
    for spec in args.option:
        key, sep, label = spec.partition("=")
        if not sep:
            log('--option takes ID=LABEL, such as allow="Allow once"')
            return 2
        option = {"id": key, "label": label}
        if key in tones:
            option["tone"] = tones[key]
        options.append(option)
    request = {"op": "ask", "id": "ask", "title": args.title, "options": options, "timeout": args.timeout}
    if args.text:
        request["text"] = args.text
    path = runtime_file(SOCKET_NAME)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.settimeout(args.timeout + 10)
    try:
        sock.connect(path)
        for msg in ({"op": "hello", "app": "ask"}, request):
            sock.sendall((json.dumps(msg) + "\n").encode())
        buf = b""
        while True:
            data = sock.recv(65536)
            if not data:
                log("the touch bar went away")
                return 1
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("op") == "error":
                    log(msg.get("message", "error"))
                    return 2
                if msg.get("op") == "answer" and msg.get("id") == "ask":
                    if msg.get("option") is None:
                        log("no answer: %s" % msg.get("reason"))
                        return 1
                    print(msg["option"])
                    return 0
    except (OSError, ValueError) as e:
        log("the touch bar is not reachable at %s: %s" % (path, e))
        return 1
    finally:
        sock.close()


def _same_hid(a, b):
    def parent(p):
        return os.path.realpath(os.path.join(SYS_INPUT, os.path.basename(p), "device", "..", ".."))

    return parent(a) == parent(b)


COMMANDS = {
    "run": cmd_run,
    "on": cmd_on,
    "off": cmd_off,
    "status": cmd_status,
    "check": cmd_check,
    "probe": cmd_probe,
    "shortcut": cmd_shortcut,
    "ask": cmd_ask,
}


def parse_args(argv):
    p = argparse.ArgumentParser(prog="screenpad-touchbar", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="the daemon (the systemd user unit runs this)")
    sub.add_parser(
        "on",
        help="write the default config if there is none, enable and start the user unit and the GNOME Shell extension",
    )
    sub.add_parser("off", help="stop and disable the user unit; the plain touchpad comes back")
    status = sub.add_parser("status", help="show the config, the touchpad and the daemon")
    check = sub.add_parser("check", help="validate the config")
    probe = sub.add_parser("probe", help="print where touches land, without grabbing")
    shortcut = sub.add_parser("shortcut", help="list, add or remove the bar's app shortcuts")
    actions = shortcut.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="the shortcut spots and their apps")
    add = actions.add_parser(
        "add", help="add an app, at a spot or in the first free one; on a full bar, an app added at a spot replaces it"
    )
    add.add_argument("app", help="desktop file id, such as org.gnome.Nautilus.desktop")
    add.add_argument("--spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    remove = actions.add_parser("remove", help="remove the shortcut in a spot")
    remove.add_argument("spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    ask = sub.add_parser(
        "ask",
        help="ask a question in the bar and print the chosen answer's "
        "id; exits 1 without an answer (a timeout, or the bar "
        "cannot show it)",
    )
    ask.add_argument("title", help="the question, at most 80 characters")
    ask.add_argument("--text", help="a second line, at most 200 characters")
    ask.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="ID=LABEL",
        required=True,
        help="an answer, up to %d: its id (printed when chosen) and label" % MAX_OPTIONS,
    )
    ask.add_argument(
        "--tone", action="append", default=[], metavar="ID=TONE", help="an answer's color: %s" % ", ".join(TONES)
    )
    ask.add_argument(
        "--timeout",
        type=float,
        default=ASK_TIMEOUT,
        metavar="SECONDS",
        help="give up after this long (default %d)" % ASK_TIMEOUT,
    )
    for s in (run, status, check, probe, shortcut):
        s.add_argument("--config", metavar="FILE", help="config file (default ~/.config/screenpad/touchbar.toml)")
    for s in (run, probe):
        s.add_argument("--device", metavar="PATH", help="touchpad evdev node (default: find it)")
    run.add_argument("--flag", default=ACTIVE_FLAG, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (OSError, TouchbarError, subprocess.CalledProcessError) as e:
        log(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
