"""Tests for the touch bar daemon's logic; no root, no hardware, no uinput.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import re
import select
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

import touchbar as t  # noqa: E402

# The GDX1505 as it reports itself: 3840x2160 units, 32 units/mm, 5 slots.
AXES = t.Axes(0, 3840, 0, 2160)
SYN = (t.EV_SYN, t.SYN_REPORT, 0)


def config(text=""):
    return t.parse_config(text)


def touch(slot, id, x, y):
    """Events that put a contact down (or move it) in a slot, in device units."""
    ev = [(t.EV_ABS, t.ABS_MT_SLOT, slot)]
    if id is not None:
        ev.append((t.EV_ABS, t.ABS_MT_TRACKING_ID, id))
    return ev + [(t.EV_ABS, t.ABS_MT_POSITION_X, x), (t.EV_ABS, t.ABS_MT_POSITION_Y, y)]


def lift(slot):
    return [(t.EV_ABS, t.ABS_MT_SLOT, slot), (t.EV_ABS, t.ABS_MT_TRACKING_ID, -1)]


class Split:
    """A Splitter with a 160 px bar, fed whole frames."""

    def __init__(self, height=160):
        self.s = t.Splitter(AXES, 5, lambda px, py: py < height)

    def frame(self, *groups, left=None):
        events = [e for g in groups for e in g]
        if left is not None:
            events.append((t.EV_KEY, t.BTN_LEFT, int(left)))
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


class KeyTests(unittest.TestCase):
    def test_names_and_combos(self):
        self.assertEqual(t.parse_key("F14"), (184,))
        self.assertEqual(t.parse_key("KEY_F14"), (184,))
        self.assertEqual(t.parse_key("ctrl+alt+t"), (29, 56, 20))
        self.assertEqual(t.parse_key(" Super + Space "), (125, 57))
        self.assertEqual(t.parse_key("micmute"), (248,))
        # alt is KEY_LEFTALT; meta and super are both KEY_LEFTMETA (Super).
        self.assertEqual(t.parse_key("alt+k"), (56, 37))
        self.assertEqual(t.parse_key("meta+k"), (125, 37))
        self.assertEqual(t.parse_key("30"), (30,))

    def test_bad_keys(self):
        for spec in ("", "  ", "ctrl+", "nosuchkey", "ctrl+ctrl", "0", "256", 14):
            with self.assertRaises(t.ConfigError, msg=repr(spec)):
                t.parse_key(spec)


class EmitterTests(unittest.TestCase):
    def setUp(self):
        self.frames = []
        self.k = t.KeyEmitter(self.frames.append)

    def test_press_release_order(self):
        self.k.press((29, 56, 20))
        self.k.release((29, 56, 20))
        self.assertEqual(
            self.frames,
            [
                [(t.EV_KEY, 29, 1), (t.EV_KEY, 56, 1), (t.EV_KEY, 20, 1), SYN],
                [(t.EV_KEY, 20, 0), (t.EV_KEY, 56, 0), (t.EV_KEY, 29, 0), SYN],
            ],
        )

    def test_shared_modifier_stays_down(self):
        self.k.press((29, 30))
        self.k.press((29, 31))
        self.k.release((29, 30))
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 30, 0), SYN])
        self.k.release((29, 31))
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 31, 0), (t.EV_KEY, 29, 0), SYN])

    def test_release_without_press_sends_nothing(self):
        self.k.release((30,))
        self.assertEqual(self.frames, [])

    def test_tap_and_release_all(self):
        self.k.tap((248,))
        self.assertEqual(self.frames, [[(t.EV_KEY, 248, 1), SYN], [(t.EV_KEY, 248, 0), SYN]])
        self.k.press((42, 184))
        self.k.release_all()
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 42, 0), (t.EV_KEY, 184, 0), SYN])
        self.assertEqual(self.k.down, {})


class ConfigTests(unittest.TestCase):
    def test_builtin_default(self):
        c = config(t.EXAMPLE_CONFIG)
        self.assertEqual(c.height, t.DEFAULT_HEIGHT)
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
        for b in config(t.EXAMPLE_CONFIG).buttons:
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
        self.assertEqual((c.height, c.buttons, c.shortcuts), (t.DEFAULT_HEIGHT, [], []))
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
            with self.assertRaises(t.ConfigError, msg=text) as cm:
                config(text)
            self.assertIn(what, str(cm.exception), text)

    def test_error_names_the_button(self):
        with self.assertRaises(t.ConfigError) as cm:
            config('[[button]]\nlabel = "PTT"\nkey = "nope"\n')
        self.assertIn("button 1 (PTT)", str(cm.exception))

    def test_load_order(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            self.assertEqual(t.load_config()[1], None)
            os.makedirs(os.path.join(d, "screenpad"))
            with open(os.path.join(d, "screenpad", "touchbar.toml"), "w") as f:
                f.write("height = 99\n")
            c, path = t.load_config()
            self.assertEqual((c.height, path), (99, os.path.join(d, "screenpad", "touchbar.toml")))
            with open(path, "w") as f:
                f.write("height = 'x'\n")
            with self.assertRaises(t.ConfigError) as cm:
                t.load_config()
            self.assertIn(path, str(cm.exception))


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


class ConfigEditTests(unittest.TestCase):
    def test_with_height_edits_only_that_line(self):
        text = '# top\nheight = 160  # mine\n\n[[button]]\nlabel = "A"\nkey = "a"\n'
        self.assertEqual(t.with_height(text, 240), text.replace("160  # mine", "240"))
        # No height line yet: one goes in before the first table.
        out = t.with_height('# top\n[[button]]\nlabel = "A"\nkey = "a"\n', 96)
        self.assertEqual(out, '# top\nheight = 96\n\n[[button]]\nlabel = "A"\nkey = "a"\n')
        self.assertEqual(t.with_height("", 96), "height = 96\n\n")
        # A height inside a table is not the bar's.
        self.assertEqual(config(t.with_height('[[shortcut]]\napp = "a"\n', 200)).height, 200)

    def test_with_shortcuts_keeps_everything_else(self):
        text = t.EXAMPLE_CONFIG
        added = t.with_shortcuts(text, ["a.desktop", "b.desktop"])
        self.assertTrue(added.startswith(text))
        self.assertEqual(config(added).shortcuts, ["a.desktop", "b.desktop"])
        removed = t.with_shortcuts(added, [])
        self.assertEqual(removed, text)
        # Repeated edits do not pile up blank lines.
        again = t.with_shortcuts(t.with_shortcuts(added, ["b.desktop"]), ["a.desktop", "b.desktop"])
        self.assertEqual(again, added)

    def test_with_shortcuts_in_the_middle(self):
        text = (
            'height = 100\n[[shortcut]]\napp = "a.desktop"  # files\n\n# Buttons:\n[[button]]\nlabel = "A"\nkey = "a"\n'
        )
        out = t.with_shortcuts(text, ["c.desktop"])
        self.assertEqual(
            out, 'height = 100\n\n# Buttons:\n[[button]]\nlabel = "A"\nkey = "a"\n\n[[shortcut]]\napp = "c.desktop"\n'
        )

    def test_add_shortcut(self):
        self.assertEqual(t.add_shortcut([], "a"), ["a"])
        self.assertEqual(t.add_shortcut(["a"], "b"), ["a", "b"])
        self.assertEqual(t.add_shortcut(["a", "b"], "c", 0), ["c", "a", "b"])
        self.assertEqual(t.add_shortcut(["a"], "c", 2), ["a", "c"])
        # Full: dropping on a spot replaces it; without a spot there is no room.
        self.assertEqual(t.add_shortcut(["a", "b", "c"], "d", 1), ["a", "d", "c"])
        with self.assertRaises(t.ConfigError):
            t.add_shortcut(["a", "b", "c"], "d")
        # One already on the bar moves.
        self.assertEqual(t.add_shortcut(["a", "b", "c"], "a", 2), ["b", "c", "a"])
        self.assertEqual(t.add_shortcut(["a", "b"], "a"), ["b", "a"])
        with self.assertRaises(t.ConfigError):
            t.add_shortcut([], "a", 3)

    def test_edit_config(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            # No file yet: it starts from the default.
            c = t.edit_config(lambda c: (c.height, ["a.desktop"]))
            self.assertEqual(c.shortcuts, ["a.desktop"])
            with open(path) as f:
                text = f.read()
            self.assertTrue(text.startswith(t.EXAMPLE_CONFIG))
            c = t.edit_config(lambda c: (320, c.shortcuts))
            self.assertEqual((c.height, c.shortcuts), (320, ["a.desktop"]))
            self.assertEqual(t.load_config()[0].height, 320)
            # A broken file is not touched.
            with open(path, "w") as f:
                f.write("height = 'x'\n")
            with self.assertRaises(t.ConfigError) as cm:
                t.edit_config(lambda c: (200, c.shortcuts))
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
            with self.assertRaises(t.ConfigError):
                t.edit_config(lambda c: (c.height, ["a.desktop"]))
            with open(path) as f:
                self.assertEqual(f.read(), text)

    def test_migrate_the_first_default(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            self.assertFalse(t.migrate_config())
            os.makedirs(os.path.dirname(path))
            with open(os.path.join(REPO, "tests", "touchbar-v1-default.toml")) as f:
                v1 = f.read()
            with open(path, "w") as f:
                f.write(v1)
            self.assertTrue(t.migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), t.EXAMPLE_CONFIG)
            # A changed one is the user's and stays, but for the F14
            # push-to-talk block (see test_migrate_the_f14_push_to_talk).
            with open(path, "w") as f:
                f.write(v1.replace("height = 160", "height = 200"))
            t.migrate_config()
            self.assertEqual(t.load_config()[0].height, 200)

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
            self.assertTrue(t.migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), t.EXAMPLE_CONFIG)
            self.assertFalse(t.migrate_config())

            edited = v2.replace("height = 160", "height = 240") + '\n[[shortcut]]\napp = "discord.desktop"\n'
            with open(path, "w") as f:
                f.write(edited)
            self.assertTrue(t.migrate_config())
            c = t.load_config()[0]
            self.assertEqual((c.height, c.shortcuts), (240, ["discord.desktop"]))
            self.assertEqual(c.buttons[0].keys, (56, 37))
            with open(path) as f:
                self.assertNotIn("F14", f.read())

            # A push-to-talk key the user chose stays.
            with open(path, "w") as f:
                f.write(v2.replace('key = "F14"', 'key = "ctrl+alt+p"'))
            t.migrate_config()
            self.assertEqual(t.load_config()[0].buttons[0].keys, (29, 56, 25))


class SplitTests(unittest.TestCase):
    def test_axes(self):
        self.assertEqual(AXES.panel(0, 0), (0, 0))
        self.assertEqual(AXES.panel(3840, 2160), (1920, 1080))
        self.assertEqual(AXES.panel(640, 320), (320, 160))

    def test_pad_contact_passes_through(self):
        sp = Split()
        pad, bar = sp.frame(touch(0, 7, 1000, 1000))
        self.assertEqual(bar, [])
        self.assertEqual(
            pad,
            [
                (t.EV_ABS, t.ABS_MT_SLOT, 0),
                (t.EV_ABS, t.ABS_MT_TRACKING_ID, 7),
                (t.EV_ABS, t.ABS_MT_TOOL_TYPE, 0),
                (t.EV_ABS, t.ABS_MT_POSITION_X, 1000),
                (t.EV_ABS, t.ABS_MT_POSITION_Y, 1000),
                (t.EV_KEY, t.BTN_TOUCH, 1),
                (t.EV_KEY, t.BTN_TOOL_FINGER, 1),
                (t.EV_ABS, t.ABS_X, 1000),
                (t.EV_ABS, t.ABS_Y, 1000),
                SYN,
            ],
        )
        pad, _ = sp.frame(touch(0, None, 1010, 990))
        self.assertEqual(
            pad,
            [
                (t.EV_ABS, t.ABS_MT_SLOT, 0),
                (t.EV_ABS, t.ABS_MT_POSITION_X, 1010),
                (t.EV_ABS, t.ABS_MT_POSITION_Y, 990),
                (t.EV_ABS, t.ABS_X, 1010),
                (t.EV_ABS, t.ABS_Y, 990),
                SYN,
            ],
        )
        pad, _ = sp.frame(lift(0))
        self.assertEqual(
            pad,
            [
                (t.EV_ABS, t.ABS_MT_SLOT, 0),
                (t.EV_ABS, t.ABS_MT_TRACKING_ID, -1),
                (t.EV_KEY, t.BTN_TOUCH, 0),
                (t.EV_KEY, t.BTN_TOOL_FINGER, 0),
                SYN,
            ],
        )

    def test_bar_contact_never_reaches_the_pad(self):
        sp = Split()
        # Starts in the bar (panel y 100 < 160), then slides well into the pad.
        pad, bar = sp.frame(touch(0, 1, 400, 200))
        self.assertEqual((pad, bar), ([], [("begin", 0, 200, 100)]))
        pad, bar = sp.frame(touch(0, None, 400, 1500))
        self.assertEqual((pad, bar), ([], [("move", 0, 200, 750)]))
        pad, bar = sp.frame(lift(0))
        self.assertEqual((pad, bar), ([], [("end", 0, 200, 750)]))

    def test_pad_contact_sliding_into_the_bar_stays_a_pad_contact(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        pad, bar = sp.frame(touch(0, None, 1000, 100))
        self.assertEqual(bar, [])
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_POSITION_Y), [100])
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), [])

    def test_boundary(self):
        sp = Split()
        _, bar = sp.frame(touch(0, 1, 100, 319))  # panel y 159.5: bar
        self.assertEqual([e[0] for e in bar], ["begin"])
        pad, bar = sp.frame(touch(1, 2, 2000, 320))  # panel y 160: pad
        self.assertEqual(bar, [])
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), [2])

    def test_bar_finger_does_not_change_the_finger_count(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        pad, bar = sp.frame(touch(1, 2, 400, 100))
        self.assertEqual(pad, [])
        self.assertEqual(bar[0][0], "begin")
        # A second pad finger makes it a two-finger gesture on the virtual pad.
        pad, _ = sp.frame(touch(2, 3, 2000, 1000))
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_TOOL_DOUBLETAP), [1])
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_TOOL_FINGER), [0])
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_TOUCH), [])

    def test_click_only_counts_with_a_pad_finger(self):
        sp = Split()
        sp.frame(touch(0, 1, 400, 100))
        pad, _ = sp.frame(left=True)
        self.assertEqual(pad, [])
        pad, _ = sp.frame(left=False)
        self.assertEqual(pad, [])
        sp.frame(lift(0))
        sp.frame(touch(0, 2, 1000, 1000))
        pad, _ = sp.frame(left=True)
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_LEFT), [1])
        pad, _ = sp.frame(left=False)
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_LEFT), [0])

    def test_palm_in_the_bar_is_ignored(self):
        sp = Split()
        _, bar = sp.frame(touch(0, 1, 400, 100) + [(t.EV_ABS, t.ABS_MT_TOOL_TYPE, t.MT_TOOL_PALM)])
        self.assertEqual(bar, [])
        sp.frame([(t.EV_ABS, t.ABS_MT_TOOL_TYPE, 0)])
        _, bar = sp.frame(touch(1, 2, 800, 100))
        self.assertEqual(bar[0][0], "begin")
        _, bar = sp.frame([(t.EV_ABS, t.ABS_MT_TOOL_TYPE, t.MT_TOOL_PALM)])
        self.assertEqual(bar, [("cancel", 1, 400, 50)])

    def test_slot_reused_by_a_new_contact(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        # The pad lifts and puts a new finger down in one frame: the virtual
        # pad sees the old contact end in a frame of its own.
        pad, _ = sp.frame(touch(0, 2, 1200, 1200))
        first = pad[: pad.index(SYN) + 1]
        self.assertIn((t.EV_ABS, t.ABS_MT_TRACKING_ID, -1), first)
        self.assertIn((t.EV_ABS, t.ABS_MT_TRACKING_ID, 2), pad[len(first) :])

    def test_dropped_events_resync(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        self.assertIsNone(sp.s.feed(t.EV_SYN, t.SYN_DROPPED, 0))
        self.assertIsNone(sp.s.feed(t.EV_ABS, t.ABS_MT_POSITION_X, 5))
        self.assertEqual(sp.s.feed(*SYN), t.RESYNC)
        ids, xs, ys = [-1, 9, -1, -1, -1], [0, 400, 0, 0, 0], [0, 100, 0, 0, 0]
        pad, bar = sp.s.resync(ids, xs, ys, [0] * 5, 0, 1)
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(bar, [("begin", 1, 200, 50)])

    def test_release_all(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000), left=True)
        sp.frame(touch(1, 2, 400, 100))
        pad, bar = sp.s.release_all()
        self.assertEqual(bar, [("cancel", 1, 200, 50)])
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_TOUCH), [0])
        self.assertEqual(codes(pad, t.EV_KEY, t.BTN_LEFT), [0])
        # Nothing more comes out for the contacts still on the pad.
        pad, bar = sp.frame(touch(0, None, 1100, 1000), touch(1, None, 500, 100))
        self.assertEqual((pad, bar), ([], []))


class BarTests(unittest.TestCase):
    # Spots: buttons at x 0, 296, 592; shortcuts at 928, 1224, 1520; grip 1816.
    CONFIG = """
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

    def setUp(self):
        self.frames, self.ran, self.launched, self.removed, self.resized = [], [], [], [], []
        self.now = 100.0
        self.bar = self.make(config(self.CONFIG))

    def make(self, c):
        def remove(i):
            self.removed.append(i)
            return True

        def resize(h):
            self.resized.append(h)
            return True

        return t.Bar(
            c,
            t.KeyEmitter(self.frames.append),
            self.ran.append,
            self.launched.append,
            remove,
            resize,
            clock=lambda: self.now,
        )

    def test_hold_sends_down_on_touch_and_up_on_release(self):
        self.assertTrue(self.bar.handle([("begin", 0, 100, 50)]))
        self.assertEqual(self.frames, [[(t.EV_KEY, 184, 1), SYN]])
        self.assertEqual(self.bar.pressed(), [0])
        # Sliding off a hold button keeps it held until the finger lifts.
        self.bar.handle([("move", 0, 900, 600)])
        self.assertEqual(len(self.frames), 1)
        self.bar.handle([("end", 0, 900, 600)])
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 184, 0), SYN])
        self.assertEqual(self.bar.pressed(), [])

    def test_tap_fires_on_lift_inside(self):
        self.bar.handle([("begin", 1, 400, 50)])
        self.assertEqual(self.frames, [])
        self.assertEqual(self.bar.pressed(), [1])
        self.bar.handle([("end", 1, 410, 60)])
        self.assertEqual(self.frames, [[(t.EV_KEY, 248, 1), SYN], [(t.EV_KEY, 248, 0), SYN]])

    def test_tap_cancelled_by_sliding_away(self):
        self.bar.handle([("begin", 1, 400, 50)])
        self.assertTrue(self.bar.handle([("move", 1, 400, 500)]))
        self.assertEqual(self.bar.pressed(), [])
        self.bar.handle([("end", 1, 400, 500)])
        self.assertEqual(self.frames, [])

    def test_tap_cancel_event(self):
        self.bar.handle([("begin", 1, 400, 50)])
        self.bar.handle([("cancel", 1, 400, 50)])
        self.assertEqual(self.frames, [])

    def test_commands(self):
        self.bar.handle([("begin", 2, 700, 50)])
        self.assertEqual(self.ran, [["/bin/sh", "-c", "a"]])
        self.bar.handle([("cancel", 2, 700, 50)])
        self.assertEqual(self.ran[-1], ["/bin/sh", "-c", "b"])

    def test_gaps_and_empty_spots_do_nothing(self):
        for x in (900, 1600):  # the gap; the third shortcut spot, empty
            self.assertFalse(self.bar.handle([("begin", 0, x, 50), ("end", 0, x, 50)]))
        self.assertEqual((self.frames, self.ran, self.launched), ([], [], []))

    def test_shortcut_tap_launches(self):
        self.bar.handle([("begin", 0, 1000, 50)])
        self.assertEqual(self.bar.pressed(), [3])
        self.now += 0.3
        self.assertFalse(self.bar.tick())
        self.bar.handle([("end", 0, 1010, 40)])
        self.assertEqual((self.launched, self.removed), (["files.desktop"], []))

    def test_shortcut_long_press_removes(self):
        self.bar.handle([("begin", 0, 1300, 50)])
        self.assertEqual(self.bar.deadline(), 100.0 + t.LONG_PRESS)
        self.now += t.LONG_PRESS
        self.assertTrue(self.bar.tick())
        self.assertEqual(self.bar.removing(), [4])
        self.assertIsNone(self.bar.deadline())
        self.assertFalse(self.bar.tick())
        self.bar.handle([("end", 0, 1300, 50)])
        self.assertEqual((self.launched, self.removed), ([], [1]))

    def test_shortcut_slide_away_cancels(self):
        self.bar.handle([("begin", 0, 1000, 50)])
        self.now += 2
        self.bar.tick()
        self.bar.handle([("move", 0, 1000, 600)])
        self.assertEqual((self.bar.pressed(), self.bar.removing()), ([], []))
        self.bar.handle([("end", 0, 1000, 600)])
        self.assertEqual((self.launched, self.removed), ([], []))

    def test_grip_resizes(self):
        self.assertEqual(self.bar.height(), 160)
        self.bar.handle([("begin", 0, 1850, 100)])
        self.assertEqual(self.bar.pressed(), [6])
        # Steps of HEIGHT_STEP, within MIN_HEIGHT..MAX_HEIGHT.
        self.assertTrue(self.bar.handle([("move", 0, 1850, 203)]))
        self.assertEqual(self.bar.height(), 264)
        self.assertFalse(self.bar.handle([("move", 0, 1850, 205)]))
        self.bar.handle([("move", 0, 1850, 1079)])
        self.assertEqual(self.bar.height(), t.MAX_HEIGHT)
        self.bar.handle([("move", 0, 1850, -500)])
        self.assertEqual(self.bar.height(), t.MIN_HEIGHT)
        # A second finger on the grip is ignored.
        self.assertFalse(self.bar.handle([("begin", 1, 1850, 50)]))
        self.bar.handle([("move", 0, 1850, 180)])
        self.bar.handle([("end", 0, 1850, 180)])
        self.assertEqual(self.resized, [240])
        # It shows the new height until the reloaded config has it.
        self.assertEqual(self.bar.height(), 240)
        taller = self.CONFIG.replace("[[button]]", "height = 240\n[[button]]", 1)
        self.bar.set_config(config(taller))
        self.assertEqual(self.bar.height(), 240)
        self.assertEqual(self.frames + self.ran, [])

    def test_grip_without_change_or_cancelled(self):
        self.bar.handle([("begin", 0, 1850, 100)])
        self.bar.handle([("move", 0, 1850, 102)])
        self.bar.handle([("end", 0, 1850, 102)])
        self.bar.handle([("begin", 0, 1850, 100), ("move", 0, 1850, 300), ("cancel", 0, 1850, 300)])
        self.assertEqual((self.resized, self.bar.height()), ([], 160))

    def test_reload_keeps_a_held_button_held(self):
        self.bar.handle([("begin", 0, 100, 50), ("begin", 1, 1000, 50), ("begin", 2, 700, 50)])
        self.frames.clear()
        # Push to talk is unchanged; the Term button's command changed.
        self.bar.set_config(config(self.CONFIG.replace('command = "a"', 'command = "c"')))
        self.assertEqual(self.frames, [])
        self.assertEqual(self.ran[-1], ["/bin/sh", "-c", "b"])
        self.assertEqual(self.bar.pressed(), [0])
        self.bar.handle([("end", 0, 100, 50), ("end", 1, 1000, 50)])
        self.assertEqual(self.frames, [[(t.EV_KEY, 184, 0), SYN]])
        self.assertEqual(self.launched, [])

    def test_release_all(self):
        self.bar.handle([("begin", 0, 100, 50), ("begin", 2, 700, 50), ("begin", 3, 1850, 50), ("move", 3, 1850, 300)])
        self.bar.release_all()
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 184, 0), SYN])
        self.assertEqual(self.ran[-1], ["/bin/sh", "-c", "b"])
        self.assertEqual((self.bar.pressed(), self.bar.height(), self.resized), ([], 160, []))


class ShortcutCommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = t.main(["shortcut"] + list(argv))
        return code, out.getvalue()

    def test_add_list_remove(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            self.assertEqual(self.run_cli("add", "org.gnome.Nautilus")[0], 0)
            self.assertEqual(self.run_cli("add", "b.desktop", "--spot", "1")[0], 0)
            _code, out = self.run_cli("list")
            self.assertEqual(out, "1  b.desktop\n2  org.gnome.Nautilus.desktop\n3  (empty)\n")
            self.assertEqual(self.run_cli("remove", "1")[0], 0)
            self.assertEqual(t.load_config(path)[0].shortcuts, ["org.gnome.Nautilus.desktop"])
            self.assertEqual(self.run_cli("remove", "3")[0], 2)
            self.assertEqual(self.run_cli("add", "bad name")[0], 2)
            self.assertEqual(self.run_cli("add", "c", "--spot", "4")[0], 2)


class StatusTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "root reads any file")
    def test_unreadable_touchpad_names_the_group(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            node = os.path.join(d, "event5")
            open(node, "w").close()
            os.chmod(node, 0)
            out = io.StringIO()
            done = mock.Mock(stdout="active\n")
            with (
                mock.patch.object(t, "find_source", return_value=node),
                mock.patch.object(t.subprocess, "run", return_value=done),
                contextlib.redirect_stdout(out),
            ):
                self.assertEqual(t.main(["status"]), 0)
            self.assertIn("touchpad: %s (not readable: add your user to group input" % node, out.getvalue())


class SourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys", "class", "input")
        os.makedirs(self.sys)

    def add(self, event, name, vendor, product, virtual=False):
        kind = "virtual/input" if virtual else "platform/i2c/0018:27C6:01F1.0001/input"
        dev = os.path.join(self.tmp.name, "sys", "devices", kind, "input%s" % event)
        os.makedirs(os.path.join(dev, "id"))
        for rel, value in (("name", name), ("id/vendor", vendor), ("id/product", product)):
            with open(os.path.join(dev, rel), "w") as f:
                f.write(value + "\n")
        os.makedirs(os.path.join(self.sys, "event%s" % event))
        os.symlink(dev, os.path.join(self.sys, "event%s" % event, "device"))

    def test_finds_only_the_real_touchpad(self):
        self.add(4, "GDX1505:00 27C6:01F1 Mouse", "27c6", "01f1")
        self.add(3, "ScreenPad Touchpad", "27c6", "01f1", virtual=True)
        self.add(7, "SYNA Touchpad", "06cb", "cd8b")
        self.assertIsNone(t.find_source(self.sys))
        self.add(12, "GDX1505:00 27C6:01F1 Touchpad", "27c6", "01f1")
        self.assertEqual(t.find_source(self.sys), "/dev/input/event12")


class MonitorIdentityTests(unittest.TestCase):
    @staticmethod
    def edid(name=b"no-audio-HD", serial_text=None):
        e = bytearray(128)
        e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
        e[8:10] = b"\x52\x62"  # TSB
        e[10:12] = b"\x88\x88"
        e[12:16] = b"\x00\x88\x88\x88"
        descriptors = []
        if name:
            descriptors.append(b"\0\0\0\xfc\0" + (name + b"\n").ljust(13, b" "))
        if serial_text:
            descriptors.append(b"\0\0\0\xff\0" + (serial_text + b"\n").ljust(13, b" "))
        for i, d in enumerate(descriptors):
            e[72 + 18 * i : 90 + 18 * i] = d
        return bytes(e)

    def test_like_mutter(self):
        # What mutter reports for the ScreenPad on this machine.
        self.assertEqual(t.edid_identity(self.edid()), ("TSB", "no-audio-HD", "0x88888800"))
        self.assertEqual(t.edid_identity(self.edid(name=None)), ("TSB", "0x8888", "0x88888800"))
        self.assertEqual(t.edid_identity(self.edid(b"ACME 27IN", b"SN0001234")), ("TSB", "ACME 27IN", "SN0001234"))
        self.assertIsNone(t.edid_identity(b"\0" * 128))
        self.assertIsNone(t.edid_identity(self.edid()[:100]))

    def test_saved_edid_or_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "edid.bin")
            self.assertEqual(t.monitor_identity(p), list(t.MONITOR_ID))
            with open(p, "wb") as f:
                f.write(self.edid(b"other"))
            self.assertEqual(t.monitor_identity(p), ["TSB", "other", "0x88888800"])


class ExtensionTests(unittest.TestCase):
    """Static checks on the GNOME Shell extension, which cannot run here."""

    # GNOME 50's LayoutManager chrome options (layout.js defaultParams).
    # Params.parse throws on any other key, after addChrome has already put
    # the actor on the stage; the shell then unloads the extension's
    # stylesheet, leaving bare labels with no bar and no pressed state.
    CHROME_OPTIONS = frozenset({"trackFullscreen", "affectsStruts"})

    def source(self):
        path = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad", "extension.js")
        with open(path) as f:
            return f.read()

    def test_chrome_options_exist_in_gnome_50(self):
        import re

        calls = re.findall(r"(?:add|addTop|track)Chrome\(([^;]*?)\);", self.source())
        self.assertTrue(calls)
        for args in calls:
            keys = set(re.findall(r"(\w+)\s*:", args))
            self.assertLessEqual(keys, self.CHROME_OPTIONS, args)

    def test_card_growth_is_animated_within_the_arming_delay(self):
        import re

        grow = int(re.search(r"const GROW_MS = (\d+);", self.source()).group(1))
        # Fully drawn before a tap on it counts.
        self.assertLess(grow, t.ASK_ARM * 1000)

    def test_grip_source_contract(self):
        # Source contract check, not a behavioral test: it only shows the
        # extension's text routes the grip to its own drawing code. The
        # grip is narrow (104 px); insets and icons scaled by the height
        # alone left it 1 px wide on a tall bar.
        src = " ".join(self.source().split())
        self.assertIn("if (tile.glyph === 'grip') { child = this._grip(width, height);", src)
        self.assertIn("Math.round(narrowest * MAX_INSET)", src)
        self.assertNotIn("list-drag-handle-symbolic", src)
        css_path = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad", "stylesheet.css")
        with open(css_path) as f:
            self.assertIn(".screenpad-touchbar-grip-bar {", f.read())

    def test_dbus_matches_the_daemon(self):
        import re

        src = self.source()
        self.assertIn("const DBUS_PATH = '%s';" % t.DBUS_PATH, src)
        self.assertIn('<interface name="%s">' % t.DBUS_IFACE, src)
        self.assertEqual(re.findall(r"^    (Launch)\(", src, re.M), ["Launch"])
        self.assertIn("--method %s.Launch" % t.DBUS_IFACE, t.LAUNCH[2])

    def test_styles_for_every_tile_style_and_class(self):
        path = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad", "stylesheet.css")
        with open(path) as f:
            css = f.read()
        for style in t.TILE_STYLES:
            self.assertIn(".screenpad-touchbar-%s {" % style, css)
        # A tap button is the plain button style; every other class has a rule.
        for name in set(t.TILE_CLASSES) - {"tap"}:
            self.assertRegex(css, r"\.%s[ {:.,]" % re.escape(name), name)
        for rule in (
            ".screenpad-touchbar-button.hold:active",
            ".screenpad-touchbar-shortcut.removing",
            ".screenpad-touchbar-shortcut.drop",
            ".screenpad-touchbar-spot.empty",
            ".screenpad-touchbar-badge",
            ".screenpad-touchbar-text",
        ):
            self.assertIn(rule + " {", css)

    def test_renderer_handshake_matches_the_daemon(self):
        src = self.source()
        self.assertIn("const RENDERER_NAME = '%s';" % t.RENDERER_NAME, src)
        self.assertIn("const RENDERER_PROTOCOL = %d;" % t.RENDERER_PROTOCOL, src)
        # It says whether the bar is on screen, every time _sync decides.
        self.assertIn("JSON.stringify({protocol: RENDERER_PROTOCOL, drawn})", src)
        self.assertIn("const drawn = this._bar !== null;", src)
        sync = src[src.index("    _sync() {") : src.index("    _announce() {")]
        self.assertEqual(sync.count("this._announce();"), 2)
        # Removed first in disable(), before the bar goes.
        disable = src[src.index("    disable() {") : src.index("    Launch(id)")]
        self.assertLess(disable.index("this._renderer.delete"), disable.index("this._destroyBar"))

    def test_draws_tiles_without_knowing_them(self):
        src = self.source()
        # A press restyles a tile; it does not rebuild the bar (and its strut).
        self.assertIn("state.tiles.map(({pressed, ...rest}) => rest)", src)
        # Only apps shown on the bar can be opened over D-Bus.
        self.assertIn("this._state?.tiles?.some(t => t.app === id)", src)
        # No tile kinds in the renderer: it never asks what a tile is.
        self.assertNotIn("state.spots", src)
        self.assertNotRegex(src, r"\.kind\s*===")


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


class SceneTests(unittest.TestCase):
    def test_spots_as_tiles(self):
        c = config(TILE_CONFIG)
        tiles = t.spot_tiles(c, pressed=[0], removing=[])
        self.assertEqual(
            [(x["id"], x["x"], x["width"], x["style"]) for x in tiles],
            [
                ("button-0", 0, 296, "button"),
                ("button-1", 296, 296, "button"),
                ("button-2", 592, 296, "button"),
                ("shortcut-0", 928, 296, "shortcut"),
                ("shortcut-1", 1224, 296, "shortcut"),
                ("shortcut-2", 1520, 296, "shortcut"),
                ("grip", 1816, 104, "grip"),
            ],
        )
        self.assertEqual(
            tiles[0],
            {
                "id": "button-0",
                "x": 0,
                "width": 296,
                "style": "button",
                "pressed": True,
                "classes": ["hold"],
                "icon": "audio-input-microphone-symbolic",
                "label": "PTT",
            },
        )
        # No app provides "agents": its config label, dimmed.
        self.assertEqual(tiles[1]["classes"], ["tile", "offline"])
        self.assertEqual(tiles[1]["label"], "Agents")
        self.assertEqual(tiles[2]["classes"], ["empty"])
        self.assertEqual(
            tiles[3],
            {"id": "shortcut-0", "x": 928, "width": 296, "style": "shortcut", "drop": 1, "app": "files.desktop"},
        )
        self.assertEqual(tiles[4]["classes"], ["empty"])
        self.assertEqual((tiles[4]["icon"], tiles[4]["icon_scale"], tiles[4]["drop"]), ("list-add-symbolic", 0.6, 2))
        self.assertTrue(tiles[4]["empty"])
        self.assertEqual(tiles[6]["glyph"], "grip")
        self.assertEqual(t.DIVIDER_X, 908)

    def test_removing_and_app_tiles(self):
        c = config(TILE_CONFIG)
        tiles = t.spot_tiles(
            c, pressed=[3], removing=[3], provided={"agents": {"tone": "warning", "label": "1 blocked", "badge": "3"}}
        )
        self.assertEqual(tiles[3]["classes"], ["removing"])
        self.assertEqual(tiles[3]["icon"], "edit-delete-symbolic")
        self.assertEqual(
            tiles[1],
            {
                "id": "button-1",
                "x": 296,
                "width": 296,
                "style": "button",
                "classes": ["tile", "tone-warning"],
                "label": "1 blocked",
                "badge": "3",
            },
        )
        # An app that says nothing yet shows the config's icon or label.
        tiles = t.spot_tiles(c, provided={"agents": {}})
        self.assertEqual((tiles[1]["classes"], tiles[1]["label"]), (["tile", "tone-neutral"], "Agents"))

    def test_every_class_is_known(self):
        c = config(TILE_CONFIG)
        ask = t.parse_ask(
            None,
            {"id": "q", "title": "T", "options": [{"id": "a", "label": "A", "tone": tone} for tone in t.TONES[:1]]},
            0,
        )
        tiles = t.spot_tiles(c, removing=[3], provided={"agents": {"tone": "accept"}}) + t.ask_tiles(ask)
        for tile in tiles:
            self.assertIn(tile["style"], t.TILE_STYLES)
            for name in tile.get("classes", []):
                self.assertIn(name, t.TILE_CLASSES)

    def test_question_tiles(self):
        ask = t.parse_ask(
            None,
            {
                "id": "q",
                "title": "Run npm test?",
                "text": "in api",
                "options": [
                    {"id": "no", "label": "Deny", "tone": "reject"},
                    {"id": "yes", "label": "Allow", "tone": "accept"},
                ],
            },
            0,
        )
        tiles = t.ask_tiles(ask, pressed=[1], queued=2)
        self.assertEqual(
            tiles[0],
            {
                "id": "ask-text",
                "x": 0,
                "width": 592,
                "style": "ask-text",
                "title": "Run npm test?",
                "text": "in api",
                "badge": "+2",
            },
        )
        # Every card can be dismissed, from the third button spot.
        self.assertEqual(
            tiles[1],
            {
                "id": "ask-dismiss",
                "x": 592,
                "width": 296,
                "style": "option",
                "classes": ["tone-neutral"],
                "icon": "window-close-symbolic",
                "icon_scale": 0.6,
                "label": "Dismiss",
            },
        )
        self.assertEqual(
            tiles[2],
            {
                "id": "ask-option-0",
                "x": 928,
                "width": 296,
                "style": "option",
                "classes": ["tone-reject"],
                "label": "Deny",
            },
        )
        self.assertEqual(tiles[3]["pressed"], True)
        self.assertTrue(t.ask_tiles(ask, pressed=[t.DISMISS])[1]["pressed"])
        self.assertEqual(
            [t.ask_option_at(ask, x) for x in (0, 591, 700, 900, 928, 1300, 1600)],
            [None, None, t.DISMISS, None, 0, 1, None],
        )


class AskBarTests(unittest.TestCase):
    """The bar with questions, on a fake clock."""

    def setUp(self):
        self.now = 100.0
        self.frames, self.launched, self.resized = [], [], []
        self.asks = t.Asks(clock=lambda: self.now)
        self.asks.set_available(True)
        self.bar = t.Bar(
            config(BarTests.CONFIG),
            t.KeyEmitter(self.frames.append),
            lambda a: None,
            self.launched.append,
            resize=lambda h: self.resized.append(h) or True,
            clock=lambda: self.now,
            asks=self.asks,
        )
        self.app = FakeClient()

    def ask(self, ask_id="q", n=2):
        options = [{"id": "o%d" % k, "label": "O%d" % k} for k in range(n)]
        self.asks.add(t.parse_ask(self.app, {"id": ask_id, "title": "T", "options": options}, self.now))
        self.now += t.ASK_ARM

    def test_lifting_on_an_answer_chooses_it(self):
        self.ask()
        self.assertTrue(self.bar.handle([("begin", 0, 1300, 50)]))
        self.assertEqual(self.bar.ask_pressed(self.asks.shown()), [1])
        self.bar.handle([("end", 0, 1310, 60)])
        self.assertEqual(self.app.last("answer"), {"op": "answer", "id": "q", "option": "o1", "reason": "chosen"})
        self.assertIsNone(self.asks.shown())
        # Nothing reached the spots under the question.
        self.assertEqual((self.frames, self.launched), ([], []))

    def test_too_soon_does_nothing(self):
        self.asks.add(
            t.parse_ask(self.app, {"id": "q", "title": "T", "options": [{"id": "a", "label": "A"}]}, self.now)
        )
        self.now += t.ASK_ARM / 2
        self.assertFalse(self.bar.handle([("begin", 0, 1000, 50)]))
        self.now += 1
        self.bar.handle([("end", 0, 1000, 50)])
        self.assertIsNone(self.app.last("answer"))
        self.assertIsNotNone(self.asks.shown())

    def test_slide_away_and_text_do_nothing(self):
        self.ask()
        self.bar.handle([("begin", 0, 1000, 50)])
        self.assertTrue(self.bar.handle([("move", 0, 1000, 600)]))
        self.assertEqual(self.bar.ask_pressed(self.asks.shown()), [])
        self.bar.handle([("end", 0, 1000, 600)])
        # The question's text, over the button spots, is not a button.
        self.assertFalse(self.bar.handle([("begin", 1, 100, 50)]))
        self.bar.handle([("end", 1, 100, 50)])
        self.assertEqual((self.app.last("answer"), self.frames), (None, []))

    def test_the_grip_still_works(self):
        self.ask()
        self.bar.handle([("begin", 0, 1850, 100), ("move", 0, 1850, 180), ("end", 0, 1850, 180)])
        self.assertEqual(self.resized, [240])

    def test_a_held_button_stays_held(self):
        self.bar.handle([("begin", 0, 100, 50)])
        self.assertEqual(self.frames, [[(t.EV_KEY, 184, 1), SYN]])
        self.ask()
        self.bar.handle([("end", 0, 100, 50)])
        self.assertEqual(self.frames[-1], [(t.EV_KEY, 184, 0), SYN])
        self.assertIsNone(self.app.last("answer"))

    def test_an_answer_that_went_away_does_nothing(self):
        self.ask()
        self.bar.handle([("begin", 0, 1000, 50)])
        self.now += 100
        self.asks.tick()
        self.assertEqual(self.app.last("answer")["reason"], "timeout")
        self.bar.handle([("end", 0, 1000, 50)])
        self.assertEqual(len([m for m in self.app.sent if m["op"] == "answer"]), 1)

    def test_queue_shows_the_next_armed_again(self):
        self.ask("first")
        self.ask("second")
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.app.last("answer")["id"], "first")
        # The next one just appeared: a tap now is too soon.
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.asks.shown().id, "second")
        self.now += t.ASK_ARM
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.app.last("answer")["id"], "second")


class TileBarTests(unittest.TestCase):
    def setUp(self):
        self.events, self.on = [], {"agents"}
        bar_self = self

        class Tiles:
            def online(self, name):
                return name in bar_self.on

            def event(self, name, kind, inside=None):
                bar_self.events.append((name, kind, inside))

        self.frames = []
        self.bar = t.Bar(config(TILE_CONFIG), t.KeyEmitter(self.frames.append), lambda a: None, tiles=Tiles())

    def test_down_and_up(self):
        self.assertTrue(self.bar.handle([("begin", 0, 400, 50)]))
        self.assertEqual(self.bar.pressed(), [1])
        self.bar.handle([("end", 0, 410, 60)])
        self.assertEqual(self.events, [("agents", "down", None), ("agents", "up", True)])
        self.bar.handle([("begin", 0, 400, 50), ("move", 0, 400, 600), ("end", 0, 400, 600)])
        self.assertEqual(self.events[-1], ("agents", "up", False))
        self.assertEqual(self.frames, [])

    def test_offline_tile_does_nothing(self):
        self.on.clear()
        self.assertFalse(self.bar.handle([("begin", 0, 400, 50), ("end", 0, 400, 50)]))
        self.assertEqual(self.events, [])

    def test_reload_release_and_app_gone(self):
        self.bar.handle([("begin", 0, 400, 50)])
        self.bar.set_config(config(TILE_CONFIG))  # same tile, same spot: still down
        self.assertEqual(self.events, [("agents", "down", None)])
        self.bar.release_all()
        self.assertEqual(self.events[-1], ("agents", "up", False))
        self.bar.handle([("begin", 0, 400, 50)])
        self.bar.tile_gone("agents")
        self.bar.handle([("end", 0, 400, 50)])
        self.assertEqual(self.events[-1], ("agents", "down", None))
        self.assertEqual(self.bar.pressed(), [])


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.apps = t.Apps(clock=lambda: self.now)
        self.apps.set_placed(["agents"])
        self.gone = []
        self.apps.on_tile_gone = self.gone.append
        self.a, self.b = FakeClient(), FakeClient()
        self.apps.connect(self.a)
        self.apps.connect(self.b)

    def test_hello_and_tiles(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents", "ci"]})
        self.assertEqual(
            self.a.last("welcome"),
            {"op": "welcome", "app": "fleet", "placed": ["agents"], "unplaced": ["ci"], "taken": []},
        )
        self.apps.handle(self.b, {"op": "hello", "app": "other", "tiles": ["agents"]})
        self.assertEqual(self.b.last("welcome")["taken"], ["agents"])
        self.assertTrue(self.apps.online("agents"))
        self.assertEqual(self.apps.names(), ["fleet", "other"])
        self.assertTrue(self.apps.take_changed())
        self.assertFalse(self.apps.take_changed())
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        self.assertIn("already", self.a.last("error")["message"])

    def test_set(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.take_changed()
        self.apps.handle(
            self.a,
            {
                "op": "set",
                "tile": "agents",
                "label": "1 blocked",
                "tone": "warning",
                "badge": "2",
                "icon": "dialog-warning-symbolic",
            },
        )
        self.assertEqual(
            self.apps.provided(),
            {"agents": {"tone": "warning", "label": "1 blocked", "badge": "2", "icon": "dialog-warning-symbolic"}},
        )
        self.assertTrue(self.apps.take_changed())
        bad = [
            {"tile": "ci"},
            {"tile": "agents", "tone": "pink"},
            {"tile": "agents", "label": "x" * 33},
            {"tile": "agents", "label": "a\nb"},
            {"tile": "agents", "icon": "../x"},
            {"tile": "agents", "colour": "red"},
        ]
        for msg in bad:
            self.apps.handle(self.a, dict(msg, op="set"))
            self.assertEqual(self.a.last("error")["request"], "set", msg)
        self.apps.handle(self.b, {"op": "hello", "app": "other"})
        self.apps.handle(self.b, {"op": "set", "tile": "agents", "label": "mine"})
        self.assertIn("not yours", self.b.last("error")["message"])

    def test_hello_first_and_bad_messages(self):
        self.apps.handle(self.a, {"op": "set", "tile": "agents"})
        self.assertIn("hello first", self.a.last("error")["message"])
        self.apps.handle(self.a, ["not", "an", "object"])
        self.apps.handle(self.a, {"op": "dance"})
        self.assertEqual(len([m for m in self.a.sent if m["op"] == "error"]), 3)
        self.apps.handle(self.a, {"op": "ping"})
        self.assertEqual(self.a.sent[-1], {"op": "pong"})
        for msg in ({"app": "Fleet"}, {"app": "x", "tiles": "agents"}, {"app": "x", "tiles": ["A"]}):
            self.apps.handle(self.b, dict(msg, op="hello"))
            self.assertEqual(self.b.last("error")["request"], "hello", msg)

    def test_events_go_to_the_owner(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.event("agents", "down")
        self.apps.event("agents", "up", True)
        self.apps.event("nobody", "down")
        self.assertEqual(
            [m for m in self.a.sent if m["op"] == "event"],
            [
                {"op": "event", "tile": "agents", "type": "down"},
                {"op": "event", "tile": "agents", "type": "up", "inside": True},
            ],
        )
        self.assertEqual(self.b.sent, [])

    def test_ask_validation(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        ok = {"op": "ask", "id": "q1", "title": "Run it?", "options": [{"id": "y", "label": "Yes"}]}
        bad = [
            dict(ok, id="no spaces"),
            dict(ok, title=""),
            dict(ok, title="x" * 81),
            dict(ok, options=[]),
            dict(ok, options=[{"id": "y", "label": "Y"}] * 2),
            dict(ok, options=[{"id": str(k), "label": "L"} for k in range(4)]),
            dict(ok, options=[{"id": "y", "label": "Y", "tone": "pink"}]),
            dict(ok, timeout=0),
            dict(ok, timeout=True),
            dict(ok, timeout=4000),
            dict(ok, colour=1),
        ]
        for msg in bad:
            self.apps.handle(self.a, msg)
            self.assertEqual(self.a.last("error")["request"], "ask", msg)
        self.assertEqual(self.a.last("error")["id"], "q1")

    def test_ask_answers(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        ask = {
            "op": "ask",
            "id": "q1",
            "title": "Run it?",
            "timeout": 5,
            "options": [{"id": "y", "label": "Yes"}, {"id": "n", "label": "No"}],
        }
        # The bar cannot show questions: answered at once.
        self.apps.handle(self.a, ask)
        self.assertEqual(self.a.last("answer"), {"op": "answer", "id": "q1", "option": None, "reason": "unavailable"})
        self.apps.asks.set_available(True)
        self.apps.handle(self.a, ask)
        self.apps.handle(self.a, ask)
        self.assertIn("already waiting", self.a.last("error")["message"])
        shown = self.apps.asks.shown()
        self.assertEqual((shown.id, shown.shown_at), ("q1", 10.0))
        self.apps.asks.choose(shown, 1)
        self.assertEqual(self.a.last("answer")["option"], "n")
        self.apps.handle(self.a, ask)
        self.apps.handle(self.a, {"op": "cancel", "id": "q1"})
        self.assertEqual(self.a.last("answer")["reason"], "cancelled")
        self.apps.handle(self.a, {"op": "cancel", "id": "q1"})
        self.assertEqual(self.a.last("error")["request"], "cancel")
        self.apps.handle(self.a, ask)
        self.assertEqual(self.apps.asks.deadline(), 15.0)
        self.now = 15.0
        self.apps.asks.tick()
        self.assertEqual(self.a.last("answer")["reason"], "timeout")
        self.apps.handle(self.a, ask)
        self.apps.asks.set_available(False)
        self.assertEqual(self.a.last("answer")["reason"], "unavailable")
        self.assertIsNone(self.apps.asks.shown())

    def test_disconnect(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.asks.set_available(True)
        self.apps.handle(self.a, {"op": "ask", "id": "q", "title": "T", "options": [{"id": "y", "label": "Y"}]})
        self.apps.disconnect(self.a)
        self.assertEqual((self.apps.online("agents"), self.apps.asks.shown(), self.gone), (False, None, ["agents"]))
        self.assertIsNone(self.a.last("answer"))
        # The tile is free for another app now.
        self.apps.handle(self.b, {"op": "hello", "app": "other", "tiles": ["agents"]})
        self.assertEqual(self.b.last("welcome")["placed"], ["agents"])

    def test_too_many_apps(self):
        apps = t.Apps()
        clients = [FakeClient() for _ in range(t.MAX_APPS + 1)]
        self.assertEqual([apps.connect(c) for c in clients], [True] * t.MAX_APPS + [False])
        self.assertEqual(clients[-1].last("error")["message"], "too many apps (%d)" % t.MAX_APPS)


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


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = ConfigHome(self.tmp.name)
        self.home.__enter__()
        self.addCleanup(self.home.__exit__)
        self.path = t.runtime_file(t.SOCKET_NAME)
        self.apps = t.Apps()
        self.apps.set_placed(["agents"])
        self.server = t.AppServer(self.path, self.apps)
        self.addCleanup(self.server.close)

    def conn(self):
        app = Conn(self.path)
        self.addCleanup(app.s.close)
        return app

    def test_hello_set_and_events(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        app = self.conn()
        app.send({"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.assertEqual(app.recv(self.server)["placed"], ["agents"])
        app.send({"op": "set", "tile": "agents", "label": "2 working"})
        app.send({"op": "ping"})
        self.assertEqual(app.recv(self.server), {"op": "pong"})
        self.assertEqual(self.apps.provided()["agents"]["label"], "2 working")
        self.apps.event("agents", "down")
        self.assertEqual(app.recv(self.server), {"op": "event", "tile": "agents", "type": "down"})
        app.s.close()
        pump(self.server, 3)
        self.assertFalse(self.apps.online("agents"))

    def test_bad_json_and_a_too_long_line(self):
        app = self.conn()
        app.s.sendall(b"{nope\n")
        self.assertEqual(app.recv(self.server)["message"], "not a JSON object")
        app.s.sendall(b"x" * (t.MAX_LINE + 10))
        self.assertIsNone(app.recv(self.server))
        self.assertEqual(self.server.clients, {})

    def test_stale_and_live_sockets(self):
        # A live daemon is not replaced.
        with self.assertRaises(t.TouchbarError):
            t.AppServer(self.path, t.Apps())
        # A socket file a dead daemon left is.
        self.server.sock.close()
        self.assertTrue(os.path.exists(self.path))
        other = t.AppServer(self.path, t.Apps())
        other.close()
        self.assertFalse(os.path.exists(self.path))

    def test_ask_command(self):
        self.apps.asks.set_available(True)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                pump(self.server)
                ask = self.apps.asks.shown()
                if ask is not None:
                    self.apps.asks.choose(ask, 1)

        thread = threading.Thread(target=serve)
        thread.start()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = t.main(
                    [
                        "ask",
                        "Run npm test?",
                        "--text",
                        "in api",
                        "--option",
                        "allow=Allow once",
                        "--option",
                        "deny=Deny",
                        "--tone",
                        "deny=reject",
                        "--timeout",
                        "5",
                    ]
                )
        finally:
            stop.set()
            thread.join()
        self.assertEqual((code, out.getvalue()), (0, "deny\n"))

    def test_ask_command_without_an_answer(self):
        err = io.StringIO()
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                pump(self.server)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with contextlib.redirect_stderr(err):
                # Unavailable (the bar is not shown), then a bad request.
                self.assertEqual(t.main(["ask", "T", "--option", "a=A"]), 1)
                self.assertEqual(t.main(["ask", "T", "--option", "a=A", "--timeout", "0"]), 2)
                self.assertEqual(t.main(["ask", "T", "--option", "noequals"]), 2)
        finally:
            stop.set()
            thread.join()
        self.assertIn("no answer: unavailable", err.getvalue())
        self.server.close()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(t.main(["ask", "T", "--option", "a=A"]), 1)


class DaemonSceneTests(unittest.TestCase):
    def test_publish_and_renderer(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = t.Daemon(config(TILE_CONFIG), None)
            self.assertFalse(daemon.renderer_ready())
            os.makedirs(os.path.dirname(t.state_path()))
            renderer = t.runtime_file(t.RENDERER_NAME)
            for i, (said, ready) in enumerate(
                (
                    ('{"protocol": 2, "drawn": true}', True),
                    ('{"protocol": 2, "drawn": false}', False),
                    ('{"protocol": 1, "drawn": true}', False),
                    ('{"protocol": true, "drawn": true}', False),
                    ('{"protocol": 2}', False),
                    ("not json at all", False),
                    ("[2]", False),
                    ('{"protocol": 3, "drawn": true}', True),
                )
            ):
                with open(renderer, "w") as f:
                    f.write(said)
                os.utime(renderer, ns=(0, 1000 + i))  # a new mtime each time
                self.assertEqual(daemon.renderer_ready(), ready, said)
            os.unlink(renderer)
            self.assertFalse(daemon.renderer_ready())
            daemon.publish()
            with open(t.state_path()) as f:
                state = json.load(f)
            self.assertEqual(state["version"], t.SCENE_VERSION)
            self.assertEqual([x["id"] for x in state["tiles"]][:2], ["button-0", "button-1"])
            # The version 1 fields, for an extension loaded before tiles.
            self.assertEqual(len(state["spots"]), 7)
            self.assertEqual((state["pressed"], state["removing"]), ([], []))
            # A question shows over the spots, the grip stays.
            app = FakeClient()
            daemon.apps.connect(app)
            daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
            # As if the pad were grabbed and the bar on screen.
            daemon.apps.asks.can_show = lambda: True
            daemon.apps.handle(app, {"op": "ask", "id": "q", "title": "T", "options": [{"id": "y", "label": "Y"}]})
            daemon.publish()
            with open(t.state_path()) as f:
                state = json.load(f)
            self.assertEqual([x["id"] for x in state["tiles"]], ["ask-text", "ask-dismiss", "ask-option-0", "grip"])
            self.assertEqual(state["apps"], ["fleet"])


class ContactsToAppsTests(unittest.TestCase):
    """Device contacts through the Splitter and the Bar to an app, end to end."""

    def test_tile_and_question(self):
        now = [50.0]
        apps = t.Apps(clock=lambda: now[0])
        apps.set_placed(["agents"])
        app = FakeClient()
        apps.connect(app)
        apps.handle(app, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        bar = t.Bar(
            config(TILE_CONFIG),
            t.KeyEmitter(lambda ev: None),
            lambda a: None,
            clock=lambda: now[0],
            tiles=apps,
            asks=apps.asks,
        )
        sp = Split()
        # The "agents" spot is x 296..592 panel px, 592..1184 device units.
        for groups in ([touch(0, 1, 800, 100)], [lift(0)]):
            pad, events = sp.frame(*groups)
            self.assertEqual(pad, [])
            bar.handle(events)
        self.assertEqual([m["type"] for m in app.sent if m["op"] == "event"], ["down", "up"])
        # A question; a tap on its second answer (x 1224..1520 px), after
        # it has been up for ASK_ARM.
        apps.asks.set_available(True)
        apps.handle(
            app,
            {
                "op": "ask",
                "id": "q",
                "title": "Run it?",
                "options": [{"id": "no", "label": "No"}, {"id": "yes", "label": "Yes"}],
            },
        )
        now[0] += t.ASK_ARM
        for groups in ([touch(1, 2, 2700, 120)], [lift(1)]):
            pad, events = sp.frame(*groups)
            self.assertEqual(pad, [])
            bar.handle(events)
        self.assertEqual(app.last("answer"), {"op": "answer", "id": "q", "option": "yes", "reason": "chosen"})
        # The pad below the bar is untouched by all this.
        pad, events = sp.frame(touch(2, 3, 2000, 1500))
        self.assertEqual((codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), events), ([3], []))


class DaemonLoopTests(unittest.TestCase):
    """The daemon's loop serving the socket, with a stand-in for the grabbed pad."""

    class Pad:
        """Grabbed, and never has events (a pipe nobody writes)."""

        def __init__(self):
            self.fd, self.w = os.pipe()
            self.grabbed = True

        def close(self):
            os.close(self.fd)
            os.close(self.w)

    def test_questions_follow_the_renderer(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d, ConfigHome(d):
            flag = os.path.join(d, "active")
            open(flag, "w").close()
            daemon = t.Daemon(config(TILE_CONFIG), None, flag=flag)
            daemon.server = t.AppServer(t.runtime_file(t.SOCKET_NAME), daemon.apps)
            pad = self.Pad()
            daemon.source = pad  # type: ignore[assignment]  # stands in for the parts loop() uses
            thread = threading.Thread(target=daemon.loop)
            thread.start()
            try:
                app = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                app.connect(t.runtime_file(t.SOCKET_NAME))
                app.settimeout(5)
                lines = app.makefile("rw")

                def ask(ask_id):
                    lines.write(
                        json.dumps({"op": "ask", "id": ask_id, "title": "T", "options": [{"id": "y", "label": "Y"}]})
                        + "\n"
                    )
                    lines.flush()

                lines.write(json.dumps({"op": "hello", "app": "fleet", "tiles": ["agents"]}) + "\n")
                lines.flush()
                self.assertEqual(json.loads(lines.readline())["placed"], ["agents"])
                # No renderer.json: the bar cannot show questions.
                ask("q1")
                self.assertEqual(json.loads(lines.readline())["reason"], "unavailable")
                with open(t.runtime_file(t.RENDERER_NAME), "w") as f:
                    f.write('{"protocol": 2, "drawn": true}')
                ask("q2")
                for _ in range(50):
                    with open(t.state_path()) as f:
                        state = json.load(f)
                    if state["tiles"][0]["id"] == "ask-text":
                        break
                    threading.Event().wait(0.05)
                self.assertEqual([x["id"] for x in state["tiles"]], ["ask-text", "ask-dismiss", "ask-option-0", "grip"])
                self.assertEqual(state["apps"], ["fleet"])
                # The bar leaves the screen: the waiting question is answered.
                os.unlink(t.runtime_file(t.RENDERER_NAME))
                self.assertEqual(
                    json.loads(lines.readline()), {"op": "answer", "id": "q2", "option": None, "reason": "unavailable"}
                )
                app.close()
            finally:
                daemon.stop = True
                thread.join()
                daemon.source = None
                daemon.server.close()
                pad.close()


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
                self.assertEqual(t.main(["check"]), 0)
            self.assertIn("button   2  x  296..592  Agents, icon none, tile agents (an app draws it)", out.getvalue())

    def test_migrate_the_default_before_tiles(self):
        with open(os.path.join(REPO, "tests", "touchbar-v3-default.toml")) as f:
            v3 = f.read()
        self.assertNotIn("tile", v3)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v3)
            self.assertTrue(t.migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), t.EXAMPLE_CONFIG)
            self.assertFalse(t.migrate_config())
            # An edited one keeps the edits and gains the 'tile' lines once.
            with open(path, "w") as f:
                f.write(v3.replace("height = 160", "height = 168") + '\n[[shortcut]]\napp = "bitwarden.desktop"\n')
            self.assertTrue(t.migrate_config())
            self.assertFalse(t.migrate_config())
            c = t.load_config()[0]
            self.assertEqual((c.height, c.shortcuts), (168, ["bitwarden.desktop"]))
            with open(path) as f:
                self.assertEqual(f.read().count("#   tile "), 1)


if __name__ == "__main__":
    unittest.main()
