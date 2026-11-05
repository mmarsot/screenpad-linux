"""Tests for the zone split and the bar's buttons, shortcuts and grip.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import unittest

from touchbar_support import AXES, BAR_CONFIG, SYN, Split, codes, config, lift, touch

from screenpad_touchbar.bar import Bar
from screenpad_touchbar.codes import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TOOL_TYPE,
    ABS_MT_TRACKING_ID,
    ABS_X,
    ABS_Y,
    BTN_LEFT,
    BTN_TOOL_DOUBLETAP,
    BTN_TOOL_FINGER,
    BTN_TOUCH,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    MT_TOOL_PALM,
    SYN_DROPPED,
)
from screenpad_touchbar.common import (
    LONG_PRESS,
    MAX_HEIGHT,
    MIN_HEIGHT,
)
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.splitter import RESYNC


class SplitTests(unittest.TestCase):
    def test_axes(self):
        self.assertEqual(AXES.panel(0, 0), (0, 0))
        self.assertEqual(AXES.panel(3840, 2160), (1920, 1080))
        self.assertEqual(AXES.panel(640, 320), (320, 160))

    def test_pad_contact_passes_through(self):
        sp = Split()
        pad, bar, _ = sp.frame(touch(0, 7, 1000, 1000))
        self.assertEqual(bar, [])
        self.assertEqual(
            pad,
            [
                (EV_ABS, ABS_MT_SLOT, 0),
                (EV_ABS, ABS_MT_TRACKING_ID, 7),
                (EV_ABS, ABS_MT_TOOL_TYPE, 0),
                (EV_ABS, ABS_MT_POSITION_X, 1000),
                (EV_ABS, ABS_MT_POSITION_Y, 1000),
                (EV_KEY, BTN_TOUCH, 1),
                (EV_KEY, BTN_TOOL_FINGER, 1),
                (EV_ABS, ABS_X, 1000),
                (EV_ABS, ABS_Y, 1000),
                SYN,
            ],
        )
        pad, _, _ = sp.frame(touch(0, None, 1010, 990))
        self.assertEqual(
            pad,
            [
                (EV_ABS, ABS_MT_SLOT, 0),
                (EV_ABS, ABS_MT_POSITION_X, 1010),
                (EV_ABS, ABS_MT_POSITION_Y, 990),
                (EV_ABS, ABS_X, 1010),
                (EV_ABS, ABS_Y, 990),
                SYN,
            ],
        )
        pad, _, _ = sp.frame(lift(0))
        self.assertEqual(
            pad,
            [
                (EV_ABS, ABS_MT_SLOT, 0),
                (EV_ABS, ABS_MT_TRACKING_ID, -1),
                (EV_KEY, BTN_TOUCH, 0),
                (EV_KEY, BTN_TOOL_FINGER, 0),
                SYN,
            ],
        )

    def test_bar_contact_never_reaches_the_pad(self):
        sp = Split()
        # Starts in the bar (panel y 100 < 160), then slides well into the pad.
        pad, bar, _ = sp.frame(touch(0, 1, 400, 200))
        self.assertEqual((pad, bar), ([], [("begin", 0, 200, 100)]))
        pad, bar, _ = sp.frame(touch(0, None, 400, 1500))
        self.assertEqual((pad, bar), ([], [("move", 0, 200, 750)]))
        pad, bar, _ = sp.frame(lift(0))
        self.assertEqual((pad, bar), ([], [("end", 0, 200, 750)]))

    def test_pad_contact_sliding_into_the_bar_stays_a_pad_contact(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        pad, bar, _ = sp.frame(touch(0, None, 1000, 100))
        self.assertEqual(bar, [])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_POSITION_Y), [100])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [])

    def test_boundary(self):
        sp = Split()
        _, bar, _ = sp.frame(touch(0, 1, 100, 319))  # panel y 159.5: bar
        self.assertEqual([e[0] for e in bar], ["begin"])
        pad, bar, _ = sp.frame(touch(1, 2, 2000, 320))  # panel y 160: pad
        self.assertEqual(bar, [])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [2])

    def test_bar_finger_does_not_change_the_finger_count(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        pad, bar, _ = sp.frame(touch(1, 2, 400, 100))
        self.assertEqual(pad, [])
        self.assertEqual(bar[0][0], "begin")
        # A second pad finger makes it a two-finger gesture on the virtual pad.
        pad, _, _ = sp.frame(touch(2, 3, 2000, 1000))
        self.assertEqual(codes(pad, EV_KEY, BTN_TOOL_DOUBLETAP), [1])
        self.assertEqual(codes(pad, EV_KEY, BTN_TOOL_FINGER), [0])
        self.assertEqual(codes(pad, EV_KEY, BTN_TOUCH), [])

    def test_click_only_counts_with_a_pad_finger(self):
        sp = Split()
        sp.frame(touch(0, 1, 400, 100))
        pad, _, _ = sp.frame(left=True)
        self.assertEqual(pad, [])
        pad, _, _ = sp.frame(left=False)
        self.assertEqual(pad, [])
        sp.frame(lift(0))
        sp.frame(touch(0, 2, 1000, 1000))
        pad, _, _ = sp.frame(left=True)
        self.assertEqual(codes(pad, EV_KEY, BTN_LEFT), [1])
        pad, _, _ = sp.frame(left=False)
        self.assertEqual(codes(pad, EV_KEY, BTN_LEFT), [0])

    def test_palm_in_the_bar_is_ignored(self):
        sp = Split()
        _, bar, _ = sp.frame(touch(0, 1, 400, 100) + [(EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PALM)])
        self.assertEqual(bar, [])
        sp.frame([(EV_ABS, ABS_MT_TOOL_TYPE, 0)])
        _, bar, _ = sp.frame(touch(1, 2, 800, 100))
        self.assertEqual(bar[0][0], "begin")
        _, bar, _ = sp.frame([(EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PALM)])
        self.assertEqual(bar, [("cancel", 1, 400, 50)])

    def test_slot_reused_by_a_new_contact(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        # The pad lifts and puts a new finger down in one frame: the virtual
        # pad sees the old contact end in a frame of its own.
        pad, _, _ = sp.frame(touch(0, 2, 1200, 1200))
        first = pad[: pad.index(SYN) + 1]
        self.assertIn((EV_ABS, ABS_MT_TRACKING_ID, -1), first)
        self.assertIn((EV_ABS, ABS_MT_TRACKING_ID, 2), pad[len(first) :])

    def test_dropped_events_resync(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000))
        self.assertIsNone(sp.s.feed(EV_SYN, SYN_DROPPED, 0))
        self.assertIsNone(sp.s.feed(EV_ABS, ABS_MT_POSITION_X, 5))
        self.assertEqual(sp.s.feed(*SYN), RESYNC)
        ids, xs, ys = [-1, 9, -1, -1, -1], [0, 400, 0, 0, 0], [0, 100, 0, 0, 0]
        pad, bar, _ = sp.s.resync(ids, xs, ys, [0] * 5, 0, 1)
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(bar, [("begin", 1, 200, 50)])

    def test_release_all(self):
        sp = Split()
        sp.frame(touch(0, 1, 1000, 1000), left=True)
        sp.frame(touch(1, 2, 400, 100))
        pad, bar, _ = sp.s.release_all()
        self.assertEqual(bar, [("cancel", 1, 200, 50)])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(codes(pad, EV_KEY, BTN_TOUCH), [0])
        self.assertEqual(codes(pad, EV_KEY, BTN_LEFT), [0])
        # Nothing more comes out for the contacts still on the pad.
        pad, bar, _ = sp.frame(touch(0, None, 1100, 1000), touch(1, None, 500, 100))
        self.assertEqual((pad, bar), ([], []))


class BarTests(unittest.TestCase):
    CONFIG = BAR_CONFIG

    def setUp(self):
        self.frames, self.ran, self.launched, self.removed, self.resized = [], [], [], [], []
        self.now = 100.0
        self.bar = self.make(config(self.CONFIG))

    def make(self, c):
        def remove(i, item):
            self.removed.append(i)
            return True

        def resize(h):
            self.resized.append(h)
            return True

        return Bar(
            c,
            KeyEmitter(self.frames.append),
            self.ran.append,
            self.launched.append,
            remove,
            resize,
            clock=lambda: self.now,
        )

    def test_hold_sends_down_on_touch_and_up_on_release(self):
        self.assertTrue(self.bar.handle([("begin", 0, 100, 50)]))
        self.assertEqual(self.frames, [[(EV_KEY, 184, 1), SYN]])
        self.assertEqual(self.bar.pressed(), [0])
        # Sliding off a hold button keeps it held until the finger lifts.
        self.bar.handle([("move", 0, 900, 600)])
        self.assertEqual(len(self.frames), 1)
        self.bar.handle([("end", 0, 900, 600)])
        self.assertEqual(self.frames[-1], [(EV_KEY, 184, 0), SYN])
        self.assertEqual(self.bar.pressed(), [])

    def test_tap_fires_on_lift_inside(self):
        self.bar.handle([("begin", 1, 400, 50)])
        self.assertEqual(self.frames, [])
        self.assertEqual(self.bar.pressed(), [1])
        self.bar.handle([("end", 1, 410, 60)])
        self.assertEqual(self.frames, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])

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
        for x in (830, 1500):  # the gap; the sixth spot, empty
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
        self.assertEqual(self.bar.deadline(), 100.0 + LONG_PRESS)
        self.now += LONG_PRESS
        self.assertTrue(self.bar.tick())
        self.assertEqual(self.bar.removing(), [4])
        self.assertIsNone(self.bar.deadline())
        self.assertFalse(self.bar.tick())
        self.bar.handle([("end", 0, 1300, 50)])
        # The app's index among the bar's items.
        self.assertEqual((self.launched, self.removed), ([], [4]))

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
        self.assertEqual(self.bar.height(), MAX_HEIGHT)
        self.bar.handle([("move", 0, 1850, -500)])
        self.assertEqual(self.bar.height(), MIN_HEIGHT)
        # A second finger on the grip is ignored.
        self.assertFalse(self.bar.handle([("begin", 1, 1850, 50)]))
        self.bar.handle([("move", 0, 1850, 180)])
        self.bar.handle([("end", 0, 1850, 180)])
        self.assertEqual(self.resized, [240])
        # It shows the new height until the reloaded config has it.
        self.assertEqual(self.bar.height(), 240)
        taller = self.CONFIG.replace("[[bar]]", "height = 240\n[[bar]]", 1)
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
        self.assertEqual(self.frames, [[(EV_KEY, 184, 0), SYN]])
        self.assertEqual(self.launched, [])

    def test_release_all(self):
        self.bar.handle([("begin", 0, 100, 50), ("begin", 2, 700, 50), ("begin", 3, 1850, 50), ("move", 3, 1850, 300)])
        self.bar.release_all()
        self.assertEqual(self.frames[-1], [(EV_KEY, 184, 0), SYN])
        self.assertEqual(self.ran[-1], ["/bin/sh", "-c", "b"])
        self.assertEqual((self.bar.pressed(), self.bar.height(), self.resized), ([], 160, []))


if __name__ == "__main__":
    unittest.main()
