"""Static checks of the GNOME Shell extension against the daemon's constants.

Run with: python3 -m unittest discover -s tests"""

import os
import re
import unittest

from touchbar_support import REPO, notify, read

from screenpad_touchbar.common import (
    ASK_ARM,
    DBUS_IFACE,
    DBUS_PATH,
    LAUNCH,
    MAX_OPTIONS,
    RENDERER_NAME,
    RENDERER_PROTOCOL,
    SOCKET_NAME,
    TILE_NAME,
    URGENCIES,
)
from screenpad_touchbar.notify import parse_notify
from screenpad_touchbar.scene import TILE_CLASSES, TILE_STYLES


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
        self.assertLess(grow, ASK_ARM * 1000)

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
        self.assertIn("const DBUS_PATH = '%s';" % DBUS_PATH, src)
        self.assertIn('<interface name="%s">' % DBUS_IFACE, src)
        self.assertEqual(re.findall(r"^    (Launch)\(", src, re.M), ["Launch"])
        self.assertIn("--method %s.Launch" % DBUS_IFACE, LAUNCH[2])

    def test_styles_for_every_tile_style_and_class(self):
        path = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad", "stylesheet.css")
        with open(path) as f:
            css = f.read()
        for style in TILE_STYLES:
            self.assertIn(".screenpad-touchbar-%s {" % style, css)
        # A tap button is the plain button style; every other class has a rule.
        for name in set(TILE_CLASSES) - {"tap"}:
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
        self.assertIn("const RENDERER_NAME = '%s';" % RENDERER_NAME, src)
        self.assertIn("const RENDERER_PROTOCOL = %d;" % RENDERER_PROTOCOL, src)
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


class ExtensionMirrorTests(unittest.TestCase):
    """The extension's messages against the daemon's protocol."""

    def test_messages_source_contract(self):
        # Source contract check, not a behavioral test: it compares the
        # constants and notify fields written in the extension's text with
        # the daemon's protocol.
        src = read("notifications.js")
        names = re.search(r"const URGENCY = \[([^\]]*)\];", src).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", names)), URGENCIES)
        self.assertTrue(TILE_NAME.fullmatch(re.search(r"const APP = '([^']+)';", src).group(1)))
        action = re.search(r"const ACTION = /(.*)/;", src).group(1)
        self.assertRegex("action-%d" % (MAX_OPTIONS - 1), action)
        body = src[re.search(r"op:\s*'notify'\s*,", src).end() :]
        body = body[: re.search(r"}\s*\)", body).start()]
        keys = set(re.findall(r"^\s*(\w+)\s*[:,]", body, re.M)) | {"id"}
        self.assertEqual(keys, set(parse_notify(notify())))
        ext = read("extension.js")
        self.assertEqual(re.search(r"const SOCKET_NAME = '([^']+)';", ext).group(1), SOCKET_NAME)


if __name__ == "__main__":
    unittest.main()
