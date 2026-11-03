"""Tests for keys, the config: parsing, tiles, writing in place and migration.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import tempfile
import unittest

from touchbar_support import REPO, SYN, TILE_CONFIG, ConfigHome, config

from screenpad_touchbar.cli import main
from screenpad_touchbar.codes import (
    EV_KEY,
)
from screenpad_touchbar.common import (
    DEFAULT_HEIGHT,
    ConfigError,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.config_edit import add_shortcut, edit_config, migrate_config, with_height, with_shortcuts
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
            [(b.label, b.icon, b.mode, b.keys) for b in c.buttons],
            [
                # Alt+K: Claude Code's "meta+k" push-to-talk binding.
                ("Push to talk", "audio-input-microphone-symbolic", "hold", (56, 37)),
                ("Mute microphone", "microphone-sensitivity-muted-symbolic", "tap", (248,)),
                ("Screenshot", "applets-screenshooter-symbolic", "tap", (99,)),
            ],
        )
        self.assertEqual(c.shortcuts, [])

    def test_default_icons_are_installed(self):
        # The icons the default config names, in GNOME's own theme.
        for b in config(EXAMPLE_CONFIG).buttons:
            found = [
                os.path.join(root, f)
                for root, _, files in os.walk("/usr/share/icons/Adwaita")
                for f in files
                if f.startswith(b.icon + ".")
            ]
            if not os.path.isdir("/usr/share/icons/Adwaita"):
                self.skipTest("no Adwaita icon theme here")
            self.assertTrue(found, b.icon)

    def test_empty_is_a_bar_without_buttons(self):
        c = config()
        self.assertEqual((c.height, c.buttons, c.shortcuts), (DEFAULT_HEIGHT, [], []))
        self.assertEqual([s.kind for s in c.spots], ["button"] * 3 + ["shortcut"] * 3 + ["grip"])
        self.assertTrue(all(s.item is None for s in c.spots))

    def test_spots(self):
        c = config()
        spots = [(s.kind, s.index, s.x, s.width) for s in c.spots]
        self.assertEqual(
            spots,
            [
                ("button", 0, 0, 296),
                ("button", 1, 296, 296),
                ("button", 2, 592, 296),
                ("shortcut", 0, 928, 296),
                ("shortcut", 1, 1224, 296),
                ("shortcut", 2, 1520, 296),
                ("grip", 0, 1816, 104),
            ],
        )
        self.assertEqual(c.spot_at(0, 0), 0)
        self.assertEqual(c.spot_at(295, 159), 0)
        self.assertEqual(c.spot_at(296, 10), 1)
        self.assertIsNone(c.spot_at(900, 10))  # the gap between the groups
        self.assertEqual(c.spot_at(928, 10), 3)
        self.assertEqual(c.spot_at(1919, 10), 6)
        self.assertIsNone(c.spot_at(100, 160))  # below the bar

    def test_buttons_and_shortcuts(self):
        c = config("""
height = 200
[[button]]
label = "Term"
command = ["gnome-terminal", "--"]
[[button]]
icon = "~/icons/ptt.svg"
mode = "hold"
command = "pactl set-source-mute @DEFAULT_SOURCE@ 0"
release_command = "pactl set-source-mute @DEFAULT_SOURCE@ 1"
[[shortcut]]
app = "org.gnome.Nautilus.desktop"
[[shortcut]]
app = "firefox_firefox"
""")
        term, ptt = c.buttons
        self.assertEqual((term.mode, term.command, term.keys, term.icon), ("tap", ["gnome-terminal", "--"], None, None))
        self.assertEqual(ptt.icon, os.path.expanduser("~/icons/ptt.svg"))
        self.assertEqual(ptt.command, ["/bin/sh", "-c", "pactl set-source-mute @DEFAULT_SOURCE@ 0"])
        self.assertEqual(ptt.release_command[2], "pactl set-source-mute @DEFAULT_SOURCE@ 1")
        self.assertEqual(c.shortcuts, ["org.gnome.Nautilus.desktop", "firefox_firefox.desktop"])
        self.assertEqual(
            [s.item for s in c.spots if s.kind == "shortcut"],
            ["org.gnome.Nautilus.desktop", "firefox_firefox.desktop", None],
        )
        self.assertEqual(
            c.spots[1].public(),
            {
                "kind": "button",
                "x": 296,
                "width": 296,
                "label": "",
                "icon": os.path.expanduser("~/icons/ptt.svg"),
                "mode": "hold",
            },
        )
        self.assertEqual(
            c.spots[3].public(), {"kind": "shortcut", "x": 928, "width": 296, "app": "org.gnome.Nautilus.desktop"}
        )

    def test_first_version_x_and_width_are_ignored(self):
        c = config('[[button]]\nlabel = "PTT"\nx = 0\nwidth = 480\nmode = "hold"\nkey = "F14"\n')
        self.assertEqual((c.buttons[0].label, c.spots[0].item), ("PTT", c.buttons[0]))

    def test_errors(self):
        button = '[[button]]\nlabel = "A"\nkey = "a"\n'
        bad = {
            "height = 0": "height",
            "height = 63": "height",
            "height = 541": "height",
            "height = 1.5": "whole number",
            "colour = 1": "unknown setting",
            '[[button]]\nkey = "a"\n': "icon or a label",
            '[[button]]\nlabel = "A"\n': "exactly one of key, command or tile",
            button + 'tile = "x"\n': "exactly one",
            '[[button]]\nlabel = "A"\ntile = "Agents"\n': "tile must be a name",
            '[[button]]\nlabel = "A"\ntile = 3\n': "tile must be a name",
            '[[button]]\nlabel = "A"\ntile = "a"\nmode = "hold"\n': "mode does not apply",
            '[[button]]\nlabel = "A"\ntile = "a"\n[[button]]\nlabel = "B"\ntile = "a"\n': "already in another spot",
            '[[button]]\nlabel = "A"\nkey = "a"\ncommand = "b"\n': "exactly one",
            button + 'mode = "toggle"\n': "mode",
            '[[button]]\nlabel = "A"\nkey = "nope"\n': "unknown key",
            button + "size = 2\n": "unknown setting",
            '[[button]]\nlabel = "A"\ncommand = []\n': "non-empty list",
            button + 'release_command = "b"\n': "release_command",
            button + 'icon = "icons/a.svg"\n': "must start with",
            button + 'icon = ""\n': "icon must be",
            button * 4: "at most 3",
            "[[shortcut]]\n": "desktop file id",
            '[[shortcut]]\napp = "a b"\n': "desktop file id",
            '[[shortcut]]\napp = "../x.desktop"\n': "desktop file id",
            '[[shortcut]]\napp = "a"\nicon = "x"\n': "unknown setting",
            '[[shortcut]]\napp = "a"\n[[shortcut]]\napp = "a.desktop"\n': "already",
            '[[shortcut]]\napp = "a"\n'
            '[[shortcut]]\napp = "b"\n'
            '[[shortcut]]\napp = "c"\n'
            '[[shortcut]]\napp = "d"\n': "at most 3",
            "shortcut = 1": "[[shortcut]] blocks",
            "height = ": "",
        }
        for text, what in bad.items():
            with self.assertRaises(ConfigError, msg=text) as cm:
                config(text)
            self.assertIn(what, str(cm.exception), text)

    def test_error_names_the_button(self):
        with self.assertRaises(ConfigError) as cm:
            config('[[button]]\nlabel = "PTT"\nkey = "nope"\n')
        self.assertIn("button 1 (PTT)", str(cm.exception))

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
    def test_tile_buttons(self):
        c = config(TILE_CONFIG)
        self.assertEqual(c.tiles(), ["agents"])
        self.assertEqual((c.buttons[1].tile, c.buttons[1].keys, c.buttons[1].command), ("agents", None, None))

    def test_check_lists_a_tile(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(TILE_CONFIG)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["check"]), 0)
            self.assertIn("button   2  x  296..592  Agents, icon none, tile agents (an app draws it)", out.getvalue())

    def test_migrate_the_default_before_tiles(self):
        with open(os.path.join(REPO, "tests", "touchbar-v3-default.toml")) as f:
            v3 = f.read()
        self.assertNotIn("tile", v3)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v3)
            self.assertTrue(migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), EXAMPLE_CONFIG)
            self.assertFalse(migrate_config())
            # An edited one keeps the edits and gains the 'tile' lines once.
            with open(path, "w") as f:
                f.write(v3.replace("height = 160", "height = 168") + '\n[[shortcut]]\napp = "bitwarden.desktop"\n')
            self.assertTrue(migrate_config())
            self.assertFalse(migrate_config())
            c = load_config()[0]
            self.assertEqual((c.height, c.shortcuts), (168, ["bitwarden.desktop"]))
            with open(path) as f:
                self.assertEqual(f.read().count("#   tile "), 1)


class ConfigEditTests(unittest.TestCase):
    def test_with_height_edits_only_that_line(self):
        text = '# top\nheight = 160  # mine\n\n[[button]]\nlabel = "A"\nkey = "a"\n'
        self.assertEqual(with_height(text, 240), text.replace("160  # mine", "240"))
        # No height line yet: one goes in before the first table.
        out = with_height('# top\n[[button]]\nlabel = "A"\nkey = "a"\n', 96)
        self.assertEqual(out, '# top\nheight = 96\n\n[[button]]\nlabel = "A"\nkey = "a"\n')
        self.assertEqual(with_height("", 96), "height = 96\n\n")
        # A height inside a table is not the bar's.
        self.assertEqual(config(with_height('[[shortcut]]\napp = "a"\n', 200)).height, 200)

    def test_with_shortcuts_keeps_everything_else(self):
        text = EXAMPLE_CONFIG
        added = with_shortcuts(text, ["a.desktop", "b.desktop"])
        self.assertTrue(added.startswith(text))
        self.assertEqual(config(added).shortcuts, ["a.desktop", "b.desktop"])
        removed = with_shortcuts(added, [])
        self.assertEqual(removed, text)
        # Repeated edits do not pile up blank lines.
        again = with_shortcuts(with_shortcuts(added, ["b.desktop"]), ["a.desktop", "b.desktop"])
        self.assertEqual(again, added)

    def test_with_shortcuts_in_the_middle(self):
        text = (
            'height = 100\n[[shortcut]]\napp = "a.desktop"  # files\n\n# Buttons:\n[[button]]\nlabel = "A"\nkey = "a"\n'
        )
        out = with_shortcuts(text, ["c.desktop"])
        self.assertEqual(
            out, 'height = 100\n\n# Buttons:\n[[button]]\nlabel = "A"\nkey = "a"\n\n[[shortcut]]\napp = "c.desktop"\n'
        )

    def test_add_shortcut(self):
        self.assertEqual(add_shortcut([], "a"), ["a"])
        self.assertEqual(add_shortcut(["a"], "b"), ["a", "b"])
        self.assertEqual(add_shortcut(["a", "b"], "c", 0), ["c", "a", "b"])
        self.assertEqual(add_shortcut(["a"], "c", 2), ["a", "c"])
        # Full: dropping on a spot replaces it; without a spot there is no room.
        self.assertEqual(add_shortcut(["a", "b", "c"], "d", 1), ["a", "d", "c"])
        with self.assertRaises(ConfigError):
            add_shortcut(["a", "b", "c"], "d")
        # One already on the bar moves.
        self.assertEqual(add_shortcut(["a", "b", "c"], "a", 2), ["b", "c", "a"])
        self.assertEqual(add_shortcut(["a", "b"], "a"), ["b", "a"])
        with self.assertRaises(ConfigError):
            add_shortcut([], "a", 3)

    def test_edit_config(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            # No file yet: it starts from the default.
            c = edit_config(lambda c: (c.height, ["a.desktop"]))
            self.assertEqual(c.shortcuts, ["a.desktop"])
            with open(path) as f:
                text = f.read()
            self.assertTrue(text.startswith(EXAMPLE_CONFIG))
            c = edit_config(lambda c: (320, c.shortcuts))
            self.assertEqual((c.height, c.shortcuts), (320, ["a.desktop"]))
            self.assertEqual(load_config()[0].height, 320)
            # A broken file is not touched.
            with open(path, "w") as f:
                f.write("height = 'x'\n")
            with self.assertRaises(ConfigError) as cm:
                edit_config(lambda c: (200, c.shortcuts))
            self.assertIn("fix this first", str(cm.exception))
            with open(path) as f:
                self.assertEqual(f.read(), "height = 'x'\n")

    def test_edit_config_refuses_an_unsafe_edit(self):
        # A [shortcut]-looking line inside a multi-line string would be cut.
        text = 'height = 100\n[[button]]\nlabel = """\n[[shortcut]]\n"""\nkey = "a"\n'
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(text)
            with self.assertRaises(ConfigError):
                edit_config(lambda c: (c.height, ["a.desktop"]))
            with open(path) as f:
                self.assertEqual(f.read(), text)

    def test_migrate_the_first_default(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            self.assertFalse(migrate_config())
            os.makedirs(os.path.dirname(path))
            with open(os.path.join(REPO, "tests", "touchbar-v1-default.toml")) as f:
                v1 = f.read()
            with open(path, "w") as f:
                f.write(v1)
            self.assertTrue(migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), EXAMPLE_CONFIG)
            # A changed one is the user's and stays, but for the F14
            # push-to-talk block (see test_migrate_the_f14_push_to_talk).
            with open(path, "w") as f:
                f.write(v1.replace("height = 160", "height = 200"))
            migrate_config()
            self.assertEqual(load_config()[0].height, 200)

    def test_migrate_the_f14_push_to_talk(self):
        # The default the previous version wrote: F14, which terminals never
        # receive, becomes Alt+K, and the user's other edits stay.
        with open(os.path.join(REPO, "tests", "touchbar-v2-default.toml")) as f:
            v2 = f.read()
        self.assertIn('key = "F14"', v2)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v2)
            self.assertTrue(migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), EXAMPLE_CONFIG)
            self.assertFalse(migrate_config())

            edited = v2.replace("height = 160", "height = 240") + '\n[[shortcut]]\napp = "discord.desktop"\n'
            with open(path, "w") as f:
                f.write(edited)
            self.assertTrue(migrate_config())
            c = load_config()[0]
            self.assertEqual((c.height, c.shortcuts), (240, ["discord.desktop"]))
            self.assertEqual(c.buttons[0].keys, (56, 37))
            with open(path) as f:
                self.assertNotIn("F14", f.read())

            # A push-to-talk key the user chose stays.
            with open(path, "w") as f:
                f.write(v2.replace('key = "F14"', 'key = "ctrl+alt+p"'))
            migrate_config()
            self.assertEqual(load_config()[0].buttons[0].keys, (29, 56, 25))


if __name__ == "__main__":
    unittest.main()
