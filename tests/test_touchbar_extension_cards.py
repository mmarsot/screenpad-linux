"""Tests for the extension's cards.js, run under node, against the daemon.

The messages it builds for a notification go through the daemon's own
parser, apps and bar; the daemon's answers come back through its answer
filter, which presses only the very button a card showed.

Run with: python3 -m unittest discover -s tests
"""

import json
import unittest

from touchbar_support import CONFIG, FakeClient, config, js

from screenpad_touchbar.apps import Apps
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.common import ASK_ARM, MAX_OPTIONS, SHORTCUTS_X, SPOT_W, TILE_NAME, URGENCIES
from screenpad_touchbar.config import NotifyPolicy
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.notify import mirror_refusal, parse_notify
from screenpad_touchbar.scene import DISMISS_X

# What GNOME says of a calendar notification, as notifications.js passes it.
CALENDAR = {
    "appId": "org.gnome.Calendar",
    "source": "Calendar",
    "title": "Standup",
    "text": "Starts in 5 minutes",
    "urgency": 1,
    "actions": ["Join", "Snooze", "Decline", "Later"],
    "banners": True,
    "dnd": False,
    "acknowledged": False,
}


def message(**kw):
    return js("cards.js", "m.notifyMessage('n1', v.n)", n=dict(CALENDAR, **kw))


class MessageTests(unittest.TestCase):
    def test_notify_is_what_the_daemon_parses(self):
        msg = message()
        self.assertEqual(msg.pop("op"), "notify")
        card = parse_notify(msg)
        # Every field the daemon reads, and no other.
        self.assertEqual(set(msg), set(card))
        self.assertEqual(
            card,
            {
                "id": "n1",
                "app": "org.gnome.Calendar.desktop",
                "source": "Calendar",
                "title": "Standup",
                "text": "Starts in 5 minutes",
                "urgency": "normal",
                "actions": ["Join", "Snooze", "Decline", "Later"],
                "banners": True,
                "dnd": False,
                "acknowledged": False,
            },
        )
        self.assertIsNone(mirror_refusal(card, NotifyPolicy()))

    def test_what_gnome_leaves_out(self):
        bare = js(
            "cards.js",
            "m.notifyMessage('n2', {urgency: 9, title: undefined, actions: []})",
        )
        card = parse_notify({k: v for k, v in bare.items() if k != "op"})
        self.assertEqual(
            (card["app"], card["source"], card["title"], card["text"], card["urgency"], card["banners"]),
            (None, "", "", "", "normal", True),
        )

    def test_urgencies_are_the_daemons(self):
        names = js("cards.js", "[0, 1, 2, 3].map(u => m.notifyMessage('n', {urgency: u}).urgency)")
        self.assertEqual(tuple(names), URGENCIES)
        self.assertEqual(tuple(js("cards.js", "m.URGENCY")), URGENCIES)
        for urgency, name in enumerate(URGENCIES):
            msg = message(urgency=urgency)
            del msg["op"]
            self.assertEqual(parse_notify(msg)["urgency"], name)

    def test_hello_and_cancel_are_accepted(self):
        apps = Apps()
        apps.asks.set_available(True)
        ext = FakeClient()
        apps.connect(ext)
        hello, notify, cancel = js("cards.js", "[m.hello(), m.notifyMessage('n1', v.n), m.cancel('n1')]", n=CALENDAR)
        self.assertTrue(TILE_NAME.fullmatch(hello["app"]))
        for msg in (hello, notify):
            apps.handle(ext, msg)
        self.assertEqual(ext.last("welcome")["op"], "welcome")
        self.assertEqual(apps.asks.shown().id, "n1")
        apps.handle(ext, cancel)
        self.assertIsNone(apps.asks.shown())
        self.assertIsNone(ext.last("error"))

    def test_lines_and_markup(self):
        msg = {"op": "notify", "id": "n1", "title": "café ☕"}
        line = bytes(js("cards.js", "Array.from(m.encodeLine(v.msg))", msg=msg))
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1)
        self.assertEqual(json.loads(line), msg)
        self.assertEqual(js("cards.js", "m.stripMarkup('<b>Join</b> <a href=\"x\">now</a>')"), "Join now")


class AnswerTests(unittest.TestCase):
    """The daemon's answers to a mirrored notification, back in the extension."""

    def setUp(self):
        self.now = 50.0
        self.apps = Apps(clock=lambda: self.now)
        self.apps.asks.set_available(True)
        self.ext = FakeClient()
        self.apps.connect(self.ext)
        self.bar = Bar(
            config(CONFIG), KeyEmitter(lambda ev: None), lambda a: None, clock=lambda: self.now, asks=self.apps.asks
        )
        hello, notify = js("cards.js", "[m.hello(), m.notifyMessage('n1', v.n)]", n=CALENDAR)
        self.apps.handle(self.ext, hello)
        self.apps.handle(self.ext, notify)
        self.now += ASK_ARM

    def tap(self, x):
        self.bar.handle([("begin", 0, x, 300), ("end", 0, x, 300)])
        [answer] = [m for m in self.ext.sent if m["op"] == "answer"]
        return answer

    def press(self, answer, keep=True):
        """The index of the button the extension presses for an answer's
        line, or None; keep=False when an update replaced the first button."""
        return js(
            "cards.js",
            "(() => { const a = m.parseAnswer(v.line); if (!a) return 'not an answer';"
            " const shown = v.labels.map(label => ({label}));"
            " const current = v.keep ? [...shown] : [{label: shown[0].label}, ...shown.slice(1)];"
            " const action = m.actionToPress(a, shown, current);"
            " return action ? shown.indexOf(action) : null; })()",
            line=json.dumps(answer, separators=(",", ":")),
            labels=CALENDAR["actions"],
            keep=keep,
        )

    def test_a_tap_presses_that_button(self):
        for k in range(MAX_OPTIONS):
            with self.subTest(k=k):
                self.setUp()
                answer = self.tap(SHORTCUTS_X + k * SPOT_W + SPOT_W // 2)
                self.assertEqual(answer["option"], "action-%d" % k)
                self.assertEqual(self.press(answer), k)

    def test_a_replaced_button_is_never_pressed(self):
        answer = self.tap(SHORTCUTS_X + SPOT_W // 2)
        self.assertEqual(self.press(answer, keep=False), None)
        self.assertEqual(self.press(dict(answer, option="action-1"), keep=False), 1)

    def test_dismiss_and_timeout_press_nothing(self):
        dismissed = self.tap(DISMISS_X + SPOT_W // 2)
        self.assertEqual((dismissed["option"], dismissed["reason"]), (None, "dismissed"))
        self.assertIsNone(self.press(dismissed))
        for reason in ("timeout", "cancelled", "unavailable"):
            self.assertIsNone(self.press({"op": "answer", "id": "n1", "option": None, "reason": reason}))
        # Not a button of the card, or not an answer at all.
        self.assertIsNone(self.press({"op": "answer", "id": "n1", "option": "action-9", "reason": "chosen"}))
        self.assertIsNone(self.press({"op": "answer", "id": "n1", "option": "yes", "reason": "chosen"}))
        for line in ('{"op": "pong"}', '{"op": "answer", "id": 1}', "not json"):
            self.assertIsNone(js("cards.js", "m.parseAnswer(v.line)", line=line))


if __name__ == "__main__":
    unittest.main()
