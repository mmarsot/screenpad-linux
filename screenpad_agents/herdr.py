"""Herdr: the agents it reports, and focusing one's pane.

Herdr is the authority for which agents run and what state each is in; this
app only reads that and asks Herdr to focus a pane. Every call names the
config's session explicitly (--session), never the ambient one. Herdr's CLI
prints one JSON object, {"result": ...} or {"error": {"code", "message"}}.

This version reads and focuses only: it sends no keys and reads no pane.
"""

import json
import subprocess

from .common import HERDR_TIMEOUT, STATES


class HerdrError(Exception):
    pass


class Agent:
    """One agent Herdr reports: its pane, what it is, and its state."""

    __slots__ = ("cwd", "kind", "pane", "state")

    def __init__(self, pane, kind, state, cwd=None):
        self.pane, self.kind, self.state, self.cwd = pane, kind, state, cwd

    def __eq__(self, other):
        return isinstance(other, Agent) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return "Agent(%r, %r, %r)" % (self.pane, self.kind, self.state)

    def _key(self):
        return (self.pane, self.kind, self.state, self.cwd)


class Herdr:
    """Herdr's CLI for one session: command is the program (and any wrapper
    words) before the subcommand."""

    def __init__(self, command, session, run=subprocess.run, timeout=HERDR_TIMEOUT):
        self.command, self.session, self._run, self.timeout = list(command), session, run, timeout

    def agents(self):
        """The agents Herdr reports, in its order; HerdrError if it cannot say."""
        result = self._call("agent", "list")
        agents = result.get("agents") if isinstance(result, dict) else None
        if not isinstance(agents, list):
            raise HerdrError("herdr agent list: no agents in its answer")
        return [a for a in (parse_agent(item) for item in agents) if a is not None]

    def focus(self, pane):
        """Focus the agent in pane; HerdrError if Herdr refuses."""
        self._call("agent", "focus", pane)

    def _call(self, *args):
        argv = self.command + list(args) + ["--session", self.session]
        try:
            done = self._run(argv, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise HerdrError("%s: %s" % (self.command[0], e.strerror))
        except subprocess.TimeoutExpired:
            raise HerdrError("herdr %s: no answer in %g s" % (" ".join(args[:2]), self.timeout))
        reply = _json(done.stdout)
        if isinstance(reply, dict) and isinstance(reply.get("error"), dict):
            raise HerdrError("herdr %s: %s" % (" ".join(args[:2]), reply["error"].get("message", "refused")))
        if done.returncode != 0:
            why = (done.stderr or done.stdout or "").strip().splitlines()
            raise HerdrError("herdr %s: %s" % (" ".join(args[:2]), why[-1] if why else "failed"))
        if not isinstance(reply, dict) or "result" not in reply:
            raise HerdrError("herdr %s: no result in its answer" % " ".join(args[:2]))
        return reply["result"]


def parse_agent(item):
    """An Agent from one entry of agent list, None if it has no pane."""
    if not isinstance(item, dict) or not isinstance(item.get("pane_id"), str) or not item["pane_id"]:
        return None
    state = item.get("agent_status")
    kind = item.get("agent") if isinstance(item.get("agent"), str) else None
    cwd = item.get("cwd") if isinstance(item.get("cwd"), str) else None
    return Agent(item["pane_id"], kind or "agent", state if state in STATES else "unknown", cwd)


def _json(text):
    try:
        return json.loads(text or "")
    except ValueError:
        return None
