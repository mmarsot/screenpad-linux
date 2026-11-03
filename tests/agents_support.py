"""What the agent app's tests share: the checkout on sys.path, a fake Herdr
CLI and a fake firstmate reader (scripts in a temporary directory, so the
real clients run them), fakes of both clients for the loop, and configs.

Nothing here talks to a real Herdr or a real firstmate home.
"""

import json
import os
import stat
import sys
import textwrap
import threading

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

from screenpad_agents.config import Config
from screenpad_agents.firstmate import Bearings, FirstmateError, Task
from screenpad_agents.herdr import Agent, HerdrError

SESSION = "lab"


def agent_item(pane, state="idle", kind="claude", cwd="/work/api"):
    """One entry of Herdr's agent list, as Herdr 0.7 prints it."""
    return {"agent": kind, "agent_status": state, "cwd": cwd, "pane_id": pane, "revision": 1, "focused": False}


def bearings_model(decisions=0, captain=0, endpoints=(), flights=(), omitted=(), captain_omitted=0):
    """An fm-bearings.v1 model as fm-bearings-snapshot.sh --json prints it."""
    return {
        "schema": "fm-bearings.v1",
        "home": "fmhome",
        "contributions": {
            "captain": [{"id": "c%d" % i, "owner": "(main)"} for i in range(captain)],
            "captain_omitted": captain_omitted,
        },
        "in_flight": [{"id": i, "kind": "ship", "state": "working", "name": n, "doing": d} for i, n, d in flights],
        "decisions_open": [{"id": "d%d" % i, "verb": "captain-hold"} for i in range(decisions)],
        "endpoints": [{"id": i, "backend": "herdr", "target": t, "exists": True} for i, t in endpoints],
        "omitted": [{"surface": s, "reveal": "--all-decisions"} for s in omitted],
    }


def write_script(path, body):
    with open(path, "w") as f:
        f.write("#!/usr/bin/python3 -I\n" + textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def fake_herdr(directory):
    """A herdr stand-in: prints agents.json for agent list, records every
    call in calls.jsonl, and refuses to focus a pane it does not list."""
    write_json(os.path.join(directory, "agents.json"), [])
    return write_script(
        os.path.join(directory, "herdr"),
        """
        import json, os, sys
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "calls.jsonl"), "a") as f:
            f.write(json.dumps(sys.argv[1:]) + "\\n")
        args = sys.argv[1:]
        with open(os.path.join(here, "agents.json")) as f:
            agents = json.load(f)
        if agents == "down":
            print("Error: Os { code: 2, kind: NotFound }", file=sys.stderr)
            sys.exit(1)
        if args[:2] == ["agent", "list"]:
            print(json.dumps({"id": "cli:agent:list", "result": {"agents": agents, "type": "agent_list"}}))
        elif args[:2] == ["agent", "focus"]:
            found = [a for a in agents if a["pane_id"] == args[2]]
            if not found:
                error = {"code": "agent_not_found", "message": "agent target %s not found" % args[2]}
                print(json.dumps({"error": error}))
                sys.exit(1)
            print(json.dumps({"id": "cli:agent:focus", "result": {"agent": found[0], "type": "agent_info"}}))
        else:
            sys.exit(2)
        """,
    )


def fake_bearings(code):
    """A fake firstmate checkout: bin/fm-bearings-snapshot.sh prints the
    model in model.json next to it, and records FM_HOME and its arguments."""
    os.makedirs(os.path.join(code, "bin"), exist_ok=True)
    write_json(os.path.join(code, "bin", "model.json"), bearings_model())
    return write_script(
        os.path.join(code, "bin", "fm-bearings-snapshot.sh"),
        """
        import json, os, sys
        here = os.path.dirname(os.path.abspath(__file__))
        fm = sorted(k for k in os.environ if k.startswith("FM_"))
        with open(os.path.join(here, "calls.jsonl"), "a") as f:
            f.write(json.dumps({"FM_HOME": os.environ.get("FM_HOME"), "fm": fm, "args": sys.argv[1:]}) + "\\n")
        with open(os.path.join(here, "model.json")) as f:
            model = json.load(f)
        if model == "fail":
            print("fm-bearings-snapshot: broken", file=sys.stderr)
            sys.exit(3)
        print(json.dumps(model, indent=2))
        """,
    )


def write_json(path, value):
    with open(path, "w") as f:
        json.dump(value, f)


def read_calls(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def config(firstmate=True, herdr_poll=0.05):
    """A Config as load() makes it, with Herdr read every herdr_poll s."""
    c = Config("agents.toml", ["herdr"], SESSION, herdr_poll)
    if firstmate:
        c.home, c.bearings = "/fm", "/fm/bin/fm-bearings-snapshot.sh"
    return c


class FakeHerdr:
    """The Herdr client for the loop: agents set by the test, focus recorded."""

    def __init__(self, agents=()):
        self.list = [Agent(*a) if isinstance(a, tuple) else a for a in agents]
        self.error = None
        self.focus_error = None
        self.focused = []
        self.reads = 0
        self.lock = threading.Lock()

    def agents(self):
        with self.lock:
            self.reads += 1
            if self.error:
                raise self.error if isinstance(self.error, Exception) else HerdrError(self.error)
            return list(self.list)

    def focus(self, pane):
        with self.lock:
            self.focused.append(pane)
            if self.focus_error:
                raise HerdrError(self.focus_error)


class FakeFirstmate:
    def __init__(self, calls=0, tasks=None):
        self.bearings = Bearings(calls, False, tasks or {})
        self.error = None
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.error:
            raise self.error if isinstance(self.error, Exception) else FirstmateError(self.error)
        return self.bearings


def task(id, doing=None):
    return Task(id, id, doing)
