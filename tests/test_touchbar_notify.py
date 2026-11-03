"""Tests for mirrored GNOME notifications: the mirror rules, notify through the
apps to the bar, and the [notifications] config.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import re
import tempfile
import unittest

from touchbar_support import CONFIG, REPO, ConfigHome, FakeClient, config, notify

from screenpad_touchbar.apps import Apps
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.cli import main
from screenpad_touchbar.common import (
    ASK_ARM,
    CARD_HEIGHT,
    MAX_NOTIFY_WAITING,
    NOTIFY_TIMEOUT,
    ConfigError,
)
from screenpad_touchbar.config import NotifyPolicy
from screenpad_touchbar.config_edit import add_app, edit_config
from screenpad_touchbar.defaults import EXAMPLE_CONFIG
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.migrate import migrate_config
from screenpad_touchbar.notify import mirror_refusal, parse_notify
from screenpad_touchbar.protocol import ProtocolError
from screenpad_touchbar.scene import ask_tiles


class MirrorRuleTests(unittest.TestCase):
    def refusal(self, policy=None, **kw):
        return mirror_refusal(parse_notify(notify(**kw)), policy or NotifyPolicy())

    def test_rules(self):
        cases: list[tuple[dict, str | None]] = [
            ({}, None),
            ({"actions": []}, "no buttons and not urgent"),
            ({"actions": [], "urgency": "high"}, None),
            ({"actions": [], "urgency": "critical"}, None),
            ({"urgency": "low"}, "low urgency"),
            ({"acknowledged": True}, "already seen"),
            # Do Not Disturb: GNOME shows banners for critical ones only.
            ({"dnd": True, "banners": False}, "do not disturb"),
            ({"dnd": True, "banners": False, "urgency": "high"}, "do not disturb"),
            ({"dnd": True, "banners": False, "urgency": "critical"}, None),
            ({"banners": False}, "banners are off for the app"),
        ]
        for kw, why in cases:
            self.assertEqual(self.refusal(**kw), why, kw)

    def test_config(self):
        cal = "org.gnome.Calendar.desktop"
        self.assertEqual(self.refusal(NotifyPolicy(mirror=False)), "mirroring is off in the config")
        self.assertEqual(self.refusal(NotifyPolicy(deny=[cal])), "the config denies " + cal)
        self.assertIsNone(self.refusal(NotifyPolicy(allow=[cal])))
        self.assertIn("not in the config's allow list", self.refusal(NotifyPolicy(allow=["x.desktop"])))
        self.assertIn("an app with no id", self.refusal(NotifyPolicy(allow=[cal]), app=None))
        # The config's say comes first: a denied app's critical card too.
        self.assertIsNotNone(self.refusal(NotifyPolicy(deny=[cal]), urgency="critical"))

    def test_normalized(self):
        card = parse_notify(
            notify(
                app="org.gnome.Calendar", title="a\nb\tc " * 40, actions=["Join the meeting right now please", "\x07"]
            )
        )
        self.assertEqual(card["app"], "org.gnome.Calendar.desktop")
        self.assertEqual(len(card["title"]), 80)
        self.assertTrue(card["title"].startswith("a b c a b c"))
        self.assertTrue(card["title"].endswith("\u2026"))
        self.assertEqual(card["actions"], ["Join the meeting right\u2026", "Action"])
        self.assertIsNone(parse_notify(notify(app="not an id!"))["app"])
        for bad in (
            {"urgency": "urgent"},
            {"id": "-x"},
            {"actions": "Join"},
            {"actions": [1]},
            {"dnd": "yes"},
            {"extra": 1},
        ):
            with self.assertRaises(ProtocolError, msg=bad):
                parse_notify(notify(**bad))


class NotifyAppsTests(unittest.TestCase):
    """The extension's notify messages, through Apps, Asks and the Bar."""

    def setUp(self):
        self.now = 50.0
        self.apps = Apps(clock=lambda: self.now)
        self.apps.asks.set_available(True)
        self.ext = FakeClient()
        self.apps.connect(self.ext)
        self.apps.handle(self.ext, {"op": "hello", "app": "gnome-notifications"})
        self.bar = Bar(
            config(CONFIG), KeyEmitter(lambda ev: None), lambda a: None, clock=lambda: self.now, asks=self.apps.asks
        )

    def send(self, **kw):
        self.apps.handle(self.ext, notify(**kw))

    def answers(self):
        return [m for m in self.ext.sent if m["op"] in ("answer", "error")]

    def test_a_notification_with_buttons_is_a_card(self):
        self.send(actions=["Join", "Snooze", "Decline", "Later"])
        ask = self.apps.asks.shown()
        self.assertEqual(
            (ask.title, ask.text, ask.deadline),
            ("Standup", "Calendar \u00b7 Starts in 5 minutes", 50.0 + NOTIFY_TIMEOUT),
        )
        self.assertEqual(
            [(o["id"], o["label"]) for o in ask.options],
            [("action-0", "Join"), ("action-1", "Snooze"), ("action-2", "Decline")],
        )
        self.assertEqual(self.bar.height(), CARD_HEIGHT)
        # Never activated by itself: nothing goes back until a tap.
        self.assertEqual(self.answers(), [])
        self.now += ASK_ARM
        self.bar.handle([("begin", 0, 1300, 300), ("end", 0, 1300, 300)])
        self.assertEqual(self.answers(), [{"op": "answer", "id": "n1", "option": "action-1", "reason": "chosen"}])
        self.assertEqual(self.bar.height(), 160)

    def test_dismiss_and_timeout_close_the_card_only(self):
        self.send()
        self.now += ASK_ARM
        self.bar.handle([("begin", 0, 700, 100), ("end", 0, 700, 100)])
        self.send(id="n2")
        self.now += NOTIFY_TIMEOUT
        self.apps.asks.tick()
        self.assertEqual(
            [(m["id"], m["option"], m["reason"]) for m in self.answers()],
            [("n1", None, "dismissed"), ("n2", None, "timeout")],
        )

    def test_not_mirrored_is_answered_at_once(self):
        self.send(actions=[])
        self.send(id="n2", dnd=True, banners=False)
        self.assertEqual(
            [(m["id"], m["reason"], m["why"]) for m in self.answers()],
            [("n1", "ignored", "no buttons and not urgent"), ("n2", "ignored", "do not disturb")],
        )
        self.assertEqual((self.apps.asks.queue, self.bar.height()), ([], 160))
        self.send(id="n3", dnd=True, banners=False, urgency="critical", actions=[])
        ask = self.apps.asks.shown()
        self.assertEqual((ask.id, ask.options), ("n3", []))

    def test_config_policy(self):
        self.apps.notify_policy = NotifyPolicy(deny=["org.gnome.Calendar.desktop"], timeout=5)
        self.send()
        self.send(id="n2", app="org.gnome.Fractal.desktop")
        self.assertEqual(self.answers()[0]["why"], "the config denies org.gnome.Calendar.desktop")
        self.assertEqual(self.apps.asks.shown().deadline, 55.0)

    def test_an_update_replaces_the_card_and_arms_it_anew(self):
        self.send()
        self.now += ASK_ARM
        old = self.apps.asks.shown()
        self.bar.handle([("begin", 0, 1000, 300)])  # a finger on "Join"
        self.send(actions=["Accept", "Join"])  # the app changed its buttons
        new = self.apps.asks.shown()
        self.assertIsNot(new, old)
        self.assertEqual((new.shown_at, len(self.apps.asks.queue)), (self.now, 1))
        # The finger was on the old version: lifting chooses nothing.
        self.bar.handle([("end", 0, 1000, 300)])
        self.assertEqual(self.answers(), [])
        # Updated into one the bar does not mirror: the card goes.
        self.send(actions=[])
        self.assertEqual([(m["reason"]) for m in self.answers()], ["cancelled"])
        self.assertIsNone(self.apps.asks.shown())

    def test_gone_unavailable_and_too_many(self):
        self.send()
        self.apps.handle(self.ext, {"op": "cancel", "id": "n1"})
        self.assertEqual(self.answers()[-1]["reason"], "cancelled")
        for k in range(MAX_NOTIFY_WAITING + 1):
            self.send(id="n%d" % (k + 10))
        self.assertEqual(len(self.apps.asks.queue), MAX_NOTIFY_WAITING)
        self.assertEqual(self.answers()[-1]["why"], "too many notifications waiting")
        self.apps.asks.set_available(False)
        self.assertEqual(len([m for m in self.answers() if m["reason"] == "unavailable"]), MAX_NOTIFY_WAITING)
        self.send(id="late")
        self.assertEqual(self.answers()[-1]["reason"], "unavailable")

    def test_agent_asks_and_notifications_share_the_card(self):
        agent = FakeClient()
        self.apps.connect(agent)
        self.apps.handle(agent, {"op": "hello", "app": "agents"})
        self.apps.handle(
            agent, {"op": "ask", "id": "q", "title": "Run npm test?", "options": [{"id": "yes", "label": "Allow once"}]}
        )
        self.send()
        tiles = ask_tiles(self.apps.asks.shown(), queued=len(self.apps.asks.queue) - 1)
        self.assertEqual((tiles[0]["title"], tiles[0]["badge"]), ("Run npm test?", "+1"))

    def test_bad_messages(self):
        stranger = FakeClient()
        self.apps.connect(stranger)
        self.apps.handle(stranger, notify())
        self.assertEqual(stranger.last("error")["message"], "say hello first")
        self.send(urgency="loud")
        self.assertEqual(self.ext.last("error")["request"], "notify")


class NotifyConfigTests(unittest.TestCase):
    def test_settings(self):
        n = config("").notifications
        self.assertEqual((n.mirror, n.allow, n.deny, n.timeout), (True, None, [], NOTIFY_TIMEOUT))
        n = config(
            '[notifications]\nmirror = false\nallow = ["org.gnome.Calendar"]\n'
            'deny = ["a.desktop", "a.desktop"]\ntimeout = 45\n'
        ).notifications
        self.assertEqual(
            (n.mirror, n.allow, n.deny, n.timeout), (False, ["org.gnome.Calendar.desktop"], ["a.desktop"], 45)
        )
        self.assertEqual(
            config("[notifications]\nallow = []\n").notifications.describe(), "mirrored for 20 s, only no app"
        )

    def test_errors(self):
        for text, error in (
            ("notifications = 3", "must be a [notifications] table"),
            ('[notifications]\nmirror = "no"', "mirror must be true or false"),
            ("[notifications]\nmute = true", "unknown setting(s): mute"),
            ('[notifications]\ndeny = "a.desktop"', "deny must be a list"),
            ('[notifications]\nallow = ["not an id!"]', "is not a desktop file id"),
            ("[notifications]\ntimeout = 0", "timeout must be a whole number"),
            ("[notifications]\ntimeout = 2.5", "timeout must be a whole number"),
        ):
            with self.assertRaises(ConfigError, msg=text) as e:
                config(text)
            self.assertIn("notifications: ", str(e.exception))
            self.assertIn(error, str(e.exception))

    def test_default_documents_it(self):
        # The commented example in the default is a valid table once uncommented.
        text = re.sub(r"(?m)^# (\[notifications\]|mirror =|deny =|allow =|timeout =)", r"\1", EXAMPLE_CONFIG)
        n = config(text).notifications
        self.assertEqual((n.mirror, n.deny, n.timeout), (False, ["org.gnome.Evolution.desktop"], NOTIFY_TIMEOUT))

    def test_check_and_shortcut_edits(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(CONFIG + '\n[notifications]\ndeny = ["a.desktop"]\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["check"]), 0)
            self.assertIn("bar: 160 of 1080 px high (10.0 mm); 456 px while a card shows", out.getvalue())
            self.assertIn("notifications: mirrored for 20 s, never a.desktop", out.getvalue())
            # Adding an app keeps the table.
            c = edit_config(lambda c: {"bar": add_app(c.bar, "web.desktop")})
            self.assertEqual(
                ([i.name for i in c.bar], c.notifications.deny), ([None, "files.desktop", "web.desktop"], ["a.desktop"])
            )

    def test_migrate_the_default_before_notifications(self):
        with open(os.path.join(REPO, "tests", "touchbar-v4-default.toml")) as f:
            v4 = f.read()
        self.assertNotIn("[notifications]", v4)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v4)
            self.assertTrue(migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), EXAMPLE_CONFIG)
            self.assertFalse(migrate_config())


if __name__ == "__main__":
    unittest.main()
