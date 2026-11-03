"""Tests for the press click: which presses the daemon counts ("clicks" in
touchbar.json), feedback.js playing the click on the scenes it publishes
(under node, with a stand-in for GNOME's sound player), and click.wav being
what gnome-shell/make_click.py writes.

Run with: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import os
import struct
import tempfile
import unittest
import wave
from unittest import mock

from touchbar_support import BAR_CONFIG, EXT, GRID_H, GRID_Y, Clock, ConfigHome, FakeClient, config, hello, js, page_msg

from screenpad_touchbar.common import APPS, ASK_ARM, HOME, RENDERER_NAME, WIDGETS_PROTOCOL, runtime_file, state_path
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter

# BAR_CONFIG's spots, in panel pixels: push to talk (hold) at 0, mute (tap)
# at 272, Term (hold, a command) at 544, the shortcuts at 856 and 1128, the
# page button at 1672 and the grip at 1816. The bar is 160 rows high.
PTT, MUTE, FILES, PAGE, GRIP = 100, 400, 1000, 1700, 1850


class Harness:
    """A daemon on a fake clock, with no touchpad: bar contacts go through
    the pager as process() sends them, and nothing is run or launched."""

    def __init__(self, test):
        test.enterContext(ConfigHome(test.enterContext(tempfile.TemporaryDirectory())))
        self.clock = Clock()
        self.daemon = d = Daemon(config(BAR_CONFIG), None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = d.apps.asks.can_show = lambda: True
        self.ran = []
        test.enterContext(mock.patch.object(d, "run_command", self.ran.append))
        d.bar = d.new_bar(KeyEmitter(lambda ev: None))
        d.bar.clock = self.clock

    def bar(self, *events):
        d = self.daemon
        d.bar.handle(d.pager.watch(list(events)))

    def tap(self, px, py=80, slot=0):
        self.bar(("begin", slot, px, py), ("end", slot, px, py))

    def clicks(self):
        return self.daemon.clicks

    def published(self):
        self.daemon.publish()
        with open(state_path()) as f:
            return json.load(f)


class CountTests(unittest.TestCase):
    """What the daemon counts: a press as its action happens, nothing else."""

    def setUp(self):
        self.h = Harness(self)

    def test_a_tap_clicks_as_it_lifts_on_the_button_and_a_hold_as_it_goes_down(self):
        h = self.h
        h.bar(("begin", 0, MUTE, 80))
        self.assertEqual(h.clicks(), 0)
        h.bar(("end", 0, MUTE + 10, 90))
        self.assertEqual(h.clicks(), 1)
        # Push to talk sends its key down at the touch: the click is then.
        h.bar(("begin", 0, PTT, 80))
        self.assertEqual(h.clicks(), 2)
        h.bar(("end", 0, PTT, 80))
        self.assertEqual(h.clicks(), 2)
        # A shortcut opens on the lift, and so clicks.
        h.tap(FILES)
        self.assertEqual((h.clicks(), len(h.ran)), (3, 1))

    def test_sliding_away_a_cancel_the_grip_and_a_swipe_click_nothing(self):
        h = self.h
        h.bar(("begin", 0, MUTE, 80), ("move", 0, MUTE, 600), ("end", 0, MUTE, 600))
        h.bar(("begin", 0, FILES, 80), ("cancel", 0, FILES, 80))
        h.bar(("begin", 0, GRIP, 80), ("move", 0, GRIP, 90), ("end", 0, GRIP, 90))
        h.bar(("begin", 0, GRIP, 80), ("move", 0, GRIP, 400), ("end", 0, GRIP, 400))
        # A swipe that starts on a button turns the page, without a click.
        h.bar(("begin", 0, FILES, 80))
        h.clock.now += 0.2
        h.bar(("move", 0, FILES - 500, 80), ("end", 0, FILES - 500, 80))
        self.assertEqual(h.daemon.pager.page, APPS)
        self.assertEqual(h.clicks(), 0)
        # A tap on the page button turns it back, with one.
        h.tap(PAGE)
        self.assertEqual((h.daemon.pager.page, h.clicks()), (HOME, 1))

    def test_a_card_answer_clicks_and_a_touch_too_soon_does_not(self):
        h = self.h
        app = FakeClient()
        h.daemon.apps.connect(app)
        h.daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
        options = [{"id": "no", "label": "No"}, {"id": "yes", "label": "Yes"}]
        h.daemon.apps.handle(app, {"op": "ask", "id": "q", "title": "Run it?", "options": options})
        h.daemon.apps.asks.tick()
        h.tap(1300, 300)
        self.assertEqual(h.clicks(), 0)
        h.clock.now += ASK_ARM
        h.tap(1300, 300)
        self.assertEqual(h.clicks(), 1)
        self.assertEqual([m["option"] for m in app.sent if m["op"] == "answer"], ["yes"])

    def test_a_widget_clicks_on_a_tap_and_not_when_it_slides_away(self):
        h, d = self.h, self.h.daemon
        os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)), exist_ok=True)
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL, "drawn": True}, f)
        client = hello(d.apps)
        d.apps.handle(client, page_msg())
        d.pager.turn(APPS)
        d.surface.handle([])
        h.clock.now += ASK_ARM
        tile = next(w for w in h.published()["surface"]["widgets"] if w["id"] == "surface/open:agents")
        x, y = tile["x"] + 20, tile["y"] + 20
        d.surface.handle([("begin", 0, x, y), ("move", 0, x, y + 600), ("end", 0, x, y + 600)])
        self.assertEqual(h.clicks(), 0)
        d.surface.handle([("begin", 0, x, y), ("end", 0, x, y)])
        self.assertEqual((h.clicks(), d.surface.open), (1, "agents"))
        d.surface.handle([])
        h.clock.now += ASK_ARM
        deny_y = GRID_Y + GRID_H * 3 // 4
        d.surface.handle([("begin", 0, 1700, deny_y), ("end", 0, 1700, deny_y)])
        self.assertEqual(h.clicks(), 2)
        self.assertEqual([m["type"] for m in client.sent if m["op"] == "event"], ["down", "tap"])

    def test_the_scene_carries_the_count(self):
        h = self.h
        self.assertEqual(h.published()["clicks"], 0)
        h.tap(MUTE)
        self.assertEqual(h.published()["clicks"], 1)
        # Letting the touchpad go keeps the count: it only goes up.
        h.daemon.bar = h.daemon.new_bar(KeyEmitter(lambda ev: None))
        self.assertEqual(h.published()["clicks"], 1)


class PlayTests(unittest.TestCase):
    """feedback.js on the scenes the daemon publishes."""

    def play(self, states):
        """Which of the states feedback.js's Clicker plays the click on,
        with a stand-in sound player, and what it was given."""
        return js(
            "feedback.js",
            """(() => {
                const played = [];
                const player = {play_from_file: (file, description, cancellable) =>
                    played.push([file, description, cancellable])};
                const clicker = new m.Clicker(player, 'the-file');
                return {plays: v.states.map(s => clicker.update(s)), played};
            })()""",
            states=states,
        )

    def test_a_click_plays_once_per_rise_of_the_count(self):
        h = Harness(self)
        first = h.published()
        h.tap(MUTE)
        tapped = h.published()
        h.bar(("begin", 0, MUTE, 80))
        down = h.published()
        h.bar(("move", 0, MUTE, 600), ("end", 0, MUTE, 600))
        slid = h.published()
        h.tap(MUTE)
        h.tap(FILES)
        twice = h.published()
        out = self.play([first, tapped, down, slid, twice])
        self.assertEqual(out["plays"], [False, True, False, False, True])
        self.assertEqual(out["played"], [["the-file", "Touch bar press", None]] * 2)

    def test_a_new_daemon_or_no_scene_plays_nothing(self):
        before = {"pid": 10, "clicks": 4}
        out = self.play([before, {"pid": 11, "clicks": 5}, None, {"pid": 11, "clicks": 6}, {"pid": 11, "clicks": 7}])
        self.assertEqual(out["plays"], [False, False, False, False, True])
        # A daemon from before the click publishes no count.
        self.assertEqual(self.play([{"pid": 10}, {"pid": 10}])["plays"], [False, False])
        self.assertFalse(
            js("feedback.js", "m.clicked(v.a, v.b)", a={"pid": 1, "clicks": 1.5}, b={"pid": 1, "clicks": 3})
        )

    def test_the_file_is_bundled(self):
        self.assertTrue(os.path.isfile(os.path.join(EXT, js("feedback.js", "m.CLICK_NAME"))))


def frames(path):
    with wave.open(path, "rb") as w:
        params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        data = w.readframes(w.getnframes())
    return params, struct.unpack("<%dh" % (len(data) // 2), data)


class SoundTests(unittest.TestCase):
    """click.wav: what make_click.py writes, short, soft and clean."""

    def setUp(self):
        script = os.path.join(EXT, "..", "make_click.py")
        spec = importlib.util.spec_from_file_location("make_click", script)
        assert spec is not None and spec.loader is not None
        self.make = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.make)
        self.params, self.samples = frames(os.path.join(EXT, "click.wav"))

    def test_the_file_is_what_the_script_writes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "click.wav")
            self.make.write(path)
            params, samples = frames(path)
        self.assertEqual(params, self.params)
        self.assertEqual(len(samples), len(self.samples))
        # Within one step, so a libm rounding a sine differently still matches.
        self.assertLessEqual(max(abs(a - b) for a, b in zip(samples, self.samples, strict=True)), 1)

    def test_short_soft_and_silent_at_both_ends(self):
        channels, width, rate = self.params
        self.assertEqual((channels, width), (1, 2))
        self.assertTrue(0.010 <= len(self.samples) / rate <= 0.030)
        self.assertLess(max(abs(s) for s in self.samples), 0.35 * 32767)
        self.assertEqual((self.samples[0], self.samples[-1]), (0, 0))


if __name__ == "__main__":
    unittest.main()
