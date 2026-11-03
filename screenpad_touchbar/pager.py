"""The pages: the touchpad page and the apps page, the swipe along the bar
and the page dots that turn them, and the return to the touchpad."""

import time

from .common import APPS, HOME, PAGE_NAMES, RETURN_AFTER, SWIPE_MIN, SWIPE_SLOPE, SWIPE_TIME


class Stroke:
    """A bar contact as the pager follows it: where and when it started,
    and whether it stayed the only finger on the bar."""

    __slots__ = ("alone", "start", "x", "y")

    def __init__(self, start, x, y, alone):
        self.start, self.x, self.y, self.alone = start, x, y, alone


class Pager:
    """Which page the panel shows, and what turns it.

    - The touchpad page (HOME) is the default: below the bar, the touchpad
      as always. On the apps page (APPS), every new contact below the bar
      is the surface's (zone() in the daemon), and none reaches the pointer.
    - watch() sees the bar's contact events before the bar does. A swipe
      along the bar (one finger, at least SWIPE_MIN across, rising at most
      SWIPE_SLOPE of that, lifted within SWIPE_TIME of touching) turns the
      page: moving the finger left shows the page on the right. Its end
      becomes a "cancel", so the spot it started on does nothing.
    - tap() turns to the next page, wrapping: a tap on the page dots.
    - touching(down) is told after each frame whether any contact is down;
      after return_after seconds with none, tick() goes back to the
      touchpad page. It also does when the apps page can no longer be shown.
    can_show() says whether the apps page can be shown now (grabbed, and
    drawn by an extension that knows the pages); enabled is [pager]'s.
    """

    def __init__(self, clock=time.monotonic, enabled=True, return_after=RETURN_AFTER, can_show=None):
        self.clock = clock
        self.enabled = enabled
        self.return_after = return_after
        self.can_show = can_show or (lambda: True)
        self.page = HOME
        self.strokes = {}  # bar slot -> Stroke
        self.idle_since = None  # since when no contact is down, or None
        self.changed = False

    def available(self):
        """Whether the dots show and the pages turn."""
        return self.enabled and self.can_show()

    def configure(self, pages):
        """Take a reloaded config's Pages."""
        self.enabled, self.return_after = pages.pager, pages.return_after
        self.tick()

    def name(self):
        return PAGE_NAMES[self.page]

    def turn(self, page):
        if page == self.page:
            return
        self.page, self.changed = page, True
        if self.idle_since is not None:
            self.idle_since = self.clock()

    def tap(self):
        if self.available():
            self.turn((self.page + 1) % len(PAGE_NAMES))

    def watch(self, events):
        """The bar's events, with a swipe's end turned into a cancel."""
        out = []
        for ev in events:
            kind, slot, px, py = ev
            if kind == "begin":
                for s in self.strokes.values():
                    s.alone = False
                self.strokes[slot] = Stroke(self.clock(), px, py, not self.strokes)
            elif kind in ("end", "cancel"):
                stroke = self.strokes.pop(slot, None)
                page = self.swiped(stroke, px, py) if kind == "end" else None
                if page is not None:
                    self.turn(page)
                    ev = ("cancel", slot, px, py)
            out.append(ev)
        return out

    def swiped(self, stroke, px, py):
        """The page a stroke lifted at px, py swipes to, or None if it is
        not a swipe (or there is no page that way)."""
        if stroke is None or not stroke.alone or not self.available():
            return None
        dx, dy = px - stroke.x, py - stroke.y
        if abs(dx) < SWIPE_MIN or abs(dy) > abs(dx) * SWIPE_SLOPE or self.clock() - stroke.start > SWIPE_TIME:
            return None
        page = self.page + (1 if dx < 0 else -1)
        return page if 0 <= page < len(PAGE_NAMES) else None

    def touching(self, down):
        if down:
            self.idle_since = None
        elif self.idle_since is None:
            self.idle_since = self.clock()

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        if self.page == HOME or self.idle_since is None:
            return None
        return self.idle_since + self.return_after

    def tick(self):
        """Go back to the touchpad page when it is time; True if that
        changed the page."""
        due = self.deadline()
        if self.page != HOME and (not self.available() or (due is not None and self.clock() >= due)):
            self.turn(HOME)
        return self.take_changed()

    def take_changed(self):
        changed, self.changed = self.changed, False
        return changed

    def reset(self):
        """Back to the touchpad page with no contact followed (letting go)."""
        self.turn(HOME)
        self.strokes.clear()
        self.idle_since = None

    def dots(self):
        """What the page dots tile draws: the page shown and how many."""
        return {"page": self.page, "pages": len(PAGE_NAMES)}

    def surface(self):
        """What the apps page shows, or None on the touchpad page."""
        if self.page != APPS:
            return None
        return {
            "title": "Apps",
            "text": "No apps here yet.",
            "hint": "Swipe the bar to the right, or leave the panel for %d s, to go back to the touchpad."
            % self.return_after,
        }


class Surface:
    """The apps page's contacts: every contact that starts below the bar
    there. Apps get them in a later version; for now nothing happens."""

    def handle(self, events):
        return False
