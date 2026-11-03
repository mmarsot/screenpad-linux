"""Tests for apps: the protocol, the socket, the daemon's scene and loop, contacts to apps.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import unittest

from touchbar_support import TILE_CONFIG, ConfigHome, Conn, FakeClient, Split, codes, config, lift, pump, touch

from screenpad_touchbar.apps import Apps, AppServer
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.cli import main
from screenpad_touchbar.codes import (
    ABS_MT_TRACKING_ID,
    EV_ABS,
)
from screenpad_touchbar.common import (
    ASK_ARM,
    MAX_APPS,
    MAX_LINE,
    RENDERER_NAME,
    SCENE_VERSION,
    SOCKET_NAME,
    TouchbarError,
    runtime_file,
    state_path,
)
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter


class AppsTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.apps = Apps(clock=lambda: self.now)
        self.apps.set_placed(["agents"])
        self.gone = []
        self.apps.on_tile_gone = self.gone.append
        self.a, self.b = FakeClient(), FakeClient()
        self.apps.connect(self.a)
        self.apps.connect(self.b)

    def test_hello_and_tiles(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents", "ci"]})
        self.assertEqual(
            self.a.last("welcome"),
            {"op": "welcome", "app": "fleet", "placed": ["agents"], "unplaced": ["ci"], "taken": [], "protocol": 1},
        )
        self.apps.handle(self.b, {"op": "hello", "app": "other", "tiles": ["agents"]})
        self.assertEqual(self.b.last("welcome")["taken"], ["agents"])
        self.assertTrue(self.apps.online("agents"))
        self.assertEqual(self.apps.names(), ["fleet", "other"])
        self.assertTrue(self.apps.take_changed())
        self.assertFalse(self.apps.take_changed())
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        self.assertIn("already", self.a.last("error")["message"])

    def test_set(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.take_changed()
        self.apps.handle(
            self.a,
            {
                "op": "set",
                "tile": "agents",
                "label": "1 blocked",
                "tone": "warning",
                "badge": "2",
                "icon": "dialog-warning-symbolic",
            },
        )
        self.assertEqual(
            self.apps.provided(),
            {"agents": {"tone": "warning", "label": "1 blocked", "badge": "2", "icon": "dialog-warning-symbolic"}},
        )
        self.assertTrue(self.apps.take_changed())
        bad = [
            {"tile": "ci"},
            {"tile": "agents", "tone": "pink"},
            {"tile": "agents", "label": "x" * 33},
            {"tile": "agents", "label": "a\nb"},
            {"tile": "agents", "icon": "../x"},
            {"tile": "agents", "colour": "red"},
        ]
        for msg in bad:
            self.apps.handle(self.a, dict(msg, op="set"))
            self.assertEqual(self.a.last("error")["request"], "set", msg)
        self.apps.handle(self.b, {"op": "hello", "app": "other"})
        self.apps.handle(self.b, {"op": "set", "tile": "agents", "label": "mine"})
        self.assertIn("not yours", self.b.last("error")["message"])

    def test_hello_first_and_bad_messages(self):
        self.apps.handle(self.a, {"op": "set", "tile": "agents"})
        self.assertIn("hello first", self.a.last("error")["message"])
        self.apps.handle(self.a, ["not", "an", "object"])
        self.apps.handle(self.a, {"op": "dance"})
        self.assertEqual(len([m for m in self.a.sent if m["op"] == "error"]), 3)
        self.apps.handle(self.a, {"op": "ping"})
        self.assertEqual(self.a.sent[-1], {"op": "pong"})
        for msg in ({"app": "Fleet"}, {"app": "x", "tiles": "agents"}, {"app": "x", "tiles": ["A"]}):
            self.apps.handle(self.b, dict(msg, op="hello"))
            self.assertEqual(self.b.last("error")["request"], "hello", msg)

    def test_events_go_to_the_owner(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.event("agents", "down")
        self.apps.event("agents", "up", True)
        self.apps.event("nobody", "down")
        self.assertEqual(
            [m for m in self.a.sent if m["op"] == "event"],
            [
                {"op": "event", "tile": "agents", "type": "down"},
                {"op": "event", "tile": "agents", "type": "up", "inside": True},
            ],
        )
        self.assertEqual(self.b.sent, [])

    def test_ask_validation(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        ok = {"op": "ask", "id": "q1", "title": "Run it?", "options": [{"id": "y", "label": "Yes"}]}
        bad = [
            dict(ok, id="no spaces"),
            dict(ok, title=""),
            dict(ok, title="x" * 81),
            dict(ok, options=[]),
            dict(ok, options=[{"id": "y", "label": "Y"}] * 2),
            dict(ok, options=[{"id": str(k), "label": "L"} for k in range(4)]),
            dict(ok, options=[{"id": "y", "label": "Y", "tone": "pink"}]),
            dict(ok, timeout=0),
            dict(ok, timeout=True),
            dict(ok, timeout=4000),
            dict(ok, colour=1),
        ]
        for msg in bad:
            self.apps.handle(self.a, msg)
            self.assertEqual(self.a.last("error")["request"], "ask", msg)
        self.assertEqual(self.a.last("error")["id"], "q1")

    def test_ask_answers(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet"})
        ask = {
            "op": "ask",
            "id": "q1",
            "title": "Run it?",
            "timeout": 5,
            "options": [{"id": "y", "label": "Yes"}, {"id": "n", "label": "No"}],
        }
        # The bar cannot show questions: answered at once.
        self.apps.handle(self.a, ask)
        self.assertEqual(self.a.last("answer"), {"op": "answer", "id": "q1", "option": None, "reason": "unavailable"})
        self.apps.asks.set_available(True)
        self.apps.handle(self.a, ask)
        self.apps.handle(self.a, ask)
        self.assertIn("already waiting", self.a.last("error")["message"])
        shown = self.apps.asks.shown()
        self.assertEqual((shown.id, shown.shown_at), ("q1", 10.0))
        self.apps.asks.choose(shown, 1)
        self.assertEqual(self.a.last("answer")["option"], "n")
        self.apps.handle(self.a, ask)
        self.apps.handle(self.a, {"op": "cancel", "id": "q1"})
        self.assertEqual(self.a.last("answer")["reason"], "cancelled")
        self.apps.handle(self.a, {"op": "cancel", "id": "q1"})
        self.assertEqual(self.a.last("error")["request"], "cancel")
        self.apps.handle(self.a, ask)
        self.assertEqual(self.apps.asks.deadline(), 15.0)
        self.now = 15.0
        self.apps.asks.tick()
        self.assertEqual(self.a.last("answer")["reason"], "timeout")
        self.apps.handle(self.a, ask)
        self.apps.asks.set_available(False)
        self.assertEqual(self.a.last("answer")["reason"], "unavailable")
        self.assertIsNone(self.apps.asks.shown())

    def test_disconnect(self):
        self.apps.handle(self.a, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.apps.asks.set_available(True)
        self.apps.handle(self.a, {"op": "ask", "id": "q", "title": "T", "options": [{"id": "y", "label": "Y"}]})
        self.apps.disconnect(self.a)
        self.assertEqual((self.apps.online("agents"), self.apps.asks.shown(), self.gone), (False, None, ["agents"]))
        self.assertIsNone(self.a.last("answer"))
        # The tile is free for another app now.
        self.apps.handle(self.b, {"op": "hello", "app": "other", "tiles": ["agents"]})
        self.assertEqual(self.b.last("welcome")["placed"], ["agents"])

    def test_too_many_apps(self):
        apps = Apps()
        clients = [FakeClient() for _ in range(MAX_APPS + 1)]
        self.assertEqual([apps.connect(c) for c in clients], [True] * MAX_APPS + [False])
        self.assertEqual(clients[-1].last("error")["message"], "too many apps (%d)" % MAX_APPS)


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = ConfigHome(self.tmp.name)
        self.home.__enter__()
        self.addCleanup(self.home.__exit__)
        self.path = runtime_file(SOCKET_NAME)
        self.apps = Apps()
        self.apps.set_placed(["agents"])
        self.server = AppServer(self.path, self.apps)
        self.addCleanup(self.server.close)

    def conn(self):
        app = Conn(self.path)
        self.addCleanup(app.s.close)
        return app

    def test_hello_set_and_events(self):
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        app = self.conn()
        app.send({"op": "hello", "app": "fleet", "tiles": ["agents"]})
        self.assertEqual(app.recv(self.server)["placed"], ["agents"])
        app.send({"op": "set", "tile": "agents", "label": "2 working"})
        app.send({"op": "ping"})
        self.assertEqual(app.recv(self.server), {"op": "pong"})
        self.assertEqual(self.apps.provided()["agents"]["label"], "2 working")
        self.apps.event("agents", "down")
        self.assertEqual(app.recv(self.server), {"op": "event", "tile": "agents", "type": "down"})
        app.s.close()
        pump(self.server, 3)
        self.assertFalse(self.apps.online("agents"))

    def test_bad_json_and_a_too_long_line(self):
        app = self.conn()
        app.s.sendall(b"{nope\n")
        self.assertEqual(app.recv(self.server)["message"], "not a JSON object")
        app.s.sendall(b"x" * (MAX_LINE + 10))
        self.assertIsNone(app.recv(self.server))
        self.assertEqual(self.server.clients, {})

    def test_stale_and_live_sockets(self):
        # A live daemon is not replaced.
        with self.assertRaises(TouchbarError):
            AppServer(self.path, Apps())
        # A socket file a dead daemon left is.
        self.server.sock.close()
        self.assertTrue(os.path.exists(self.path))
        other = AppServer(self.path, Apps())
        other.close()
        self.assertFalse(os.path.exists(self.path))

    def test_ask_command(self):
        self.apps.asks.set_available(True)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                pump(self.server)
                ask = self.apps.asks.shown()
                if ask is not None:
                    self.apps.asks.choose(ask, 1)

        thread = threading.Thread(target=serve)
        thread.start()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "ask",
                        "Run npm test?",
                        "--text",
                        "in api",
                        "--option",
                        "allow=Allow once",
                        "--option",
                        "deny=Deny",
                        "--tone",
                        "deny=reject",
                        "--timeout",
                        "5",
                    ]
                )
        finally:
            stop.set()
            thread.join()
        self.assertEqual((code, out.getvalue()), (0, "deny\n"))

    def test_ask_command_without_an_answer(self):
        err = io.StringIO()
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                pump(self.server)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with contextlib.redirect_stderr(err):
                # Unavailable (the bar is not shown), then a bad request.
                self.assertEqual(main(["ask", "T", "--option", "a=A"]), 1)
                self.assertEqual(main(["ask", "T", "--option", "a=A", "--timeout", "0"]), 2)
                self.assertEqual(main(["ask", "T", "--option", "noequals"]), 2)
        finally:
            stop.set()
            thread.join()
        self.assertIn("no answer: unavailable", err.getvalue())
        self.server.close()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["ask", "T", "--option", "a=A"]), 1)


class DaemonSceneTests(unittest.TestCase):
    def test_publish_and_renderer(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = Daemon(config(TILE_CONFIG), None)
            self.assertFalse(daemon.renderer_ready())
            os.makedirs(os.path.dirname(state_path()))
            renderer = runtime_file(RENDERER_NAME)
            for i, (said, ready) in enumerate(
                (
                    ('{"protocol": 2, "drawn": true}', True),
                    ('{"protocol": 2, "drawn": false}', False),
                    ('{"protocol": 1, "drawn": true}', False),
                    ('{"protocol": true, "drawn": true}', False),
                    ('{"protocol": 2}', False),
                    ("not json at all", False),
                    ("[2]", False),
                    ('{"protocol": 3, "drawn": true}', True),
                )
            ):
                with open(renderer, "w") as f:
                    f.write(said)
                os.utime(renderer, ns=(0, 1000 + i))  # a new mtime each time
                self.assertEqual(daemon.renderer_ready(), ready, said)
            os.unlink(renderer)
            self.assertFalse(daemon.renderer_ready())
            daemon.publish()
            with open(state_path()) as f:
                state = json.load(f)
            self.assertEqual(state["version"], SCENE_VERSION)
            self.assertEqual([x["id"] for x in state["tiles"]][:2], ["item-0", "item-1"])
            # The version 1 fields, for an extension loaded before tiles.
            self.assertEqual(len(state["spots"]), 7)
            self.assertEqual((state["pressed"], state["removing"]), ([], []))
            # A question shows over the spots, the grip stays.
            app = FakeClient()
            daemon.apps.connect(app)
            daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
            # As if the pad were grabbed and the bar on screen.
            daemon.apps.asks.can_show = lambda: True
            daemon.apps.handle(app, {"op": "ask", "id": "q", "title": "T", "options": [{"id": "y", "label": "Y"}]})
            daemon.publish()
            with open(state_path()) as f:
                state = json.load(f)
            self.assertEqual([x["id"] for x in state["tiles"]], ["ask-text", "ask-dismiss", "ask-option-0", "grip"])
            self.assertEqual(state["apps"], ["fleet"])


class ContactsToAppsTests(unittest.TestCase):
    """Device contacts through the Splitter and the Bar to an app, end to end."""

    def test_tile_and_question(self):
        now = [50.0]
        apps = Apps(clock=lambda: now[0])
        apps.set_placed(["agents"])
        app = FakeClient()
        apps.connect(app)
        apps.handle(app, {"op": "hello", "app": "fleet", "tiles": ["agents"]})
        bar = Bar(
            config(TILE_CONFIG),
            KeyEmitter(lambda ev: None),
            lambda a: None,
            clock=lambda: now[0],
            tiles=apps,
            asks=apps.asks,
        )
        sp = Split()
        # The "agents" spot is x 272..544 panel px, 544..1088 device units.
        for groups in ([touch(0, 1, 800, 100)], [lift(0)]):
            pad, events, _ = sp.frame(*groups)
            self.assertEqual(pad, [])
            bar.handle(events)
        self.assertEqual([m["type"] for m in app.sent if m["op"] == "event"], ["down", "up"])
        # A question; a tap on its second answer (x 1128..1400 px), after
        # it has been up for ASK_ARM.
        apps.asks.set_available(True)
        apps.handle(
            app,
            {
                "op": "ask",
                "id": "q",
                "title": "Run it?",
                "options": [{"id": "no", "label": "No"}, {"id": "yes", "label": "Yes"}],
            },
        )
        now[0] += ASK_ARM
        for groups in ([touch(1, 2, 2700, 120)], [lift(1)]):
            pad, events, _ = sp.frame(*groups)
            self.assertEqual(pad, [])
            bar.handle(events)
        self.assertEqual(app.last("answer"), {"op": "answer", "id": "q", "option": "yes", "reason": "chosen"})
        # The pad below the bar is untouched by all this.
        pad, events, _ = sp.frame(touch(2, 3, 2000, 1500))
        self.assertEqual((codes(pad, EV_ABS, ABS_MT_TRACKING_ID), events), ([3], []))


class DaemonLoopTests(unittest.TestCase):
    """The daemon's loop serving the socket, with a stand-in for the grabbed pad."""

    class Pad:
        """Grabbed, and never has events (a pipe nobody writes)."""

        def __init__(self):
            self.fd, self.w = os.pipe()
            self.grabbed = True

        def close(self):
            os.close(self.fd)
            os.close(self.w)

    def test_questions_follow_the_renderer(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as d, ConfigHome(d):
            flag = os.path.join(d, "active")
            open(flag, "w").close()
            daemon = Daemon(config(TILE_CONFIG), None, flag=flag)
            daemon.server = AppServer(runtime_file(SOCKET_NAME), daemon.apps)
            pad = self.Pad()
            daemon.source = pad  # type: ignore[assignment]  # stands in for the parts loop() uses
            thread = threading.Thread(target=daemon.loop)
            thread.start()
            try:
                app = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                app.connect(runtime_file(SOCKET_NAME))
                app.settimeout(5)
                lines = app.makefile("rw")

                def ask(ask_id):
                    lines.write(
                        json.dumps({"op": "ask", "id": ask_id, "title": "T", "options": [{"id": "y", "label": "Y"}]})
                        + "\n"
                    )
                    lines.flush()

                lines.write(json.dumps({"op": "hello", "app": "fleet", "tiles": ["agents"]}) + "\n")
                lines.flush()
                self.assertEqual(json.loads(lines.readline())["placed"], ["agents"])
                # No renderer.json: the bar cannot show questions.
                ask("q1")
                self.assertEqual(json.loads(lines.readline())["reason"], "unavailable")
                with open(runtime_file(RENDERER_NAME), "w") as f:
                    f.write('{"protocol": 2, "drawn": true}')
                ask("q2")
                for _ in range(50):
                    with open(state_path()) as f:
                        state = json.load(f)
                    if state["tiles"][0]["id"] == "ask-text":
                        break
                    threading.Event().wait(0.05)
                self.assertEqual([x["id"] for x in state["tiles"]], ["ask-text", "ask-dismiss", "ask-option-0", "grip"])
                self.assertEqual(state["apps"], ["fleet"])
                # The bar leaves the screen: the waiting question is answered.
                os.unlink(runtime_file(RENDERER_NAME))
                self.assertEqual(
                    json.loads(lines.readline()), {"op": "answer", "id": "q2", "option": None, "reason": "unavailable"}
                )
                app.close()
            finally:
                daemon.stop = True
                thread.join()
                daemon.source = None
                daemon.server.close()
                pad.close()


if __name__ == "__main__":
    unittest.main()
