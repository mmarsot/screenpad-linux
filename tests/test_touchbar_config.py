"""Tests for keys and the config: its items of every kind on the bar and the apps page, tiles, loading.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import re
import tempfile
import unittest

from touchbar_support import SYN, TILE_CONFIG, ConfigHome, config

from screenpad_touchbar.cli import main
from screenpad_touchbar.codes import (
    EV_KEY,
)
from screenpad_touchbar.common import (
    DEFAULT_HEIGHT,
    PAGE_ICONS,
    ConfigError,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.defaults import EXAMPLE_CONFIG
from screenpad_touchbar.keys import KeyEmitter, parse_key


class KeyTests(unittest.TestCase):
    def test_names_and_combos(self):
        self.assertEqual(parse_key("F14"), (184,))
        self.assertEqual(parse_key("KEY_F14"), (184,))
        self.assertEqual(parse_key("ctrl+alt+t"), (29, 56, 20))
        self.assertEqual(parse_key(" Super + Space "), (125, 57))
        self.assertEqual(parse_key("micmute"), (248,))
        # alt is KEY_LEFTALT; meta and super are both KEY_LEFTMETA (Super).
        self.assertEqual(parse_key("alt+k"), (56, 37))
        self.assertEqual(parse_key("meta+k"), (125, 37))
        self.assertEqual(parse_key("30"), (30,))

    def test_bad_keys(self):
        for spec in ("", "  ", "ctrl+", "nosuchkey", "ctrl+ctrl", "0", "256", 14):
            with self.assertRaises(ConfigError, msg=repr(spec)):
                parse_key(spec)


class EmitterTests(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.k = KeyEmitter(self.frames.append)

    def test_press_release_order(self):
        self.k.press((29, 56, 20))
        self.k.release((29, 56, 20))
        self.assertEqual(
            self.frames,
            [
                [(EV_KEY, 29, 1), (EV_KEY, 56, 1), (EV_KEY, 20, 1), SYN],
                [(EV_KEY, 20, 0), (EV_KEY, 56, 0), (EV_KEY, 29, 0), SYN],
            ],
        )

    def test_shared_modifier_stays_down(self):
        self.k.press((29, 30))
        self.k.press((29, 31))
        self.k.release((29, 30))
        self.assertEqual(self.frames[-1], [(EV_KEY, 30, 0), SYN])
        self.k.release((29, 31))
        self.assertEqual(self.frames[-1], [(EV_KEY, 31, 0), (EV_KEY, 29, 0), SYN])

    def test_release_without_press_sends_nothing(self):
        self.k.release((30,))
        self.assertEqual(self.frames, [])

    def test_tap_and_release_all(self):
        self.k.tap((248,))
        self.assertEqual(self.frames, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])
        self.k.press((42, 184))
        self.k.release_all()
        self.assertEqual(self.frames[-1], [(EV_KEY, 42, 0), (EV_KEY, 184, 0), SYN])
        self.assertEqual(self.k.down, {})


class ConfigTests(unittest.TestCase):
    def test_builtin_default(self):
        c = config(EXAMPLE_CONFIG)
        self.assertEqual(c.height, DEFAULT_HEIGHT)
        self.assertEqual(
            [(b.kind, b.label, b.icon, b.mode, b.keys) for b in c.bar],
            [
                # Alt+K: Claude Code's "meta+k" push-to-talk binding.
                ("key", "Push to talk", "audio-input-microphone-symbolic", "hold", (56, 37)),
                ("key", "Mute microphone", "microphone-sensitivity-muted-symbolic", "tap", (248,)),
                ("key", "Screenshot", "applets-screenshooter-symbolic", "tap", (99,)),
            ],
        )
        self.assertEqual(c.apps, [])
        # Its commented examples are items too.
        examples = "\n".join(re.findall(r"(?m)^# (\[\[(?:bar|apps)\]\]|(?:app|page) = .*)$", EXAMPLE_CONFIG))
        self.assertEqual(
            [(i.kind, i.name) for i in config(examples).bar + config(examples).apps],
            [("app", "org.gnome.Nautilus.desktop"), ("app", "org.gnome.Settings.desktop"), ("page", "agents")],
        )

    def test_default_icons_are_installed(self):
        # The icons the default config and the page button name, in GNOME's
        # own theme.
        for icon in [b.icon for b in config(EXAMPLE_CONFIG).bar] + list(PAGE_ICONS):
            found = [
                os.path.join(root, f)
                for root, _, files in os.walk("/usr/share/icons/Adwaita")
                for f in files
                if f.startswith(icon + ".")
            ]
            if not os.path.isdir("/usr/share/icons/Adwaita"):
                self.skipTest("no Adwaita icon theme here")
            self.assertTrue(found, icon)

    def test_empty_is_a_bar_without_items(self):
        c = config()
        self.assertEqual((c.height, c.bar, c.apps), (DEFAULT_HEIGHT, [], []))
        self.assertEqual([s.kind for s in c.spots], ["item"] * 6 + ["grip", "page"])
        self.assertTrue(all(s.item is None for s in c.spots))

    def test_spots(self):
        c = config()
        spots = [(s.kind, s.index, s.x, s.width) for s in c.spots]
        self.assertEqual(
            spots,
            [
                ("item", 0, 0, 272),
                ("item", 1, 272, 272),
                ("item", 2, 544, 272),
                ("item", 3, 856, 272),
                ("item", 4, 1128, 272),
                ("item", 5, 1400, 272),
                ("grip", 0, 1816, 104),
                ("page", 0, 1672, 144),
            ],
        )
        self.assertEqual(c.spot_at(0, 0), 0)
        self.assertEqual(c.spot_at(271, 159), 0)
        self.assertEqual(c.spot_at(272, 10), 1)
        self.assertIsNone(c.spot_at(830, 10))  # the gap in the middle
        self.assertEqual(c.spot_at(856, 10), 3)
        self.assertEqual(c.spot_at(1672, 10), 7)
        self.assertEqual(c.spot_at(1815, 10), 7)
        self.assertEqual(c.spot_at(1919, 10), 6)
        self.assertIsNone(c.spot_at(100, 160))  # below the bar

    def test_items_of_every_kind_in_both_places(self):
        c = config("""
height = 200
[[bar]]
app = "org.gnome.Nautilus.desktop"
[[bar]]
label = "Term"
command = ["gnome-terminal", "--"]
[[bar]]
icon = "~/icons/ptt.svg"
mode = "hold"
command = "pactl set-source-mute @DEFAULT_SOURCE@ 0"
release_command = "pactl set-source-mute @DEFAULT_SOURCE@ 1"
[[bar]]
page = "agents"
[[apps]]
app = "firefox_firefox"
label = "Firefox"
[[apps]]
label = "Mute"
key = "micmute"
[[apps]]
tile = "calls"
[[apps]]
page = "agents"
icon = "x-symbolic"
""")
        files, term, ptt, page = c.bar
        self.assertEqual(
            (files.kind, files.name, files.mode, files.looks()), ("app", "org.gnome.Nautilus.desktop", None, {})
        )
        self.assertEqual(
            (term.kind, term.mode, term.command, term.keys, term.icon),
            ("command", "tap", ["gnome-terminal", "--"], None, None),
        )
        self.assertEqual(ptt.icon, os.path.expanduser("~/icons/ptt.svg"))
        self.assertEqual(ptt.command, ["/bin/sh", "-c", "pactl set-source-mute @DEFAULT_SOURCE@ 0"])
        self.assertEqual(ptt.release_command[2], "pactl set-source-mute @DEFAULT_SOURCE@ 1")
        self.assertEqual((page.kind, page.name, page.mode), ("page", "agents", None))
        self.assertEqual(
            [(i.kind, i.name, i.label) for i in c.apps],
            [
                ("app", "firefox_firefox.desktop", "Firefox"),
                ("key", None, "Mute"),
                ("tile", "calls", ""),
                ("page", "agents", ""),
            ],
        )
        self.assertEqual([s.item for s in c.spots[:6]], c.bar + [None, None])
        self.assertEqual(c.tiles(), ["calls"])
        self.assertEqual(c.items("apps"), c.apps)
        # Version 1's spots: apps are its shortcuts, the rest its buttons.
        self.assertEqual(
            c.spots[2].public(),
            {
                "kind": "button",
                "x": 544,
                "width": 272,
                "label": "",
                "icon": os.path.expanduser("~/icons/ptt.svg"),
                "mode": "hold",
            },
        )
        self.assertEqual(
            c.spots[0].public(), {"kind": "shortcut", "x": 0, "width": 272, "app": "org.gnome.Nautilus.desktop"}
        )
        self.assertEqual(
            c.spots[5].public(), {"kind": "button", "x": 1400, "width": 272, "label": None, "icon": None, "mode": None}
        )

    def test_errors(self):
        key = '[[bar]]\nlabel = "A"\nkey = "a"\n'
        bad = {
            "height = 0": "height",
            "height = 63": "height",
            "height = 541": "height",
            "height = 1.5": "whole number",
            "colour = 1": "unknown setting",
            '[[bar]]\nkey = "a"\n': "icon or a label",
            '[[apps]]\ncommand = "a"\n': "icon or a label",
            '[[bar]]\nlabel = "A"\n': "exactly one of key, command, app, tile or page",
            key + 'tile = "x"\n': "exactly one",
            '[[bar]]\nlabel = "A"\ntile = "Agents"\n': "tile must be a name",
            '[[bar]]\nlabel = "A"\ntile = 3\n': "tile must be a name",
            '[[bar]]\npage = "Agents"\n': "page must be a name",
            '[[bar]]\nlabel = "A"\ntile = "a"\nmode = "hold"\n': "mode does not apply to a tile",
            '[[bar]]\napp = "a"\nmode = "tap"\n': "mode does not apply to an app",
            '[[apps]]\npage = "a"\nmode = "tap"\n': "mode does not apply to a page",
            '[[bar]]\nlabel = "A"\ntile = "a"\n[[bar]]\nlabel = "B"\ntile = "a"\n': "already in another spot",
            # One tile in one place: its events do not say where.
            '[[bar]]\ntile = "a"\n[[apps]]\ntile = "a"\n': "already in another spot",
            '[[bar]]\nlabel = "A"\nkey = "a"\ncommand = "b"\n': "exactly one",
            key + 'mode = "toggle"\n': "mode",
            '[[bar]]\nlabel = "A"\nkey = "nope"\n': "unknown key",
            key + "size = 2\n": "unknown setting",
            '[[bar]]\nlabel = "A"\ncommand = []\n': "non-empty list",
            key + 'release_command = "b"\n': "release_command",
            '[[bar]]\napp = "a"\nrelease_command = "b"\n': "release_command",
            key + 'icon = "icons/a.svg"\n': "must start with",
            key + 'icon = ""\n': "icon must be",
            key * 7: "at most 6 [[bar]]",
            '[[apps]]\napp = "a"\n' * 9: "at most 8 [[apps]]",
            '[[bar]]\napp = ""\n': "desktop file id",
            '[[bar]]\napp = "a b"\n': "desktop file id",
            '[[apps]]\napp = "../x.desktop"\n': "desktop file id",
            '[[bar]]\napp = "a"\n[[bar]]\napp = "a.desktop"\n': "already on the bar",
            '[[apps]]\npage = "a"\n[[apps]]\npage = "a"\n': "already on the apps page",
            "bar = 1": "[[bar]] blocks",
            "apps = [1]": "must be a [[apps]] block",
            # An earlier version's: migrate.py converts them.
            '[[button]]\nlabel = "A"\nkey = "a"\n': "[[button]] blocks are from an earlier version",
            '[[shortcut]]\napp = "a"\n': "[[shortcut]] blocks are from an earlier version",
            "height = ": "",
        }
        for text, what in bad.items():
            with self.assertRaises(ConfigError, msg=text) as cm:
                config(text)
            self.assertIn(what, str(cm.exception), text)
        # The same app may be in both places.
        self.assertEqual(len(config('[[bar]]\napp = "a"\n[[apps]]\napp = "a"\n').apps), 1)

    def test_error_names_the_item(self):
        with self.assertRaises(ConfigError) as cm:
            config('[[bar]]\nlabel = "PTT"\nkey = "nope"\n')
        self.assertIn("bar item 1 (PTT)", str(cm.exception))
        with self.assertRaises(ConfigError) as cm:
            config('[[apps]]\napp = "a"\n[[apps]]\nkey = "a"\n')
        self.assertIn("apps item 2", str(cm.exception))

    def test_load_order(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            self.assertEqual(load_config()[1], None)
            os.makedirs(os.path.join(d, "screenpad"))
            with open(os.path.join(d, "screenpad", "touchbar.toml"), "w") as f:
                f.write("height = 99\n")
            c, path = load_config()
            self.assertEqual((c.height, path), (99, os.path.join(d, "screenpad", "touchbar.toml")))
            with open(path, "w") as f:
                f.write("height = 'x'\n")
            with self.assertRaises(ConfigError) as cm:
                load_config()
            self.assertIn(path, str(cm.exception))


class TileConfigTests(unittest.TestCase):
    def test_tile_items(self):
        c = config(TILE_CONFIG)
        self.assertEqual(c.tiles(), ["agents"])
        self.assertEqual(
            (c.bar[1].kind, c.bar[1].name, c.bar[1].keys, c.bar[1].command), ("tile", "agents", None, None)
        )

    def test_check_lists_a_tile(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(TILE_CONFIG)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["check"]), 0)
            self.assertIn("  spot  2  x  272..544  Agents, icon none, tile agents (an app draws it)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
