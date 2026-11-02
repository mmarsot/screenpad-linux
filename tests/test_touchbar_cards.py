"""Tests for attention cards: the elastic bar and mirrored GNOME notifications.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest

from test_touchbar import AXES, REPO, ConfigHome, FakeClient, codes, config, lift, t, touch

# Buttons at x 0, 296, 592; shortcuts at 928, 1224, 1520; grip 1816. The
# bar is 160 px high (device rows 0..320); a card makes it 456 (0..912).
CONFIG = """
[[button]]
label = "PTT"
mode = "hold"
key = "F14"
[[shortcut]]
app = "files.desktop"
"""
EXT = os.path.join(REPO, "gnome-shell", "screenpad-touchbar@screenpad")


def read(name):
    with open(os.path.join(EXT, name)) as f:
        return f.read()


class Card:
    """A bar and its questions on a fake clock; clear says whether the band
    a growing bar would cover is free of touchpad fingers."""

    def __init__(self, text=CONFIG):
        self.now = 100.0
        self.frames = []
        self.clear = True
        self.asks = t.Asks(clock=lambda: self.now)
        self.asks.band_clear = lambda: self.clear
        self.asks.set_available(True)
        self.bar = t.Bar(config(text), t.KeyEmitter(self.frames.append), lambda a: None,
                         clock=lambda: self.now, asks=self.asks)
        self.app = FakeClient()

    def ask(self, ask_id="q", n=2, timeout=60):
        options = [{"id": "o%d" % k, "label": "O%d" % k} for k in range(n)]
        self.asks.add(t.parse_ask(self.app, {"id": ask_id, "title": "T", "options": options,
                                             "timeout": timeout}, self.now))

    def tap(self, slot, px, py):
        self.bar.handle([("begin", slot, px, py), ("end", slot, px, py)])

    def answers(self):
        return [(m["id"], m["option"], m["reason"]) for m in self.app.sent if m["op"] == "answer"]


class ElasticBarTests(unittest.TestCase):
    def test_grows_for_a_card_and_shrinks_after_the_answer(self):
        c = Card()
        self.assertEqual((c.bar.height(), c.bar.touch_height()), (160, 160))
        c.ask()
        self.assertEqual((c.bar.height(), c.bar.touch_height(), c.bar.base_height()),
                         (456, 456, 160))
        c.now += t.ASK_ARM
        # On the second answer, in the area the card added.
        c.tap(0, 1300, 400)
        self.assertEqual(c.answers(), [("q", "o1", "chosen")])
        self.assertEqual((c.bar.height(), c.bar.touch_height(), c.asks.raised), (160, 160, False))

    def test_shrinks_after_dismiss_and_after_a_timeout(self):
        c = Card()
        c.ask("a")
        c.now += t.ASK_ARM
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
        c.now += t.ASK_ARM
        c.tap(0, 1000, 300)
        self.assertEqual((c.asks.shown().id, c.bar.height()), ("second", 456))
        c.tap(0, 1000, 300)                         # too soon for the second
        self.assertEqual(len(c.answers()), 1)
        c.now += t.ASK_ARM
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
        self.assertEqual(c.asks.deadline(), c.now + t.GROW_WAIT)
        c.tap(0, 1000, 50)                          # the bar's own spots, not a card
        self.assertEqual(c.answers(), [])
        c.now += 0.1
        c.clear = True                              # the finger left the band
        c.asks.tick()
        self.assertEqual((c.asks.shown().shown_at, c.bar.height()), (c.now, 456))
        # The arming delay counts from the growth, not from the ask: taps
        # on the area that just appeared are ignored for ASK_ARM.
        grew = c.now
        c.now = grew + t.ASK_ARM - 0.01
        c.tap(0, 1000, 400)
        self.assertEqual(c.answers(), [])
        c.now = grew + t.ASK_ARM
        c.tap(0, 1000, 400)
        self.assertEqual(c.answers(), [("q", "o0", "chosen")])

    def test_grows_anyway_after_the_wait(self):
        c = Card()
        c.clear = False
        c.ask()
        c.now += t.GROW_WAIT - 0.01
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
        self.assertEqual((c.asks.raised, c.asks.waiting_since, c.asks.deadline()),
                         (False, None, None))

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
        self.assertEqual([f[0] for f in c.frames], [(t.EV_KEY, 184, 1), (t.EV_KEY, 184, 0)])


class ElasticSplitTests(unittest.TestCase):
    """The Splitter while the bar's height changes under contacts."""

    def setUp(self):
        self.height = 160
        self.s = t.Splitter(AXES, 5, lambda px, py: py < self.height)

    def frame(self, *groups):
        out = None
        for e in [e for g in groups for e in g] + [(t.EV_SYN, t.SYN_REPORT, 0)]:
            out = self.s.feed(*e)
        return out

    def test_contacts_keep_their_kind_and_new_ones_take_the_new_height(self):
        # A touchpad finger at panel row 300, in the band a card would cover.
        pad, bar = self.frame(touch(0, 1, 2000, 600))
        self.assertEqual((codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), bar), ([1], []))
        self.assertTrue(self.s.pad_in_band(160, t.CARD_HEIGHT))
        self.height = t.CARD_HEIGHT
        # It stays a touchpad contact, at its full device coordinates.
        pad, bar = self.frame(touch(0, None, 2010, 620))
        self.assertEqual((codes(pad, t.EV_ABS, t.ABS_MT_POSITION_Y), bar), ([620], []))
        # A new contact in the band is the bar's now.
        pad, bar = self.frame(touch(1, 2, 1000, 600))
        self.assertEqual((pad, bar), ([], [("begin", 1, 500.0, 300.0)]))
        self.height = 160
        # It stays the bar's after the bar shrank, to its end.
        pad, bar = self.frame(touch(1, None, 1000, 700), lift(0))
        self.assertEqual(codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), [-1])
        self.assertEqual(bar, [("move", 1, 500.0, 350.0)])
        pad, bar = self.frame(lift(1))
        self.assertEqual((pad, bar), ([], [("end", 1, 500.0, 350.0)]))
        # A new contact in the band is the touchpad's again.
        pad, bar = self.frame(touch(2, 3, 1000, 600))
        self.assertEqual((codes(pad, t.EV_ABS, t.ABS_MT_TRACKING_ID), bar), ([3], []))

    def test_band_check(self):
        self.frame(touch(0, 1, 2000, 1200))           # row 600: below any card
        self.assertFalse(self.s.pad_in_band(160, t.CARD_HEIGHT))
        self.frame(touch(1, 2, 2000, 200))             # row 100: in the bar
        self.assertFalse(self.s.pad_in_band(160, t.CARD_HEIGHT))
        self.frame(touch(0, None, 2000, 900))          # row 450: in the band
        self.assertTrue(self.s.pad_in_band(160, t.CARD_HEIGHT))
        self.frame(lift(0))
        self.assertFalse(self.s.pad_in_band(160, t.CARD_HEIGHT))


class DaemonCardTests(unittest.TestCase):
    """The daemon's own wiring: its band check and what it publishes."""

    def test_growth_waits_for_the_finger_end_to_end(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            now = [10.0]
            daemon = t.Daemon(config(CONFIG), None)
            daemon.apps.asks.clock = lambda: now[0]
            daemon.apps.asks.can_show = lambda: True
            daemon.bar = daemon.new_bar(t.KeyEmitter(lambda ev: None))
            daemon.bar.clock = lambda: now[0]
            daemon.splitter = t.Splitter(AXES, 5, lambda px, py: py < daemon.bar.touch_height())
            app = FakeClient()
            daemon.apps.connect(app)
            daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
            sp = daemon.splitter
            # A pointer stroke in the band; then a question.
            for ev in touch(0, 1, 2000, 600) + [(t.EV_SYN, t.SYN_REPORT, 0)]:
                sp.feed(*ev)
            daemon.apps.handle(app, {"op": "ask", "id": "q", "title": "Run it?",
                                     "options": [{"id": "y", "label": "Yes"}]})
            self.assertFalse(daemon.apps.asks.raised)
            daemon.publish()
            with open(t.state_path()) as f:
                state = json.load(f)
            self.assertEqual((state["height"], state["strut"], state["tiles"][0]["id"]),
                             (160, 160, "button-0"))
            # The finger lifts: the bar grows; the strut stays the bar's own.
            for ev in lift(0) + [(t.EV_SYN, t.SYN_REPORT, 0)]:
                sp.feed(*ev)
            daemon.apps.asks.tick()
            daemon.publish()
            with open(t.state_path()) as f:
                state = json.load(f)
            self.assertEqual((state["height"], state["strut"], state["tiles"][0]["id"]),
                             (456, 160, "ask-text"))
            # After ASK_ARM, a tap in the new area answers; the pad sees nothing.
            now[0] += t.ASK_ARM
            for groups in (touch(1, 2, 2000, 600), lift(1)):
                out = None
                for ev in groups + [(t.EV_SYN, t.SYN_REPORT, 0)]:
                    out = sp.feed(*ev)
                pad, bar = out
                self.assertEqual(pad, [])
                daemon.bar.handle(bar)
            self.assertEqual(app.last("answer"),
                             {"op": "answer", "id": "q", "option": "y", "reason": "chosen"})
            self.assertEqual(daemon.bar.touch_height(), 160)


def notify(**kw):
    msg = {"op": "notify", "id": "n1", "app": "org.gnome.Calendar.desktop", "source": "Calendar",
           "title": "Standup", "text": "Starts in 5 minutes", "urgency": "normal",
           "actions": ["Join", "Snooze"], "banners": True, "dnd": False, "acknowledged": False}
    msg.update(kw)
    return msg


class MirrorRuleTests(unittest.TestCase):
    def refusal(self, policy=None, **kw):
        return t.mirror_refusal(t.parse_notify(notify(**kw)), policy or t.NotifyPolicy())

    def test_rules(self):
        cases = [
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
        self.assertEqual(self.refusal(t.NotifyPolicy(mirror=False)),
                         "mirroring is off in the config")
        self.assertEqual(self.refusal(t.NotifyPolicy(deny=[cal])), "the config denies " + cal)
        self.assertIsNone(self.refusal(t.NotifyPolicy(allow=[cal])))
        self.assertIn("not in the config's allow list",
                      self.refusal(t.NotifyPolicy(allow=["x.desktop"])))
        self.assertIn("an app with no id", self.refusal(t.NotifyPolicy(allow=[cal]), app=None))
        # The config's say comes first: a denied app's critical card too.
        self.assertIsNotNone(self.refusal(t.NotifyPolicy(deny=[cal]), urgency="critical"))

    def test_normalized(self):
        card = t.parse_notify(notify(app="org.gnome.Calendar", title="a\nb\tc " * 40,
                                     actions=["Join the meeting right now please", "\x07"]))
        self.assertEqual(card["app"], "org.gnome.Calendar.desktop")
        self.assertEqual(len(card["title"]), 80)
        self.assertTrue(card["title"].startswith("a b c a b c"))
        self.assertTrue(card["title"].endswith("\u2026"))
        self.assertEqual(card["actions"], ["Join the meeting right\u2026", "Action"])
        self.assertIsNone(t.parse_notify(notify(app="not an id!"))["app"])
        for bad in ({"urgency": "urgent"}, {"id": "-x"}, {"actions": "Join"},
                    {"actions": [1]}, {"dnd": "yes"}, {"extra": 1}):
            with self.assertRaises(t.ProtocolError, msg=bad):
                t.parse_notify(notify(**bad))


class NotifyAppsTests(unittest.TestCase):
    """The extension's notify messages, through Apps, Asks and the Bar."""

    def setUp(self):
        self.now = 50.0
        self.apps = t.Apps(clock=lambda: self.now)
        self.apps.asks.set_available(True)
        self.ext = FakeClient()
        self.apps.connect(self.ext)
        self.apps.handle(self.ext, {"op": "hello", "app": "gnome-notifications"})
        self.bar = t.Bar(config(CONFIG), t.KeyEmitter(lambda ev: None), lambda a: None,
                         clock=lambda: self.now, asks=self.apps.asks)

    def send(self, **kw):
        self.apps.handle(self.ext, notify(**kw))

    def answers(self):
        return [m for m in self.ext.sent if m["op"] in ("answer", "error")]

    def test_a_notification_with_buttons_is_a_card(self):
        self.send(actions=["Join", "Snooze", "Decline", "Later"])
        ask = self.apps.asks.shown()
        self.assertEqual((ask.title, ask.text, ask.deadline), (
            "Standup", "Calendar \u00b7 Starts in 5 minutes", 50.0 + t.NOTIFY_TIMEOUT))
        self.assertEqual([(o["id"], o["label"]) for o in ask.options],
                         [("action-0", "Join"), ("action-1", "Snooze"), ("action-2", "Decline")])
        self.assertEqual(self.bar.height(), t.CARD_HEIGHT)
        # Never activated by itself: nothing goes back until a tap.
        self.assertEqual(self.answers(), [])
        self.now += t.ASK_ARM
        self.bar.handle([("begin", 0, 1300, 300), ("end", 0, 1300, 300)])
        self.assertEqual(self.answers(), [{"op": "answer", "id": "n1", "option": "action-1",
                                           "reason": "chosen"}])
        self.assertEqual(self.bar.height(), 160)

    def test_dismiss_and_timeout_close_the_card_only(self):
        self.send()
        self.now += t.ASK_ARM
        self.bar.handle([("begin", 0, 700, 100), ("end", 0, 700, 100)])
        self.send(id="n2")
        self.now += t.NOTIFY_TIMEOUT
        self.apps.asks.tick()
        self.assertEqual([(m["id"], m["option"], m["reason"]) for m in self.answers()],
                         [("n1", None, "dismissed"), ("n2", None, "timeout")])

    def test_not_mirrored_is_answered_at_once(self):
        self.send(actions=[])
        self.send(id="n2", dnd=True, banners=False)
        self.assertEqual([(m["id"], m["reason"], m["why"]) for m in self.answers()],
                         [("n1", "ignored", "no buttons and not urgent"),
                          ("n2", "ignored", "do not disturb")])
        self.assertEqual((self.apps.asks.queue, self.bar.height()), ([], 160))
        self.send(id="n3", dnd=True, banners=False, urgency="critical", actions=[])
        ask = self.apps.asks.shown()
        self.assertEqual((ask.id, ask.options), ("n3", []))

    def test_config_policy(self):
        self.apps.notify_policy = t.NotifyPolicy(deny=["org.gnome.Calendar.desktop"], timeout=5)
        self.send()
        self.send(id="n2", app="org.gnome.Fractal.desktop")
        self.assertEqual(self.answers()[0]["why"], "the config denies org.gnome.Calendar.desktop")
        self.assertEqual(self.apps.asks.shown().deadline, 55.0)

    def test_an_update_replaces_the_card_and_arms_it_anew(self):
        self.send()
        self.now += t.ASK_ARM
        old = self.apps.asks.shown()
        self.bar.handle([("begin", 0, 1000, 300)])     # a finger on "Join"
        self.send(actions=["Accept", "Join"])           # the app changed its buttons
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
        for k in range(t.MAX_NOTIFY_WAITING + 1):
            self.send(id="n%d" % (k + 10))
        self.assertEqual(len(self.apps.asks.queue), t.MAX_NOTIFY_WAITING)
        self.assertEqual(self.answers()[-1]["why"], "too many notifications waiting")
        self.apps.asks.set_available(False)
        self.assertEqual(len([m for m in self.answers() if m["reason"] == "unavailable"]),
                         t.MAX_NOTIFY_WAITING)
        self.send(id="late")
        self.assertEqual(self.answers()[-1]["reason"], "unavailable")

    def test_agent_asks_and_notifications_share_the_card(self):
        agent = FakeClient()
        self.apps.connect(agent)
        self.apps.handle(agent, {"op": "hello", "app": "agents"})
        self.apps.handle(agent, {"op": "ask", "id": "q", "title": "Run npm test?",
                                 "options": [{"id": "yes", "label": "Allow once"}]})
        self.send()
        tiles = t.ask_tiles(self.apps.asks.shown(), queued=len(self.apps.asks.queue) - 1)
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
        self.assertEqual((n.mirror, n.allow, n.deny, n.timeout), (True, None, [], t.NOTIFY_TIMEOUT))
        n = config('[notifications]\nmirror = false\nallow = ["org.gnome.Calendar"]\n'
                   'deny = ["a.desktop", "a.desktop"]\ntimeout = 45\n').notifications
        self.assertEqual((n.mirror, n.allow, n.deny, n.timeout),
                         (False, ["org.gnome.Calendar.desktop"], ["a.desktop"], 45))
        self.assertEqual(config('[notifications]\nallow = []\n').notifications.describe(),
                         "mirrored for 20 s, only no app")

    def test_errors(self):
        for text, error in (
                ('notifications = 3', "must be a [notifications] table"),
                ('[notifications]\nmirror = "no"', "mirror must be true or false"),
                ('[notifications]\nmute = true', "unknown setting(s): mute"),
                ('[notifications]\ndeny = "a.desktop"', "deny must be a list"),
                ('[notifications]\nallow = ["not an id!"]', "is not a desktop file id"),
                ('[notifications]\ntimeout = 0', "timeout must be a whole number"),
                ('[notifications]\ntimeout = 2.5', "timeout must be a whole number")):
            with self.assertRaises(t.ConfigError, msg=text) as e:
                config(text)
            self.assertIn("notifications: ", str(e.exception))
            self.assertIn(error, str(e.exception))

    def test_default_documents_it(self):
        # The commented example in the default is a valid table once uncommented.
        text = re.sub(r"(?m)^# (\[notifications\]|mirror =|deny =|allow =|timeout =)", r"\1",
                      t.EXAMPLE_CONFIG)
        n = config(text).notifications
        self.assertEqual((n.mirror, n.deny, n.timeout),
                         (False, ["org.gnome.Evolution.desktop"], t.NOTIFY_TIMEOUT))

    def test_check_and_shortcut_edits(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(CONFIG + '\n[notifications]\ndeny = ["a.desktop"]\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(t.main(["check"]), 0)
            self.assertIn("bar: 160 of 1080 px high (10.0 mm); 456 px while a card shows",
                          out.getvalue())
            self.assertIn("notifications: mirrored for 20 s, never a.desktop", out.getvalue())
            # Adding a shortcut keeps the table (and appends after it).
            c = t.edit_config(lambda c: (c.height, c.shortcuts + ["web.desktop"]))
            self.assertEqual((c.shortcuts, c.notifications.deny),
                             (["files.desktop", "web.desktop"], ["a.desktop"]))

    def test_migrate_the_default_before_notifications(self):
        with open(os.path.join(REPO, "tests", "touchbar-v4-default.toml")) as f:
            v4 = f.read()
        self.assertNotIn("[notifications]", v4)
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(v4)
            self.assertTrue(t.migrate_config())
            with open(path) as f:
                self.assertEqual(f.read(), t.EXAMPLE_CONFIG)
            self.assertFalse(t.migrate_config())


class ExtensionMirrorTests(unittest.TestCase):
    """Static checks on notifications.js, which cannot run here."""

    def test_wired_into_the_extension(self):
        ext = read("extension.js")
        self.assertIn("import {NotificationMirror} from './notifications.js';", ext)
        self.assertIn("const SOCKET_NAME = '%s';" % t.SOCKET_NAME, ext)
        disable = ext[ext.index("    disable() {"):ext.index("    Launch(id)")]
        self.assertIn("this._mirror?.destroy();", disable)

    def test_follows_the_message_tray(self):
        src = read("notifications.js")
        for signal in ("'source-added'", "'source-removed'", "'notification-request-banner'",
                       "'destroy'", "'action-added'", "'action-removed'"):
            self.assertIn(signal, src)
        self.assertIn("dnd: !this._settings.get_boolean('show-banners')", src)
        self.assertIn("banners: policy?.showBanners ?? true", src)
        # Only an app's own policy names an app; the generic one is "generic".
        self.assertIn("policy instanceof MessageTray.NotificationApplicationPolicy", src)
        # GNOME's own banner handler runs first and may acknowledge it:
        # whether it was seen is taken when it was added.
        self.assertIn("this._added.set(notification, notification.acknowledged)", src)
        self.assertNotIn("acknowledged: notification.acknowledged", src)

    def test_never_activates_by_itself(self):
        src = read("notifications.js")
        # One activation, of a button, in the answer to a tap; never the
        # notification's own activate().
        self.assertEqual(src.count("activate()"), 1)
        answer = src[src.index("    _answer(msg) {"):src.index("    _read(input) {")]
        self.assertIn("action.activate();", answer)
        self.assertLess(answer.index("msg.reason !== 'chosen'"), answer.index("action.activate"))
        self.assertIn("card.notification.actions.includes(action)", answer)
        self.assertNotRegex(src, r"notification\.activate\(")

    def test_messages_match_the_daemon(self):
        src = read("notifications.js")
        names = re.search(r"const URGENCY = \[([^\]]*)\];", src).group(1)
        self.assertEqual(tuple(re.findall(r"'(\w+)'", names)), t.URGENCIES)
        self.assertTrue(t.TILE_NAME.fullmatch(re.search(r"const APP = '([^']+)';", src).group(1)))
        action = re.search(r"const ACTION = /(.*)/;", src).group(1)
        self.assertRegex("action-%d" % (t.MAX_OPTIONS - 1), action)
        body = src[src.index("op: 'notify', id,"):]
        body = body[:body.index("});")]
        keys = set(re.findall(r"^\s*(\w+)[:,]", body, re.M)) - {"op"} | {"id"}
        msg = {k: None for k in keys}
        t._known(msg, {"id", "app", "source", "title", "text", "urgency", "actions", "banners",
                       "dnd", "acknowledged"})
        self.assertEqual(keys, set(notify()) - {"op"})


if __name__ == "__main__":
    unittest.main()
