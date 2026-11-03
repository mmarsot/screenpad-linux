"""Herdr: the agents it reports, focusing one, reading its screen, sending keys.

Herdr is the authority for which agents run and what state each is in; this
app reads that, asks Herdr to focus a pane, reads a pane's screen for the
blocked card, and sends keys to an agent. Keys go only through the guard in
screen.py (an answer) or the interrupt in app.py, never from here on its own.
Every call names the config's session explicitly (--session), never the
ambient one. Herdr's CLI prints one JSON object, {"result": ...} or
{"error": {"code", "message"}}, except pane read, which prints the screen's
text.
"""

import json
import subprocess

from .common import HERDR_TIMEOUT, STATES


class HerdrError(Exception):
    pass


class Agent:
    """One agent Herdr reports: its pane, what it is, its state, and
    state_change_seq (seq), which Herdr moves on every state change."""

    __slots__ = ("cwd", "kind", "pane", "seq", "state")

    def __init__(self, pane, kind, state, cwd=None, seq=None):
        self.pane, self.kind, self.state, self.cwd, self.seq = pane, kind, state, cwd, seq

    def __eq__(self, other):
        return isinstance(other, Agent) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return "Agent(%r, %r, %r, seq=%r)" % (self.pane, self.kind, self.state, self.seq)

    def _key(self):
        return (self.pane, self.kind, self.state, self.cwd, self.seq)


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

    def agent(self, pane):
        """The agent in pane now (agent get); HerdrError if there is none."""
        result = self._call("agent", "get", pane)
        agent = parse_agent(result.get("agent") if isinstance(result, dict) else None)
        if agent is None or agent.pane != pane:
            raise HerdrError("herdr agent get: no agent in pane %s" % pane)
        return agent

    def focus(self, pane):
        """Focus the agent in pane; HerdrError if Herdr refuses."""
        self._call("agent", "focus", pane)

    def read(self, pane):
        """The text pane shows now (pane read, the visible screen)."""
        return self._call("pane", "read", pane, "--source", "visible", "--format", "text", text=True)

    def send_keys(self, pane, keys):
        """Send keys (Herdr key names, like "1" or "esc") to the agent in pane."""
        self._call("agent", "send-keys", pane, *keys)

    def _call(self, *args, text=False):
        argv = self.command + list(args) + ["--session", self.session]
        what = " ".join(args[:2])
        try:
            done = self._run(argv, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise HerdrError("%s: %s" % (self.command[0], e.strerror))
        except subprocess.TimeoutExpired:
            raise HerdrError("herdr %s: no answer in %g s" % (what, self.timeout))
        reply = _json(done.stdout)
        if isinstance(reply, dict) and isinstance(reply.get("error"), dict):
            raise HerdrError("herdr %s: %s" % (what, reply["error"].get("message", "refused")))
        if done.returncode != 0:
            why = (done.stderr or done.stdout or "").strip().splitlines()
            raise HerdrError("herdr %s: %s" % (what, why[-1] if why else "failed"))
        if text:
            return done.stdout or ""
        if not isinstance(reply, dict) or "result" not in reply:
            raise HerdrError("herdr %s: no result in its answer" % what)
        return reply["result"]


def parse_agent(item):
    """An Agent from one entry of agent list (or agent get), None if it has
    no pane."""
    if not isinstance(item, dict) or not isinstance(item.get("pane_id"), str) or not item["pane_id"]:
        return None
    state = item.get("agent_status")
    kind = item.get("agent") if isinstance(item.get("agent"), str) else None
    cwd = item.get("cwd") if isinstance(item.get("cwd"), str) else None
    seq = item.get("state_change_seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
    return Agent(item["pane_id"], kind or "agent", state if state in STATES else "unknown", cwd, seq)


def _json(text):
    try:
        return json.loads(text or "")
    except ValueError:
        return None
