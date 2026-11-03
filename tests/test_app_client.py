"""Tests for the client library (screenpad_app) against the daemon's own
socket: the handshake both ways of a version mismatch (the daemon's version
range patched) and with a bar from before the version, tiles, pages,
questions, Link's reconnects and what it logs, and that the library imports
nothing of the daemon.

Run with: python3 -m unittest discover -s tests"""

import select
import subprocess
import sys
import unittest
from unittest import mock

from app_support import REPO, Clock, DaemonBar

from screenpad_app import (
    PROTOCOL,
    HelloRefused,
    Link,
    ProtocolMismatch,
    Touchbar,
    TouchbarError,
    TouchbarGone,
    clip,
    socket_path,
)
from screenpad_touchbar.apps import Apps
from screenpad_touchbar.protocol import known_fields

PAGE = {"op": "page", "page": "counter", "widgets": [{"id": "plus", "col": 0, "row": 0, "label": "+1"}]}


class RecordingApps(Apps):
    """The daemon's Apps, keeping every message it handled."""

    def __init__(self):
        super().__init__()
        self.received = []

    def handle(self, client, msg):
        self.received.append(msg)
        super().handle(client, msg)


class LegacyApps(RecordingApps):
    """A daemon from before the version: hello knows no "protocol" field,
    and its welcome says no version."""

    def _hello(self, client, msg):
        known_fields(msg, {"app", "tiles"})
        sent = client.send
        client.send = lambda reply: sent({k: v for k, v in reply.items() if k != "protocol"})
        try:
            super()._hello(client, msg)
        finally:
            client.send = sent


def speaking(low, high):
    """The daemon speaking app protocols low to high."""
    return mock.patch.multiple("screenpad_touchbar.protocol", MIN_APP_PROTOCOL=low, APP_PROTOCOL=high)


def bar(test, apps=None, placed=("counter",)):
    return DaemonBar(test, placed=placed, apps=apps or RecordingApps())


def client(test, b, app="counter", tiles=("counter",), **kwargs):
    tb = Touchbar(socket_path(b.environ), app, tiles, **kwargs)
    test.addCleanup(tb.close)
    return tb


def messages(tb, until, timeout=2.0):
    """What tb received until until(messages) held."""
    got: list[dict] = []
    for _ in range(int(timeout / 0.01)):
        if until(got):
            return got
        if select.select([tb], [], [], 0.01)[0]:
            got += tb.receive()
    raise AssertionError("not received: %r" % got)


class HandshakeTests(unittest.TestCase):
    def test_hello_says_the_version_and_the_welcome_answers_it(self):
        b = bar(self)
        tb = client(self, b, tiles=["counter", "other"])
        welcome = tb.handshake()
        self.assertEqual(
            welcome,
            {
                "op": "welcome",
                "app": "counter",
                "placed": ["counter"],
                "unplaced": ["other"],
                "taken": [],
                "protocol": PROTOCOL,
            },
        )
        self.assertEqual(tb.protocol, PROTOCOL)
        self.assertEqual(
            b.apps.received[0], {"op": "hello", "app": "counter", "tiles": ["counter", "other"], "protocol": 2}
        )

    def test_a_bar_too_new_for_the_app(self):
        b = bar(self)
        with speaking(3, 4), self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertEqual((e.exception.speaks, e.exception.supported), (2, (3, 4)))
        self.assertEqual(str(e.exception), "the touch bar speaks app protocol 3 to 4, the app 2; update the app")
        with b.lock:
            self.assertEqual(b.apps.names(), [])

    def test_a_bar_too_old_for_the_app(self):
        b = bar(self)
        with speaking(1, 1), self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertEqual(
            str(e.exception), "the touch bar speaks app protocol 1 to 1, the app 2; update screenpad-linux"
        )
        self.assertIsInstance(e.exception, HelloRefused)
        self.assertIsInstance(e.exception, TouchbarError)

    def test_a_bar_from_before_the_version(self):
        b = bar(self, LegacyApps())
        with self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertIsNone(e.exception.supported)
        self.assertIn("speaks only version 1; update screenpad-linux", str(e.exception))
        # An app that speaks version 1 says no version, and is welcome there.
        tb = client(self, b, protocol=1)
        self.assertEqual(
            tb.handshake(), {"op": "welcome", "app": "counter", "placed": ["counter"], "unplaced": [], "taken": []}
        )
        self.assertEqual(b.apps.received[-1], {"op": "hello", "app": "counter", "tiles": ["counter"]})
        self.assertEqual(tb.protocol, 1)

    def test_the_refusal_comes_through_receive_too(self):
        b = bar(self)
        with speaking(1, 1):
            tb = client(self, b)
            with self.assertRaises(ProtocolMismatch):
                messages(tb, lambda got: False)

    def test_other_refusals_and_no_daemon(self):
        b = bar(self)
        with self.assertRaises(HelloRefused) as e:
            client(self, b, app="Not A Name").handshake()
        self.assertNotIsInstance(e.exception, ProtocolMismatch)
        self.assertIn("refused hello: app must be", str(e.exception))
        with self.assertRaises(OSError):
            Touchbar(b.path + ".none", "counter")
        with self.assertRaises(TouchbarGone):
            socket_path({})

    def test_the_bar_going_away(self):
        b = bar(self)
        tb = client(self, b)
        tb.handshake()
        b.drop()
        with self.assertRaises(TouchbarGone):
            messages(tb, lambda got: False)


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.bar = bar(self)
        self.tb = client(self, self.bar)
        self.tb.handshake()

    def test_tiles_and_pages_are_sent_when_they_change(self):
        self.tb.tile("counter", label="0")
        self.tb.show([PAGE, {"op": "set", "tile": "counter", "label": "0"}, {"op": "ping"}])
        self.tb.tile("counter", label="1")
        messages(self.tb, lambda got: {"op": "pong"} in got)
        self.bar.wait(lambda: self.bar.apps.provided().get("counter") == {"label": "1", "tone": "neutral"})
        self.assertEqual([m["op"] for m in self.bar.apps.received], ["hello", "set", "page", "ping", "set"])
        self.tb.unpage("counter")
        self.bar.wait(lambda: "counter" not in self.bar.apps.pages)
        self.tb.show([PAGE])
        self.bar.wait(lambda: "counter" in self.bar.apps.pages)

    def test_a_page_unpaged_any_way_is_sent_again(self):
        for unpage in (
            lambda: self.tb.show([{"op": "unpage", "page": "counter"}]),
            lambda: self.tb.send({"op": "unpage", "page": "counter"}),
        ):
            self.tb.show([PAGE])
            self.bar.wait(lambda: "counter" in self.bar.apps.pages)
            unpage()
            self.bar.wait(lambda: "counter" not in self.bar.apps.pages)
        self.tb.show([PAGE])
        self.bar.wait(lambda: "counter" in self.bar.apps.pages)

    def test_taps_and_answers_come_back(self):
        with self.bar.lock:
            self.bar.apps.asks.available = True
        self.tb.show([PAGE])
        self.tb.ask("q1", "Reset?", [("no", "Keep"), ("yes", "Reset", "reject")], text="the count", timeout=30)
        self.bar.wait(lambda: self.bar.apps.asks.queue and "counter" in self.bar.apps.pages)
        with self.bar.lock:
            apps = self.bar.apps
            (ask,) = apps.asks.queue
            self.assertEqual(
                ask.options,
                [{"id": "no", "label": "Keep", "tone": "neutral"}, {"id": "yes", "label": "Reset", "tone": "reject"}],
            )
            apps.event("counter", "down")
            apps.event("counter", "up", inside=True)
            apps.press(ask.client, "counter", "plus", "down")
            apps.press(ask.client, "counter", "plus", "long")
            apps.asks.choose(ask, 1)
        got = messages(self.tb, lambda got: len(got) >= 5)
        self.assertEqual(
            got,
            [
                {"op": "event", "tile": "counter", "type": "down"},
                {"op": "event", "tile": "counter", "type": "up", "inside": True},
                {"op": "event", "page": "counter", "widget": "plus", "type": "down"},
                {"op": "event", "page": "counter", "widget": "plus", "type": "long"},
                {"op": "answer", "id": "q1", "option": "yes", "reason": "chosen"},
            ],
        )
        self.tb.ask("q2", "Again?", [("y", "Yes")])
        self.bar.wait(lambda: bool(self.bar.apps.asks.queue))
        self.tb.cancel("q2")
        got = messages(self.tb, lambda got: bool(got))
        self.assertEqual(got, [{"op": "answer", "id": "q2", "option": None, "reason": "cancelled"}])

    def test_errors_are_messages_after_the_welcome(self):
        self.tb.tile("someone-elses", label="x")
        got = messages(self.tb, lambda got: bool(got))
        self.assertEqual(got[0]["op"], "error")
        self.assertIn("not yours", got[0]["message"])

    def test_one_app_per_tile_and_page(self):
        other = client(self, self.bar, app="other", tiles=["counter"])
        self.assertEqual(other.handshake()["taken"], ["counter"])
        page = {"op": "page", "page": "p", "widgets": PAGE["widgets"]}
        self.tb.show([page])
        self.bar.wait(lambda: "p" in self.bar.apps.pages)
        other.show([page])
        self.assertIn("another app's", messages(other, lambda got: bool(got))[0]["message"])
        self.tb.close()
        self.bar.wait(lambda: self.bar.apps.names() == ["other"] and "p" not in self.bar.apps.pages)

    def test_clip(self):
        self.assertEqual(clip("  a\n b  ", 10), "a b")
        self.assertEqual(clip("abcdef", 4), "abc…")


class LinkTests(unittest.TestCase):
    def link(self, b, **kwargs):
        self.clock, self.logged = Clock(), []
        link = Link(b.path, "counter", ["counter"], retry=5.0, log=self.logged.append, clock=self.clock, **kwargs)
        self.addCleanup(link.close)
        return link

    def receive(self, link, until):
        got: list[dict] = []
        for _ in range(200):
            if until(got) or not link.connected:
                return got
            if select.select([link.fileno()], [], [], 0.01)[0]:
                got += link.receive()
        raise AssertionError("not received: %r" % got)

    def test_it_connects_when_due_and_logs_a_loss_once(self):
        b = bar(self)
        link = self.link(b)
        link.show([{"op": "set", "tile": "counter", "label": "lost"}])  # not connected: nothing
        self.assertTrue(link.connect())
        self.assertFalse(link.connect())
        link.show([{"op": "set", "tile": "counter", "label": "1"}])
        self.assertEqual(self.receive(link, bool)[0]["op"], "welcome")
        b.wait(lambda: b.apps.provided().get("counter") == {"label": "1", "tone": "neutral"})
        b.drop()
        self.receive(link, lambda got: False)
        self.assertFalse(link.connected)
        self.assertIsNone(link.fileno())
        self.assertEqual(self.logged, ["lost the touch bar (the touch bar closed the connection); retrying every 5 s"])
        self.assertFalse(link.connect())  # not due yet
        self.clock.now += 5
        self.assertTrue(link.connect())
        link.show([{"op": "set", "tile": "counter", "label": "1"}])  # a new connection sends it again
        b.wait(lambda: b.apps.provided().get("counter") == {"label": "1", "tone": "neutral"})

    def test_a_page_unpaged_through_send_is_sent_again(self):
        b = bar(self)
        link = self.link(b)
        self.assertTrue(link.connect())
        link.show([PAGE])
        b.wait(lambda: "counter" in b.apps.pages)
        link.send({"op": "unpage", "page": "counter"})
        b.wait(lambda: "counter" not in b.apps.pages)
        link.show([PAGE])
        b.wait(lambda: "counter" in b.apps.pages)

    def test_a_refusal_is_logged_once_and_retried(self):
        b = bar(self)
        link = self.link(b)
        with speaking(1, 1):
            for _ in range(3):
                self.assertTrue(link.connect())
                self.receive(link, lambda got: False)
                self.clock.now += 5
            self.assertIsInstance(link.refused, ProtocolMismatch)
            self.assertEqual(
                self.logged,
                ["the touch bar speaks app protocol 1 to 1, the app 2; update screenpad-linux; retrying every 5 s"],
            )
            # The same app speaking version 1 is welcome there.
            link.protocol = 1
            self.assertTrue(link.connect())
            self.assertEqual(self.receive(link, bool)[0]["op"], "welcome")
            self.assertIsNone(link.refused)

    def test_no_daemon_is_quiet(self):
        b = bar(self)
        link = Link(b.path + ".none", "counter", log=self.fail)
        self.assertFalse(link.connect())
        self.assertFalse(link.connected)


class ImportTests(unittest.TestCase):
    def test_the_library_imports_nothing_of_the_daemon(self):
        code = (
            "import sys; sys.path.insert(0, %r); import screenpad_app; "
            "print(sorted(m for m in sys.modules if m.startswith('screenpad_')))" % REPO
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "['screenpad_app', 'screenpad_app.client', 'screenpad_app.common']")


if __name__ == "__main__":
    unittest.main()
