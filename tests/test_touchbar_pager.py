"""Tests for the pages: the swipe along the bar and its thresholds, the page
dots, the mode-aware zone split (contacts already down keep their kind, the
apps page never reaches the virtual touchpad), the return to the touchpad,
what the daemon publishes, and the [screensaver] and [pager] settings.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import AXES, BAR_CONFIG, SYN, TILE_CONFIG, ConfigHome, codes, config, lift, touch

from screenpad_touchbar import daemon as daemon_module
from screenpad_touchbar.apps import Apps
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.cli import main
from screenpad_touchbar.codes import ABS_MT_TOOL_TYPE, ABS_MT_TRACKING_ID, BTN_LEFT, EV_ABS, EV_KEY, MT_TOOL_PALM
from screenpad_touchbar.common import (
    APPS,
    ASK_ARM,
    HOME,
    RENDERER_NAME,
    RETURN_AFTER,
    SWIPE_MIN,
    SWIPE_TIME,
    ConfigError,
    runtime_file,
    state_path,
)
from screenpad_touchbar.config_edit import edit_config, migrate_config
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.defaults import EXAMPLE_CONFIG, LAYOUT_DOC, OLD_LAYOUT_DOC
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.pager import Pager
from screenpad_touchbar.splitter import Splitter

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def stroke(pager, clock, x0, y0, x1, y1, seconds=0.2, slot=0):
    """A bar contact from x0, y0 to x1, y1 taking seconds; the events the
    bar then gets for its move and its lift."""
    pager.watch([("begin", slot, x0, y0)])
    clock.now += seconds
    return pager.watch([("move", slot, x1, y1), ("end", slot, x1, y1)])


class SwipeTests(unittest.TestCase):
    """The Pager's swipe recognition, on bar events."""

    def setUp(self):
        self.clock = Clock()
        self.pager = Pager(clock=self.clock)

    def test_a_swipe_left_opens_the_apps_page_and_takes_the_lift(self):
        out = stroke(self.pager, self.clock, 1400, 80, 1400 - SWIPE_MIN, 80)
        self.assertEqual(self.pager.page, APPS)
        self.assertEqual(out, [("move", 0, 1000, 80), ("cancel", 0, 1000, 80)])
        self.assertTrue(self.pager.take_changed())
        # Swiping back, to the right, shows the touchpad page.
        out = stroke(self.pager, self.clock, 600, 80, 1100, 100)
        self.assertEqual((self.pager.page, out[-1][0]), (HOME, "cancel"))

    def test_no_page_that_way(self):
        out = stroke(self.pager, self.clock, 600, 80, 1100, 80)
        self.assertEqual((self.pager.page, out[-1][0]), (HOME, "end"))
        self.pager.turn(APPS)
        out = stroke(self.pager, self.clock, 1400, 80, 900, 80)
        self.assertEqual((self.pager.page, out[-1][0]), (APPS, "end"))

    def test_thresholds(self):
        for what, (dx, dy, seconds), page in (
            ("at every limit", (-SWIPE_MIN, SWIPE_MIN // 2, SWIPE_TIME - 0.001), APPS),
            ("short", (-(SWIPE_MIN - 1), 0, 0.1), HOME),
            ("slow", (-600, 0, SWIPE_TIME + 0.01), HOME),
            ("too steep", (-SWIPE_MIN, SWIPE_MIN // 2 + 1, 0.1), HOME),
            ("vertical", (0, -SWIPE_MIN, 0.1), HOME),
        ):
            with self.subTest(what):
                self.pager.turn(HOME)
                stroke(self.pager, self.clock, 1500, 100, 1500 + dx, 100 + dy, seconds)
                self.assertEqual(self.pager.page, page)

    def test_only_one_finger(self):
        # A second finger on the bar while it moves, even one that lifted
        # before it, makes it not a swipe.
        self.pager.watch([("begin", 0, 1400, 80), ("begin", 1, 300, 80)])
        self.pager.watch([("end", 1, 300, 80)])
        self.pager.watch([("end", 0, 900, 80)])
        self.assertEqual(self.pager.page, HOME)
        # One finger alone again swipes.
        stroke(self.pager, self.clock, 1400, 80, 900, 80)
        self.assertEqual(self.pager.page, APPS)

    def test_a_cancelled_contact_is_no_swipe(self):
        self.pager.watch([("begin", 0, 1400, 80)])
        self.assertEqual(self.pager.watch([("cancel", 0, 900, 80)]), [("cancel", 0, 900, 80)])
        self.assertEqual(self.pager.page, HOME)

    def test_off_or_not_drawn(self):
        for pager in (Pager(clock=self.clock, enabled=False), Pager(clock=self.clock, can_show=lambda: False)):
            out = stroke(pager, self.clock, 1400, 80, 900, 80)
            self.assertEqual((pager.page, out[-1][0]), (HOME, "end"))
            pager.tap()
            self.assertEqual(pager.page, HOME)


class BarTests(unittest.TestCase):
    """The page dots and swipes as the bar sees them."""

    def setUp(self):
        self.clock = Clock()
        self.resized = []
        self.pager = Pager(clock=self.clock)
        self.apps = Apps(clock=self.clock)
        self.bar = Bar(
            config(BAR_CONFIG),
            KeyEmitter(lambda ev: None),
            lambda argv: None,
            resize=lambda h: self.resized.append(h) or True,
            clock=self.clock,
            asks=self.apps.asks,
            pager=self.pager,
        )

    def handle(self, *events):
        return self.bar.handle(self.pager.watch(list(events)))

    def test_a_tap_on_the_dots_turns_the_page(self):
        self.assertTrue(self.handle(("begin", 0, 1700, 80)))
        self.assertEqual(self.bar.pressed(), [7])
        self.handle(("end", 0, 1710, 70))
        self.assertEqual(self.pager.page, APPS)
        self.handle(("begin", 0, 1740, 80), ("end", 0, 1740, 80))
        self.assertEqual(self.pager.page, HOME)
        # Sliding away cancels it.
        self.handle(("begin", 0, 1700, 80), ("move", 0, 1500, 80), ("end", 0, 1500, 80))
        self.assertEqual(self.pager.page, HOME)

    def test_dots_without_the_pager_are_nothing(self):
        self.pager.enabled = False
        self.assertFalse(self.handle(("begin", 0, 1700, 80)))
        self.handle(("end", 0, 1700, 80))
        self.assertEqual((self.pager.page, self.bar.pressed()), (HOME, []))

    def test_a_swipe_from_the_grip_does_not_resize(self):
        # Without the swipe, lifting there would save the moved grip.
        self.handle(("begin", 0, 1900, 60), ("move", 0, 1700, 76))
        self.assertEqual(self.bar.base_height(), 176)
        self.clock.now += 0.2
        self.handle(("end", 0, 1450, 76))
        self.assertEqual((self.pager.page, self.resized, self.bar.base_height()), (APPS, [], 160))

    def test_the_dots_stay_beside_a_card(self):
        self.apps.asks.set_available(True)
        client = type("C", (), {"send": lambda self, msg: None, "name": None})()
        self.apps.connect(client)
        self.apps.handle(client, {"op": "hello", "app": "a"})
        self.apps.handle(client, {"op": "ask", "id": "q", "title": "T", "options": [{"id": "y", "label": "Y"}]})
        self.clock.now += ASK_ARM
        self.handle(("begin", 0, 1700, 300), ("end", 0, 1700, 300))
        self.assertEqual(self.pager.page, APPS)
        self.assertIsNotNone(self.apps.asks.shown())


class RoutingTests(unittest.TestCase):
    """The daemon's mode-aware zone split, frame by frame, as the virtual
    touchpad sees it."""

    def setUp(self):
        self.enterContext(ConfigHome(self.enterContext(tempfile.TemporaryDirectory())))
        self.clock = Clock()
        self.daemon = d = Daemon(config(BAR_CONFIG), None)
        d.pager.clock = self.clock
        d.pager.can_show = lambda: True
        d.bar = d.new_bar(KeyEmitter(lambda ev: None))
        d.bar.clock = self.clock
        d.splitter = Splitter(AXES, 5, d._zone)
        self.sent = []
        self.enterContext(mock.patch.object(daemon_module, "write_events", lambda fd, ev: self.sent.extend(ev)))

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
        # As the daemon's process() does: it then publishes a change.
        self.daemon.route(*out)
        self.daemon.pager.take_changed()
        return list(self.sent)

    def swipe(self, x0, x1, slot=4, id=90):
        """A swipe along the bar (device units: rows below 320 are the bar)."""
        self.assertEqual(self.frame(touch(slot, id, x0, 100)), [])
        self.clock.now += 0.2
        self.assertEqual(self.frame(touch(slot, None, x1, 100)), [])
        self.assertEqual(self.frame(lift(slot)), [])

    def test_a_swipe_below_the_bar_is_a_pointer_move(self):
        pad = self.frame(touch(0, 1, 3000, 1200))
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [1])
        self.clock.now += 0.2
        self.assertTrue(self.frame(touch(0, None, 1800, 1200)))
        self.assertEqual(codes(self.frame(lift(0)), EV_ABS, ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(self.daemon.pager.page, HOME)

    def test_a_swipe_on_the_bar_turns_the_page_and_moves_no_pointer(self):
        self.swipe(3000, 2000)
        self.assertEqual(self.daemon.pager.page, APPS)
        self.swipe(1000, 2000)
        self.assertEqual(self.daemon.pager.page, HOME)

    def test_contacts_already_down_keep_their_kind(self):
        # A touchpad finger down; the page turns; it stays the touchpad's.
        self.assertEqual(codes(self.frame(touch(0, 1, 2000, 1200)), EV_ABS, ABS_MT_TRACKING_ID), [1])
        self.swipe(3000, 2000)
        self.assertEqual(self.daemon.pager.page, APPS)
        self.assertTrue(self.frame(touch(0, None, 2100, 1250)))
        # A new contact below the bar is the surface's: the touchpad never
        # hears of it, not even as a second finger.
        self.assertEqual(self.frame(touch(1, 2, 1000, 1500)), [])
        self.assertEqual(self.frame(touch(1, None, 1100, 1600)), [])
        self.assertEqual(codes(self.frame(lift(0)), EV_ABS, ABS_MT_TRACKING_ID), [-1])
        # Back on the touchpad page with the surface contact still down: it
        # stays the surface's, while a new one is the touchpad's.
        self.swipe(1000, 2000)
        self.assertEqual(self.daemon.pager.page, HOME)
        self.assertEqual(self.frame(touch(1, None, 1200, 1700)), [])
        self.assertEqual(codes(self.frame(touch(2, 3, 2500, 1500)), EV_ABS, ABS_MT_TRACKING_ID), [3])
        self.assertEqual(self.frame(lift(1)), [])
        # Bar contacts are the bar's on both pages.
        self.assertEqual(self.frame(touch(3, 4, 600, 100)), [])
        self.assertEqual(self.daemon.bar.pressed(), [1])

    def test_the_apps_page_never_reaches_the_virtual_touchpad(self):
        self.swipe(3000, 2000)
        frames = [
            [touch(0, 1, 1000, 1000)],
            [touch(1, 2, 3000, 2000), touch(0, None, 1200, 1100)],
            [touch(2, 3, 2000, 1500), [(EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PALM)]],
            [touch(0, None, 1300, 400)],  # up into the bar: still the surface's
            [lift(0)],
            [lift(1), lift(2)],
        ]
        for groups in frames:
            self.assertEqual(self.frame(*groups), [])
        # A click with only surface fingers down reaches nothing either.
        self.frame(touch(0, 5, 1000, 1000))
        self.assertEqual(self.frame(left=True), [])
        self.assertEqual(self.frame(left=False), [])
        self.assertEqual(self.frame(lift(0)), [])
        self.assertEqual(self.daemon.splitter.sent, {})

    def test_back_to_the_touchpad_after_a_while_without_a_touch(self):
        pager = self.daemon.pager
        self.swipe(3000, 2000)
        lifted = self.clock.now
        self.assertEqual(pager.deadline(), lifted + RETURN_AFTER)
        # A touch holds it, and its lift starts the wait again.
        self.clock.now += 5
        self.frame(touch(0, 1, 1000, 1000))
        self.assertIsNone(pager.deadline())
        self.clock.now += 30
        self.assertFalse(pager.tick())
        self.frame(lift(0))
        self.assertEqual(pager.deadline(), self.clock.now + RETURN_AFTER)
        self.clock.now += RETURN_AFTER - 0.1
        self.assertFalse(pager.tick())
        self.clock.now += 0.1
        self.assertTrue(pager.tick())
        self.assertEqual((pager.page, pager.deadline()), (HOME, None))
        # A new contact below the bar is the touchpad's again.
        self.assertEqual(codes(self.frame(touch(0, 2, 1000, 1000)), EV_ABS, ABS_MT_TRACKING_ID), [2])

    def test_back_to_the_touchpad_when_the_page_cannot_show(self):
        self.swipe(3000, 2000)
        self.daemon.pager.can_show = lambda: False
        self.assertTrue(self.daemon.pager.tick())
        self.assertEqual(self.daemon.pager.page, HOME)

    def test_letting_go_resets(self):
        self.swipe(3000, 2000)
        self.frame(touch(0, 1, 1000, 1000))
        pad, bar, surface = self.daemon.splitter.release_all()
        self.assertEqual((pad, bar, surface), ([], [], [("cancel", 0, 500.0, 500.0)]))
        self.daemon.pager.reset()
        self.assertEqual((self.daemon.pager.page, self.daemon.pager.deadline()), (HOME, None))


class PublishTests(unittest.TestCase):
    """What the daemon publishes for the pages, and when it can show them."""

    def published(self, text=BAR_CONFIG, page=HOME):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = Daemon(config(text), None)
            daemon.pager.can_show = lambda: True
            daemon.pager.turn(page)
            daemon.publish()
            with open(state_path()) as f:
                return json.load(f)

    def test_the_touchpad_page(self):
        state = self.published()
        self.assertEqual((state["page"], state["screensaver"], state["surface"]), ("touchpad", {"clock": True}, None))
        self.assertEqual(state["tiles"][-1]["style"], "dots")
        # The version 1 spots an older extension draws have no dots.
        self.assertNotIn("dots", [s["kind"] for s in state["spots"]])

    def test_the_apps_page(self):
        state = self.published(page=APPS)
        self.assertEqual(state["page"], "apps")
        self.assertEqual(state["surface"]["title"], "Apps")
        self.assertIn("%d s" % RETURN_AFTER, state["surface"]["hint"])
        self.assertEqual((state["tiles"][-1]["page"], state["tiles"][-1]["text"]), (1, "apps"))

    def test_settings(self):
        state = self.published(BAR_CONFIG + "[screensaver]\nclock = false\n[pager]\nreturn_after = 3\n", APPS)
        self.assertEqual(state["screensaver"], {"clock": False})
        self.assertIn("3 s", state["surface"]["hint"])
        state = self.published(BAR_CONFIG + "[screensaver]\nenabled = false\n[pager]\nenabled = false\n")
        self.assertIsNone(state["screensaver"])
        self.assertNotIn("dots", [t["style"] for t in state["tiles"]])

    def test_the_pages_need_an_extension_that_draws_them(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = Daemon(config(BAR_CONFIG), None)
            daemon.source = type("Grabbed", (), {"grabbed": True})()  # a stand-in for the grabbed pad
            os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)))
            for i, (protocol, asks, pages) in enumerate(((2, True, False), (3, True, True), (1, False, False))):
                with open(runtime_file(RENDERER_NAME), "w") as f:
                    json.dump({"protocol": protocol, "drawn": True}, f)
                os.utime(runtime_file(RENDERER_NAME), ns=(0, 1000 + i))
                self.assertEqual((daemon.renderer_ready(), daemon.pager.available()), (asks, pages), protocol)
            daemon.source = None
            self.assertFalse(daemon.pager.available())


class ConfigTests(unittest.TestCase):
    """[screensaver] and [pager]."""

    def test_defaults_and_settings(self):
        p = config().pages
        self.assertEqual((p.screensaver, p.clock, p.pager, p.return_after), (True, True, True, RETURN_AFTER))
        p = config(
            "[screensaver]\nenabled = false\nclock = false\n[pager]\nenabled = false\nreturn_after = 3600\n"
        ).pages
        self.assertEqual((p.screensaver, p.clock, p.pager, p.return_after), (False, False, False, 3600))
        self.assertEqual(p.describe(), "screensaver off; apps page off")
        self.assertEqual(
            config().pages.describe(), "screensaver black with a clock; apps page back to the touchpad after 8 s"
        )

    def test_errors(self):
        for text, error in (
            ("[screensaver]\nclock = 1\n", "screensaver: clock must be true or false"),
            ("[screensaver]\nblack = true\n", "screensaver: unknown setting(s): black"),
            ("screensaver = true\n", "screensaver: must be a [screensaver] table"),
            ('[pager]\nenabled = "yes"\n', "pager: enabled must be true or false"),
            ("[pager]\nreturn_after = 0\n", "return_after must be a whole number of seconds, 1 to 3600"),
            ("[pager]\nreturn_after = 3601\n", "return_after must be"),
            ("[pager]\nreturn_after = 2.5\n", "return_after must be"),
            ("[pager]\nreturn_after = true\n", "return_after must be"),
        ):
            with self.subTest(text), self.assertRaises(ConfigError) as e:
                config(text)
            self.assertIn(error, str(e.exception))

    def test_check_and_edits(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(TILE_CONFIG + "\n[pager]\nreturn_after = 5\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["check"]), 0)
            self.assertIn(
                "pages: screensaver black with a clock; apps page back to the touchpad after 5 s", out.getvalue()
            )
            self.assertIn("dots     1  x 1672..1816 tap or swipe the bar to turn the page", out.getvalue())
            # Adding a shortcut keeps the table.
            c = edit_config(lambda c: (c.height, c.shortcuts + ["web.desktop"]))
            self.assertEqual(c.pages.return_after, 5)

    def test_migrate_the_default_before_the_pages(self):
        with open(os.path.join(REPO, "tests", "touchbar-v5-default.toml")) as f:
            v5 = f.read()
        self.assertNotIn("[pager]", v5)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v5)
            self.assertTrue(migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), EXAMPLE_CONFIG)
            self.assertFalse(migrate_config())
            # An edited one gets the bar's new description, once.
            with open(path, "w") as f:
                f.write(v5.replace("height = 160", "height = 200"))
            self.assertTrue(migrate_config())
            with open(path) as f:
                text = f.read()
            self.assertEqual((text.count(LAYOUT_DOC), text.count(OLD_LAYOUT_DOC)), (1, 0))
            self.assertIn("height = 200", text)
            self.assertFalse(migrate_config())


if __name__ == "__main__":
    unittest.main()
