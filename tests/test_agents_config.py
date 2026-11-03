"""The agent app's config (agents.toml), the opt-in it is, and the command."""

import os
import subprocess
import tempfile
import unittest

from agents_support import REPO, fake_bearings, fake_herdr, read_calls, write_json

from screenpad_agents.cli import clients
from screenpad_agents.common import ConfigError
from screenpad_agents.config import load, parse


class ConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.code = os.path.join(self.dir, "fm")
        fake_bearings(self.code)
        # A checkout with the reader and without the control plane.
        self.nocontrol = os.path.join(self.dir, "old")
        fake_bearings(self.nocontrol)
        os.remove(os.path.join(self.nocontrol, "bin", "fm-control.sh"))
        self.home = os.path.join(self.dir, "home")
        os.mkdir(self.home)

    def write(self, text):
        path = os.path.join(self.dir, "agents.toml")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_herdr_only(self):
        c = load(self.write('[herdr]\nsession = "default"\n'))
        self.assertEqual((c.herdr, c.session, c.herdr_poll), (["herdr"], "default", 2.0))
        self.assertIsNone(c.home)

    def test_every_setting(self):
        c = load(
            self.write(
                '[herdr]\ncommand = ["wrap", "herdr"]\nsession = "lab-1"\npoll = 5\n'
                '[firstmate]\nhome = "%s"\ncode = "%s"\npoll = 120\n' % (self.home, self.code)
            )
        )
        self.assertEqual((c.herdr, c.session, c.herdr_poll), (["wrap", "herdr"], "lab-1", 5.0))
        self.assertEqual(c.home, self.home)
        self.assertEqual(c.bearings, os.path.join(self.code, "bin", "fm-bearings-snapshot.sh"))
        self.assertEqual(c.control, os.path.join(self.code, "bin", "fm-control.sh"))
        self.assertEqual(c.firstmate_poll, 120.0)

    def test_the_clients_interrupt_through_the_configured_control_plane(self):
        c = load(
            self.write('[herdr]\nsession = "lab"\n[firstmate]\nhome = "%s"\ncode = "%s"\n' % (self.home, self.code))
        )
        _, firstmate = clients(c)
        self.assertEqual(firstmate.interrupt("fix-api"), "delivered verified=agent-alive cancel=unconfirmed")
        calls = read_calls(os.path.join(self.code, "bin", "control.jsonl"))
        self.assertEqual(calls, [{"FM_HOME": self.home, "fm": ["FM_HOME"], "args": ["fix-api", "interrupt"]}])
        self.assertIsNone(clients(load(self.write('[herdr]\nsession = "lab"\n')))[1])

    def test_a_missing_config_is_no_opt_in(self):
        with self.assertRaisesRegex(ConfigError, "runs only once you write it"):
            load(os.path.join(self.dir, "nope.toml"))

    def test_errors(self):
        fm = '[firstmate]\nhome = "%s"\ncode = "%s"\n' % (self.home, self.code)
        cases = [
            ("", "herdr\\] is required"),
            ("[herdr]\n", "session must be"),
            ('[herdr]\nsession = "a b"\n', "session must be"),
            ('[herdr]\nsession = "x"\nwho = 1\n', "unknown key in \\[herdr\\] 'who'"),
            ('[herdr]\nsession = "x"\n[other]\n', "unknown table 'other'"),
            ("herdr = 1\n", "herdr must be a table"),
            ('[herdr]\nsession = "x"\ncommand = []\n', "command must be"),
            ('[herdr]\nsession = "x"\ncommand = ["herdr", 2]\n', "command must be"),
            ('[herdr]\nsession = "x"\npoll = 0.5\n', "poll must be 1 to 60"),
            ('[herdr]\nsession = "x"\npoll = true\n', "poll must be"),
            ('[herdr]\nsession = "x"\n' + fm + "poll = 30\n", "poll must be 60 to 3600"),
            (
                '[herdr]\nsession = "x"\n[firstmate]\nhome = "rel"\ncode = "%s"\n' % self.code,
                "home must be an absolute",
            ),
            ('[herdr]\nsession = "x"\n[firstmate]\nhome = "%s"\n' % self.home, "code must be an absolute"),
            (
                '[herdr]\nsession = "x"\n[firstmate]\nhome = "/nonexistent"\ncode = "%s"\n' % self.code,
                "not a directory",
            ),
            (
                '[herdr]\nsession = "x"\n[firstmate]\nhome = "%s"\ncode = "%s"\n' % (self.home, self.home),
                "no executable",
            ),
            ('[herdr]\nsession = "x"\n' + fm + "extra = 1\n", "unknown key in \\[firstmate\\] 'extra'"),
            ('[herdr]\nsession = "x"\n' + fm.replace(self.code, self.nocontrol), "no executable bin/fm-control.sh"),
            ("[herdr\n", "agents.toml"),
        ]
        for text, error in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ConfigError, error):
                load(self.write(text))

    def test_no_default_points_at_a_firstmate_home(self):
        # Without [firstmate] nothing of firstmate is read; its paths have no default.
        c = parse("x", {"herdr": {"session": "default"}})
        self.assertEqual((c.home, c.bearings, c.control), (None, None, None))


class CommandTests(unittest.TestCase):
    """The installed-style launcher, run in isolated mode as the unit runs it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.env = dict(os.environ, XDG_CONFIG_HOME=os.path.join(self.dir, "config"), XDG_RUNTIME_DIR=self.dir)

    def agents(self, *args):
        return subprocess.run(
            [os.path.join(REPO, "agents.py"), *args], capture_output=True, text=True, env=self.env, timeout=30
        )

    def test_refuses_to_run_without_a_config(self):
        for command in ("run", "show"):
            with self.subTest(command=command):
                done = self.agents(command)
                self.assertEqual(done.returncode, 2)
                self.assertIn("agents.toml does not exist; the agent app runs only once you write it", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "screenpad")))

    def test_show(self):
        herdr = fake_herdr(self.dir)
        write_json(
            os.path.join(self.dir, "agents.json"),
            [
                {"agent": "claude", "agent_status": "blocked", "pane_id": "w1:p1", "cwd": "/w/api"},
                {"agent": "codex", "agent_status": "working", "pane_id": "w2:p1", "cwd": "/w/web"},
            ],
        )
        code = os.path.join(self.dir, "fm")
        fake_bearings(code)
        model = os.path.join(code, "bin", "model.json")
        write_json(
            model,
            {
                "schema": "fm-bearings.v1",
                "decisions_open": [{"id": "d1"}],
                "contributions": {"captain": [{"id": "c1"}]},
                "endpoints": [{"id": "fix-api", "backend": "herdr", "target": "lab:w1:p1"}],
            },
        )
        path = os.path.join(self.dir, "agents.toml")
        with open(path, "w") as f:
            f.write(
                '[herdr]\ncommand = "%s"\nsession = "lab"\n[firstmate]\nhome = "%s"\ncode = "%s"\n'
                % (herdr, self.dir, code)
            )
        done = self.agents("show", "--config", path)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout.splitlines(),
            [
                "Herdr session lab: 2 agents · 1 blocked · 1 working",
                "  w1:p1    claude   blocked    fix-api",
                "  w2:p1    codex    working    -",
                "Captain's Call: 2 waiting",
            ],
        )
        done = self.agents("show", "--json", "--config", path)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('"page": "agents"', done.stdout)
        write_json(os.path.join(self.dir, "agents.json"), "down")
        done = self.agents("show", "--config", path)
        self.assertEqual(done.returncode, 1)
        self.assertIn("Herdr unavailable", done.stdout)


if __name__ == "__main__":
    unittest.main()
