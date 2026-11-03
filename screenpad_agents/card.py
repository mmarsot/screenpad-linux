"""An agent's card: its screen, and the buttons that act on it.

Tapping an agent on the Agents page opens its card in place of the grid: the
last lines of its own screen (screen.py), then Back, Focus and Interrupt, and
while Herdr reports it blocked on a permission dialog its harness's keymap
knows (keymap.py), the answers: Allow once (a tap; a long press is Always,
when the dialog offers it) and Deny.

The answer buttons' ids carry the screen's token (its state_change_seq and
the digest of the lines drawn), so a screen that changed is a new pair of
widgets: the touch bar arms them again for 0.3 s and cancels a finger already
on them, and a tap names the very screen it was made on. Pure: the loop
(app.py) does the reading and the sending.
"""

from screenpad_app import clip
from screenpad_app.common import MAX_LABEL, MAX_TEXT

from .keymap import ALWAYS, DENY, ONCE, harness, offered

COLUMNS, ROWS = 5, 4
SCREEN, BACK, FOCUS, INTERRUPT, STATE = "card.screen", "card.back", "card.focus", "card.interrupt", "card.state"
ANSWER = "answer."


class Card:
    """The card open for pane: the last Screen read (None until the first
    read), a note on what last happened, whether an answer is on its way
    (its buttons are hidden meanwhile, so a second tap cannot follow it), and
    whether an interrupt is (a second one would be a second Escape, which
    Claude Code takes for something else)."""

    __slots__ = ("interrupting", "note", "pane", "pending", "screen")

    def __init__(self, pane):
        self.pane, self.screen, self.note, self.pending, self.interrupting = pane, None, None, False, False

    def answers(self, agent):
        """{answer: keys} the card offers now for agent (Herdr's latest)."""
        s = self.screen
        if self.pending or s is None or not s.blocked or agent is None or agent.state != "blocked":
            return {}
        if s.agent.seq is None or s.agent.kind != agent.kind:
            return {}
        return offered(s.agent.kind, s.lines)

    def answer_for(self, agent, widget, touch):
        """(answer, keys) a touch on widget means, None if nothing: the
        widget must name the screen drawn now and an answer it offers."""
        if self.screen is None or not isinstance(widget, str) or not widget.startswith(ANSWER):
            return None
        kind, _, token = widget[len(ANSWER) :].partition(".")
        if token != self.screen.token():
            return None
        answers = self.answers(agent)
        if kind == ONCE:
            answer = ALWAYS if touch == "long" and ALWAYS in answers else ONCE if touch == "tap" else None
        else:
            answer = DENY if kind == DENY and touch == "tap" else None
        return (answer, answers[answer]) if answer in answers else None

    def widgets(self, name, agent, interrupt):
        """The card's widgets. interrupt is (True, None) when Interrupt can
        act, or (False, why not)."""
        lines = self._lines()
        out = [{"id": SCREEN, "col": 0, "row": 0, "cols": 4, "rows": 3, "touch": "none", "lines": lines}]
        out.append({"id": BACK, "col": 4, "row": 0, "label": "Back", "icon": "go-previous-symbolic"})
        out.append({"id": FOCUS, "col": 4, "row": 1, "label": "Focus", "text": "its pane"})
        can, why = interrupt
        stop = {"id": INTERRUPT, "col": 4, "row": 2, "label": "Interrupt", "icon": "media-playback-stop-symbolic"}
        if can:
            stop["tone"] = "warning"
        else:
            stop.update({"text": clip(why, MAX_TEXT), "touch": "none"})
            del stop["icon"]
        out.append(stop)
        answers = self.answers(agent)
        state = {"id": STATE, "row": 3, "label": clip(name, MAX_LABEL), "touch": "none"}
        state["text"] = clip(self._state_text(agent, answers), MAX_TEXT)
        if answers and self.screen is not None:
            token = self.screen.token()
            once = {"id": ANSWER + ONCE + "." + token, "col": 0, "row": 3, "cols": 2, "label": "Allow once"}
            once["tone"] = "accept"
            if ALWAYS in answers:
                once.update({"text": "hold: Always", "touch": "long"})
            out.append(once)
            if DENY in answers:
                deny = {"id": ANSWER + DENY + "." + token, "col": 2, "row": 3, "cols": 2, "label": "Deny"}
                out.append(dict(deny, tone="reject"))
            state["col"] = 4
        else:
            state.update({"col": 0, "cols": 5})
        out.append(state)
        return out

    def _lines(self):
        s = self.screen
        if s is None:
            return ["Reading the pane…"]
        if s.error:
            return [clip(s.error, 120)]
        return s.lines or ["(the pane shows nothing)"]

    def _state_text(self, agent, answers):
        h = harness(agent.kind) if agent else None
        parts = [h.name if h else (agent.kind if agent else "agent"), agent.state if agent else "gone"]
        if self.pending:
            parts.append("sending…")
        elif agent is not None and agent.state == "blocked" and not answers and self.screen is not None:
            parts.append("answer it in its pane" if h is None or self.screen.error else "no dialog this card knows")
        if self.note:
            parts.append(self.note)
        return " · ".join(parts)
