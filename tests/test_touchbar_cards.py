"""Tests for attention cards: the elastic bar, the split under it, the daemon's card.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import json
import tempfile
import unittest

from touchbar_support import AXES, CONFIG, ConfigHome, FakeClient, codes, config, lift, touch

from screenpad_touchbar.asks import Asks
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.codes import (
    ABS_MT_POSITION_Y,
    ABS_MT_TRACKING_ID,
    EV_ABS,
    EV_KEY,
    EV_SYN,
    SYN_REPORT,
)
from screenpad_touchbar.common import (
    ASK_ARM,
    CARD_HEIGHT,
    GROW_WAIT,
    state_path,
)
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.protocol import parse_ask
from screenpad_touchbar.splitter import BAR, PAD, Splitter


class Card:
    """A bar and its questions on a fake clock; clear says whether the band
    a growing bar would cover is free of touchpad fingers."""

    def __init__(self, text=CONFIG):
        self.now = 100.0
        self.frames = []
        self.clear = True
        self.asks = Asks(clock=lambda: self.now)
        self.asks.band_clear = lambda: self.clear
        self.asks.set_available(True)
        self.bar = Bar(
            config(text), KeyEmitter(self.frames.append), lambda a: None, clock=lambda: self.now, asks=self.asks
        )
        self.app = FakeClient()

    def ask(self, ask_id="q", n=2, timeout=60):
        options = [{"id": "o%d" % k, "label": "O%d" % k} for k in range(n)]
        self.asks.add(
            parse_ask(self.app, {"id": ask_id, "title": "T", "options": options, "timeout": timeout}, self.now)
        )

    def tap(self, slot, px, py):
        self.bar.handle([("begin", slot, px, py), ("end", slot, px, py)])

    def answers(self):
        return [(m["id"], m["option"], m["reason"]) for m in self.app.sent if m["op"] == "answer"]


class ElasticBarTests(unittest.TestCase):
    def test_grows_for_a_card_and_shrinks_after_the_answer(self):
        c = Card()
        self.assertEqual((c.bar.height(), c.bar.touch_height()), (160, 160))
        c.ask()
        self.assertEqual((c.bar.height(), c.bar.touch_height(), c.bar.base_height()), (456, 456, 160))
        c.now += ASK_ARM
        # On the second answer, in the area the card added.
        c.tap(0, 1300, 400)
        self.assertEqual(c.answers(), [("q", "o1", "chosen")])
        self.assertEqual((c.bar.height(), c.bar.touch_height(), c.asks.raised), (160, 160, False))

    def test_shrinks_after_dismiss_and_after_a_timeout(self):
        c = Card()
        c.ask("a")
        c.now += ASK_ARM
        c.tap(0, 700, 300)
        self.assertEqual(c.answers(), [("a", None, "dismissed")])
        self.assertEqual(c.bar.height(), 160)
        c.ask("b", timeout=5)
        self.assertEqual(c.asks.deadline(), c.now + 5)
        c.now += 5
        c.asks.tick()
        self.assertEqual(c.answers()[-1], ("b", None, "timeout"))
        self.assertEqual((c.bar.height(), c.asks.raised), (160, False))

    def test_a_queue_stays_grown_and_arms_each_card_anew(self):
        c = Card()
        c.ask("first")
        c.ask("second")
        c.now += ASK_ARM
        c.tap(0, 1000, 300)
        self.assertEqual((c.asks.shown().id, c.bar.height()), ("second", 456))
        c.tap(0, 1000, 300)  # too soon for the second
        self.assertEqual(len(c.answers()), 1)
        c.now += ASK_ARM
        c.tap(0, 1000, 300)
        self.assertEqual([a[0] for a in c.answers()], ["first", "second"])
        self.assertEqual(c.bar.height(), 160)

    def test_waits_for_a_touchpad_finger_in_the_band(self):
        c = Card()
        c.clear = False
        c.ask()
        # Not shown, not grown; the loop wakes when the wait ends.
        self.assertIsNone(c.asks.shown())
        self.assertEqual((c.bar.height(), c.bar.touch_height()), (160, 160))
        self.assertEqual(c.asks.deadline(), c.now + GROW_WAIT)
        c.tap(0, 1000, 50)  # the bar's own spots, not a card
        self.assertEqual(c.answers(), [])
        c.now += 0.1
        c.clear = True  # the finger left the band
        c.asks.tick()
        self.assertEqual((c.asks.shown().shown_at, c.bar.height()), (c.now, 456))
        # The arming delay counts from the growth, not from the ask: taps
        # on the area that just appeared are ignored for ASK_ARM.
        grew = c.now
        c.now = grew + ASK_ARM - 0.01
        c.tap(0, 1000, 400)
        self.assertEqual(c.answers(), [])
        c.now = grew + ASK_ARM
        c.tap(0, 1000, 400)
        self.assertEqual(c.answers(), [("q", "o0", "chosen")])

    def test_grows_anyway_after_the_wait(self):
        c = Card()
        c.clear = False
        c.ask()
        c.now += GROW_WAIT - 0.01
        c.asks.tick()
        self.assertFalse(c.asks.raised)
        c.now += 0.01
        c.asks.tick()
        self.assertTrue(c.asks.raised)
        self.assertEqual(c.asks.deadline(), c.asks.queue[0].deadline)

    def test_a_card_answered_while_waiting_never_grows(self):
        c = Card()
        c.clear = False
        c.ask()
        c.asks.cancel(c.app, "q")
        self.assertEqual((c.asks.raised, c.asks.waiting_since, c.asks.deadline()), (False, None, None))

    def test_a_taller_bar_does_not_grow(self):
        c = Card("height = 480\n" + CONFIG)
        c.ask()
        self.assertEqual((c.bar.height(), c.bar.touch_height()), (480, 480))

    def test_the_grip_spans_the_card(self):
        c = Card()
        c.ask()
        c.bar.handle([("begin", 0, 1850, 300), ("move", 0, 1850, 340)])
        self.assertEqual((c.bar.base_height(), c.bar.height()), (200, 456))

    def test_a_held_button_stays_held_through_the_growth(self):
        c = Card()
        c.bar.handle([("begin", 0, 100, 50)])
        c.ask()
        c.bar.handle([("move", 0, 100, 300), ("end", 0, 100, 300)])
        self.assertEqual([f[0] for f in c.frames], [(EV_KEY, 184, 1), (EV_KEY, 184, 0)])


class ElasticSplitTests(unittest.TestCase):
    """The Splitter while the bar's height changes under contacts."""

    def setUp(self):
        self.height = 160
        self.s = Splitter(AXES, 5, lambda px, py: BAR if py < self.height else PAD)

    def frame(self, *groups):
        out = None
        for e in [e for g in groups for e in g] + [(EV_SYN, SYN_REPORT, 0)]:
            out = self.s.feed(*e)
        return out

    def test_contacts_keep_their_kind_and_new_ones_take_the_new_height(self):
        # A touchpad finger at panel row 300, in the band a card would cover.
        pad, bar, _ = self.frame(touch(0, 1, 2000, 600))
        self.assertEqual((codes(pad, EV_ABS, ABS_MT_TRACKING_ID), bar), ([1], []))
        self.assertTrue(self.s.pad_in_band(160, CARD_HEIGHT))
        self.height = CARD_HEIGHT
        # It stays a touchpad contact, at its full device coordinates.
        pad, bar, _ = self.frame(touch(0, None, 2010, 620))
        self.assertEqual((codes(pad, EV_ABS, ABS_MT_POSITION_Y), bar), ([620], []))
        # A new contact in the band is the bar's now.
        pad, bar, _ = self.frame(touch(1, 2, 1000, 600))
        self.assertEqual((pad, bar), ([], [("begin", 1, 500.0, 300.0)]))
        self.height = 160
        # It stays the bar's after the bar shrank, to its end.
        pad, bar, _ = self.frame(touch(1, None, 1000, 700), lift(0))
        self.assertEqual(codes(pad, EV_ABS, ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(bar, [("move", 1, 500.0, 350.0)])
        pad, bar, _ = self.frame(lift(1))
        self.assertEqual((pad, bar), ([], [("end", 1, 500.0, 350.0)]))
        # A new contact in the band is the touchpad's again.
        pad, bar, _ = self.frame(touch(2, 3, 1000, 600))
        self.assertEqual((codes(pad, EV_ABS, ABS_MT_TRACKING_ID), bar), ([3], []))

    def test_band_check(self):
        self.frame(touch(0, 1, 2000, 1200))  # row 600: below any card
        self.assertFalse(self.s.pad_in_band(160, CARD_HEIGHT))
        self.frame(touch(1, 2, 2000, 200))  # row 100: in the bar
        self.assertFalse(self.s.pad_in_band(160, CARD_HEIGHT))
        self.frame(touch(0, None, 2000, 900))  # row 450: in the band
        self.assertTrue(self.s.pad_in_band(160, CARD_HEIGHT))
        self.frame(lift(0))
        self.assertFalse(self.s.pad_in_band(160, CARD_HEIGHT))


class DaemonCardTests(unittest.TestCase):
    """The daemon's own wiring: its band check and what it publishes."""

    def test_growth_waits_for_the_finger_end_to_end(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            now = [10.0]
            daemon = Daemon(config(CONFIG), None)
            daemon.apps.asks.clock = lambda: now[0]
            daemon.apps.asks.can_show = lambda: True
            daemon.bar = daemon.new_bar(KeyEmitter(lambda ev: None))
            daemon.bar.clock = lambda: now[0]
            daemon.splitter = Splitter(AXES, 5, daemon._zone)
            app = FakeClient()
            daemon.apps.connect(app)
            daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
            sp = daemon.splitter
            # A pointer stroke in the band; then a question.
            for ev in touch(0, 1, 2000, 600) + [(EV_SYN, SYN_REPORT, 0)]:
                sp.feed(*ev)
            daemon.apps.handle(
                app, {"op": "ask", "id": "q", "title": "Run it?", "options": [{"id": "y", "label": "Yes"}]}
            )
            self.assertFalse(daemon.apps.asks.raised)
            daemon.publish()
            with open(state_path()) as f:
                state = json.load(f)
            self.assertEqual((state["height"], state["strut"], state["tiles"][0]["id"]), (160, 160, "item-0"))
            # The finger lifts: the bar grows; the strut stays the bar's own.
            for ev in lift(0) + [(EV_SYN, SYN_REPORT, 0)]:
                sp.feed(*ev)
            daemon.apps.asks.tick()
            daemon.publish()
            with open(state_path()) as f:
                state = json.load(f)
            self.assertEqual((state["height"], state["strut"], state["tiles"][0]["id"]), (456, 160, "ask-text"))
            # After ASK_ARM, a tap on the answer (panel x 1400) answers; the pad sees nothing.
            now[0] += ASK_ARM
            for groups in (touch(1, 2, 2800, 600), lift(1)):
                out = None
                for ev in groups + [(EV_SYN, SYN_REPORT, 0)]:
                    out = sp.feed(*ev)
                assert out is not None
                pad, bar, _ = out
                self.assertEqual(pad, [])
                daemon.bar.handle(bar)
            self.assertEqual(app.last("answer"), {"op": "answer", "id": "q", "option": "y", "reason": "chosen"})
            self.assertEqual(daemon.bar.touch_height(), 160)


if __name__ == "__main__":
    unittest.main()
