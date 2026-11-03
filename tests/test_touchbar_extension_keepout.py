"""Tests for the extension's fence.js, what keeps the desktop off the
ScreenPad, run under node: the pointer barriers on the edges it shares with
other monitors (on the other monitor's pixels, letting the pointer leave),
none when there is nothing to fence, which windows go back to the primary
monitor and where, and the overview view left out.

keepout.js, which puts this into GNOME Shell, is not run here; see
docs/development.md for the headless GNOME Shell check.

Run with: python3 -m unittest discover -s tests
"""

import unittest

from touchbar_support import js

LAPTOP = {"x": 0, "y": 0, "width": 1920, "height": 1080}
# The ScreenPad below the laptop screen, as GNOME's display settings join it.
BELOW = {"x": 0, "y": 1080, "width": 1920, "height": 515}
DIRECTIONS = {"POSITIVE_X", "NEGATIVE_X", "POSITIVE_Y", "NEGATIVE_Y"}
NORMAL = {"type": "NORMAL", "skipTaskbar": False, "attached": False, "grabbed": False, "monitor": 1}


def fence(index, monitors, primary=0):
    return js("fence.js", "m.fenceFor(v.index, v.monitors, v.primary)", index=index, monitors=monitors, primary=primary)


def barriers(index, monitors, primary=0):
    return [[b["x1"], b["y1"], b["x2"], b["y2"], b["direction"]] for b in fence(index, monitors, primary)["barriers"]]


class Barriers(unittest.TestCase):
    def test_below_the_laptop_screen(self):
        # On the laptop screen's last row, so the pointer stops there, and
        # crossing upwards only: off the ScreenPad.
        self.assertEqual(barriers(1, [LAPTOP, BELOW]), [[0, 1079, 1920, 1079, "NEGATIVE_Y"]])
        self.assertEqual(fence(1, [LAPTOP, BELOW])["index"], 1)
        self.assertEqual(fence(1, [LAPTOP, BELOW])["primary"], 0)

    def test_every_side(self):
        pad = {"x": 1920, "y": 0, "width": 1920, "height": 515}
        self.assertEqual(barriers(1, [LAPTOP, pad]), [[1919, 0, 1919, 515, "NEGATIVE_X"]])
        laptop = dict(LAPTOP, x=1920)
        pad = {"x": 0, "y": 0, "width": 1920, "height": 515}
        self.assertEqual(barriers(1, [laptop, pad]), [[1920, 0, 1920, 515, "POSITIVE_X"]])
        laptop = dict(LAPTOP, y=515)
        self.assertEqual(barriers(1, [laptop, pad]), [[0, 515, 1920, 515, "POSITIVE_Y"]])

    def test_only_the_shared_part(self):
        pad = {"x": 480, "y": 1080, "width": 960, "height": 258}
        self.assertEqual(barriers(1, [LAPTOP, pad]), [[480, 1079, 1440, 1079, "NEGATIVE_Y"]])
        wide = {"x": -200, "y": 1080, "width": 2400, "height": 515}
        self.assertEqual(barriers(1, [LAPTOP, wide]), [[0, 1079, 1920, 1079, "NEGATIVE_Y"]])

    def test_one_per_neighbour(self):
        # An external monitor right of the ScreenPad gets its own barrier,
        # whichever is the primary; one touching only a corner gets none.
        external = {"x": 1920, "y": 1080, "width": 2560, "height": 1440}
        corner = {"x": 1920, "y": 0, "width": 1920, "height": 1080}
        self.assertEqual(
            barriers(1, [LAPTOP, BELOW, external, corner], primary=2),
            [[0, 1079, 1920, 1079, "NEGATIVE_Y"], [1920, 1080, 1920, 1595, "POSITIVE_X"]],
        )

    def test_directions_are_meta_names(self):
        layouts = [
            [LAPTOP, BELOW],
            [LAPTOP, {"x": 1920, "y": 0, "width": 1920, "height": 515}],
            [dict(LAPTOP, x=1920), {"x": 0, "y": 0, "width": 1920, "height": 515}],
            [dict(LAPTOP, y=515), {"x": 0, "y": 0, "width": 1920, "height": 515}],
        ]
        seen = {b[4] for monitors in layouts for b in barriers(1, monitors)}
        self.assertEqual(seen, DIRECTIONS)

    def test_nothing_to_fence(self):
        self.assertIsNone(fence(0, [LAPTOP, BELOW], primary=0))  # the ScreenPad is the primary
        self.assertIsNone(fence(1, [LAPTOP]))  # no such monitor
        self.assertIsNone(fence(0, [BELOW], primary=1))  # no primary to go back to
        apart = dict(BELOW, y=1200)
        self.assertEqual(barriers(1, [LAPTOP, apart]), [])  # windows still go back


class Windows(unittest.TestCase):
    def evicts(self, fenced=True, **window):
        return js(
            "fence.js",
            "m.evicts(v.window, v.fenced ? m.fenceFor(1, v.monitors, 0) : null)",
            window=dict(NORMAL, **window),
            fenced=fenced,
            monitors=[LAPTOP, BELOW],
        )

    def test_which_go_back(self):
        for kind in js("fence.js", "m.EVICTED_TYPES"):
            self.assertTrue(self.evicts(type=kind), kind)
        for kind in ("DESKTOP", "DOCK", "MENU", "TOOLTIP", "NOTIFICATION", "DND", "POPUP_MENU", "COMBO"):
            self.assertFalse(self.evicts(type=kind), kind)
        self.assertFalse(self.evicts(skipTaskbar=True))
        self.assertFalse(self.evicts(attached=True))
        self.assertFalse(self.evicts(grabbed=True))
        self.assertFalse(self.evicts(monitor=0))
        self.assertFalse(self.evicts(fenced=False))

    def frame(self, frame, area=None):
        area = area or {"x": 0, "y": 32, "width": 1920, "height": 1048}
        return js("fence.js", "m.evictFrame(v.frame, v.area)", frame=frame, area=area)

    def test_hanging_over_moves_up_to_fit(self):
        self.assertEqual(
            self.frame({"x": 300, "y": 1040, "width": 800, "height": 600}),
            {"x": 300, "y": 480, "width": 800, "height": 600},
        )
        # Hanging over a corner too: in from both sides.
        self.assertEqual(
            self.frame({"x": 1700, "y": 1000, "width": 800, "height": 600}),
            {"x": 1120, "y": 480, "width": 800, "height": 600},
        )

    def test_larger_than_the_work_area_shrinks(self):
        self.assertEqual(
            self.frame({"x": -50, "y": 900, "width": 2400, "height": 1300}),
            {"x": 0, "y": 32, "width": 1920, "height": 1048},
        )

    def test_entirely_on_the_screenpad_is_left_to_mutter(self):
        self.assertIsNone(self.frame({"x": 100, "y": 1200, "width": 400, "height": 300}))
        # Touching the work area's edge is not over it.
        self.assertIsNone(self.frame({"x": 100, "y": 1080, "width": 400, "height": 300}))


class Overview(unittest.TestCase):
    def test_only_the_screenpad_view_is_hidden(self):
        hidden = js(
            "fence.js",
            "[0, 1, 2].map(i => m.hidesView(m.fenceFor(1, v.monitors, 0), i))",
            monitors=[LAPTOP, BELOW, {"x": 1920, "y": 0, "width": 1920, "height": 1080}],
        )
        self.assertEqual(hidden, [False, True, False])
        self.assertFalse(js("fence.js", "m.hidesView(null, 1)"))


if __name__ == "__main__":
    unittest.main()
