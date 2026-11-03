"""The app's loop: Herdr and firstmate called off the loop, the touch bar on it.

Worker threads do the slow calls and hand their results to the loop through
a queue (and a pipe that wakes its select):

- Herdr's: reads the agents every [herdr] poll seconds, and the screen of the
  agent whose card is open; does the jobs the loop gives it (focus a pane,
  send an answer through the guard of screen.py, send an interrupt's keys),
  each followed by a read at once;
- firstmate's: runs its reader every [firstmate] poll seconds (at least 60),
  and again on demand when the Captain's Call tile or widget is tapped; taps
  during a read add one read after it, not one each;
- firstmate's control: interrupts a task's agent through fm-control.sh, which
  takes seconds, so neither reader waits on it, and opens the decisions board
  (firstmate.py) when Captain's Call is tapped.

The loop keeps the connection to the touch bar through the client library's
Link (retrying every RECONNECT seconds while the daemon is not running or
refuses the app's protocol version), sends the page and tiles when what
they show changed (all of them again after a reconnect), and turns touches into work for the threads: a tap on an
agent's widget opens its card (fleet.py, card.py), whose buttons focus,
interrupt and answer; a tap on the agents tile focuses the first blocked
agent; a tap on Captain's Call reads firstmate again and opens firstmate's
decisions board in the browser on the main screen, or, when no board is
open, asks a one-answer question in the bar saying so (the panel answers no
Captain's Call).
"""

import os
import queue
import select
import sys
import threading
import time

from screenpad_app import Link, clip
from screenpad_app.common import MAX_TEXT

from . import card as cards
from .common import APP, BOARD_CARD_TIMEOUT, CALLS_TILE, FLEET_TILE, PAGE, RECONNECT
from .firstmate import FirstmateError
from .fleet import CALLS_WIDGET, Fleet
from .herdr import HerdrError
from .screen import guarded_send, read_screen


class App:
    def __init__(self, config, herdr, firstmate, socket_path, log=None, clock=time.monotonic):
        self.config, self.herdr, self.firstmate = config, herdr, firstmate
        self.socket_path, self.clock = socket_path, clock
        self.log = log or (lambda line: print("screenpad-agents: " + line, file=sys.stderr, flush=True))
        self.fleet = Fleet(config.session, firstmate is not None)
        self.stopping = threading.Event()
        self._results: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        self._control: queue.Queue = queue.Queue()
        self._read_firstmate = threading.Event()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self.link = Link(socket_path, APP, self.fleet.tiles(), retry=RECONNECT, log=self.log, clock=clock)
        self._threads = []
        self._told = set()
        self._dirty = False
        self._opening = False
        self._asked = None
        self._asks = 0

    # --- the worker threads ------------------------------------------------

    def start(self):
        workers = [("herdr", self._herdr_worker)]
        if self.firstmate is not None:
            workers += [("firstmate", self._firstmate_worker), ("control", self._control_worker)]
        self._threads = [threading.Thread(target=run, name=name, daemon=True) for name, run in workers]
        for t in self._threads:
            t.start()

    def _post(self, *item):
        self._results.put(item)
        self._wake()

    def _wake(self):
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass

    def _herdr_worker(self):
        due, watching = 0.0, None
        while not self.stopping.is_set():
            try:
                job = self._jobs.get(timeout=max(0.0, due - self.clock()))
            except queue.Empty:
                job = None
            if self.stopping.is_set():
                return
            if job is not None and job[0] == "watch":
                watching = job[1]
            elif job is not None:
                try:
                    self._herdr_job(*job)
                except Exception as e:
                    self._post("log", _why(e, HerdrError))
            try:
                self._post("agents", self.herdr.agents(), None)
            except Exception as e:
                self._post("agents", None, _why(e, HerdrError))
            if watching is not None:
                try:
                    self._post("screen", read_screen(self.herdr, watching))
                except Exception as e:
                    self._post("log", _why(e, HerdrError))
            due = self.clock() + self.config.herdr_poll

    def _herdr_job(self, what, *args):
        if what == "focus":
            self.herdr.focus(args[0])
        elif what == "answer":
            drawn, answer, keys = args
            try:
                why, fresh = guarded_send(self.herdr, drawn, keys)
            except Exception as e:
                why, fresh = "sending failed: %s" % _why(e, HerdrError), None
            self._post("answered", drawn.pane, answer, why, fresh)
        elif what == "keys":
            pane, kind, keys = args
            try:
                agent = self.herdr.agent(pane)
                if agent.kind != kind:
                    raise HerdrError("another agent is in pane %s now" % pane)
                self.herdr.send_keys(pane, keys)
            except Exception as e:
                self._post("interrupted", pane, None, _why(e, HerdrError))
            else:
                self._post("interrupted", pane, "sent %s" % " ".join(keys), None)

    def _firstmate_worker(self):
        while not self.stopping.is_set():
            self._read_firstmate.clear()
            try:
                self._post("bearings", self.firstmate.read(), None)
            except Exception as e:
                self._post("bearings", None, _why(e, FirstmateError))
            self._read_firstmate.wait(self.config.firstmate_poll)

    def _control_worker(self):
        while not self.stopping.is_set():
            job = self._control.get()
            if job is None or self.stopping.is_set():
                return
            if job[0] == "board":
                try:
                    self._post("board", self.firstmate.open_board(), None)
                except Exception as e:
                    self._post("board", None, _why(e, FirstmateError))
                continue
            _, pane, task_id = job
            try:
                self._post("interrupted", pane, self.firstmate.interrupt(task_id), None)
            except Exception as e:
                self._post("interrupted", pane, None, _why(e, FirstmateError))

    # --- the loop ------------------------------------------------------------

    def run(self):
        self.start()
        try:
            while not self.stopping.is_set():
                self.step(1.0)
        finally:
            self.close()

    def stop(self):
        self.stopping.set()
        self._wake()

    def step(self, timeout):
        """One turn of the loop: connect if due, wait up to timeout, then
        handle what the threads and the touch bar sent."""
        if self.link.connect():
            self._dirty = True  # a new connection has shown nothing yet
            self._asked = None  # nor asked anything
        fd = self.link.fileno()
        ready, _, _ = select.select([self._wake_r] + ([fd] if fd is not None else []), [], [], timeout)
        changed = self._drain()
        if fd is not None and fd in ready:
            for msg in self.link.receive():
                self.handle(msg)
        if (changed or self._dirty) and self.link.connected:
            self._dirty = False
            self.link.show(self.fleet.messages())

    def _drain(self):
        try:
            while os.read(self._wake_r, 4096):
                pass
        except BlockingIOError:
            pass
        changed = False
        while True:
            try:
                what, *rest = self._results.get_nowait()
            except queue.Empty:
                return changed
            changed = self._result(what, *rest) or changed

    def _result(self, what, *rest):
        """Take one result of a thread in; True if what the bar shows changed."""
        if what == "agents":
            value, error = rest
            had_card = self.fleet.card is not None
            self.fleet.set_agents(value, error)
            if had_card and self.fleet.card is None:
                self._jobs.put(("watch", None))
            self._tell("herdr", error)
        elif what == "bearings":
            value, error = rest
            self.fleet.set_bearings(value, error)
            self._tell("firstmate", error)
        elif what == "screen":
            self.fleet.set_screen(rest[0])
        elif what == "answered":
            self._answered(*rest)
        elif what == "interrupted":
            pane, done, error = rest
            self.log("interrupt %s: %s" % (pane, done or error))
            card = self.fleet.card
            if card is not None and card.pane == pane:
                card.interrupting = False
                card.note = "interrupt: %s" % (done or "failed: %s" % error)
        elif what == "board":
            self._board(*rest)
            return False
        elif what == "log":
            self.log(rest[0])
            return False
        return True

    def _board(self, url, error):
        self._opening = False
        if url is not None:
            self.log("opened the decisions board: %s" % url)
        elif error is not None:
            self.log("the decisions board did not open: %s" % error)
            self._ask("Decisions board not opened", error)
        else:
            self.log("no decisions board is open")
            self._ask("No decisions board open", "Ask firstmate for /bearings lavish; it builds the board.")

    def _ask(self, title, text):
        """Show title and text in the bar as a card with one answer, OK,
        in place of the last one if it is still up. The answer does nothing."""
        if not self.link.connected:
            return
        if self._asked is not None:
            self.link.send({"op": "cancel", "id": self._asked})
        self._asks += 1
        self._asked = "board.%d" % self._asks
        msg = {"op": "ask", "id": self._asked, "title": title, "text": clip(text, MAX_TEXT)}
        msg.update({"options": [{"id": "ok", "label": "OK"}], "timeout": BOARD_CARD_TIMEOUT})
        self.link.send(msg)

    def _answered(self, pane, answer, why, fresh):
        card = self.fleet.card
        if why is None:
            self.log("answered %s in pane %s" % (answer, pane))
            if card is not None and card.pane == pane:
                self.fleet.close_card()
                self._jobs.put(("watch", None))
            return
        self.log("nothing sent to pane %s: %s" % (pane, why))
        if card is not None and card.pane == pane:
            card.pending = False
            card.note = "nothing sent: %s" % why
            if fresh is not None:
                self.fleet.set_screen(fresh)

    def _tell(self, source, error):
        """Log a source's error once, and once again when it recovers."""
        if error and source not in self._told:
            self._told.add(source)
            self.log(error)
        elif not error and source in self._told:
            self._told.discard(source)
            self.log("%s answers again" % source)

    # --- touches -------------------------------------------------------------

    def handle(self, msg):
        op = msg.get("op")
        if op == "welcome":
            for tile in msg.get("unplaced") or []:
                self.log('tile "%s" has no spot; give it one with tile = "%s" in touchbar.toml' % (tile, tile))
            for tile in msg.get("taken") or []:
                self.log('another app already provides the tile "%s"' % tile)
        elif op == "error":
            self.log("the touch bar refused %s: %s" % (msg.get("request"), msg.get("message")))
        elif op == "answer" and msg.get("id") == self._asked:
            self._asked = None
            if msg.get("reason") == "unavailable":
                self.log("the touch bar could not show the board's card")
        elif op == "event" and msg.get("page") == PAGE and msg.get("type") in ("tap", "long"):
            self._touch(msg.get("widget"), msg["type"])
        elif op == "event" and msg.get("type") == "up" and msg.get("inside") is True:
            if msg.get("tile") == FLEET_TILE:
                self._focus_pane(self.fleet.first_blocked())
            elif msg.get("tile") == CALLS_TILE:
                self.captains_call()

    def _touch(self, widget, touch):
        card = self.fleet.shown_card()
        if card is not None:
            self._card_touch(card, widget, touch)
        elif touch != "tap":
            return
        elif widget == CALLS_WIDGET:
            self.captains_call()
        else:
            pane = self.fleet.pane_of(widget)
            if pane is not None and self.fleet.open_card(pane) is not None:
                self._jobs.put(("watch", pane))
                self._dirty = True

    def _card_touch(self, card, widget, touch):
        agent = self.fleet.agent_of(card.pane)
        chosen = card.answer_for(agent, widget, touch)
        if chosen is not None:
            answer, keys = chosen
            card.pending, card.note = True, None
            self._jobs.put(("answer", card.screen, answer, keys))
            self._dirty = True
        elif touch != "tap":
            return
        elif widget == cards.BACK:
            self.fleet.close_card()
            self._jobs.put(("watch", None))
            self._dirty = True
        elif widget == cards.FOCUS:
            self._focus_pane(card.pane)
        elif widget == cards.INTERRUPT:
            self._interrupt(card, agent)

    def _interrupt(self, card, agent):
        route, value = self.fleet.interrupt_route(agent)
        if route is None or card.interrupting:
            return
        card.interrupting, card.note = True, "interrupting…"
        self._dirty = True
        if route == "firstmate":
            self._control.put(("interrupt", card.pane, value))
        else:
            self._jobs.put(("keys", card.pane, agent.kind, value))

    def _focus_pane(self, pane):
        if pane is not None:
            self._jobs.put(("focus", pane))

    def refresh_calls(self):
        if self.firstmate is not None:
            self._read_firstmate.set()

    def captains_call(self):
        """A tap on Captain's Call: read firstmate again, and open its
        decisions board (one at a time: taps while it opens add nothing)."""
        if self.firstmate is None:
            return
        self.refresh_calls()
        if not self._opening:
            self._opening = True
            self._control.put(("board",))

    def close(self):
        self.stopping.set()
        self._read_firstmate.set()
        self._jobs.put(None)
        self._control.put(None)
        self.link.close()
        for t in self._threads:
            t.join(timeout=2.0)
        # A thread still running could write to a reused descriptor number,
        # so the pipe is closed only once they all ended.
        if self._wake_r >= 0 and not any(t.is_alive() for t in self._threads):
            for fd in (self._wake_r, self._wake_w):
                os.close(fd)
            self._wake_r = self._wake_w = -1


def _why(error, expected):
    """What to show and log for an error a worker caught: an expected one
    says why itself; any other is a bug, named by its type."""
    return str(error) if isinstance(error, expected) else "%s: %s" % (type(error).__name__, error)
