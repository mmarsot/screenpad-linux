"""The fleet as the touch bar shows it: the agents page and the two tiles.

Pure: it turns what Herdr and firstmate last said into the "page" and "set"
messages of docs/app-api.md, and says what a touch on them means. The page
is a grid of COLUMNS columns: the Captain's Call widget first (when firstmate
is configured), then one widget per agent, in Herdr's order so a widget stays
where it is while its agent runs. Herdr's state colors it: working green,
blocked amber, the rest grey. An agent's widget is named after its firstmate
task, or else its directory. Tapping one opens its card (card.py) in place of
the grid, until Back, an answer sent, or the agent leaving Herdr's list.
"""

import hashlib
import os

from screenpad_app import clip
from screenpad_app.common import ID as WIDGET_ID
from screenpad_app.common import MAX_BADGE, MAX_LABEL, MAX_ROWS, MAX_TEXT, MAX_TILE_TEXT

from . import card as cards
from .common import CALLS_TILE, COLUMNS, FLEET_TILE, MIN_ROWS, PAGE, STATE_TONES
from .keymap import interrupt_keys

CALLS_WIDGET = "calls"


def widget_id(pane):
    """The widget id for an agent's pane: the pane id when it fits the
    touch bar's id rules, else a digest of it."""
    wid = "agent." + pane.replace(":", ".")
    if WIDGET_ID.fullmatch(wid):
        return wid
    return "agent.h" + hashlib.sha256(pane.encode()).hexdigest()[:20]


class Fleet:
    """What the app last knew: Herdr's agents (or why it could not say), and
    firstmate's bearings when firstmate is configured."""

    def __init__(self, session, firstmate):
        self.session, self.firstmate = session, firstmate
        self.agents = None
        self.herdr_error = None
        self.bearings = None
        self.firstmate_error = None
        self.card = None
        self._panes = {}

    def tiles(self):
        return [FLEET_TILE, CALLS_TILE] if self.firstmate else [FLEET_TILE]

    def set_agents(self, agents, error=None):
        self.agents, self.herdr_error = (None, error) if error else (list(agents), None)
        if self.card is not None and self.agents is not None and self.agent_of(self.card.pane) is None:
            self.card = None

    def set_bearings(self, bearings, error=None):
        if error:
            self.firstmate_error = error
        else:
            self.bearings, self.firstmate_error = bearings, None

    def task(self, agent):
        if self.bearings is None:
            return None
        return self.bearings.tasks.get("%s:%s" % (self.session, agent.pane))

    def agent_of(self, pane):
        return next((a for a in self.agents or [] if a.pane == pane), None)

    def name(self, agent):
        task = self.task(agent)
        return task.id if task else os.path.basename((agent.cwd or "").rstrip("/")) or agent.kind

    def counts(self):
        counts: dict[str, int] = {}
        for a in self.agents or []:
            counts[a.state] = counts.get(a.state, 0) + 1
        return counts

    # --- what touches mean ---------------------------------------------------

    def pane_of(self, widget):
        """The pane an agent widget of the page last built stands for."""
        return self._panes.get(widget)

    def first_blocked(self):
        return next((a.pane for a in self.agents or [] if a.state == "blocked"), None)

    # --- the card --------------------------------------------------------------

    def open_card(self, pane):
        self.card = cards.Card(pane) if self.agent_of(pane) is not None else None
        return self.card

    def shown_card(self):
        """The card the page shows: the open one while its agent is in
        Herdr's list (the grid shows while Herdr cannot say)."""
        if self.card is not None and self.agent_of(self.card.pane) is not None:
            return self.card
        return None

    def close_card(self):
        self.card = None

    def set_screen(self, screen):
        """A fresh read of the open card's pane; ignored for any other pane."""
        if self.card is not None and screen.pane == self.card.pane:
            self.card.screen = screen

    def interrupt_route(self, agent):
        """How Interrupt reaches agent: ("firstmate", task id) for a firstmate
        task, ("keys", keys) through Herdr for another agent whose harness
        the keymap knows, or (None, why it cannot)."""
        task = self.task(agent)
        if task is not None:
            return "firstmate", task.id
        if self.firstmate and self.bearings is None:
            return None, "firstmate unreadable" if self.firstmate_error else "reading firstmate…"
        keys = interrupt_keys(agent.kind)
        return ("keys", keys) if keys else (None, "no interrupt known for %s" % agent.kind)

    # --- the messages --------------------------------------------------------

    def messages(self):
        """The page and the tiles, as messages for the socket."""
        return [self.page()] + [self.fleet_tile()] + ([self.calls_tile()] if self.firstmate else [])

    def summary(self):
        if self.herdr_error:
            return "Herdr unavailable"
        if self.agents is None:
            return "reading Herdr…"
        counts = self.counts()
        parts = ["%d agent%s" % (len(self.agents), "" if len(self.agents) == 1 else "s")]
        parts += ["%d %s" % (counts[s], s) for s in ("blocked", "working") if counts.get(s)]
        return " · ".join(parts)

    def tone(self):
        counts = self.counts()
        return "warning" if counts.get("blocked") else "accept" if counts.get("working") else "neutral"

    def page(self):
        blocked = self.counts().get("blocked", 0)
        card = self.shown_card()
        if card is not None:
            agent = self.agent_of(card.pane)
            self._panes = {}
            route, why = self.interrupt_route(agent)
            widgets = card.widgets(self.name(agent), agent, (True, None) if route else (False, why))
            columns, rows = cards.COLUMNS, cards.ROWS
        else:
            widgets, rows = self._widgets()
            columns = COLUMNS
        msg = {"op": "page", "page": PAGE, "title": "Agents", "text": clip(self.summary(), MAX_TILE_TEXT)}
        msg.update({"tone": self.tone(), "columns": columns, "rows": rows, "widgets": widgets})
        if blocked:
            msg["badge"] = clip(str(blocked), MAX_BADGE)
        return msg

    def fleet_tile(self):
        msg = {"op": "set", "tile": FLEET_TILE, "label": "Agents", "tone": self.tone()}
        if self.agents is not None:
            msg["label"] = clip("%d agent%s" % (len(self.agents), "" if len(self.agents) == 1 else "s"), MAX_LABEL)
        counts = self.counts()
        # A tile is narrow: only the most urgent state, the page has the rest.
        text = next(("%d %s" % (counts[s], s) for s in ("blocked", "working", "idle") if counts.get(s)), "")
        if self.herdr_error:
            text = "Herdr unavailable"
        if text:
            msg["text"] = clip(text, MAX_TILE_TEXT)
        if counts.get("blocked"):
            msg["badge"] = clip(str(counts["blocked"]), MAX_BADGE)
        return msg

    def calls_tile(self):
        badge, text, tone = self._calls()
        msg = {"op": "set", "tile": CALLS_TILE, "label": "Calls", "text": clip(text, MAX_TILE_TEXT), "tone": tone}
        if badge:
            msg["badge"] = badge
        return msg

    def _calls(self):
        """The Captain's Call count as a badge, a line of text and a tone."""
        if self.bearings is None:
            return None, "firstmate unreadable" if self.firstmate_error else "reading firstmate…", "neutral"
        n = self.bearings.calls
        badge = ("%d+" % n if self.bearings.more else str(n)) if n or self.bearings.more else None
        text = "%s waiting" % (badge or "none")
        if self.firstmate_error:
            text += " (stale)"
        return badge and clip(badge, MAX_BADGE), text, "warning" if badge else "neutral"

    def _widgets(self):
        cells = []
        if self.firstmate:
            badge, text, tone = self._calls()
            calls = {"id": CALLS_WIDGET, "label": "Captain's Call", "text": text, "tone": tone}
            if badge:
                calls["badge"] = badge
            cells.append(calls)
        self._panes = {}
        if self.herdr_error or self.agents is None or not self.agents:
            cells.append(self._note())
        else:
            for a in self.agents:
                cells.append(self._agent(a))
        rows = max(MIN_ROWS, min(MAX_ROWS, -(-len(cells) // COLUMNS)))
        room = COLUMNS * rows
        if len(cells) > room:
            hidden = len(cells) - room + 1
            for c in cells[room - 1 :]:
                self._panes.pop(c["id"], None)
            cells = cells[: room - 1] + [{"id": "more", "label": "+%d more" % hidden, "touch": "none"}]
        for i, c in enumerate(cells):
            c["col"], c["row"] = i % COLUMNS, i // COLUMNS
        return cells, rows

    def _note(self):
        if self.herdr_error:
            return {
                "id": "herdr",
                "label": "Herdr unavailable",
                "text": clip(self.herdr_error, MAX_TEXT),
                "touch": "none",
            }
        if self.agents is None:
            return {"id": "herdr", "label": "Reading Herdr…", "touch": "none"}
        text = "Herdr reports no agent in session %s" % self.session
        return {"id": "herdr", "label": "No agents", "text": clip(text, MAX_TEXT), "touch": "none"}

    def _agent(self, agent):
        wid = widget_id(agent.pane)
        self._panes[wid] = agent.pane
        task = self.task(agent)
        line = [agent.kind, agent.state]
        if task and task.doing:
            line.append(task.doing)
        widget = {"id": wid, "label": clip(self.name(agent), MAX_LABEL)}
        widget["text"] = clip(" · ".join(line), MAX_TEXT)
        widget["tone"] = STATE_TONES.get(agent.state, "neutral")
        return widget
