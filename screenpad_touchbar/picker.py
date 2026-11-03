"""The picker: what a + on a free spot of the bar or of the apps page's home list adds there.

A tap on the + opens the picker on the apps page (Surface draws it). It
offers, in this order: "Custom shortcut...", while the extension draws it
(a window on the main screen to capture a key combination or type a
command, which writes the item with 'screenpad-touchbar shortcut
add-key' or 'add-command'); the presets, keys GNOME and most apps know;
and the dock's favourite apps that place does not have yet. Tapping one
adds it at the end of that place's items, written in the config like
every edit (Daemon.add_item(), config_edit.with_items()), and closes the
picker; Back closes it with nothing added. The favourites are read once
when the picker opens, never polled.
"""

import ast
import subprocess

from .common import APP, MAX_COLUMNS, MAX_ROWS, ConfigError
from .config import LISTS
from .items import Item, app_id, parse_item

# label, icon (in the Adwaita theme), key: tap items.
PRESETS = (
    ("Screenshot", "applets-screenshooter-symbolic", "printscreen"),
    ("Mute", "audio-volume-muted-symbolic", "mute"),
    ("Mute microphone", "microphone-sensitivity-muted-symbolic", "micmute"),
    ("Play or pause", "media-playback-start-symbolic", "playpause"),
    ("Next track", "media-skip-forward-symbolic", "nextsong"),
    ("Previous track", "media-skip-backward-symbolic", "previoussong"),
    ("Volume up", "audio-volume-high-symbolic", "volumeup"),
    ("Volume down", "audio-volume-low-symbolic", "volumedown"),
    ("Lock screen", "system-lock-screen-symbolic", "super+l"),
    ("Copy", "edit-copy-symbolic", "ctrl+c"),
    ("Paste", "edit-paste-symbolic", "ctrl+v"),
    ("Show desktop", "user-desktop-symbolic", "super+d"),
    ("Workspace right", "go-next-symbolic", "super+pagedown"),
    ("Workspace left", "go-previous-symbolic", "super+pageup"),
)
CUSTOM_LOOK = {"icon": "document-edit-symbolic", "label": "Custom shortcut…"}
TITLES = {"bar": "Add to the bar", "apps": "Add to the apps page"}
# What the picker shows at most: an app's page's grid.
MAX_CHOICES = MAX_COLUMNS * MAX_ROWS
FAVOURITES = ["gsettings", "get", "org.gnome.shell", "favorite-apps"]


def read_favourites():
    """The dock's favourite apps, as GNOME keeps them; [] if it cannot say."""
    try:
        out = subprocess.run(FAVOURITES, capture_output=True, text=True, timeout=2, check=True).stdout
        value = ast.literal_eval(out.strip().removeprefix("@as "))
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError):
        return []
    apps = []
    for v in value if isinstance(value, list) else []:
        try:
            apps.append(app_id(v))
        except ConfigError:
            continue  # an id the config would refuse is not offered
    return apps


class Picker:
    """Which place the picker adds to (place, None while closed), and what it offers.

    config() is the config shown; add(place, item) writes an item at the
    end of a place's items; custom(place) opens the extension's window
    for a custom shortcut, offered only while can_custom(); favourites()
    lists the dock's favourite apps.
    """

    def __init__(self, config, add, custom=None, can_custom=None, favourites=None):
        self.config, self.add = config, add
        self.custom = custom or (lambda place: None)
        self.can_custom = can_custom or (lambda: False)
        self.read_favourites = favourites or list
        self.place = None
        self.favourites: list[str] = []

    def room(self, place):
        """Whether place's list has room for another item."""
        return len(self.config().items(place)) < LISTS[place]

    def open(self, place):
        if self.room(place):
            self.place = place
            self.favourites = list(self.read_favourites())

    def close(self):
        self.place = None

    def title(self):
        return TITLES[self.place] if self.place else ""

    def choices(self):
        """What it offers: (look, item), item None for the custom shortcut."""
        if self.place is None:
            return []
        out: list[tuple[dict, Item | None]] = [(CUSTOM_LOOK, None)] if self.can_custom() else []
        for label, icon, key in PRESETS:
            out.append(({"icon": icon, "label": label}, parse_item({"label": label, "icon": icon, "key": key})))
        there = {i.name for i in self.config().items(self.place) if i.kind == APP}
        out += [({"app": a}, Item.app(a)) for a in self.favourites if a not in there]
        return out[:MAX_CHOICES]

    def choose(self, n):
        """The n-th choice tapped: add it (or open the custom window) and close."""
        place, choices = self.place, self.choices()
        self.close()
        if place is None or not 0 <= n < len(choices):
            return
        item = choices[n][1]
        if item is None:
            self.custom(place)
        else:
            self.add(place, item)
