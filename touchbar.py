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
again. It publishes the bar layout and which spots are pressed in
$XDG_RUNTIME_DIR/screenpad/touchbar.json for the GNOME Shell extension that
draws the bar on the ScreenPad and takes apps dropped on it.

Runs as the desktop user (group input, plus access to /dev/uinput).
Commands: run, on, off, status, check, probe, shortcut.
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
LAUNCH = ["/bin/sh", "-c",
          'gdbus call --session --dest org.gnome.Shell --object-path %s --method %s.Launch "$1"'
          ' >/dev/null 2>&1 || exec gtk-launch "$1"' % (DBUS_PATH, DBUS_IFACE), "sh"]
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
FINGER_TOOLS = (BTN_TOOL_FINGER, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP,
                BTN_TOOL_QUADTAP, BTN_TOOL_QUINTTAP)
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
    names.update({
        "esc": 1, "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
        "leftbrace": 26, "rightbrace": 27, "enter": 28, "leftctrl": 29,
        "semicolon": 39, "apostrophe": 40, "grave": 41, "leftshift": 42,
        "backslash": 43, "comma": 51, "dot": 52, "slash": 53, "rightshift": 54,
        "leftalt": 56, "space": 57, "capslock": 58, "numlock": 69,
        "scrolllock": 70, "rightctrl": 97, "sysrq": 99, "rightalt": 100,
        "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106,
        "end": 107, "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
        "mute": 113, "volumedown": 114, "volumeup": 115, "pause": 119,
        "leftmeta": 125, "rightmeta": 126, "compose": 127, "menu": 139,
        "calc": 140, "sleep": 142, "nextsong": 163, "playpause": 164,
        "previoussong": 165, "stopcd": 166, "record": 167, "print": 210,
        "camera": 212, "search": 217, "brightnessdown": 224,
        "brightnessup": 225, "micmute": 248,
    })
    names.update({
        "ctrl": 29, "control": 29, "shift": 42, "alt": 56, "altgr": 100,
        "super": 125, "meta": 125, "win": 125, "escape": 1, "return": 28,
        "del": 111, "pgup": 104, "pgdn": 109, "printscreen": 99,
        "playpause": 164, "next": 163, "previous": 165, "prev": 165,
    })
    return names


KEY_NAMES = _key_names()


def parse_key(spec):
    """'ctrl+alt+t' -> (29, 56, 20). Names are evdev key names, KEY_ optional."""
    if not isinstance(spec, str) or not spec.strip():
        raise ConfigError("key must be a non-empty string like \"alt+k\" or \"ctrl+alt+t\"")
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
    def __init__(self, label, mode, keys=None, command=None, release_command=None,
                 key=None, icon=None):
        self.label = label
        self.icon = icon
        self.key = key
        self.mode = mode
        self.keys = keys
        self.command = command
        self.release_command = release_command

    def action(self):
        return (self.mode, self.keys, self.command, self.release_command)

    def public(self):
        return {"label": self.label, "icon": self.icon, "mode": self.mode}


class Spot:
    __slots__ = ("kind", "index", "x", "width", "item")

    def __init__(self, kind, index, x, width, item=None):
        self.kind, self.index, self.x, self.width, self.item = kind, index, x, width, item

    def public(self):
        out = {"kind": self.kind, "x": self.x, "width": self.width}
        if self.kind == BUTTON:
            out.update(self.item.public() if self.item else {"label": None, "icon": None,
                                                              "mode": None})
        elif self.kind == SHORTCUT:
            out["app"] = self.item
        return out


class Config:
    def __init__(self, height, buttons, shortcuts=()):
        self.height = height
        self.buttons = list(buttons)
        self.shortcuts = list(shortcuts)
        self.spots = (
            [Spot(BUTTON, i, i * SPOT_W, SPOT_W, self.buttons[i] if i < len(self.buttons) else None)
             for i in range(MAX_BUTTONS)]
            + [Spot(SHORTCUT, i, SHORTCUTS_X + i * SPOT_W, SPOT_W,
                    self.shortcuts[i] if i < len(self.shortcuts) else None)
               for i in range(MAX_SHORTCUTS)]
            + [Spot(GRIP, 0, GRIP_X, PANEL_W - GRIP_X)])

    def spot_at(self, px, py):
        """The index in spots of what is under a bar touch, or None."""
        if not 0 <= py < self.height:
            return None
        for i, s in enumerate(self.spots):
            if s.x <= px < s.x + s.width:
                return i
        return None

    def near_spot(self, i, px, py):
        s = self.spots[i]
        return (s.x - TAP_SLOP <= px < s.x + s.width + TAP_SLOP
                and -TAP_SLOP <= py < self.height + TAP_SLOP)


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
""" % {"height": DEFAULT_HEIGHT, "min": MIN_HEIGHT, "max": MAX_HEIGHT, "ptt": PTT_BLOCK}

# sha256 of the default config earlier versions wrote. A file still exactly
# that is replaced by the current default (see migrate_config()).
OLD_DEFAULTS = {"dfabfa5d73979ec2302e2a4d343426085da0c6fcd9093f31e3cee4067bc30c36"}

# Default blocks earlier versions wrote, each with its current text. A config
# that still holds one word for word gets the new one in its place and keeps
# the rest (see migrate_config()).
OLD_BLOCKS = (
    (OLD_PTT_BLOCK, PTT_BLOCK),
    ('''\
#   key              a key or combo: "F14", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional)
''', '''\
#   key              a key or combo: "alt+k", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional;
#                    "meta" and "super" are the Super key, not Alt)
'''),
)

# x and width placed buttons in the first version; the spots do it now.
BUTTON_KEYS = {"label", "icon", "mode", "key", "command", "release_command", "x", "width"}
SHORTCUT_KEYS = {"app"}
APP_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.desktop")


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be a whole number of pixels" % what)
    return value


def _command(value, what):
    if isinstance(value, str) and value.strip():
        return ["/bin/sh", "-c", value]
    if (isinstance(value, list) and value
            and all(isinstance(a, str) for a in value) and value[0]):
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
        raise ConfigError("app must be a desktop file id like \"org.gnome.Nautilus.desktop\"")
    value = value.strip()
    if not value.endswith(".desktop"):
        value += ".desktop"
    if not APP_ID.fullmatch(value):
        raise ConfigError("app %r is not a desktop file id like \"org.gnome.Nautilus.desktop\""
                          % value)
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
    if ("key" in b) == ("command" in b):
        raise ConfigError("needs exactly one of key or command")
    keys = parse_key(b["key"]) if "key" in b else None
    command = _command(b["command"], "command") if "command" in b else None
    release = None
    if "release_command" in b:
        if mode != "hold" or command is None:
            raise ConfigError("release_command only goes with a command in mode \"hold\"")
        release = _command(b["release_command"], "release_command")
    return Button(label, mode, keys, command, release, b.get("key"), icon)


def parse_config(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(str(e))
    unknown = set(data) - {"height", "button", "shortcut"}
    if unknown:
        raise ConfigError("unknown setting(s): %s" % ", ".join(sorted(unknown)))
    height = _int(data.get("height", DEFAULT_HEIGHT), "height")
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ConfigError("height must be between %d and %d" % (MIN_HEIGHT, MAX_HEIGHT))
    buttons = []
    for where, b in _blocks(data, "button", BUTTON_KEYS, MAX_BUTTONS):
        try:
            buttons.append(_button(b))
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
    return Config(height, buttons, shortcuts)


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "screenpad", "touchbar.toml")


def load_config(path=None):
    """The user's config, else the installed default, else the built-in one."""
    for p in ([path] if path else [config_path(), DEFAULT_CONFIG]):
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
        if ((new.height, new.shortcuts) != (height, shortcuts)
                or [b.public() for b in new.buttons] != [b.public() for b in old.buttons]
                or [b.action() for b in new.buttons] != [b.action() for b in old.buttons]):
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
        d = edid[o:o + 18]
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


# --- the split ---------------------------------------------------------------


class Axes:
    """Maps digitizer units to panel pixels."""

    def __init__(self, xmin, xmax, ymin, ymax):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    def panel(self, x, y):
        return ((x - self.xmin) * PANEL_W / (self.xmax - self.xmin),
                (y - self.ymin) * PANEL_H / (self.ymax - self.ymin))


PAD, BAR, IGNORED = "pad", "bar", "ignored"
# What feed() returns for the SYN_REPORT that ends a SYN_DROPPED gap.
RESYNC = "resync"


class Slot:
    __slots__ = ("id", "x", "y", "tool")

    def __init__(self):
        self.id, self.x, self.y, self.tool = -1, 0, 0, 0


class Contact:
    __slots__ = ("id", "kind", "x", "y", "tool", "order")

    def __init__(self, id, kind, x, y, tool, order):
        self.id, self.kind, self.x, self.y, self.tool, self.order = id, kind, x, y, tool, order


class Splitter:
    """Splits the source touchpad's frames between the bar and the virtual pad.

    feed() takes the source's events one by one. At each SYN_REPORT it returns
    (pad, bar), or RESYNC after a SYN_DROPPED gap (then call resync()): the events for the virtual touchpad (whole frames, ending in
    SYN_REPORT, or nothing if the pad did not change) and the bar's contact
    events, ("begin"|"move"|"end"|"cancel", slot, px, py) in panel pixels.
    Contacts keep the kind they started with: a bar contact that slides down
    stays out of the pad, and a pad contact that slides up stays a pad contact,
    so the pointer never sees a touch appear or vanish mid-stroke.
    """

    def __init__(self, axes, nslots, in_bar, cur=0):
        self.axes = axes
        self.in_bar = in_bar          # in_bar(px, py) -> bool
        self.slots = [Slot() for _ in range(nslots)]
        # The kernel sends ABS_MT_SLOT only when it changes, so a reader
        # starts from the value EVIOCGABS gives at open.
        self.cur = cur
        self.contacts = {}            # slot -> Contact
        self.sent = {}                # slot -> (id, x, y, tool) on the virtual pad
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
                ev += [(EV_ABS, ABS_MT_TRACKING_ID, new[0]), (EV_ABS, ABS_MT_TOOL_TYPE, new[3]),
                       (EV_ABS, ABS_MT_POSITION_X, new[1]), (EV_ABS, ABS_MT_POSITION_Y, new[2])]
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
        bar = [("cancel", s) + self.axes.panel(c.x, c.y)
               for s, c in sorted(self.contacts.items()) if c.kind == BAR]
        for c in self.contacts.values():
            c.kind = IGNORED
        self.src_left = False
        self.left_changed = True
        return self.pad_frames({}), bar


class Touch:
    __slots__ = ("spot", "armed", "start", "removing", "start_y", "start_height")

    def __init__(self, spot, start, start_y=0, start_height=0):
        self.spot, self.armed, self.start, self.removing = spot, True, start, False
        self.start_y, self.start_height = start_y, start_height


class Bar:
    """Turns bar contacts into button presses, shortcut taps and resizes.

    - button: "hold" sends its keys down on touch and up on lift; "tap" sends
      them when the finger lifts on (or near) the button;
    - shortcut: a tap opens the app; held for LONG_PRESS it turns to
      "remove", and lifting then removes it; sliding away cancels either;
    - grip: dragging it up or down resizes the bar, saved on lift.
    launch(app), remove(index) and resize(height) do the saving and
    launching; remove and resize return whether they did.
    """

    def __init__(self, config, keys, run, launch=None, remove=None, resize=None,
                 clock=time.monotonic):
        self.config = config
        self.keys = keys              # KeyEmitter
        self.run = run                # run(argv)
        self.launch = launch or (lambda app: None)
        self.remove = remove or (lambda index: False)
        self.resize = resize or (lambda height: False)
        self.clock = clock
        self.touch = {}               # slot -> Touch
        self.preview = None           # the height while the grip moves

    def spot(self, t):
        return self.config.spots[t.spot]

    def handle(self, events):
        changed = False
        for kind, slot, px, py in events:
            if kind == "begin":
                changed |= self._begin(slot, px, py)
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
                if not (kind == "end" and self.preview != self.config.height
                        and self.resize(self.preview)):
                    self.preview = None
            elif s.kind == SHORTCUT:
                if lifted and t.removing:
                    self.remove(s.index)
                elif lifted:
                    self.launch(s.item)
            elif s.item.mode == "hold":
                self._fire(s.item, press=False)
            elif lifted:
                self._fire(s.item, press=True)
                self._fire(s.item, press=False)
        return changed

    def _begin(self, slot, px, py):
        i = self.config.spot_at(px, py)
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
        self.touch[slot] = Touch(i, self.clock())
        if s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=True)
        return True

    def tick(self):
        """Turn long-held shortcuts to "remove"; True if that changed anything."""
        now, changed = self.clock(), False
        for t in self.touch.values():
            if (self.spot(t).kind == SHORTCUT and not t.removing
                    and now >= t.start + LONG_PRESS):
                t.removing = True
                changed |= t.armed
        return changed

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        times = [t.start + LONG_PRESS for t in self.touch.values()
                 if self.spot(t).kind == SHORTCUT and not t.removing]
        return min(times) if times else None

    def _fire(self, b, press):
        if b.keys:
            (self.keys.press if press else self.keys.release)(b.keys)
        elif press and b.command:
            self.run(b.command)
        elif not press and b.release_command:
            self.run(b.release_command)

    def height(self):
        return self.config.height if self.preview is None else self.preview

    def pressed(self):
        return sorted({t.spot for t in self.touch.values() if t.armed})

    def removing(self):
        return sorted({t.spot for t in self.touch.values() if t.armed and t.removing})

    def _end(self, slot):
        t = self.touch.pop(slot)
        s = self.spot(t)
        if s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=False)

    def set_config(self, config):
        """Take a reloaded config. Buttons held through it stay held if the
        same button is still in the same spot; every other touch ends."""
        for slot, t in list(self.touch.items()):
            s, n = self.spot(t), config.spots[t.spot]
            if not (s.kind == BUTTON and n.kind == BUTTON and n.item
                    and n.item.action() == s.item.action()):
                self._end(slot)
        self.config = config
        self.preview = None

    def release_all(self):
        for slot in list(self.touch):
            self._end(slot)
        self.preview = None
        self.keys.release_all()


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
        if (vendor and product and int(vendor, 16) == SOURCE_VENDOR
                and int(product, 16) == SOURCE_PRODUCT and dev_name.endswith(SOURCE_SUFFIX)):
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
            self.name = bytes(name[:max(n - 1, 0)]).decode(errors="replace")
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
        return [INPUT_EVENT.unpack_from(data, o)[2:]
                for o in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size)]

    def close(self):
        os.close(self.fd)


def uinput_open():
    try:
        return os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except PermissionError:
        raise TouchbarError("cannot open /dev/uinput; 'sudo ./install.sh' installs a udev rule "
                            "that gives the desktop user access")


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
        self.source = self.pad = self.keys_fd = None
        self.splitter = self.bar = None
        self.children = []
        self.stop = False
        self.reload = False
        self.published = None
        self.monitor = monitor_identity()
        self.wakeup = None

    def _mtime(self):
        p = self.config_file or config_path()
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    # The state file the GNOME Shell extension draws from.
    def publish(self):
        state = {
            "pid": os.getpid(),
            "active": self.source is not None and self.source.grabbed,
            "monitor": self.monitor,
            "panel": [PANEL_W, PANEL_H],
            "height": self.bar.height() if self.bar else self.config.height,
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
            self.children.append(subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, start_new_session=True))
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
            return c.height, c.shortcuts[:index] + c.shortcuts[index + 1:]
        return self.edit(change, "remove the shortcut")

    def resize(self, height):
        return self.edit(lambda c: (height, c.shortcuts), "resize the bar")

    def new_bar(self, keys):
        return Bar(self.config, keys, self.run_command, self.launch, self.remove_shortcut,
                   self.resize)

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
        self.splitter = Splitter(source.axes(), source.nslots,
                                 lambda px, py: py < self.config.height,
                                 source.abs[ABS_MT_SLOT][0])
        self.bar = self.new_bar(KeyEmitter(lambda ev: write_events(self.keys_fd, ev)))
        log("grabbed %s (%s), %d slots, x 0..%d, y 0..%d, %d units/mm; bar %d px high, "
            "%d button(s), %d shortcut(s)"
            % (path, source.name, source.nslots, x[2], y[2], x[5], self.config.height,
               len(self.config.buttons), len(self.config.shortcuts)))
        return True

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
        if self.bar:
            self.bar.set_config(config)
        log("loaded %s: bar %d px high, %d button(s), %d shortcut(s)"
            % (path or "the built-in default", config.height, len(config.buttons),
               len(config.shortcuts)))
        self.publish()

    def loop(self):
        self.publish()
        retry_at = 0
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
                    timeout = 0.2   # a finger is down, or the pad is not there yet
            elif self.source is not None and not self.want_active():
                self.deactivate("%s is gone (screenpad off)" % self.flag)
                self.publish()
            if self.bar and self.bar.deadline() is not None:
                timeout = min(timeout, max(self.bar.deadline() - time.monotonic(), 0))
            fds = ([self.source.fd] if self.source else []) + (
                [self.wakeup] if self.wakeup is not None else [])
            ready, _, _ = select.select(fds, [], [], timeout)
            if self.wakeup in ready:
                try:
                    os.read(self.wakeup, 64)
                except BlockingIOError:
                    pass
            if self.source and self.source.fd in ready:
                self.process()
                if self.source is None:
                    self.publish()
            if self.bar and self.bar.tick():
                self.publish()

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
            self.loop()
        finally:
            self.deactivate("stopping")
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
    print("bar: %d of %d px high (%.1f mm)" % (config.height, PANEL_H, config.height / 16))
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
            action = ("key %s (codes %s)" % (b.key, "+".join(str(k) for k in b.keys)) if b.keys
                      else "command %s" % shlex.join(b.command))
            print(where + "%s, icon %s, %s %s" % (b.label or "-", b.icon or "none", b.mode, action))
    return 0


def systemctl(*argv):
    return subprocess.run(["systemctl", "--user"] + list(argv)).returncode


def enable_extension():
    """Enable the GNOME Shell extension; a new one loads at the next login."""
    if subprocess.run(["gnome-extensions", "enable", UUID], capture_output=True).returncode == 0:
        return True
    r = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("could not read the enabled GNOME Shell extensions: %s" % r.stderr.strip())
        return False
    value = r.stdout.strip()
    if value.startswith("@as "):
        value = value[4:]
    enabled = ast.literal_eval(value)
    if UUID not in enabled:
        enabled.append(UUID)
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions",
                        str(enabled)], check=True)
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
        print("config: %s, bar %d px, %d button(s), shortcuts: %s"
              % (path or "built-in default", config.height, len(config.buttons),
                 ", ".join(config.shortcuts) or "none"))
    except (OSError, ConfigError) as e:
        print("config: %s" % e)
    source = find_source()
    if source and not os.access(source, os.R_OK):
        source += " (not readable: add your user to group input and log in again)"
    print("touchpad: %s" % (source or "not found"))
    print("screenpad: %s" % ("on (%s)" % ACTIVE_FLAG if os.path.exists(ACTIVE_FLAG)
                             else "off; the bar waits for 'screenpad on'"))
    print("uinput: %s" % ("writable" if os.access("/dev/uinput", os.W_OK) else "not writable"))
    r = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
    print("unit: %s %s" % (UNIT, r.stdout.strip() or "unknown"))
    try:
        with open(state_path()) as f:
            state = json.load(f)
        print("daemon: pid %d, %s, pressed %s" % (
            state["pid"], "touchpad grabbed" if state["active"] else "idle", state["pressed"]))
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
    others = [p for p in (os.path.join("/dev/input", n) for n in os.listdir(SYS_INPUT)
                          if n.startswith("event")) if p != path and _same_hid(p, path)]
    other_fds = {}
    for p in others:
        try:
            other_fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
        except OSError:
            pass
    print("reading %s (%s), not grabbing; bar %d px high. Touch the panel; Ctrl+C stops."
          % (path, source.name, config.height))
    seen = {}   # slot -> Contact as last printed
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
                            print("slot %d touch  at panel %4.0f,%4.0f (device %d,%d): %s%s"
                                  % (s, px, py, c.x, c.y, c.kind,
                                     "" if spot is None else ", %s %d" % (spot.kind,
                                                                          spot.index + 1)))
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
            config, path = load_config(args.config)
            for i in range(MAX_SHORTCUTS):
                print("%d  %s" % (i + 1, config.shortcuts[i] if i < len(config.shortcuts)
                                  else "(empty)"))
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
                return c.height, c.shortcuts[:args.spot - 1] + c.shortcuts[args.spot:]
        config = edit_config(change, args.config)
    except ConfigError as e:
        log(str(e))
        return 2
    poke_daemon()
    print("shortcuts: %s" % (", ".join(config.shortcuts) or "none"))
    return 0


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
}


def parse_args(argv):
    p = argparse.ArgumentParser(prog="screenpad-touchbar", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="the daemon (the systemd user unit runs this)")
    sub.add_parser("on", help="write the default config if there is none, enable and start the "
                              "user unit and the GNOME Shell extension")
    sub.add_parser("off", help="stop and disable the user unit; the plain touchpad comes back")
    status = sub.add_parser("status", help="show the config, the touchpad and the daemon")
    check = sub.add_parser("check", help="validate the config")
    probe = sub.add_parser("probe", help="print where touches land, without grabbing")
    shortcut = sub.add_parser("shortcut", help="list, add or remove the bar's app shortcuts")
    actions = shortcut.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="the shortcut spots and their apps")
    add = actions.add_parser("add", help="add an app, at a spot or in the first free one; on a "
                                         "full bar, an app added at a spot replaces it")
    add.add_argument("app", help="desktop file id, such as org.gnome.Nautilus.desktop")
    add.add_argument("--spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    remove = actions.add_parser("remove", help="remove the shortcut in a spot")
    remove.add_argument("spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    for s in (run, status, check, probe, shortcut):
        s.add_argument("--config", metavar="FILE",
                       help="config file (default ~/.config/screenpad/touchbar.toml)")
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
