"""The agent app's sources: the Herdr client and firstmate's reader, run
against stand-in scripts (never a real Herdr or firstmate home)."""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from agents_support import (
    agent_item,
    bearings_model,
    fake_bearings,
    fake_herdr,
    read_calls,
    write_json,
)

from screenpad_agents.firstmate import Firstmate, FirstmateError, Task, parse
from screenpad_agents.herdr import Agent, Herdr, HerdrError, parse_agent


class HerdrTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.herdr = Herdr([fake_herdr(self.dir)], "lab")

    def agents(self, items):
        write_json(os.path.join(self.dir, "agents.json"), items)

    def calls(self):
        return read_calls(os.path.join(self.dir, "calls.jsonl"))

    def test_every_call_names_the_session(self):
        self.agents([agent_item("w1:p1", "blocked"), agent_item("w2:p3", "working", "codex", "/w/web")])
        self.assertEqual(
            self.herdr.agents(),
            [Agent("w1:p1", "claude", "blocked", "/work/api"), Agent("w2:p3", "codex", "working", "/w/web")],
        )
        self.herdr.focus("w1:p1")
        self.assertEqual(
            self.calls(), [["agent", "list", "--session", "lab"], ["agent", "focus", "w1:p1", "--session", "lab"]]
        )

    def test_a_wrapper_command_goes_first(self):
        herdr = Herdr(["/usr/bin/env", os.path.join(self.dir, "herdr")], "lab")
        self.assertEqual(herdr.agents(), [])

    def test_states_and_odd_entries(self):
        self.assertEqual(parse_agent(agent_item("w1:p1", "done")).state, "done")
        self.assertEqual(parse_agent(agent_item("w1:p1", "thinking")).state, "unknown")
        self.assertEqual(parse_agent({"pane_id": "w1:p1"}).kind, "agent")
        for item in ({}, {"pane_id": ""}, {"pane_id": 3}, "w1:p1"):
            self.assertIsNone(parse_agent(item))

    def test_errors(self):
        self.agents([])
        with self.assertRaisesRegex(HerdrError, "herdr agent focus: agent target w9:p9 not found"):
            self.herdr.focus("w9:p9")
        self.agents("down")
        with self.assertRaisesRegex(HerdrError, "herdr agent list: Error: Os"):
            self.herdr.agents()
        with self.assertRaisesRegex(HerdrError, "nonexistent-herdr: No such file or directory"):
            Herdr(["nonexistent-herdr"], "lab").agents()
        with self.assertRaisesRegex(HerdrError, "Permission denied"):
            Herdr([self.dir], "lab").agents()

    def test_a_herdr_that_hangs_or_says_nothing(self):
        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 5)

        with self.assertRaisesRegex(HerdrError, "no answer in 5 s"):
            Herdr(["herdr"], "lab", run=hang).agents()
        nothing = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "{}", ""))
        with self.assertRaisesRegex(HerdrError, "herdr agent list: no result"):
            Herdr(["herdr"], "lab", run=nothing).agents()
        no_list = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '{"result": {}}', ""))
        with self.assertRaisesRegex(HerdrError, "no agents"):
            Herdr(["herdr"], "lab", run=no_list).agents()


class FirstmateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.code = os.path.join(self.dir, "fm")
        self.script = fake_bearings(self.code)
        self.home = os.path.join(self.dir, "home")
        self.reader = Firstmate(self.home, self.script)

    def model(self, value):
        write_json(os.path.join(self.code, "bin", "model.json"), value)

    def test_runs_the_reader_on_the_configured_home_only(self):
        self.model(bearings_model(decisions=2, captain=1))
        with mock.patch.dict(os.environ, {"FM_HOME": "/home/me/firstmate", "FM_BACKEND": "herdr"}):
            bearings = self.reader.read()
        self.assertEqual((bearings.calls, bearings.more), (3, False))
        calls = read_calls(os.path.join(self.code, "bin", "calls.jsonl"))
        self.assertEqual(
            calls, [{"FM_HOME": self.home, "fm": ["FM_HOME"], "args": ["--json", "--fields", "endpoints"]}]
        )

    def test_counts_and_more(self):
        self.assertEqual(parse(bearings_model()).calls, 0)
        self.assertEqual(parse(bearings_model(decisions=20, omitted=["decisions_open showing 20 of 31"])).more, True)
        self.assertEqual(parse(bearings_model(captain=1, captain_omitted=4)).more, True)
        self.assertEqual(parse(bearings_model(omitted=["task paths"])).more, False)

    def test_tasks_by_endpoint(self):
        b = parse(
            bearings_model(
                endpoints=[("fix-api", "lab:w1:p1"), ("docs", "default:w2:p4")],
                flights=[("fix-api", "Fix the API", "running tests")],
            )
        )
        self.assertEqual(
            b.tasks, {"lab:w1:p1": Task("fix-api", "Fix the API", "running tests"), "default:w2:p4": Task("docs")}
        )
        model = bearings_model()
        model["endpoints"] = [
            {"id": "t", "backend": "tmux", "target": "fm:1"},
            {"id": "t", "backend": "herdr", "target": "nocolon"},
            {"id": 3, "backend": "herdr", "target": "lab:w1:p1"},
            "junk",
        ]
        self.assertEqual(parse(model).tasks, {})

    def test_errors(self):
        for model, error in (
            ("fail", "reader failed: fm-bearings-snapshot: broken"),
            ({"schema": "other"}, "not fm-bearings.v1"),
        ):
            self.model(model)
            with self.subTest(model=model), self.assertRaisesRegex(FirstmateError, error):
                self.reader.read()
        with self.assertRaisesRegex(FirstmateError, "No such file"):
            Firstmate(self.home, os.path.join(self.dir, "missing.sh")).read()

        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 30)

        with self.assertRaisesRegex(FirstmateError, "no answer in 30 s"):
            Firstmate(self.home, self.script, run=hang).read()
        garbage = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "TOON", ""))
        with self.assertRaisesRegex(FirstmateError, "no JSON"):
            Firstmate(self.home, self.script, run=garbage).read()


if __name__ == "__main__":
    unittest.main()
