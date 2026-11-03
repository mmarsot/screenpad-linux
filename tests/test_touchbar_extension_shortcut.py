"""Tests for the extension's shortcut.js, the custom shortcut window's logic,
run under node: a key press while recording, what the button shows, and
the arguments of 'screenpad-touchbar shortcut' it builds, handed to the
real command line; and the daemon offering the window only with an
extension that has it.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import tempfile
import unittest

from touchbar_support import ConfigHome, js

from screenpad_touchbar.cli import main
from screenpad_touchbar.config import load_config
from screenpad_touchbar.keys import MODIFIERS
from screenpad_touchbar.picker import TITLES

NONE = {"ctrl": False, "shift": False, "alt": False, "super": False}


class ShortcutTests(unittest.TestCase):
    def test_a_key_press_while_recording(self):
        # Clutter's key codes are the evdev ones plus 8: T is 20.
        self.assertEqual(js("shortcut.js", "m.combo(v.mods, 28)", mods=dict(NONE, ctrl=True, alt=True)), [29, 56, 20])
        self.assertEqual(js("shortcut.js", "m.combo(v.mods, 107)", mods=NONE), [99])  # Print
        self.assertEqual(
            js("shortcut.js", "m.combo(v.mods, 38)", mods={"ctrl": True, "shift": True, "alt": True, "super": True}),
            [29, 42, 56, 125, 30],
        )
        # A modifier alone, or no key, makes no combination yet.
        for code in MODIFIERS:
            self.assertIsNone(js("shortcut.js", "m.combo(v.mods, %d)" % (code + 8), mods=dict(NONE, ctrl=True)), code)
        self.assertIsNone(js("shortcut.js", "m.combo(v.mods, 0)", mods=NONE))
        self.assertEqual(sorted(js("shortcut.js", "m.MODIFIER_CODES")), sorted(MODIFIERS))

    def test_what_the_button_shows(self):
        self.assertEqual(
            js("shortcut.js", "m.comboText(v.mods, 't')", mods=dict(NONE, ctrl=True, alt=True)), "Ctrl+Alt+T"
        )
        self.assertEqual(js("shortcut.js", "m.comboText(v.mods, 'Print')", mods=NONE), "Print")
        self.assertEqual(
            js("shortcut.js", "m.comboText(v.mods, 'Page_Down')", mods=dict(NONE, super=True)), "Super+Page_Down"
        )

    def test_what_is_missing(self):
        for fields, error in (
            ({"label": " ", "codes": [20]}, "Give it a name."),
            ({"label": "T", "codes": [], "command": "  "}, "Press a key combination, or type a command."),
        ):
            self.assertEqual(js("shortcut.js", "m.addArgs('bar', v.f)", f=fields), {"error": error})
        self.assertEqual(js("shortcut.js", "m.addArgs('dock', {label: 'T', codes: [20]})"), {"error": "no place dock"})
        self.assertEqual(js("shortcut.js", "m.PLACES"), TITLES)

    def test_the_arguments_add_the_item(self):
        built = js(
            "shortcut.js",
            "[m.addArgs('apps', {label: ' Terminal ', codes: [29, 56, 20], command: 'x'}),"
            " m.addArgs('bar', {label: 'Hi', command: ' notify-send \"hi there\" ', hold: true})]",
        )
        self.assertEqual(
            [b["args"] for b in built],
            [
                ["shortcut", "add-key", "Terminal", "29+56+20", "--apps"],
                ["shortcut", "add-command", "Hi", 'notify-send "hi there"', "--hold"],
            ],
        )
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write("")
            for b in built:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(b["args"]), 0)
            c = load_config()[0]
            self.assertEqual((c.apps[0].label, c.apps[0].key), ("Terminal", "ctrl+alt+t"))
            self.assertEqual((c.bar[0].command, c.bar[0].mode), (["/bin/sh", "-c", 'notify-send "hi there"'], "hold"))


if __name__ == "__main__":
    unittest.main()
