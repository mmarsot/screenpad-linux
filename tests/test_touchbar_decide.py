"""Tests for the apps page's touch decision (decide.py), through the daemon:
one finger moves the pointer, a still tap or press acts on the widget or
item under it, a long press, an item held then slid to the bar, two
fingers and the pad's button for the touchpad, the 0.3 s guard, the timer,
and the settings.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import AXES, GRID_H, GRID_Y, SYN, Clock, ConfigHome, codes, hello, lift, page_msg, touch

from screenpad_touchbar import daemon as daemon_module
from screenpad_touchbar.codes import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_TRACKING_ID,
    BTN_LEFT,
    EV_ABS,
    EV_KEY,
)
from screenpad_touchbar.common import APPS, ASK_ARM, LONG_PRESS, RENDERER_NAME, TAP_TIME, WIDGETS_PROTOCOL, runtime_file
from screenpad_touchbar.config import load_config
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.splitter import Splitter

# The bar holds PTT, then five empty spots; the apps page's home list Mute
# (cell 0, x 0..480) and then the agents page (cell 1, x 480..960), in the
# row from GRID_Y; the cells after them have a + each. Above them, the
# header's title takes no touch.
CONFIG = """
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"
[[apps]]
label = "Mute"
key = "micmute"
"""
ROW = GRID_Y + GRID_H // 4  # the home list's first row, in panel pixels
MUTE = (200, ROW)
FREE = (1700, ROW)
TITLE = (1000, GRID_Y - 60)
# On the agents page (3 by 2): Allow (long press) and Deny in the lower row.
LOWER = GRID_Y + GRID_H * 3 // 4
ALLOW, DENY = (300, LOWER), (1700, LOWER)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.path = self.enterContext(ConfigHome(self.enterContext(tempfile.TemporaryDirectory())))
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write(CONFIG)
        self.clock = Clock()
        self.daemon = d = Daemon(load_config()[0], None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = lambda: True
        self.keys = []
        d.bar = d.new_bar(KeyEmitter(self.keys.append))
        d.bar.clock = self.clock
        d.splitter = Splitter(AXES, 5, d._zone)
        self.sent = []
        self.enterContext(mock.patch.object(daemon_module, "write_events", lambda fd, ev: self.sent.extend(ev)))
        os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)), exist_ok=True)
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL, "drawn": True}, f)
        self.client = hello(d.apps)
        d.apps.handle(self.client, page_msg())
        d.pager.turn(APPS)
        d.surface.refresh()
        self.clock.now += ASK_ARM  # the page has been shown a while
        self.contact = 0

    def frame(self, *groups, left=None):
        """Feeds a frame; the virtual touchpad's events for it."""
        self.sent.clear()
        events = [e for g in groups for e in g]
        if left is not None:
            events.append((EV_KEY, BTN_LEFT, int(left)))
        out = None
        for e in events + [SYN]:
            out = self.daemon.splitter.feed(*e)
        assert out is not None and out != "resync"
        self.daemon.route(*out)
        return list(self.sent)

    def down(self, at, slot=0):
        """A finger down at a point in panel pixels (device units are twice that)."""
        self.contact += 1
        return self.frame(touch(slot, self.contact, at[0] * 2, at[1] * 2))

    def move(self, at, slot=0):
        return self.frame(touch(slot, None, at[0] * 2, at[1] * 2))

    def wait(self, seconds):
        self.clock.now += seconds
        self.daemon.surface.tick()
        return self.daemon.settle()

    def events(self):
        return [m["type"] for m in self.client.sent if m["op"] == "event"]

    def open_agents(self):
        self.daemon.surface.open = "agents"
        self.daemon.surface.refresh()
        self.clock.now += ASK_ARM

    def test_a_still_tap_presses_and_never_reaches_the_pointer(self):
        clicks = self.daemon.clicks
        self.assertEqual(self.down(MUTE), [])
        self.assertEqual(self.move((MUTE[0] + 20, MUTE[1] + 20)), [])  # 28 px, under 2.5 mm
        self.clock.now += TAP_TIME - 0.05
        self.assertEqual(self.frame(lift(0)), [])
        self.assertEqual(self.keys, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])
        self.assertEqual(self.daemon.clicks, clicks + 1)
        self.assertEqual(self.daemon.splitter.sent, {})

    def test_a_slide_moves_the_pointer_and_presses_nothing(self):
        clicks = self.daemon.clicks
        self.down(MUTE)
        pad = self.move((MUTE[0] + 50, MUTE[1]))
        # Handed to the touchpad from where it went down, then where it is:
        # libinput sees all its motion.
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [1])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_POSITION_X), [MUTE[0] * 2, (MUTE[0] + 50) * 2])
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_POSITION_Y), [MUTE[1] * 2])
        self.assertEqual(codes(pad, EV_KEY, BTN_LEFT), [])
        # From then on it is a touchpad finger, held still or moved.
        self.assertEqual(self.wait(1), False)
        self.assertEqual(codes(self.move((MUTE[0], MUTE[1])), EV_ABS, ABS_MT_POSITION_X), [MUTE[0] * 2])
        # It keeps the apps page up while it is down.
        self.assertIsNone(self.daemon.pager.deadline())
        self.assertEqual(codes(self.frame(lift(0)), EV_ABS, ABS_MT_TRACKING_ID), [-1])
        self.assertEqual((self.keys, self.daemon.clicks), ([], clicks))

    def test_a_slide_over_a_widget_sends_its_app_nothing(self):
        self.open_agents()
        self.down(DENY)
        self.move((DENY[0] - 100, DENY[1]))
        self.frame(lift(0))
        self.assertEqual(self.events(), [])

    def test_a_still_press_then_its_lift(self):
        self.open_agents()
        self.down(DENY)
        self.assertTrue(self.wait(TAP_TIME))
        self.assertEqual(self.events(), ["down"])
        # Now the surface's: a slide does not move the pointer.
        self.assertEqual(self.move((DENY[0] + 30, DENY[1] - 30)), [])
        self.assertEqual(self.move((DENY[0] + 100, DENY[1] - 100)), [])
        self.frame(lift(0))
        self.assertEqual(self.events(), ["down", "tap"])
        # Sliding away from it cancels.
        self.down(DENY)
        self.wait(TAP_TIME)
        self.move((DENY[0] - 600, DENY[1]))
        self.frame(lift(0))
        self.assertEqual(self.events()[2:], ["down", "cancel"])

    def test_a_long_press(self):
        self.open_agents()
        self.down(ALLOW)
        self.wait(TAP_TIME)
        self.wait(LONG_PRESS - TAP_TIME)
        widget = next(w for w in self.daemon.surface.scene()["widgets"] if w["id"] == "agents/allow")
        self.assertIn("held", widget["classes"])
        self.frame(lift(0))
        self.assertEqual(self.events(), ["down", "long"])

    def test_a_late_lift_and_a_still_touch_on_nothing(self):
        # Still past the tap time on nothing that takes a press: no timer,
        # the lift does nothing, and a move is still the pointer's.
        self.down(TITLE)
        self.assertEqual(self.daemon.decider.deadline(), self.clock.now + TAP_TIME)
        self.assertFalse(self.wait(TAP_TIME + 0.5))
        self.assertIsNone(self.daemon.decider.deadline())
        self.assertEqual(self.frame(lift(0)), [])
        self.down(TITLE)
        self.wait(2)
        self.assertEqual(codes(self.move((TITLE[0] + 60, TITLE[1])), EV_ABS, ABS_MT_TRACKING_ID), [2])
        self.frame(lift(0))
        # A tap on it does nothing either: no click.
        self.down(TITLE)
        self.assertEqual(self.frame(lift(0)), [])
        self.assertEqual(self.daemon.clicks, 0)

    def test_hold_then_slide_moves_an_item_and_a_quick_slide_does_not(self):
        spot1 = (400, 80)  # the bar's second spot, empty
        self.down(MUTE)
        self.assertTrue(self.move(spot1))  # the pointer's
        self.frame(lift(0))
        self.daemon.check_config()
        self.assertEqual([i.label for i in self.daemon.config.bar], ["PTT"])
        self.down(MUTE)
        self.wait(TAP_TIME)
        self.assertEqual(self.move(spot1), [])
        self.assertEqual(self.daemon.scene()[1]["classes"][-1], "target")
        self.frame(lift(0))
        self.daemon.check_config()
        self.assertEqual([i.label for i in self.daemon.config.bar], ["PTT", "Mute"])
        self.assertEqual(self.keys, [])  # its own tap is cancelled

    def test_two_fingers_go_to_the_touchpad(self):
        self.down(MUTE)
        pad = self.down(FREE, slot=1)
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [1, 2])
        self.assertEqual(self.wait(1), False)
        self.assertEqual(codes(self.frame(lift(0), lift(1)), EV_ABS, ABS_MT_TRACKING_ID), [-1, -1])
        self.assertEqual((self.keys, self.daemon.clicks), ([], 0))
        # Two going down together, too.
        self.contact += 2
        pad = self.frame(touch(0, 10, 400, 800), touch(1, 11, 1400, 800))
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [10, 11])
        self.frame(lift(0), lift(1))

    def test_a_resting_finger_is_not_a_second_finger(self):
        # A thumb resting still on nothing past its tap time, then a tap
        # beside it: the tap presses, not a two-finger tap on the touchpad.
        self.down(TITLE)
        self.wait(TAP_TIME + 0.5)
        self.assertEqual(self.down(MUTE, slot=1), [])
        self.assertEqual(self.frame(lift(1)), [])
        self.assertEqual(self.keys, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])
        self.assertEqual(self.daemon.splitter.sent, {})
        # The thumb moved is still the pointer's.
        self.assertEqual(codes(self.move((TITLE[0] + 60, TITLE[1])), EV_ABS, ABS_MT_TRACKING_ID), [1])
        self.frame(lift(0))

    def test_a_finger_held_as_a_press_keeps_a_new_one_its_own(self):
        self.open_agents()
        self.down(DENY)
        self.wait(TAP_TIME)
        self.assertEqual(self.down(FREE, slot=1), [])
        self.assertTrue(self.move((FREE[0] - 100, FREE[1]), slot=1))
        self.frame(lift(0), lift(1))
        self.assertEqual(self.events(), ["down", "tap"])

    def test_the_pad_button_is_the_pointers(self):
        self.down(MUTE)
        pad = self.frame(left=True)
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [1])
        self.assertEqual(codes(pad, EV_KEY, BTN_LEFT), [1])
        # Press and drag: the pointer drags.
        self.assertEqual(codes(self.move((MUTE[0] + 200, MUTE[1])), EV_ABS, ABS_MT_POSITION_X), [(MUTE[0] + 200) * 2])
        self.assertEqual(codes(self.frame(left=False), EV_KEY, BTN_LEFT), [0])
        self.frame(lift(0))
        # A finger going down with the button pressed is the pointer's too.
        self.frame(left=True)
        self.assertEqual(codes(self.down(MUTE), EV_KEY, BTN_LEFT), [1])
        self.frame(lift(0), left=False)
        self.assertEqual((self.keys, self.daemon.clicks), ([], 0))

    def test_a_touch_too_soon_after_a_widget_appears_does_nothing(self):
        self.open_agents()
        self.daemon.apps.handle(self.client, page_msg(widgets=[{"id": "deny", "col": 2, "row": 1, "label": "No"}]))
        self.daemon.surface.refresh()
        # A tap that went down within the guard and lifted after it.
        self.clock.now += ASK_ARM - 0.1
        self.down(DENY)
        self.clock.now += 0.2
        self.frame(lift(0))
        # Held still from within the guard of another change: armed when
        # the tap time is over, but not when it went down, so no press.
        self.daemon.apps.handle(self.client, page_msg(widgets=[{"id": "deny", "col": 2, "row": 1, "label": "Never"}]))
        self.daemon.surface.refresh()
        self.down(DENY)
        self.wait(TAP_TIME)
        self.frame(lift(0))
        self.assertEqual(self.events(), [])
        # Down after the guard: a tap.
        self.down(DENY)
        self.frame(lift(0))
        self.assertEqual(self.events(), ["down", "tap"])

    def test_the_settings(self):
        with open(self.path, "a") as f:
            f.write("[pager]\ntap_time = 1.0\ntap_move = 5\n")
        self.daemon.reload = True
        self.daemon.check_config()
        self.down(MUTE)
        self.assertEqual(self.move((MUTE[0] + 70, MUTE[1])), [])  # 70 px is under 5 mm
        self.clock.now += 0.9
        self.daemon.settle()
        self.frame(lift(0))
        self.assertEqual(len(self.keys), 2)


if __name__ == "__main__":
    unittest.main()
