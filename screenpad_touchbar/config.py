"""touchbar.toml: the bar's spots, buttons and shortcuts, the [notifications] policy, parsing and loading."""

import os
import re
import tomllib

from .common import (
    BUTTON,
    DEFAULT_CONFIG,
    DEFAULT_HEIGHT,
    GRIP,
    GRIP_X,
    MAX_ASK_TIMEOUT,
    MAX_BUTTONS,
    MAX_HEIGHT,
    MAX_SHORTCUTS,
    MIN_HEIGHT,
    NOTIFY_TIMEOUT,
    PANEL_W,
    SHORTCUT,
    SHORTCUTS_X,
    SPOT_W,
    TAP_SLOP,
    TILE_NAME,
    ConfigError,
)
from .defaults import EXAMPLE_CONFIG
from .keys import parse_key


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
