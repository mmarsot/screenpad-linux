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

Tapping Captain's Call opens firstmate's decisions board, the Lavish board
of /bearings lavish, in the browser on the main screen, where the decisions
are answered; the panel answers none. Its file is what firstmate says,

  FM_HOME=<home> <code>/bin/fm-bearings-board.sh path

and it is open when Lavish's own session listing (lavish-axi, run with no
argument) shows that file "open", with its session's URL. That URL, and only
a loopback http one, is handed to the desktop's URL handler (gio open). With
no session open nothing is built or armed from here: firstmate composes the
board, so the panel says to ask it for /bearings lavish.
"""

import json
import os
import re
import subprocess

from .common import BEARINGS_SCHEMA, BOARD_TIMEOUT, CONTROL_TIMEOUT, FIRSTMATE_TIMEOUT, LAVISH_TIMEOUT, OPEN_TIMEOUT

# A task id as fm-control.sh takes it: a bare id, never an option or an
# endpoint.
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# A row of lavish-axi's session listing after its file and comma:
# <status>,"<url>",... (the listing is TOON: sessions[N]{file,status,url,...}).
SESSION_ROW = re.compile(r'(?P<status>[^,]*),"(?P<url>[^"]*)"')
# The board's URL as Lavish serves it: on this machine only.
BOARD_URL = re.compile(r"http://(127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}/[A-Za-z0-9._~/%-]*")


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
    def __init__(
        self,
        home,
        bearings,
        control,
        run=subprocess.run,
        timeout=FIRSTMATE_TIMEOUT,
        board=None,
        lavish=("lavish-axi",),
        opener=("gio", "open"),
    ):
        self.home, self.bearings, self.control, self._run, self.timeout = home, bearings, control, run, timeout
        self.board, self.lavish, self.opener = board, list(lavish), list(opener)

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

    def board_url(self):
        """The URL of firstmate's decisions board when Lavish has it open,
        None when it has not; FirstmateError when either cannot say."""
        if not self.board or not os.access(self.board, os.X_OK):
            raise FirstmateError("firstmate has no bin/fm-bearings-board.sh")
        done = self._call([self.board, "path"], "fm-bearings-board.sh path", BOARD_TIMEOUT)
        lines = (done.stdout or "").strip().splitlines()
        if len(lines) != 1 or not os.path.isabs(lines[0]):
            raise FirstmateError("fm-bearings-board.sh path printed no board path")
        listing = self._call(self.lavish, "lavish-axi", LAVISH_TIMEOUT, home=False)
        return listed_url(listing.stdout or "", os.path.realpath(lines[0]))

    def open_board(self):
        """Open the decisions board in the browser: its URL, or None when no
        board is open (then nothing is opened); FirstmateError on a failure."""
        url = self.board_url()
        if url is not None:
            self._call(self.opener + [url], "opening the board", OPEN_TIMEOUT, home=False, detach=True)
        return url

    def _call(self, argv, what, timeout, home=True, detach=False):
        """Run one of firstmate's commands with FM_HOME (or, home False,
        another program without it), and none of the other FM_* variables of
        this environment. Detached, the program gets no pipe, so a child it
        leaves running holds none: its output goes nowhere, its errors to the
        app's own (the journal), in a session of its own."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("FM_")}
        if home:
            env["FM_HOME"] = self.home
        if detach:
            streams = {"stdout": subprocess.DEVNULL, "start_new_session": True}
        else:
            streams = {"capture_output": True}
        try:
            done = self._run(argv, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL, **streams)
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


def listed_url(listing, path):
    """The URL lavish-axi's listing gives the session open on path, None
    when it lists none open; FirstmateError for a URL off this machine."""
    for line in listing.splitlines():
        line = line.strip()
        if not line.startswith(path + ","):
            continue
        row = SESSION_ROW.match(line[len(path) + 1 :])
        if row is None or row["status"] != "open":
            continue
        if not BOARD_URL.fullmatch(row["url"]):
            raise FirstmateError("Lavish lists the board at %r, not a local URL" % row["url"])
        return row["url"]
    return None


def _list(model, key):
    value = model.get(key) if isinstance(model, dict) else None
    return value if isinstance(value, list) else []


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None
