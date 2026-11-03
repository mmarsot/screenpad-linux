"""Tests for bringing earlier configs up to date: defaults, default blocks, buttons and shortcuts made items.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import tempfile
import unittest

from touchbar_support import REPO, ConfigHome

from screenpad_touchbar.common import ConfigError
from screenpad_touchbar.config import load_config
from screenpad_touchbar.defaults import EXAMPLE_CONFIG, ITEMS_DOC, LAYOUT_DOC, V7_BUTTONS_DOC
from screenpad_touchbar.migrate import convert_items, migrate_config


def default(version):
    with open(os.path.join(REPO, "tests", "touchbar-v%d-default.toml" % version)) as f:
        return f.read()


# A config of the version before items, as its user edited it: Push to
# talk, two apps' tiles, then two shortcuts.
V7_EDITED = (
    default(7)
    .replace("height = 160", "height = 184")
    .replace(
        """# Mutes and unmutes the microphone, like a keyboard's mic mute key.
[[button]]
icon = "microphone-sensitivity-muted-symbolic"
label = "Mute microphone"
mode = "tap"
key = "micmute"

# GNOME's screenshot tool, like the Print Screen key.
[[button]]
icon = "applets-screenshooter-symbolic"
label = "Screenshot"
mode = "tap"
key = "printscreen"
""",
        """# Herdr's agents, drawn by screenpad-agents.
[[button]]
icon = "utilities-terminal-symbolic"
label = "Agents"
tile = "agents"

[[button]]
icon = "dialog-question-symbolic"
label = "Calls"
tile = "calls"
""",
    )
    + '\n[[shortcut]]\napp = "bitwarden.desktop"\n\n[[shortcut]]\napp = "org.gnome.Settings.desktop"\n'
)


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = ConfigHome(self.tmp.name)
        self.path = self.home.__enter__()
        self.addCleanup(self.home.__exit__, None, None, None)
        os.makedirs(os.path.dirname(self.path))

    def migrate(self, text):
        """What migrate_config() makes of text, and whether it changed it."""
        with open(self.path, "w") as f:
            f.write(text)
        with contextlib.redirect_stderr(io.StringIO()):
            changed = migrate_config()
        with open(self.path) as f:
            return f.read(), changed

    def test_every_earlier_default_becomes_the_current_one(self):
        for version in range(1, 9):
            out, changed = self.migrate(default(version))
            self.assertTrue(changed, version)
            self.assertEqual(out, EXAMPLE_CONFIG, version)
            self.assertEqual(self.migrate(out), (EXAMPLE_CONFIG, False))

    def test_an_edited_default_keeps_its_edits(self):
        # The two last defaults with their height changed become the
        # current default with that change, block by block.
        expected = EXAMPLE_CONFIG.replace("height = 160", "height = 200")
        for version in (6, 7, 8):
            out, changed = self.migrate(default(version).replace("height = 160", "height = 200"))
            self.assertTrue(changed, version)
            self.assertEqual(out, expected, version)
            self.assertEqual(self.migrate(out), (expected, False), version)
        # The earlier ones lack some of its comments, which they do not
        # gain; their buttons become items.
        for version in range(1, 6):
            out, _ = self.migrate(default(version).replace("height = 160", "height = 200"))
            c = load_config()[0]
            # The first had Push to talk alone.
            labels = ["Push to talk"] + ([] if version == 1 else ["Mute microphone", "Screenshot"])
            self.assertEqual((c.height, [i.label for i in c.bar]), (200, labels), version)
            # The first's Push to talk block was not the one Alt+K replaced.
            self.assertEqual(c.bar[0].keys, (184,) if version == 1 else (56, 37), version)
            self.assertNotIn("\n[[button]]\n", out, version)
            self.assertEqual(self.migrate(out), (out, False), version)

    def test_the_version_before_items(self):
        out, changed = self.migrate(V7_EDITED)
        self.assertTrue(changed)
        c = load_config()[0]
        self.assertEqual(
            [(i.kind, i.name or i.label) for i in c.bar],
            [
                ("key", "Push to talk"),
                ("tile", "agents"),
                ("tile", "calls"),
                ("app", "bitwarden.desktop"),
                ("app", "org.gnome.Settings.desktop"),
            ],
        )
        self.assertEqual((c.height, c.apps), (184, []))
        # Each block where it was, with its comments; the comments about
        # buttons and shortcuts are now the ones about items.
        self.assertEqual(out.count("\n[[bar]]\n"), 5)
        self.assertIn("# Herdr's agents, drawn by screenpad-agents.\n[[bar]]\nicon", out)
        self.assertEqual((out.count(ITEMS_DOC), out.count(LAYOUT_DOC), out.count(V7_BUTTONS_DOC)), (1, 1, 0))
        self.assertNotIn("shortcut]]", out)
        self.assertEqual(self.migrate(out), (out, False))

    def test_shortcuts_before_buttons_go_after_them(self):
        text = (
            'height = 100\n\n[[shortcut]]\napp = "a.desktop"\n\n# Talk.\n[[button]]\nlabel = "PTT"\n'
            'x = 0\nwidth = 480\nmode = "hold"\nkey = "F14"\n\n[pager]\nreturn_after = 5\n'
        )
        out, changed = self.migrate(text)
        self.assertTrue(changed)
        self.assertEqual(
            out,
            'height = 100\n\n\n# Talk.\n[[bar]]\nlabel = "PTT"\nmode = "hold"\nkey = "F14"\n\n'
            '[[bar]]\napp = "a.desktop"\n\n[pager]\nreturn_after = 5\n',
        )
        self.assertEqual(load_config()[0].pages.return_after, 5)

    def test_what_cannot_be_converted_is_left_alone(self):
        for text in (
            # Old and new blocks together, an invalid button, a header
            # inside a string.
            '[[bar]]\napp = "a"\n[[shortcut]]\napp = "b"\n',
            '[[button]]\nlabel = "A"\nkey = "nope"\n',
            '[[button]]\nlabel = """\n[[shortcut]]\n"""\nkey = "a"\n',
            "height = ",
        ):
            self.assertEqual(self.migrate(text), (text, False), text)
        with self.assertRaises(ConfigError):
            convert_items('[[button]]\nlabel = "A"\nkey = "nope"\n')
        # Nothing of an earlier version's: nothing to do.
        self.assertIsNone(convert_items('[[bar]]\napp = "a"\n'))
        self.assertEqual(self.migrate("height = 100\n"), ("height = 100\n", False))
        # No file: nothing to do.
        os.unlink(self.path)
        self.assertFalse(migrate_config())

    def test_a_push_to_talk_key_the_user_chose_stays(self):
        self.migrate(default(2).replace('key = "F14"', 'key = "ctrl+alt+p"'))
        self.assertEqual(load_config()[0].bar[0].keys, (29, 56, 25))

    def test_the_tile_lines_are_added_once(self):
        out, _ = self.migrate(
            default(3).replace("height = 160", "height = 168") + '\n[[shortcut]]\napp = "bitwarden.desktop"\n'
        )
        c = load_config()[0]
        self.assertEqual((c.height, [i.name for i in c.bar][3:]), (168, ["bitwarden.desktop"]))
        self.assertEqual(out.count("#   tile "), 1)


if __name__ == "__main__":
    unittest.main()
