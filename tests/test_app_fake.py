"""Tests for the client library (screenpad_app) against its own FakeBar:
the handshake both ways of a version mismatch, tiles, pages, questions,
Link's reconnects and what it logs, and that the library imports nothing of
the daemon. The same client against the daemon itself is test_app_protocol.py.

Run with: python3 -m unittest discover -s tests"""

import select
import subprocess
import sys
import unittest

from app_support import REPO, Clock

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
from screenpad_app.testing import FakeBar


def bar(test, **kwargs):
    b = FakeBar(**kwargs).start()
    test.addCleanup(b.close)
    return b


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
        b = bar(self, placed=["counter"])
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
            b.received[0][1], {"op": "hello", "app": "counter", "tiles": ["counter", "other"], "protocol": 2}
        )
        self.assertEqual(b.protocol_of("counter"), PROTOCOL)

    def test_a_bar_too_new_for_the_app(self):
        b = bar(self, min_protocol=3, max_protocol=4)
        with self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertEqual((e.exception.speaks, e.exception.supported), (2, (3, 4)))
        self.assertEqual(str(e.exception), "the touch bar speaks app protocol 3 to 4, the app 2; update the app")
        self.assertEqual(b.apps, [])

    def test_a_bar_too_old_for_the_app(self):
        b = bar(self, max_protocol=1)
        with self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertEqual(
            str(e.exception), "the touch bar speaks app protocol 1 to 1, the app 2; update screenpad-linux"
        )
        self.assertIsInstance(e.exception, HelloRefused)
        self.assertIsInstance(e.exception, TouchbarError)

    def test_a_bar_from_before_the_version(self):
        b = bar(self, legacy=True)
        with self.assertRaises(ProtocolMismatch) as e:
            client(self, b).handshake()
        self.assertIsNone(e.exception.supported)
        self.assertIn("speaks only version 1; update screenpad-linux", str(e.exception))
        # An app that speaks version 1 says no version, and is welcome there.
        tb = client(self, b, protocol=1)
        self.assertEqual(tb.handshake()["app"], "counter")
        self.assertEqual(b.received[-1][1], {"op": "hello", "app": "counter", "tiles": ["counter"]})
        self.assertEqual(tb.protocol, 1)

    def test_the_refusal_comes_through_receive_too(self):
        b = bar(self, max_protocol=1)
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
        self.bar = bar(self, placed=["counter"])
        self.tb = client(self, self.bar)
        self.tb.handshake()

    def test_tiles_and_pages_are_sent_when_they_change(self):
        page = {"op": "page", "page": "counter", "widgets": [{"id": "plus", "col": 0, "row": 0, "label": "+1"}]}
        self.tb.tile("counter", label="0")
        self.tb.show([page, {"op": "set", "tile": "counter", "label": "0"}, {"op": "ping"}])
        self.tb.tile("counter", label="1")
        self.bar.wait(lambda: self.bar.tiles.get("counter") == {"label": "1"} and "counter" in self.bar.pages)
        messages(self.tb, lambda got: {"op": "pong"} in got)
        self.assertEqual([m["op"] for _, m in self.bar.received], ["hello", "set", "page", "ping", "set"])
        self.tb.unpage("counter")
        self.bar.wait(lambda: "counter" not in self.bar.pages)
        self.tb.show([page])
        self.bar.wait(lambda: "counter" in self.bar.pages)

    def test_taps_and_answers_come_back(self):
        self.tb.show([{"op": "page", "page": "counter", "widgets": []}])
        self.tb.ask("q1", "Reset?", [("no", "Keep"), ("yes", "Reset", "reject")], text="the count", timeout=30)
        self.bar.wait(lambda: "q1" in self.bar.asks and "counter" in self.bar.pages)
        self.assertEqual(
            self.bar.asks["q1"]["options"],
            [{"id": "no", "label": "Keep"}, {"id": "yes", "label": "Reset", "tone": "reject"}],
        )
        self.bar.press("counter")
        self.bar.tap("counter", "plus", "long")
        self.bar.answer("q1", "yes")
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
        self.bar.wait(lambda: "q2" in self.bar.asks)
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
        self.tb.show([{"op": "page", "page": "p", "widgets": []}])
        self.bar.wait(lambda: "p" in self.bar.pages)
        other.show([{"op": "page", "page": "p", "widgets": []}])
        self.assertIn("another app's", messages(other, lambda got: bool(got))[0]["message"])
        self.tb.close()
        self.bar.wait(lambda: self.bar.apps == ["other"] and "p" not in self.bar.pages)

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
        b = bar(self, placed=["counter"])
        link = self.link(b)
        link.show([{"op": "set", "tile": "counter", "label": "lost"}])  # not connected: nothing
        self.assertTrue(link.connect())
        self.assertFalse(link.connect())
        link.show([{"op": "set", "tile": "counter", "label": "1"}])
        self.assertEqual(self.receive(link, bool)[0]["op"], "welcome")
        b.wait(lambda: b.tiles.get("counter") == {"label": "1"})
        b.drop()
        self.receive(link, lambda got: False)
        self.assertFalse(link.connected)
        self.assertIsNone(link.fileno())
        self.assertEqual(self.logged, ["lost the touch bar (the touch bar closed the connection); retrying every 5 s"])
        self.assertFalse(link.connect())  # not due yet
        self.clock.now += 5
        self.assertTrue(link.connect())
        link.show([{"op": "set", "tile": "counter", "label": "1"}])  # a new connection sends it again
        b.wait(lambda: b.tiles.get("counter") == {"label": "1"})

    def test_a_refusal_is_logged_once_and_retried(self):
        b = bar(self, max_protocol=1)
        link = self.link(b)
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
            "import sys; sys.path.insert(0, %r); import screenpad_app, screenpad_app.testing; "
            "print(sorted(m for m in sys.modules if m.startswith('screenpad_')))" % REPO
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.strip(),
            "['screenpad_app', 'screenpad_app.client', 'screenpad_app.common', 'screenpad_app.testing']",
        )


if __name__ == "__main__":
    unittest.main()
