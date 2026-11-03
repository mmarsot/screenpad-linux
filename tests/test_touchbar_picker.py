"""Tests for the picker (picker.py): the + on the apps page's free cells and
on the bar's empty spots, what it offers (presets, the dock's favourites,
the custom shortcut), the item written in the config as a block of its own,
Back, the dock's favourites read from GNOME, and adding a key or a command
from the command line.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from touchbar_support import AXES, GRID_H, GRID_Y, SYN, Clock, ConfigHome, config, lift, touch

from screenpad_touchbar import daemon as daemon_module
from screenpad_touchbar import picker as picker_module
from screenpad_touchbar.cli import main
from screenpad_touchbar.common import (
    ADD_ITEM,
    APPS,
    ASK_ARM,
    CUSTOM_PROTOCOL,
    HOME,
    RENDERER_NAME,
    WIDGETS_PROTOCOL,
    ConfigError,
    runtime_file,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.config_edit import append_item, with_items
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.items import Item, parse_item
from screenpad_touchbar.keys import KeyEmitter, key_spec, parse_key
from screenpad_touchbar.picker import CUSTOM_LOOK, PRESETS, read_favourites
from screenpad_touchbar.splitter import Splitter

CONFIG = """\
# Mine.
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"

# The apps page.
[[apps]]
app = "settings.desktop"
"""
FAVOURITES = ["settings.desktop", "files.desktop", "web.desktop"]
ROW = GRID_Y + GRID_H // 4  # the home list's first row


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.path = self.enterContext(ConfigHome(self.enterContext(tempfile.TemporaryDirectory())))
        os.makedirs(os.path.dirname(self.path))
        self.write(CONFIG)
        self.clock = Clock()
        self.favourites_read = 0
        self.daemon = d = Daemon(load_config()[0], None, favourites=self.favourites)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = lambda: True
        d.bar = d.new_bar(KeyEmitter(lambda ev: None))
        d.bar.clock = self.clock
        d.splitter = Splitter(AXES, 5, d._zone)
        self.enterContext(mock.patch.object(daemon_module, "write_events", lambda fd, ev: None))
        os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)), exist_ok=True)
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL, "drawn": True}, f)
        self.contact = 0

    def favourites(self):
        self.favourites_read += 1
        return FAVOURITES

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def text(self):
        with open(self.path) as f:
            return f.read()

    def tap(self, at):
        """A quick tap at a point in panel pixels, once what is there is armed."""
        self.daemon.surface.scene()
        self.clock.now += ASK_ARM
        self.contact += 1
        for groups in ([touch(0, self.contact, at[0] * 2, at[1] * 2)], [lift(0)]):
            out = None
            for e in [e for g in groups for e in g] + [SYN]:
                out = self.daemon.splitter.feed(*e)
            assert out is not None and out != "resync"
            self.daemon.route(*out)
        self.daemon.check_config()

    def widgets(self):
        return {w["id"].removeprefix("surface/"): w for w in self.daemon.surface.scene()["widgets"]}

    def centre(self, widget):
        return widget["x"] + widget["width"] // 2, widget["y"] + widget["height"] // 2

    def test_a_plus_on_each_free_cell_opens_the_picker(self):
        self.daemon.pager.turn(APPS)
        w = self.widgets()
        # Settings, then a + in each of the 7 other cells.
        self.assertEqual(sorted(k for k in w if k.startswith("add:")), ["add:%d" % i for i in range(1, 8)])
        self.assertEqual(self.favourites_read, 0)
        clicks = self.daemon.clicks
        self.tap(self.centre(w["add:3"]))
        state = self.daemon.surface.scene()
        self.assertEqual((state["title"], state["page"]), ("Add to the apps page", None))
        self.assertEqual(self.daemon.clicks, clicks + 1)
        self.assertEqual(self.favourites_read, 1)  # once, as it opens
        w = self.widgets()
        self.assertEqual(w["back"]["label"], "Back")
        picks = [w["pick:%d" % i] for i in range(len(w) - 2)]
        # The presets, then the favourites the apps page does not have; no
        # custom shortcut without an extension that draws it.
        self.assertEqual([p.get("label") for p in picks[: len(PRESETS)]], [p[0] for p in PRESETS])
        self.assertEqual([p.get("app") for p in picks[len(PRESETS) :]], ["files.desktop", "web.desktop"])
        self.assertNotIn(CUSTOM_LOOK["label"], [p.get("label") for p in picks])
        # A preset: written as its own block after the last [[apps]] one.
        self.tap(self.centre(w["pick:0"]))
        self.assertEqual(
            self.text(),
            CONFIG + '\n[[apps]]\nkey = "printscreen"\nicon = "applets-screenshooter-symbolic"\nlabel = "Screenshot"\n',
        )
        self.assertEqual([i.title() for i in self.daemon.config.apps], ["settings", "Screenshot"])
        self.assertEqual(self.daemon.surface.scene()["title"], "Apps")

    def test_a_full_list_has_no_plus(self):
        self.write(CONFIG + "".join('[[apps]]\nlabel = "K%d"\nkey = "a"\n' % i for i in range(7)))
        self.daemon.reload = True
        self.daemon.check_config()
        self.daemon.pager.turn(APPS)
        self.assertFalse([k for k in self.widgets() if k.startswith("add:")])

    def test_back_adds_nothing(self):
        self.daemon.pager.turn(APPS)
        self.tap(self.centre(self.widgets()["add:1"]))
        self.tap(self.centre(self.widgets()["back"]))
        self.assertEqual((self.text(), self.daemon.surface.scene()["title"]), (CONFIG, "Apps"))
        # The page going back to the touchpad closes it too.
        self.tap(self.centre(self.widgets()["add:1"]))
        self.daemon.pager.turn(HOME)
        self.daemon.surface.scene()
        self.daemon.pager.turn(APPS)
        self.assertEqual(self.daemon.surface.scene()["title"], "Apps")

    def test_an_empty_spot_of_the_bar_opens_it_for_the_bar(self):
        clicks = self.daemon.clicks
        self.tap((400, 80))  # the second spot, empty
        state = self.daemon.surface.scene()
        self.assertEqual((self.daemon.pager.page, state["title"]), (APPS, "Add to the bar"))
        self.assertEqual(self.daemon.clicks, clicks + 1)
        w = self.widgets()
        picks = [w["pick:%d" % i] for i in range(len(w) - 2)]
        # The bar has no app: every favourite is offered.
        self.assertEqual([p["app"] for p in picks if "app" in p], FAVOURITES)
        web = next(k for k, v in w.items() if v.get("app") == "web.desktop")
        self.tap(self.centre(w[web]))
        self.assertEqual([i.title() for i in self.daemon.config.bar], ["PTT", "web"])
        self.assertIn(
            'label = "PTT"\nmode = "hold"\nkey = "F14"\n\n[[bar]]\napp = "web.desktop"\n\n# The apps', self.text()
        )
        # Without the pages, an empty spot does nothing.
        self.daemon.pager.turn(HOME)
        self.daemon.pager.can_show = lambda: False
        self.tap((700, 80))
        self.assertEqual(self.daemon.pager.page, HOME)

    def test_the_custom_shortcut_needs_the_extension_that_has_it(self):
        ran = []
        self.daemon.run_command = ran.append  # type: ignore[method-assign]  # nothing runs
        self.daemon.pager.turn(APPS)
        self.tap(self.centre(self.widgets()["add:1"]))
        self.assertNotEqual(self.widgets()["pick:0"]["label"], CUSTOM_LOOK["label"])
        self.tap(self.centre(self.widgets()["back"]))
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": CUSTOM_PROTOCOL, "drawn": True}, f)
        os.utime(runtime_file(RENDERER_NAME), ns=(0, 2000))
        self.tap(self.centre(self.widgets()["add:1"]))
        w = self.widgets()
        self.assertEqual(w["pick:0"]["label"], CUSTOM_LOOK["label"])
        self.tap(self.centre(w["pick:0"]))
        # The extension's window opens for the apps page; nothing is written yet.
        self.assertEqual((ran, self.text()), ([ADD_ITEM + ["apps"]], CONFIG))
        self.assertEqual(self.daemon.surface.scene()["title"], "Apps")

    def test_the_presets_icons_are_in_the_adwaita_theme(self):
        if not os.path.isdir("/usr/share/icons/Adwaita"):
            self.skipTest("no Adwaita icon theme here")
        files = {f for _, _, fs in os.walk("/usr/share/icons/Adwaita") for f in fs}
        for icon in [p[1] for p in PRESETS] + [CUSTOM_LOOK["icon"], "list-add-symbolic", "go-previous-symbolic"]:
            self.assertTrue(any(f.startswith(icon + ".") for f in files), icon)
        for _label, _icon, key in PRESETS:
            parse_key(key)


class WritingTests(unittest.TestCase):
    def test_keys_and_commands_written_as_blocks(self):
        key = parse_item({"label": 'Say "hi"', "key": "ctrl+alt+t"})
        sh = parse_item({"label": "Run", "command": "echo 'a b' \\ c", "mode": "hold"})
        argv = parse_item({"icon": "/tmp/a b.svg", "command": ["notify-send", "hi there"]})
        out = with_items("", "apps", [], [key, sh, argv], added={id(key), id(sh), id(argv)})
        self.assertEqual([i.spec() for i in config(out).apps], [i.spec() for i in (key, sh, argv)])
        self.assertIn('[[apps]]\nkey = "ctrl+alt+t"\nlabel = "Say \\"hi\\""\n', out)
        # A key not added (one from elsewhere, without its block) is not written.
        with self.assertRaises(ConfigError):
            with_items("", "apps", [], [key])

    def test_append_item(self):
        a, k = Item.app("a.desktop"), parse_item({"label": "K", "key": "k"})
        self.assertEqual(append_item([a], k, 2), [a, k])
        with self.assertRaisesRegex(ConfigError, "no free spot"):
            append_item([a, k], k, 2)
        with self.assertRaisesRegex(ConfigError, "already there"):
            append_item([a], Item.app("a.desktop"), 6)

    def test_key_spec(self):
        self.assertEqual(key_spec((56, 29, 20)), "ctrl+alt+t")
        self.assertEqual(key_spec((125, 109)), "super+pagedown")
        self.assertEqual(key_spec((99,)), "printscreen")
        # evdev's KEY_1 is code 2, and parse_key reads a bare digit as a code.
        self.assertEqual(key_spec((29, 2)), "ctrl+2")
        self.assertEqual(parse_key("ctrl+2"), (29, 2))
        for code in range(1, 256):
            self.assertEqual(parse_key(key_spec((code,))), (code,), code)

    def test_the_dock_favourites(self):
        def run(out):
            return mock.patch.object(
                picker_module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, out, "")
            )

        with run("['org.gnome.Nautilus.desktop', 'bad id.desktop', 'x']\n"):
            self.assertEqual(read_favourites(), ["org.gnome.Nautilus.desktop", "x.desktop"])
        for out in ("@as []\n", "garbage(\n", "42\n"):
            with run(out):
                self.assertEqual(read_favourites(), [], out)
        with mock.patch.object(picker_module.subprocess, "run", side_effect=OSError("no gsettings")):
            self.assertEqual(read_favourites(), [])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["shortcut", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_add_key_and_command(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(CONFIG)
            # Key codes, as the extension's window sends them, written by name.
            self.assertEqual(
                self.run_cli("add-key", "Terminal", "56+29+20", "--apps"), (0, "added Terminal to the apps page\n", "")
            )
            self.assertEqual(self.run_cli("add-command", "Hi", "notify-send hi"), (0, "added Hi to the bar\n", ""))
            c = load_config()[0]
            self.assertEqual((c.apps[-1].key, c.apps[-1].mode), ("ctrl+alt+t", "tap"))
            self.assertEqual(
                (c.bar[-1].command, c.bar[-1].mode, c.bar[-1].icon),
                (["/bin/sh", "-c", "notify-send hi"], "tap", None),
            )
            with open(path) as f:
                self.assertTrue(f.read().startswith(CONFIG.split("\n\n")[0]))
            code, _, err = self.run_cli("add-key", "Bad", "nokey")
            self.assertEqual(code, 2)
            self.assertIn("unknown key", err)


if __name__ == "__main__":
    unittest.main()
