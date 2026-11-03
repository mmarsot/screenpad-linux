"""A blocked agent's screen on the card, and the guard on every answer.

The card draws the last lines of the agent's own screen (Herdr's pane read)
and remembers what it drew: the agent's state, its state_change_seq and a
digest of the lines. An answer sends keys only if, at the moment of sending,
all three still hold (guarded_send):

- Herdr still reports the agent blocked (and of the same kind);
- its state_change_seq is the one recorded when the card was drawn;
- the lines of a fresh pane read, taken immediately before send-keys, have
  the digest of the lines drawn.

On any mismatch nothing is sent, and the fresh screen is handed back so the
card is drawn again with it (new widget ids, so the touch bar arms its
buttons again for 0.3 s and a fresh tap is needed). Herdr's pane revision is
not used: it does not change when a dialog does (docs/agents.md).
"""

import hashlib

from .common import MAX_LINE_CHARS, MAX_LINES
from .herdr import HerdrError

# Box drawing a TUI frames its dialog with: stripped from line ends, and a
# line of nothing else is dropped.
BOX = "│┃║|╭╮╰╯┌┐└┘╔╗╚╝├┤┬┴┼─━═╌╍┄┅ \t"


def screen_lines(text):
    """The lines the card draws for a screen's text: each without its frame,
    blank and frame-only lines dropped, the last MAX_LINES of them, each at
    most MAX_LINE_CHARS characters."""
    out = []
    for raw in (text or "").splitlines():
        line = "".join(c for c in raw if c.isprintable()).strip(BOX)
        if line:
            out.append(line if len(line) <= MAX_LINE_CHARS else line[: MAX_LINE_CHARS - 1] + "…")
    return out[-MAX_LINES:]


def digest(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


class Screen:
    """What one read of a pane gave: the agent (Herdr's Agent) and the lines
    the card draws, or error when Herdr could not say."""

    __slots__ = ("agent", "digest", "error", "lines", "pane")

    def __init__(self, pane, agent=None, lines=(), error=None):
        self.pane, self.agent, self.lines, self.error = pane, agent, list(lines), error
        self.digest = digest(self.lines)

    @property
    def blocked(self):
        return self.agent is not None and self.agent.state == "blocked"

    def token(self):
        """A short name for this exact screen: its seq and lines. It goes into
        the answer buttons' ids, so any change is a new widget."""
        seq = self.agent.seq if self.agent is not None else None
        return hashlib.sha256(("%r\n%s" % (seq, self.digest)).encode()).hexdigest()[:12]

    def __eq__(self, other):
        return isinstance(other, Screen) and (self.pane, self.agent, self.lines, self.error) == (
            other.pane,
            other.agent,
            other.lines,
            other.error,
        )

    def __hash__(self):
        return hash((self.pane, self.digest))


def read_screen(herdr, pane):
    """The Screen of pane now: the agent first (agent get), then its text
    (pane read). A HerdrError is a Screen with the error."""
    try:
        agent = herdr.agent(pane)
        lines = screen_lines(herdr.read(pane))
    except HerdrError as e:
        return Screen(pane, error=str(e))
    return Screen(pane, agent, lines)


def mismatch(drawn, now):
    """Why the screen now is not the one drawn, None when it is."""
    if not drawn.blocked:
        return "the card showed no dialog"
    if now.error:
        return now.error
    if not now.blocked:
        return "the agent is %s now" % (now.agent.state if now.agent else "gone")
    if drawn.agent is None or now.agent.kind != drawn.agent.kind:
        return "another agent is in the pane"
    if drawn.agent.seq is None or now.agent.seq != drawn.agent.seq:
        return "the agent changed state since the card was drawn"
    if now.digest != drawn.digest:
        return "the dialog changed since the card was drawn"
    return None


def guarded_send(herdr, drawn, keys):
    """Send keys to drawn's pane if it still shows drawn (see the module).
    (None, None) when they were sent; (why, fresh Screen) when not."""
    now = read_screen(herdr, drawn.pane)
    why = mismatch(drawn, now)
    if why is not None:
        return why, now
    herdr.send_keys(drawn.pane, keys)
    return None, None
