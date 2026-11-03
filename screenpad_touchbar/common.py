"""What the whole touch bar shares.

The panel and the bar's geometry, names, paths, timings and limits, the
errors, logging, and the runtime files next to touchbar.json.
"""

import os
import re
import sys

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
RENDERER_PROTOCOL = 4
# What each feature needs of the renderer: questions came with protocol 2,
# the pages (the black screensaver, the page dots and the apps page) with 3,
# apps' widgets on the apps page with 4. An extension GNOME keeps from
# before an update until the next login keeps showing what it knows; the
# rest waits for the new one.
ASKS_PROTOCOL = 2
PAGES_PROTOCOL = 3
WIDGETS_PROTOCOL = 4
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


# The bar, left to right: three button spots, a gap, three shortcut spots,
# the page dots and the resize grip at the right end, all as tall as the
# bar. A spot is 17 mm wide and the dots 9 mm, a fingertip.
MAX_BUTTONS = 3
MAX_SHORTCUTS = 3
SPOT_W = 272
GROUP_GAP = 40
SHORTCUTS_X = MAX_BUTTONS * SPOT_W + GROUP_GAP
DOTS_X = SHORTCUTS_X + MAX_SHORTCUTS * SPOT_W
DOTS_W = 144
GRIP_X = DOTS_X + DOTS_W
MIN_HEIGHT = 64
MAX_HEIGHT = PANEL_H // 2
# The grip changes the height in steps of this many pixels (half a mm).
HEIGHT_STEP = 8
BUTTON, SHORTCUT, GRIP, DOTS = "button", "shortcut", "grip", "dots"

# The pages, left to right: the touchpad page (home, below the bar a
# touchpad over a black screensaver) and the apps page (below the bar a
# surface that takes every new contact, kept away from the pointer).
HOME, APPS = 0, 1
PAGE_NAMES = ("touchpad", "apps")
# A swipe along the bar turns the page: one finger, starting on the bar,
# at least SWIPE_MIN panel pixels (25 mm at 16 px/mm) across, mostly
# across (rising at most SWIPE_SLOPE of that), lifted within SWIPE_TIME
# seconds. Moving the finger left shows the page on the right.
SWIPE_MIN = 400
SWIPE_SLOPE = 0.5
SWIPE_TIME = 0.4
# Seconds without a touch before the apps page goes back to the touchpad.
RETURN_AFTER = 8
MAX_RETURN_AFTER = 3600
# The apps page below the bar: a header strip (9 mm, a fingertip) with the
# page's title and, on an app's page, Home; under it the page's grid of
# widgets, at most MAX_COLUMNS by MAX_ROWS cells. The home page lists the
# apps' pages HOME_COLUMNS by HOME_ROWS. An app has at most MAX_PAGES pages
# of at most MAX_WIDGETS widgets; a widget's lines at most MAX_LINES.
HEADER_H = 144
HOME_W = 2 * SPOT_W
MAX_COLUMNS = 6
MAX_ROWS = 4
HOME_COLUMNS = 4
HOME_ROWS = 2
MAX_PAGES = 4
MAX_WIDGETS = 24
MAX_LINES = 16


class TouchbarError(Exception):
    pass


class ConfigError(TouchbarError):
    pass


def log(msg):
    print("screenpad-touchbar: %s" % msg, file=sys.stderr, flush=True)


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
