"""firstmate: its tasks' endpoints and how many Captain's Calls wait.

Read through firstmate's own read-only reader, never its files:

  FM_HOME=<home> <code>/bin/fm-bearings-snapshot.sh --json --fields endpoints

prints the fm-bearings.v1 model. The Captain's Calls waiting are
len(decisions_open) + len(contributions.captain); "endpoints" gives each
task's recorded endpoint, for Herdr "<session>:<pane id>", which joins a task
to the agent Herdr reports in that pane. "in_flight" gives the task's name
and what it is doing. The reader takes about 3 s; app.py runs it off the
loop, at most once a minute plus on demand.

Interrupting a task's agent goes through firstmate's control plane, which
knows the task's harness and checks what it did:

  FM_HOME=<home> <code>/bin/fm-control.sh <task id> interrupt

It is the only firstmate command that acts; nothing writes into firstmate.
"""

import json
import os
import re
import subprocess

from .common import BEARINGS_SCHEMA, CONTROL_TIMEOUT, FIRSTMATE_TIMEOUT

# A task id as fm-control.sh takes it: a bare id, never an option or an
# endpoint.
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class FirstmateError(Exception):
    pass


class Task:
    """A firstmate task whose endpoint is a Herdr pane."""

    __slots__ = ("doing", "id", "name")

    def __init__(self, id, name=None, doing=None):
        self.id, self.name, self.doing = id, name, doing

    def __eq__(self, other):
        return isinstance(other, Task) and (self.id, self.name, self.doing) == (other.id, other.name, other.doing)

    def __hash__(self):
        return hash((self.id, self.name, self.doing))

    def __repr__(self):
        return "Task(%r)" % self.id


class Bearings:
    """What one read gave: the Captain's Calls waiting (more when the reader
    left some out), and the tasks by endpoint target."""

    __slots__ = ("calls", "more", "tasks")

    def __init__(self, calls, more, tasks):
        self.calls, self.more, self.tasks = calls, more, tasks

    def __eq__(self, other):
        return isinstance(other, Bearings) and (self.calls, self.more, self.tasks) == (
            other.calls,
            other.more,
            other.tasks,
        )

    def __hash__(self):
        return hash((self.calls, self.more))


class Firstmate:
    def __init__(self, home, bearings, control, run=subprocess.run, timeout=FIRSTMATE_TIMEOUT):
        self.home, self.bearings, self.control, self._run, self.timeout = home, bearings, control, run, timeout

    def read(self):
        """The Bearings now; FirstmateError when the reader fails."""
        done = self._call([self.bearings, "--json", "--fields", "endpoints"], "firstmate's reader", self.timeout)
        try:
            model = json.loads(done.stdout)
        except ValueError:
            raise FirstmateError("firstmate's reader printed no JSON")
        return parse(model)

    def interrupt(self, task_id):
        """Interrupt task_id's agent through fm-control.sh; what it reported,
        or FirstmateError when it refused or failed."""
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            raise FirstmateError("not a firstmate task id: %r" % (task_id,))
        done = self._call([self.control, task_id, "interrupt"], "fm-control.sh interrupt", CONTROL_TIMEOUT)
        out = (done.stdout or "").strip().splitlines()
        return out[-1] if out else "interrupted"

    def _call(self, argv, what, timeout):
        """Run one of firstmate's commands with FM_HOME, and none of the other
        FM_* variables of this environment."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("FM_")}
        env["FM_HOME"] = self.home
        try:
            done = self._run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise FirstmateError("%s: %s" % (argv[0], e.strerror))
        except subprocess.TimeoutExpired:
            raise FirstmateError("%s gave no answer in %g s" % (what, timeout))
        if done.returncode != 0:
            why = (done.stderr or "").strip().splitlines()
            raise FirstmateError("%s failed: %s" % (what, why[-1] if why else "exit %d" % done.returncode))
        return done


def parse(model):
    """Bearings from an fm-bearings.v1 model; FirstmateError if it is not one."""
    if not isinstance(model, dict) or model.get("schema") != BEARINGS_SCHEMA:
        raise FirstmateError("firstmate's reader is not %s" % BEARINGS_SCHEMA)
    decisions = _list(model, "decisions_open")
    contributions = model.get("contributions")
    captain = _list(contributions, "captain") if isinstance(contributions, dict) else []
    more = any(
        isinstance(o, dict) and str(o.get("surface", "")).startswith("decisions_open") for o in _list(model, "omitted")
    )
    if isinstance(contributions, dict) and isinstance(contributions.get("captain_omitted"), int):
        more = more or contributions["captain_omitted"] > 0
    flights = {f["id"]: f for f in _list(model, "in_flight") if isinstance(f, dict) and isinstance(f.get("id"), str)}
    tasks = {}
    for e in _list(model, "endpoints"):
        if not isinstance(e, dict) or e.get("backend") != "herdr":
            continue
        task_id, target = e.get("id"), e.get("target")
        if not isinstance(task_id, str) or not isinstance(target, str) or ":" not in target:
            continue
        flight = flights.get(task_id, {})
        tasks[target] = Task(task_id, _text(flight.get("name")), _text(flight.get("doing")))
    return Bearings(len(decisions) + len(captain), more, tasks)


def _list(model, key):
    value = model.get(key) if isinstance(model, dict) else None
    return value if isinstance(value, list) else []


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None
