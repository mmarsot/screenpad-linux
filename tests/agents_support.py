"""What the agent app's tests share: the checkout on sys.path, a fake Herdr
CLI and a fake firstmate reader (scripts in a temporary directory, so the
real clients run them), fakes of both clients for the loop, configs, and the
Rig: the daemon's own Apps on its AppServer with the app, driven in turns.

Nothing here talks to a real Herdr or a real firstmate home.
"""

import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import time

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

from touchbar_support import pump

from screenpad_agents.app import App
from screenpad_agents.config import Config
from screenpad_agents.firstmate import Bearings, FirstmateError, Task
from screenpad_agents.herdr import Agent, HerdrError
from screenpad_touchbar.apps import Apps, AppServer

SESSION = "lab"


def agent_item(pane, state="idle", kind="claude", cwd="/work/api", seq=2):
    """One entry of Herdr's agent list (or agent get), as Herdr 0.7 prints it."""
    item = {"agent": kind, "agent_status": state, "cwd": cwd, "pane_id": pane, "revision": 1, "focused": False}
    item["state_change_seq"] = seq
    return item


# Claude Code's selection cursor, and its permission dialog as Herdr's pane
# read prints it.
CURSOR = chr(0x276F)
DIALOG = """\
some earlier output
╭──────────────────────────────────────────────╮
│ Bash command                                 │
│                                              │
│   {command}
│   Run the command                            │
│                                              │
│ Do you want to proceed?                      │
│ {cursor} 1. Yes                                     │
│   2. Yes, and don't ask again for this command│
│   3. No, and tell Claude what to do differently (esc)│
╰──────────────────────────────────────────────╯
"""


def dialog(command="npm test", always=True):
    text = DIALOG.format(command=command, cursor=CURSOR)
    return text if always else text.replace("│   2. Yes, and don't ask again for this command│\n", "")


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
    """A herdr stand-in: prints agents.json for agent list, a listed agent
    for agent get, screen-<pane>.txt for pane read; records every call in
    calls.jsonl, and refuses a pane it does not list."""
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
            sys.exit(0)
        if args[:2] == ["pane", "read"]:
            try:
                with open(os.path.join(here, "screen-%s.txt" % args[2].replace(":", "_"))) as f:
                    sys.stdout.write(f.read())
            except FileNotFoundError:
                print(json.dumps({"error": {"code": "pane_not_found", "message": "pane %s not found" % args[2]}}))
                sys.exit(1)
            sys.exit(0)
        found = [a for a in agents if a["pane_id"] == args[2]] if len(args) > 2 else []
        if not found:
            error = {"code": "agent_not_found", "message": "agent target %s not found" % args[2]}
            print(json.dumps({"error": error}))
            sys.exit(1)
        if args[:2] in (["agent", "focus"], ["agent", "get"]):
            print(json.dumps({"id": "cli:" + args[1], "result": {"agent": found[0], "type": "agent_info"}}))
        elif args[:2] == ["agent", "send-keys"]:
            print(json.dumps({"id": "cli:agent:send_keys", "result": {"type": "ok"}}))
        else:
            sys.exit(2)
        """,
    )


def fake_bearings(code):
    """A fake firstmate checkout: bin/fm-bearings-snapshot.sh prints the
    model in model.json next to it, and records FM_HOME and its arguments;
    bin/fm-control.sh is fake_control's."""
    fake_control(code)
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


def fake_control(code):
    """firstmate's bin/fm-control.sh in a fake checkout: records FM_HOME and
    its arguments in control.jsonl, and fails when control.fail exists."""
    os.makedirs(os.path.join(code, "bin"), exist_ok=True)
    return write_script(
        os.path.join(code, "bin", "fm-control.sh"),
        """
        import json, os, sys
        here = os.path.dirname(os.path.abspath(__file__))
        fm = sorted(k for k in os.environ if k.startswith("FM_"))
        with open(os.path.join(here, "control.jsonl"), "a") as f:
            f.write(json.dumps({"FM_HOME": os.environ.get("FM_HOME"), "fm": fm, "args": sys.argv[1:]}) + "\\n")
        if os.path.exists(os.path.join(here, "control.fail")):
            print("error: task %s has no state record" % sys.argv[1], file=sys.stderr)
            sys.exit(1)
        print("interrupt %s" % sys.argv[1])
        print("delivered verified=agent-alive cancel=unconfirmed")
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
        c.home, c.bearings, c.control = "/fm", "/fm/bin/fm-bearings-snapshot.sh", "/fm/bin/fm-control.sh"
    return c


class FakeHerdr:
    """The Herdr client for the loop: agents and screens set by the test,
    focus and keys recorded. after_read(pane), when set, runs after each
    pane read: the test changes the pane there, between draw and tap."""

    def __init__(self, agents=()):
        self.list = [Agent(*a) if isinstance(a, tuple) else a for a in agents]
        self.screens = {}
        self.error = None
        self.focus_error = None
        self.keys_error = None
        self.focused = []
        self.sent = []
        self.reads = 0
        self.screen_reads = 0
        self.after_read = None
        self.lock = threading.Lock()

    def agent(self, pane):
        with self.lock:
            found = [a for a in self.list if a.pane == pane]
            if not found:
                raise HerdrError("herdr agent get: agent target %s not found" % pane)
            return found[0]

    def read(self, pane):
        with self.lock:
            self.screen_reads += 1
            text = self.screens.get(pane, "")
        if self.after_read is not None:
            self.after_read(pane)
        return text

    def send_keys(self, pane, keys):
        with self.lock:
            if self.keys_error:
                raise HerdrError(self.keys_error)
            self.sent.append((pane, tuple(keys)))

    def set(self, index, agent):
        with self.lock:
            self.list[index] = agent

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
        self.interrupted = []
        self.interrupt_error = None

    def interrupt(self, task_id):
        self.interrupted.append(task_id)
        if self.interrupt_error:
            raise FirstmateError(self.interrupt_error)
        return "delivered verified=agent-alive cancel=unconfirmed"

    def read(self):
        self.reads += 1
        if self.error:
            raise self.error if isinstance(self.error, Exception) else FirstmateError(self.error)
        return self.bearings


def task(id, doing=None):
    return Task(id, id, doing)


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
