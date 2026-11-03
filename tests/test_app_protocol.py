"""Tests for the app protocol's version: the daemon's side of the handshake,
and the client library (screenpad_app) against the daemon's own socket, so
the library and the daemon agree. The rest of the client is test_app_client.py.

Run with: python3 -m unittest discover -s tests"""

import select
import unittest

from app_support import DaemonBar
from touchbar_support import FakeClient

import screenpad_app
from screenpad_app import ProtocolMismatch, Touchbar
from screenpad_app import common as lib
from screenpad_touchbar import common
from screenpad_touchbar.apps import Apps


class HelloTests(unittest.TestCase):
    def setUp(self):
        self.apps = Apps()
        self.apps.set_placed(["agents"])
        self.a = FakeClient()
        self.apps.connect(self.a)

    def hello(self, **fields):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"], **fields})
        return self.a.sent[-1]

    def test_a_hello_without_a_version_is_version_1(self):
        self.assertEqual(self.hello()["protocol"], 1)
        self.assertEqual(self.a.protocol, 1)

    def test_every_supported_version_is_welcome(self):
        for version in range(common.MIN_APP_PROTOCOL, common.APP_PROTOCOL + 1):
            self.setUp()
            self.assertEqual(self.hello(protocol=version)["op"], "welcome")
            self.assertEqual((self.a.protocol, self.a.sent[-1]["protocol"]), (version, version))

    def test_a_version_not_spoken_is_a_typed_error_and_hello_can_be_said_again(self):
        error = self.hello(protocol=common.APP_PROTOCOL + 1)
        self.assertEqual(
            error,
            {
                "op": "error",
                "request": "hello",
                "code": "unsupported-protocol",
                "message": "app protocol 3 is not supported: this touch bar speaks 1 to 2; update screenpad-linux",
                "protocol": 3,
                "supported": {"min": 1, "max": 2},
            },
        )
        self.assertIsNone(self.a.name)
        self.assertFalse(self.apps.online("agents"))
        self.assertEqual(self.hello(protocol=2)["op"], "welcome")

    def test_a_version_that_is_not_one(self):
        for bad in (0, -1, True, "2", 2.0):
            self.setUp()
            error = self.hello(protocol=bad)
            self.assertEqual((error["op"], error["request"]), ("error", "hello"), bad)
            self.assertNotIn("code", error)

    def test_the_library_speaks_what_the_daemon_does(self):
        self.assertEqual(screenpad_app.PROTOCOL, common.APP_PROTOCOL)
        self.assertEqual(lib.MIN_PROTOCOL, common.MIN_APP_PROTOCOL)
        self.assertEqual(lib.SOCKET, ("screenpad", common.SOCKET_NAME))
        for name in ("MAX_LINE", "MAX_OPTIONS", "MAX_ASK_TIMEOUT", "MAX_PAGES", "MAX_COLUMNS", "MAX_ROWS"):
            self.assertEqual(getattr(lib, name), getattr(common, name), name)
        self.assertEqual(
            (lib.MAX_WIDGETS, lib.MAX_LINES, lib.TONES), (common.MAX_WIDGETS, common.MAX_LINES, common.TONES)
        )
        self.assertEqual((lib.NAME.pattern, lib.ID.pattern), (common.TILE_NAME.pattern, common.OPTION_ID.pattern))

    def test_the_library_limits_are_the_daemons(self):
        client = FakeClient()
        self.apps.connect(client)
        self.apps.handle(client, {"op": "hello", "app": "limits"})
        wide = {"id": "w", "col": 0, "row": 0, "label": "x" * lib.MAX_LABEL, "text": "x" * lib.MAX_TEXT}
        lines = {"id": "l", "col": 1, "row": 0, "lines": ["x" * lib.MAX_LINE_CHARS] * lib.MAX_LINES}
        self.apps.handle(client, {"op": "page", "page": "p", "widgets": [wide, lines]})
        self.assertIsNone(client.last("error"))
        for widget in (
            dict(wide, label="x" * (lib.MAX_LABEL + 1)),
            dict(lines, lines=["x" * (lib.MAX_LINE_CHARS + 1)]),
        ):
            self.apps.handle(client, {"op": "page", "page": "p", "widgets": [widget]})
            self.assertEqual(client.last("error")["request"], "page")


class DaemonTests(unittest.TestCase):
    def test_the_client_against_the_daemon(self):
        bar = DaemonBar(self)
        tb = Touchbar(screenpad_app.socket_path(bar.environ), "counter", ["counter"])
        self.addCleanup(tb.close)
        welcome = tb.handshake()
        self.assertEqual((welcome["placed"], welcome["protocol"]), (["counter"], common.APP_PROTOCOL))
        tb.tile("counter", label="1", tone="accept")
        # No bar is drawn here, so the daemon answers the question at once.
        tb.ask("q1", "Reset?", [("no", "Keep"), ("yes", "Reset", "reject")], timeout=30)
        self.assertEqual(self.receive(tb), [{"op": "answer", "id": "q1", "option": None, "reason": "unavailable"}])
        with bar.lock:
            self.assertEqual(bar.apps.provided()["counter"], {"label": "1", "tone": "accept"})
            bar.apps.event("counter", "up", inside=True)
        self.assertEqual(self.receive(tb), [{"op": "event", "tile": "counter", "type": "up", "inside": True}])

    def receive(self, tb):
        got: list[dict] = []
        for _ in range(200):
            if got:
                return got
            if select.select([tb], [], [], 0.01)[0]:
                got += tb.receive()
        raise AssertionError("nothing received")

    def test_a_newer_app_fails_clearly_on_the_daemon(self):
        bar = DaemonBar(self)
        tb = Touchbar(screenpad_app.socket_path(bar.environ), "counter", protocol=common.APP_PROTOCOL + 1)
        self.addCleanup(tb.close)
        with self.assertRaises(ProtocolMismatch) as e:
            tb.handshake()
        self.assertEqual(e.exception.supported, (common.MIN_APP_PROTOCOL, common.APP_PROTOCOL))
        self.assertIn("update screenpad-linux", str(e.exception))

    def test_an_app_from_before_the_version_still_works(self):
        bar = DaemonBar(self)
        tb = Touchbar(screenpad_app.socket_path(bar.environ), "counter", ["counter"], protocol=1)
        self.addCleanup(tb.close)
        self.assertEqual(tb.handshake()["protocol"], 1)


if __name__ == "__main__":
    unittest.main()
