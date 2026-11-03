"""touchbar.toml: the bar's and the apps page's items and the tables; parsing and loading."""

import os
import tomllib

from .common import (
    APP,
    BAR_ITEMS,
    DEFAULT_CONFIG,
    DEFAULT_HEIGHT,
    GRIP,
    GRIP_X,
    ITEM,
    MAX_APPS_ITEMS,
    MAX_ASK_TIMEOUT,
    MAX_HEIGHT,
    MAX_RETURN_AFTER,
    MIN_HEIGHT,
    NOTIFY_TIMEOUT,
    PAGE,
    PAGE_ITEM,
    PAGE_W,
    PAGE_X,
    PANEL_W,
    RETURN_AFTER,
    SPOT_W,
    SPOT_X,
    TAP_SLOP,
    TILE,
    ConfigError,
)
from .defaults import EXAMPLE_CONFIG
from .items import ITEM_KEYS, Item, app_id, parse_item

# Where the items go: the bar's spots and the apps page's home list.
LISTS = {"bar": BAR_ITEMS, "apps": MAX_APPS_ITEMS}


class Spot:
    __slots__ = ("index", "item", "kind", "width", "x")

    def __init__(self, kind, index, x, width, item=None):
        self.kind, self.index, self.x, self.width, self.item = kind, index, x, width, item

    def public(self):
        """The spot as version 1 had it (an extension from before tiles)."""
        if self.item is not None:
            out = self.item.public()
        elif self.kind == ITEM:
            out = {"kind": "button", "label": None, "icon": None, "mode": None}
        else:
            out = {"kind": self.kind}
        out.update(x=self.x, width=self.width)
        return out


class NotifyPolicy:
    """Which GNOME notifications the bar mirrors ([notifications])."""

    def __init__(self, mirror=True, allow=None, deny=(), timeout=NOTIFY_TIMEOUT):
        self.mirror = mirror
        self.allow = None if allow is None else list(allow)
        self.deny = list(deny)
        self.timeout = timeout

    def spec(self):
        return (self.mirror, self.allow, self.deny, self.timeout)

    def describe(self):
        if not self.mirror:
            return "not mirrored"
        out = "mirrored for %d s" % self.timeout
        if self.allow is not None:
            out += ", only %s" % (", ".join(self.allow) or "no app")
        if self.deny:
            out += ", never %s" % ", ".join(self.deny)
        return out


class Pages:
    """The pages' settings: [screensaver] and [pager]."""

    def __init__(self, clock=True, return_after=RETURN_AFTER):
        self.clock = clock  # a faint clock on the black below the bar
        self.return_after = return_after

    def spec(self):
        return (self.clock, self.return_after)

    def describe(self):
        return "screensaver black%s; apps page back to the touchpad after %d s" % (
            " with a clock" if self.clock else "",
            self.return_after,
        )


class Config:
    """The config: the bar's height, its items (bar) and the apps page's
    (apps), each a list of items.Item, and the tables."""

    def __init__(self, height, bar=(), apps=(), notifications=None, pages=None):
        self.height = height
        self.bar = list(bar)
        self.apps = list(apps)
        self.notifications = notifications or NotifyPolicy()
        self.pages = pages or Pages()
        self.spots = (
            [Spot(ITEM, i, SPOT_X[i], SPOT_W, self.bar[i] if i < len(self.bar) else None) for i in range(BAR_ITEMS)]
            + [Spot(GRIP, 0, GRIP_X, PANEL_W - GRIP_X)]
            # Last, so the other spots keep the numbers an extension from
            # before the pages knows them by.
            + [Spot(PAGE, 0, PAGE_X, PAGE_W)]
        )

    def items(self, where):
        """The items of a list: "bar" or "apps"."""
        return self.bar if where == "bar" else self.apps

    def spec(self):
        """Everything the config says, to compare two."""
        return (
            self.height,
            [i.spec() for i in self.bar],
            [i.spec() for i in self.apps],
            self.notifications.spec(),
            self.pages.spec(),
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

    def near_spot(self, i, px, py, height=None):
        """Whether px, py is on or near spot i, in a bar of height (the
        config's, or taller while a card shows)."""
        s = self.spots[i]
        return near(s.x, s.width, height or self.height, px, py)

    def tiles(self):
        """The tile names the config places, on the bar and the apps page."""
        return [i.name for i in self.bar + self.apps if i.kind == TILE]

    def describe(self):
        """A line on what the bar and the apps page hold."""
        return "bar %d px, %d of %d items, apps page %d item(s)" % (
            self.height,
            len(self.bar),
            BAR_ITEMS,
            len(self.apps),
        )


def near(x, width, height, px, py):
    """Whether a touch at px, py is on, or within TAP_SLOP of, a bar rectangle."""
    return x - TAP_SLOP <= px < x + width + TAP_SLOP and -TAP_SLOP <= py < height + TAP_SLOP


# What an earlier version called the items: [[button]] and [[shortcut]]
# blocks (migrate.py converts them).
OLD_LISTS = ("button", "shortcut")


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be a whole number of pixels" % what)
    return value


def _blocks(data, name, allowed, limit):
    raw = data.get(name, [])
    if not isinstance(raw, list):
        raise ConfigError("%s items go in [[%s]] blocks" % (name, name))
    if len(raw) > limit:
        raise ConfigError("at most %d [[%s]] blocks (got %d)" % (limit, name, len(raw)))
    for n, b in enumerate(raw, 1):
        where = "%s item %d" % (name, n)
        if not isinstance(b, dict):
            raise ConfigError("%s: must be a [[%s]] block" % (where, name))
        if isinstance(b.get("label"), str) and b["label"]:
            where += " (%s)" % b["label"]
        unknown = set(b) - allowed
        if unknown:
            raise ConfigError("%s: unknown setting(s): %s" % (where, ", ".join(sorted(unknown))))
        yield where, b


def _items(data, name, tiles):
    """A list's items; tiles holds the tile names placed so far, as each
    tile goes in one place only."""
    items: list[Item] = []
    for where, b in _blocks(data, name, ITEM_KEYS, LISTS[name]):
        try:
            item = parse_item(b)
            if item.kind == TILE and item.name in tiles:
                raise ConfigError("tile %s is already in another spot" % item.name)
            if item.kind in (APP, PAGE_ITEM) and any((o.kind, o.name) == (item.kind, item.name) for o in items):
                raise ConfigError(
                    "%s %s is already on the %s" % (item.kind, item.name, "bar" if name == "bar" else "apps page")
                )
        except ConfigError as e:
            raise ConfigError("%s: %s" % (where, e))
        if item.kind == TILE:
            tiles.add(item.name)
        items.append(item)
    return items


def parse_config(text):
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(str(e))
    return parse_data(data)


def parse_data(data):
    """A Config from the file's parsed TOML."""
    old = [n for n in OLD_LISTS if n in data]
    if old:
        raise ConfigError(
            "%s blocks are from an earlier version: the bar's items are [[bar]] blocks now, in order "
            "(see Configs from earlier versions in configuration.md)" % " and ".join("[[%s]]" % n for n in old)
        )
    unknown = set(data) - {"height", "notifications", "screensaver", "pager"} - set(LISTS)
    if unknown:
        raise ConfigError("unknown setting(s): %s" % ", ".join(sorted(unknown)))
    height = _int(data.get("height", DEFAULT_HEIGHT), "height")
    if not MIN_HEIGHT <= height <= MAX_HEIGHT:
        raise ConfigError("height must be between %d and %d" % (MIN_HEIGHT, MAX_HEIGHT))
    tiles: set[str] = set()
    bar = _items(data, "bar", tiles)
    apps = _items(data, "apps", tiles)
    try:
        notifications = _notifications(data.get("notifications", {}))
    except ConfigError as e:
        raise ConfigError("notifications: %s" % e)
    return Config(height, bar, apps, notifications, _pages(data))


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


def _table(data, name, keys):
    table = data.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError("%s: must be a [%s] table" % (name, name))
    unknown = set(table) - keys
    if unknown:
        raise ConfigError("%s: unknown setting(s): %s" % (name, ", ".join(sorted(unknown))))
    return table


def _bool(table, name, key):
    value = table.get(key, True)
    if not isinstance(value, bool):
        raise ConfigError("%s: %s must be true or false" % (name, key))
    return value


def _pages(data):
    saver = _table(data, "screensaver", {"clock"})
    pager = _table(data, "pager", {"return_after"})
    after = pager.get("return_after", RETURN_AFTER)
    if isinstance(after, bool) or not isinstance(after, int) or not 1 <= after <= MAX_RETURN_AFTER:
        raise ConfigError("pager: return_after must be a whole number of seconds, 1 to %d" % MAX_RETURN_AFTER)
    return Pages(_bool(saver, "screensaver", "clock"), after)


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
