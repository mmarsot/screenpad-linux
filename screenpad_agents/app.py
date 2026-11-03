"""The app's loop: Herdr and firstmate read off the loop, the touch bar on it.

Two worker threads do the slow calls and hand their results to the loop
through a queue (and a pipe that wakes its select):

- Herdr's: reads the agents every [herdr] poll seconds, and focuses a pane
  when asked (then reads the agents again at once);
- firstmate's: runs its reader every [firstmate] poll seconds (at least 60),
  and again on demand when the Captain's Call tile or widget is tapped; taps
  during a read add one read after it, not one each.

The loop keeps the connection to the touch bar (retrying every RECONNECT
seconds while the daemon is not running), sends the page and tiles when what
they show changed, and turns touches into work for the threads: a tap on an
agent's widget focuses its pane, a tap on the agents tile focuses the first
blocked agent, a tap on Captain's Call reads firstmate again.
"""

import os
import queue
import select
import sys
import threading
import time

from .client import Touchbar, TouchbarGone
from .common import CALLS_TILE, FLEET_TILE, PAGE, RECONNECT
from .firstmate import FirstmateError
from .fleet import CALLS_WIDGET, Fleet
from .herdr import HerdrError


class App:
    def __init__(self, config, herdr, firstmate, socket_path, log=None, clock=time.monotonic):
        self.config, self.herdr, self.firstmate = config, herdr, firstmate
        self.socket_path, self.clock = socket_path, clock
        self.log = log or (lambda line: print("screenpad-agents: " + line, file=sys.stderr, flush=True))
        self.fleet = Fleet(config.session, firstmate is not None)
        self.stopping = threading.Event()
        self._results: queue.Queue = queue.Queue()
        self._focus: queue.Queue = queue.Queue()
        self._read_firstmate = threading.Event()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self.touchbar = None
        self._retry_at = 0.0
        self._threads = []
        self._told = set()

    # --- the worker threads ------------------------------------------------

    def start(self):
        self._threads = [threading.Thread(target=self._herdr_worker, name="herdr", daemon=True)]
        if self.firstmate is not None:
            self._threads.append(threading.Thread(target=self._firstmate_worker, name="firstmate", daemon=True))
        for t in self._threads:
            t.start()

    def _post(self, item):
        self._results.put(item)
        self._wake()

    def _wake(self):
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass

    def _herdr_worker(self):
        due = 0.0
        while not self.stopping.is_set():
            try:
                pane = self._focus.get(timeout=max(0.0, due - self.clock()))
            except queue.Empty:
                pane = None
            if self.stopping.is_set():
                return
            if pane is not None:
                try:
                    self.herdr.focus(pane)
                except Exception as e:
                    self._post(("log", _why(e, HerdrError), None))
            try:
                self._post(("agents", self.herdr.agents(), None))
            except Exception as e:
                self._post(("agents", None, _why(e, HerdrError)))
            due = self.clock() + self.config.herdr_poll

    def _firstmate_worker(self):
        while not self.stopping.is_set():
            self._read_firstmate.clear()
            try:
                self._post(("bearings", self.firstmate.read(), None))
            except Exception as e:
                self._post(("bearings", None, _why(e, FirstmateError)))
            self._read_firstmate.wait(self.config.firstmate_poll)

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
        if self.touchbar is None and self.clock() >= self._retry_at:
            self._connect()
        fds = [self._wake_r] + ([self.touchbar.fileno()] if self.touchbar else [])
        ready, _, _ = select.select(fds, [], [], timeout)
        changed = self._drain()
        if self.touchbar is not None:
            try:
                if self.touchbar.fileno() in ready:
                    for msg in self.touchbar.receive():
                        self.handle(msg)
                if changed:
                    self.touchbar.show(self.fleet.messages())
            except TouchbarGone as e:
                self.log("lost the touch bar (%s); retrying every %g s" % (e, RECONNECT))
                self._disconnect()

    def _connect(self):
        try:
            self.touchbar = Touchbar(self.socket_path, self.fleet.tiles())
            self.touchbar.show(self.fleet.messages())
        except (OSError, TouchbarGone):
            self._disconnect()

    def _disconnect(self):
        if self.touchbar is not None:
            self.touchbar.close()
        self.touchbar = None
        self._retry_at = self.clock() + RECONNECT

    def _drain(self):
        try:
            while os.read(self._wake_r, 4096):
                pass
        except BlockingIOError:
            pass
        changed = False
        while True:
            try:
                what, value, error = self._results.get_nowait()
            except queue.Empty:
                return changed
            if what == "agents":
                self.fleet.set_agents(value, error)
                changed = True
                self._tell("herdr", error)
            elif what == "bearings":
                self.fleet.set_bearings(value, error)
                changed = True
                self._tell("firstmate", error)
            elif what == "log":
                self.log(value)

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
        elif op == "event" and msg.get("page") == PAGE and msg.get("type") == "tap":
            self._tap(msg.get("widget"))
        elif op == "event" and msg.get("type") == "up" and msg.get("inside") is True:
            if msg.get("tile") == FLEET_TILE:
                self._focus_pane(self.fleet.first_blocked())
            elif msg.get("tile") == CALLS_TILE:
                self.refresh_calls()

    def _tap(self, widget):
        if widget == CALLS_WIDGET:
            self.refresh_calls()
        else:
            self._focus_pane(self.fleet.pane_of(widget))

    def _focus_pane(self, pane):
        if pane is not None:
            self._focus.put(pane)

    def refresh_calls(self):
        if self.firstmate is not None:
            self._read_firstmate.set()

    def close(self):
        self.stopping.set()
        self._read_firstmate.set()
        self._focus.put(None)
        self._disconnect()
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
