"""Tests for the extension's fnkey.js, the "ScreenPad on" indicator Fn+F6
shows when it lights the ScreenPad's display, run under node on the scenes
the daemon publishes, with a stand-in clock: shown once per press that
lights the display, in place of GNOME's touchpad-off indicator for that
press whichever comes first, and never for another press, another
indicator, a daemon restarted or one from before the indicator.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest

from touchbar_support import ConfigHome, FakePanel, FakeSetting, config, hotkey, js

from screenpad_touchbar.common import state_path
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.fnkey import FLIP_WAIT, Toggle

# Replays v.events ([time in ms, "scene", a scene] or [time, "gnome", an
# indicator's icon names]) on a ScreenpadOn: for each, whether the extension
# shows "ScreenPad on" (a scene) or shows it in place of GNOME's (an
# indicator).
SIM = """(() => {
    let t = 0;
    const s = new m.ScreenpadOn(() => t);
    return v.events.map(([time, what, arg]) => {
        t = time;
        return what === 'scene' ? s.update(arg) : s.replaces(arg);
    });
})()"""

OFF = ["touchpad-disabled-symbolic", "input-touchpad-symbolic"]
ON = ["input-touchpad-symbolic"]


class ScreenpadOnTests(unittest.TestCase):
    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(ConfigHome(d))
        flag = os.path.join(d, "active")
        open(flag, "w").close()
        self.setting = FakeSetting()
        self.addCleanup(self.setting.stop)
        self.panel = FakePanel(lit=True)
        self.toggle = Toggle(self.setting, self.panel, find=lambda: None, flag=flag)
        hotkey(self, self.toggle)
        self.daemon = Daemon(config(), None, flag=flag, toggle=self.toggle)

    def scene(self):
        self.daemon.publish()
        with open(state_path()) as f:
            return json.load(f)

    def press(self):
        """Fn+F6, with GNOME's flip; the scene after it."""
        self.toggle.press()
        self.setting.flip()
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        return self.scene()

    def cycle(self):
        """The scenes in (3), then after each press: (1), (2), (3)."""
        return [self.scene(), self.press(), self.press(), self.press()]

    def sim(self, *events):
        return js("fnkey.js", SIM, events=list(events))

    def test_the_scene_first_then_gnome(self):
        lit, off, pad, on = self.cycle()
        self.assertEqual(on["fnkey"]["lit"], 1)
        self.assertEqual(
            self.sim(
                [0, "scene", lit],
                [10, "scene", off],
                [15, "gnome", OFF],  # (3) to (1): the touchpad is off
                [20, "scene", pad],
                [25, "gnome", ON],
                [30, "scene", on],  # (2) to (3): shown at once
                [40, "gnome", OFF],  # and GNOME's shows as ours
                [50, "gnome", OFF],  # once only
            ),
            [False, False, False, False, False, True, True, False],
        )

    def test_gnome_first_then_the_scene(self):
        _, _, pad, on = self.cycle()
        self.assertTrue(pad["fnkey"]["lights_next"])
        self.assertEqual(
            # GNOME's shows as ours before the scene says why; the scene
            # then shows nothing more, and the next indicator is GNOME's.
            self.sim([0, "scene", pad], [10, "gnome", OFF], [20, "scene", on], [30, "gnome", OFF]),
            [False, True, False, False],
        )

    def test_without_gnome_it_still_shows(self):
        _, _, pad, on = self.cycle()
        # GNOME does not handle the key, or its indicator comes too late.
        late = 1000 * FLIP_WAIT + 10
        self.assertEqual(
            self.sim([0, "scene", pad], [10, "scene", on], [late, "gnome", OFF]),
            [False, True, False],
        )

    def test_only_the_touchpad_off_indicator_is_replaced(self):
        _, _, pad, on = self.cycle()
        self.assertEqual(
            self.sim(
                [0, "scene", pad],
                [5, "gnome", ["audio-volume-high-symbolic"]],
                [6, "gnome", ON],
                [7, "gnome", []],  # an icon that is not a themed one
                [10, "scene", on],
                [12, "gnome", ["display-brightness-symbolic"]],
            ),
            [False, False, False, False, True, False],
        )

    def test_a_new_daemon_or_an_old_one_shows_nothing(self):
        _, _, pad, on = self.cycle()
        restarted = dict(on, pid=on["pid"] + 1)
        old = {k: v for k, v in on.items() if k != "fnkey"}
        self.assertEqual(
            self.sim(
                [0, "scene", on],  # the first scene read
                [10, "scene", pad],
                [20, "scene", restarted],
                [30, "scene", None],  # the daemon stopped
                [40, "scene", on],
                [50, "scene", old],
                [60, "gnome", OFF],
            ),
            [False, False, False, False, False, False, False],
        )
        # A daemon without the cycle, or not following Fn+F6.
        none = dict(pad, fnkey=None)
        self.assertEqual(self.sim([0, "scene", none], [10, "gnome", OFF]), [False, False])

    def test_the_indicator_matches_the_daemon(self):
        self.assertEqual(js("fnkey.js", "m.SAME_PRESS_MS"), 1000 * FLIP_WAIT)
        self.assertEqual(js("fnkey.js", "m.SCREENPAD_ON_LABEL"), "ScreenPad on")
        self.assertTrue(js("fnkey.js", "m.SCREENPAD_ON_ICON").endswith("-symbolic"))


if __name__ == "__main__":
    unittest.main()
