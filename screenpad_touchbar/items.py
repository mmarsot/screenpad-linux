"""The items the bar and the apps page show: what one [[bar]] or [[apps]] block is, parsed.

An item is one of five kinds, by the one key that says what it does:

- key: a key or combo the bar's keyboard sends ("hold": down while the
  finger is on it; "tap": one press when it lifts on it);
- command: a command to run (with "hold", on touch, and release_command
  on lift);
- app: an app by its desktop file id, which a tap opens (a shortcut);
- tile: a tile an app on the socket draws and handles (apps.py);
- page: one of the apps page's pages an app gives (widgets.py), which a
  tap opens.

Every kind goes on the bar and on the apps page alike; icon and label are
what it shows (an app's own icon and name, an app's tile or page as the app
draws it, unless set).
"""

import configparser
import functools
import os
import re
import shlex

from .common import APP, COMMAND, ITEM_KINDS, KEY, PAGE_ITEM, TILE, TILE_NAME, ConfigError
from .keys import parse_key

ITEM_KEYS = {"label", "icon", "mode", "release_command"} | set(ITEM_KINDS)
APP_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.desktop")


class Item:
    """One item: its kind, what it shows and what a touch on it does.

    name is the app's desktop file id, or the tile's or the page's name;
    mode ("hold" or "tap") applies to a key or a command only.
    """

    __slots__ = ("command", "icon", "key", "keys", "kind", "label", "mode", "name", "release_command")

    def __init__(
        self, kind, label="", icon=None, mode=None, key=None, keys=None, command=None, release_command=None, name=None
    ):
        self.kind, self.label, self.icon, self.mode = kind, label, icon, mode
        self.key, self.keys, self.command, self.release_command = key, keys, command, release_command
        self.name = name

    @classmethod
    def app(cls, app_id, label="", icon=None):
        return cls(APP, label, icon, name=app_id)

    def hold(self):
        return self.mode == "hold"

    def action(self):
        """What a touch on it does; a held item stays held through a reload
        only while this stays the same."""
        return (self.kind, self.mode, self.keys, self.command, self.release_command, self.name)

    def spec(self):
        """Everything the config says of it."""
        return self.action() + (self.label, self.icon, self.key)

    def looks(self):
        """Its icon and label, those it has."""
        return {k: v for k, v in (("icon", self.icon), ("label", self.label)) if v}

    def public(self):
        """The item as a version 1 spot (an extension from before tiles)."""
        if self.kind == APP:
            return {"kind": "shortcut", "app": self.name}
        return {"kind": "button", "label": self.label, "icon": self.icon, "mode": self.mode}

    def describe(self):
        """One line for 'check' and 'shortcut list'."""
        if self.kind == APP:
            return self.name + (" (%s)" % self.label if self.label else "")
        looks = "%s, icon %s" % (self.label or "-", self.icon or "none")
        if self.kind == TILE:
            return "%s, tile %s (an app draws it)" % (looks, self.name)
        if self.kind == PAGE_ITEM:
            return "%s, page %s (opens an app's page)" % (looks, self.name)
        if self.kind == KEY:
            return "%s, %s key %s (codes %s)" % (looks, self.mode, self.key, "+".join(str(k) for k in self.keys))
        return "%s, %s command %s" % (looks, self.mode, shlex.join(self.command))


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


def _name(value, kind):
    if not isinstance(value, str) or not TILE_NAME.fullmatch(value):
        raise ConfigError(
            '%s must be a name like "agents" (lower case letters, digits, dot, dash or underscore, at most 32)' % kind
        )
    return value


def parse_item(b):
    """An Item from a [[bar]] or [[apps]] block (a dict); ConfigError if it is not one."""
    kinds = [k for k in ITEM_KINDS if k in b]
    if len(kinds) != 1:
        raise ConfigError("needs exactly one of key, command, app, tile or page")
    kind = kinds[0]
    label = b.get("label", "")
    if not isinstance(label, str):
        raise ConfigError("label must be a string")
    icon = _icon(b["icon"]) if "icon" in b else None
    if kind in (KEY, COMMAND):
        if not icon and not label:
            raise ConfigError("needs an icon or a label")
        mode = b.get("mode", "tap")
        if mode not in ("hold", "tap"):
            raise ConfigError('mode must be "hold" or "tap"')
    elif "mode" in b:
        raise ConfigError(
            "mode does not apply to a%s %s: %s"
            % ("n" if kind == APP else "", kind, "a tap opens it" if kind in (APP, PAGE_ITEM) else "the app decides")
        )
    else:
        mode = None
    if "release_command" in b and not (kind == COMMAND and mode == "hold"):
        raise ConfigError('release_command only goes with a command in mode "hold"')
    item = Item(kind, label, icon, mode)
    if kind == KEY:
        item.key, item.keys = b["key"], parse_key(b["key"])
    elif kind == COMMAND:
        item.command = _command(b["command"], "command")
        if "release_command" in b:
            item.release_command = _command(b["release_command"], "release_command")
    elif kind == APP:
        item.name = app_id(b["app"])
    else:
        item.name = _name(b[kind], kind)
    return item


def _desktop_file(app):
    """The path of an app's desktop file, where GLib looks for it, or None."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [home] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for d in dirs:
        path = os.path.join(d, "applications", app)
        if d and os.path.isfile(path):
            return path
    return None


@functools.cache
def app_label(app):
    """An app's name, from its desktop file (Name), else from its id:
    "org.gnome.Nautilus.desktop" is "Nautilus". Read once per app; the
    daemon forgets them when it reloads the config (cache_clear())."""
    path = _desktop_file(app)
    if path:
        parser = configparser.RawConfigParser(strict=False, interpolation=None)
        parser.optionxform = str  # type: ignore[assignment,method-assign]  # desktop keys are case sensitive
        try:
            parser.read(path, encoding="utf-8")
            name = parser.get("Desktop Entry", "Name", fallback="").strip()
        except (configparser.Error, OSError, UnicodeDecodeError):
            name = ""
        if name:
            return name[:32]
    return app[: -len(".desktop")].rsplit(".", 1)[-1][:32]
