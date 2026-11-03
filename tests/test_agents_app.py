"""The agent app's loop against the touch bar daemon's own socket server and
Apps: hello, the page and tiles, touches turned into focus and reads, and
the connection coming and going. Herdr and firstmate are fakes."""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agents_support import REPO, FakeFirstmate, FakeHerdr, config, fake_herdr, task, write_json
from touchbar_support import pump

from screenpad_agents.app import App
from screenpad_agents.herdr import Agent
from screenpad_touchbar.apps import Apps, AppServer


class Rig:
    """A daemon side (Apps on an AppServer) and the app, driven in turns."""

    def __init__(self, test, herdr, firstmate, cfg=None):
        tmp = tempfile.TemporaryDirectory(dir="/tmp")
        test.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "screenpad", "touchbar.sock")
        self.logged = []
        self.app = App(cfg or config(firstmate is not None), herdr, firstmate, self.path, log=self.logged.append)
        test.addCleanup(self.app.close)
        self.server = None
        self.apps = None
        self.handled = []
        self.serve()
        test.addCleanup(self.close_server)
        self.app.start()

    def serve(self):
        self.apps = Apps()
        self.apps.set_placed(["agents", "calls"])
        self.server = AppServer(self.path, self.apps)
        handle = self.apps.handle

        def counting(client, msg):
            self.handled.append(msg)
            handle(client, msg)

        self.apps.handle = counting  # type: ignore[method-assign]  # records what the app sends

    def close_server(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def turn(self, rounds=1):
        for _ in range(rounds):
            self.app.step(0.01)
            if self.server is not None:
                pump(self.server)

    def until(self, check, seconds=3.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.turn()
            if check():
                return
        raise AssertionError("not reached in %g s" % seconds)

    def client(self):
        return next(iter(self.server.clients.values()))

    def widgets(self):
        page = self.apps.pages.get("agents")
        return [w.id for w in page.widgets] if page else None


class AppTests(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr([("w1:p1", "claude", "blocked"), ("w2:p1", "codex", "working")])
        self.firstmate = FakeFirstmate(calls=3, tasks={"lab:w1:p1": task("fix-api")})

    def rig(self, **kwargs):
        rig = Rig(self, self.herdr, self.firstmate, **kwargs)
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1", "agent.w2.p1"])
        rig.until(lambda: rig.apps.provided().get("calls", {}).get("badge") == "3")
        return rig

    def test_hello_page_and_tiles(self):
        rig = self.rig()
        self.assertEqual(rig.handled[0], {"op": "hello", "app": "agents", "tiles": ["agents", "calls"]})
        self.assertEqual(rig.apps.provided()["agents"]["badge"], "1")
        self.assertEqual(rig.apps.pages["agents"].widgets[1].look["label"], "fix-api")
        self.assertEqual(rig.logged, [])

    def test_a_tap_on_an_agent_focuses_its_pane(self):
        rig = self.rig()
        rig.apps.press(rig.client(), "agents", "agent.w2.p1", "down")
        rig.apps.press(rig.client(), "agents", "agent.w2.p1", "tap")
        rig.until(lambda: self.herdr.focused == ["w2:p1"])
        for kind in ("cancel", "long"):
            rig.apps.press(rig.client(), "agents", "agent.w1.p1", kind)
        rig.apps.press(rig.client(), "agents", "unknown", "tap")
        rig.turn(20)
        self.assertEqual(self.herdr.focused, ["w2:p1"])

    def test_the_agents_tile_focuses_the_first_blocked_agent(self):
        rig = self.rig()
        rig.apps.event("agents", "up", inside=False)
        rig.turn(10)
        self.assertEqual(self.herdr.focused, [])
        rig.apps.event("agents", "up", inside=True)
        rig.until(lambda: self.herdr.focused == ["w1:p1"])

    def test_captain_call_taps_read_firstmate_again(self):
        rig = self.rig()
        reads = self.firstmate.reads
        rig.apps.event("calls", "up", inside=True)
        rig.until(lambda: self.firstmate.reads == reads + 1)
        self.firstmate.bearings.calls = 5
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: rig.apps.provided()["calls"].get("badge") == "5")

    def test_changes_are_sent_and_the_rest_is_not(self):
        rig = self.rig()
        rig.until(lambda: self.herdr.reads >= 3)
        rig.turn(5)
        sent = len(rig.handled)
        rig.until(lambda: self.herdr.reads >= 5)
        rig.turn(5)
        self.assertEqual(len(rig.handled), sent)
        with self.herdr.lock:
            self.herdr.list[1] = Agent("w2:p1", "codex", "blocked")
        rig.until(lambda: rig.apps.provided()["agents"].get("badge") == "2")
        self.assertEqual([m["op"] for m in rig.handled[sent:]], ["page", "set"])

    def test_herdr_failing_and_coming_back(self):
        rig = self.rig()
        self.herdr.error = "herdr agent list: Error: Os { code: 2 }"
        rig.until(lambda: rig.widgets() == ["calls", "herdr"])
        rig.until(lambda: self.herdr.reads >= 6)
        self.assertEqual(rig.logged, ["herdr agent list: Error: Os { code: 2 }"])
        self.herdr.error = None
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1", "agent.w2.p1"])
        self.assertEqual(rig.logged[-1], "herdr answers again")

    @mock.patch("screenpad_agents.app.RECONNECT", 0.05)
    def test_the_touch_bar_going_and_coming_back(self):
        rig = self.rig()
        rig.close_server()
        rig.until(lambda: rig.app.touchbar is None)
        self.assertIn("lost the touch bar", rig.logged[-1])
        rig.turn(5)
        rig.serve()
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1", "agent.w2.p1"])
        self.assertEqual(rig.handled[0]["op"], "hello")
        self.assertEqual(rig.apps.provided()["calls"]["badge"], "3")

    def test_without_firstmate(self):
        rig = Rig(self, self.herdr, None)
        rig.until(lambda: rig.widgets() == ["agent.w1.p1", "agent.w2.p1"])
        self.assertEqual(rig.handled[0]["tiles"], ["agents"])
        rig.apps.event("calls", "up", inside=True)
        rig.turn(5)
        self.assertEqual(self.firstmate.reads, 0)

    def test_welcome_and_errors_are_logged(self):
        rig = Rig(self, self.herdr, self.firstmate)
        rig.apps.set_placed(["agents"])
        rig.until(lambda: any("calls" in line for line in rig.logged))
        self.assertIn('tile "calls" has no spot; give it one with tile = "calls" in touchbar.toml', rig.logged)
        rig.app.handle({"op": "error", "request": "page", "message": "the apps page is full (8 pages)"})
        self.assertEqual(rig.logged[-1], "the touch bar refused page: the apps page is full (8 pages)")


class ProcessTests(unittest.TestCase):
    def test_imports_nothing_of_the_daemon(self):
        code = (
            "import sys; sys.path.insert(0, %r); import screenpad_agents.cli; "
            "print(sorted(m for m in sys.modules if m.startswith('screenpad_')))" % REPO
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=REPO, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("screenpad_touchbar", done.stdout)
        self.assertIn("screenpad_agents.app", done.stdout)

    def test_run_serves_the_bar_and_stops_on_sigterm(self):
        tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(tmp.cleanup)
        herdr = fake_herdr(tmp.name)
        write_json(
            os.path.join(tmp.name, "agents.json"), [{"agent": "claude", "agent_status": "idle", "pane_id": "w1:p1"}]
        )
        path = os.path.join(tmp.name, "agents.toml")
        with open(path, "w") as f:
            f.write('[herdr]\ncommand = "%s"\nsession = "lab"\n' % herdr)
        apps = Apps()
        apps.set_placed(["agents"])
        server = AppServer(os.path.join(tmp.name, "screenpad", "touchbar.sock"), apps)
        self.addCleanup(server.close)
        env = dict(os.environ, XDG_RUNTIME_DIR=tmp.name)
        proc = subprocess.Popen(
            [os.path.join(REPO, "agents.py"), "run", "--config", path], env=env, stderr=subprocess.PIPE, text=True
        )
        self.addCleanup(proc.kill)
        end = time.monotonic() + 10
        while time.monotonic() < end and not ("agents" in apps.pages and apps.pages["agents"].widgets[0].id != "herdr"):
            pump(server)
        self.assertEqual([w.id for w in apps.pages["agents"].widgets], ["agent.w1.p1"])
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, err), (0, ""))


if __name__ == "__main__":
    unittest.main()
