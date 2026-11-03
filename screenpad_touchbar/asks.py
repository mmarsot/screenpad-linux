"""The questions waiting for an answer and the attention card the bar grows for them."""

import time

from .common import GROW_WAIT
from .protocol import ProtocolError


class Asks:
    """Questions waiting for an answer; the first is shown, the rest queue.

    Questions show only while the bar can show them (available): the daemon
    holds the touchpad and a renderer that knows questions is running. When
    it cannot, a new question is answered "unavailable" at once and waiting
    ones are too, so whoever asked can fall back to asking another way.

    The bar grows to CARD_HEIGHT for the first question (raised), once no
    touchpad contact lies in the band it would cover (band_clear(), which
    the daemon sets) or GROW_WAIT after it started waiting, whichever comes
    first. The question shows from then on, so its ASK_ARM counts from the
    moment the bar grew. It shrinks back when no question is left.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.queue = []
        self.available = False
        self.changed = False
        # Whether the bar can show questions now; the daemon sets it.
        self.can_show = None
        self.band_clear = lambda: True
        self.raised = False
        self.waiting_since = None

    def refresh(self):
        """Ask can_show again: a question arriving right after the bar came
        on screen shows, and waiting ones go when it left."""
        if self.can_show is not None:
            self.set_available(bool(self.can_show()))

    def find(self, client, ask_id):
        return next((a for a in self.queue if a.client is client and a.id == ask_id), None)

    def add(self, ask):
        if self.find(ask.client, ask.id):
            raise ProtocolError("question %s is already waiting" % ask.id)
        self.refresh()
        if not self.available:
            self._reply(ask, None, "unavailable")
            return
        self.queue.append(ask)
        self.settle()

    def replace(self, ask):
        """A newer version of a waiting question (an updated notification)
        takes its place; shown, it is armed anew. False if none waits."""
        old = self.find(ask.client, ask.id)
        if old is None:
            return False
        i = self.queue.index(old)
        self.queue[i] = ask
        if i == 0 and self.raised:
            ask.shown_at = self.clock()
        self.changed = True
        return True

    def shown(self):
        if self.available and self.queue and self.raised:
            return self.queue[0]
        return None

    def settle(self):
        """Grow the bar for the first question, or let it shrink back."""
        head = self.queue[0] if self.available and self.queue else None
        if head is None:
            if self.raised or self.waiting_since is not None:
                self.raised, self.waiting_since = False, None
                self.changed = True
            return
        if self.raised:
            return
        now = self.clock()
        if self.waiting_since is None:
            self.waiting_since = now
        if now >= self.waiting_since + GROW_WAIT or self.band_clear():
            self.raised, self.waiting_since = True, None
            head.shown_at = now
            self.changed = True

    def choose(self, ask, k):
        self._finish(ask, ask.options[k]["id"], "chosen")

    def dismiss(self, ask):
        """Closed with no answer: for a notification, the card only."""
        self._finish(ask, None, "dismissed")

    def cancel(self, client, ask_id):
        ask = self.find(client, ask_id)
        if ask is None:
            return False
        self._finish(ask, None, "cancelled")
        return True

    def drop(self, client):
        """The app went away: its questions go, unanswered."""
        for a in [a for a in self.queue if a.client is client]:
            self._finish(a, None, None)

    def set_available(self, ok):
        if ok == self.available:
            return
        self.available = ok
        if not ok:
            for a in list(self.queue):
                self._finish(a, None, "unavailable")
        self.changed = True
        self.settle()

    def tick(self):
        now = self.clock()
        for a in [a for a in self.queue if now >= a.deadline]:
            self._finish(a, None, "timeout")
        self.settle()

    def deadline(self):
        """When tick() next has something to do: a timeout, or the end of
        the wait for the band to clear."""
        times = [a.deadline for a in self.queue]
        if self.waiting_since is not None:
            times.append(self.waiting_since + GROW_WAIT)
        return min(times, default=None)

    def _finish(self, ask, option, reason):
        head = self.queue and self.queue[0] is ask
        self.queue.remove(ask)
        if head and self.queue and self.raised:
            # The next one shows in the bar already grown, armed anew.
            self.queue[0].shown_at = self.clock()
        self.changed = True
        if reason:
            self._reply(ask, option, reason)
        self.settle()

    @staticmethod
    def _reply(ask, option, reason):
        ask.client.send({"op": "answer", "id": ask.id, "option": option, "reason": reason})
