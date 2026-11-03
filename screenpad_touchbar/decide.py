"""The apps page's touches: which move the pointer, and which press what is under them.

On the apps page one finger moves the pointer, and a widget or an item
takes only a clear touch. Every contact that starts below the bar there (a
SURFACE contact of the splitter) waits here, undecided, until one of:

- it moves more than tap_move from where it went down, the pad's button is
  pressed, or another finger is down below the bar (one undecided within
  its tap_time, or one already the pointer's): it is the pointer's. A
  finger resting still past its tap_time does not count: a new touch
  beside it is decided on its own, so a tap beside a resting thumb is not
  a two-finger tap. The daemon hands it to the
  virtual touchpad (Splitter.handoff()), from where it went down, and the
  surface never hears of it, so it cannot also press anything;
- it lifts within tap_time, nearly still: a tap. The surface gets its
  begin, where and when it went down, and its end, at once;
- it stays nearly still for tap_time on what takes a press (holdable(): a
  widget or an item that takes touches, armed): a press. The surface gets
  its begin, where and when it went down, and from then on every event, as
  any of its contacts: a widget's long press, a hold item held, an item
  dragged to the bar, sliding away to cancel;
- it lifts later, or is cancelled (a palm): nothing.

A touch still for tap_time on nothing that takes a press stays undecided,
with no timer: moving it later makes it the pointer's (the pad's button
too, or a finger on the touchpad when another goes down). The only timer is
tap_time after an undecided touch went down.
"""

import math
import time


class Pending:
    """An undecided contact: where and when it went down, where it is now,
    and whether its tap_time is over (late)."""

    __slots__ = ("late", "start", "x", "x0", "y", "y0")

    def __init__(self, start, x, y):
        self.start, self.x0, self.y0, self.x, self.y, self.late = start, x, y, x, y, False


class Decider:
    """Decides the apps page's contacts, between the splitter and the surface.

    settings() is (tap_time in seconds, tap_move in panel pixels);
    holdable(px, py, at) whether a touch that went down at px, py at time at
    would press something there (Surface.holdable()).
    """

    def __init__(self, settings, holdable, clock=time.monotonic):
        self.settings, self.holdable, self.clock = settings, holdable, clock
        self.pending = {}  # slot -> Pending
        self.passed = set()  # slots decided a press: the surface's

    def feed(self, events, pad_down=False, pressing=False):
        """A frame's surface events; pad_down is whether a touchpad contact
        is down, pressing whether the pad's button is. Returns the events
        for the surface and the slots to hand to the pointer."""
        out = self.settle()
        pointer = []
        for ev in events:
            kind, slot, px, py = ev
            if slot in self.passed:
                if kind in ("end", "cancel"):
                    self.passed.discard(slot)
                out.append(ev)
                continue
            if kind == "begin":
                if pad_down or pressing:
                    taken = list(self.pending)
                else:
                    taken = [s for s, q in self.pending.items() if not q.late]
                if pad_down or pressing or taken:
                    pointer += [slot, *taken]
                    for s in taken:
                        del self.pending[s]
                else:
                    self.pending[slot] = Pending(self.clock(), px, py)
                continue
            p = self.pending.get(slot)
            if p is None:
                continue
            if kind == "move":
                p.x, p.y = px, py
                if math.hypot(px - p.x0, py - p.y0) > self.settings()[1]:
                    del self.pending[slot]
                    pointer.append(slot)
                continue
            del self.pending[slot]
            if kind == "end" and not p.late:
                out += [("begin", slot, p.x0, p.y0, p.start), ev]
        if pressing and self.pending:
            pointer += list(self.pending)
            self.pending.clear()
        return out, pointer

    def settle(self):
        """The presses whose tap_time is over: their begins (and where
        they are now) for the surface."""
        out: list[tuple] = []
        now, tap_time = self.clock(), self.settings()[0]
        for slot, p in list(self.pending.items()):
            if p.late or now < p.start + tap_time:
                continue
            if not self.holdable(p.x0, p.y0, p.start):
                p.late = True
                continue
            del self.pending[slot]
            self.passed.add(slot)
            out.append(("begin", slot, p.x0, p.y0, p.start))
            if (p.x, p.y) != (p.x0, p.y0):
                out.append(("move", slot, p.x, p.y))
        return out

    def deadline(self):
        """When settle() next has something to do, on the clock; or None."""
        times = [p.start for p in self.pending.values() if not p.late]
        return min(times) + self.settings()[0] if times else None
