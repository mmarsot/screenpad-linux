"""The apps surface: what the apps page shows below the bar, and what its touches do.

The apps page shows either the home page, a list of the apps' pages, or one
app's page of widgets (widgets.py). Every contact that starts below the bar
on the apps page is the surface's, and acts on the widget under it:

- a touch that starts within ASK_ARM of that widget appearing, moving or
  changing does nothing, so a tap already on its way when a page turned or
  a widget changed lands on nothing;
- a tap is a lift on the widget (or within TAP_SLOP of it); a "long" widget
  held for LONG_PRESS is drawn held, and a lift then is a long press.
  Sliding away and lifting cancels. A widget that changes or goes while a
  finger is on it cancels that press: the finger chooses nothing;
- the app gets "down", then "tap", "long" or "cancel" (Apps.press). The
  surface's own widgets (Home, the home page's list) open pages instead.
A touch keeps what it started on, like the bar's: one that started on a
widget stays that widget's (or nothing) until it lifts.
"""

import json
import time

from .common import (
    APPS,
    ASK_ARM,
    HEADER_H,
    HOME_COLUMNS,
    HOME_ROWS,
    HOME_W,
    LONG_PRESS,
    PANEL_H,
    PANEL_W,
    TAP_SLOP,
    TONES,
)

# The surface's own widgets: Home, and a page in the home page's list.
HOME_WIDGET = "home"
OPEN = "open:"
# Every CSS class a widget can carry; the stylesheet has a rule for each
# (tests/test_touchbar_surface.py checks it).
WIDGET_CLASSES = ("inert", "long", "held") + tuple("tone-" + tone for tone in TONES)


class Spot:
    """A widget as drawn: where (panel pixels), what, and whose."""

    __slots__ = ("client", "h", "look", "page", "touch", "w", "wid", "x", "y")

    def __init__(self, page, wid, x, y, w, h, look, touch, client=None):
        # page and client are None for the surface's own widgets
        self.page, self.wid, self.x, self.y, self.w, self.h = page, wid, x, y, w, h
        self.look, self.touch, self.client = look, touch, client

    def key(self):
        return json.dumps([self.page, self.wid, self.x, self.y, self.w, self.h, self.touch, self.look], sort_keys=True)

    def near(self, px, py):
        return (
            self.x - TAP_SLOP <= px < self.x + self.w + TAP_SLOP
            and self.y - TAP_SLOP <= py < self.y + self.h + TAP_SLOP
        )

    def on(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class Press:
    """A contact on the surface: on a widget (spot), or inert (spot None)."""

    __slots__ = ("held", "inside", "key", "spot", "start")

    def __init__(self, spot, start):
        self.spot, self.start, self.inside, self.held = spot, start, spot is not None, False
        self.key = spot.key() if spot else None


def grid(x0, y0, width, height, columns, rows, col, row, cols, nrows):
    """A widget's rectangle in a grid of columns by rows cells."""
    x = x0 + round(col * width / columns)
    y = y0 + round(row * height / rows)
    return x, y, x0 + round((col + cols) * width / columns) - x, y0 + round((row + nrows) * height / rows) - y


class Surface:
    """The apps page's widgets and contacts.

    apps has pages (name -> widgets.Page) and press(client, page, widget,
    kind);
    pager says which page shows (page, return_after); top() is the bar's
    own height (where the surface starts), cover() the height the bar
    covers now (taller while a card shows); can_draw() whether the renderer
    draws widgets (WIDGETS_PROTOCOL): without it the page shows only its
    text, and touches on it do nothing. click() is called for each tap or
    long press, as the finger lifts on the widget; never for a cancel.
    """

    def __init__(self, apps, pager, top, cover, can_draw, clock=time.monotonic, click=None):
        self.apps, self.pager, self.clock = apps, pager, clock
        self.click = click or (lambda: None)
        self.top, self.cover, self.can_draw = top, cover, can_draw
        self.open = None  # the app page shown, or None for the home page
        self.presses = {}  # slot -> Press
        self.since = {}  # Spot.key() -> when it appeared as it is
        self.covered = None  # cover() when the widgets were last drawn

    def shown(self):
        return self.pager.page == APPS and bool(self.can_draw())

    def page(self):
        """The app page shown: the one opened, while it still exists."""
        page = self.apps.pages.get(self.open) if self.open else None
        if page is None:
            self.open = None
        return page

    def spots(self):
        """The widgets drawn now, header first; none while not shown."""
        if not self.shown():
            return []
        top = self.top()
        y0, height = top + HEADER_H, PANEL_H - top - HEADER_H
        page = self.page()
        if page is not None:
            client = getattr(page.client, "name", None)
            spots = [
                Spot(None, HOME_WIDGET, 0, top, HOME_W, HEADER_H, {"icon": "go-home-symbolic", "label": "Home"}, "tap"),
                Spot(
                    None,
                    "title",
                    HOME_W,
                    top,
                    PANEL_W - HOME_W,
                    HEADER_H,
                    {"title": page.title(), **({"text": client} if client else {})},
                    "none",
                ),
            ]
            for w in page.widgets:
                x, y, wd, h = grid(0, y0, PANEL_W, height, page.columns, page.rows, w.col, w.row, w.cols, w.rows)
                spots.append(Spot(page.name, w.id, x, y, wd, h, w.look, w.touch, page.client))
            return spots
        hint = "Swipe the bar to the right, or leave the panel for %d s, to go back to the touchpad."
        spots = [
            Spot(
                None,
                "title",
                0,
                top,
                PANEL_W,
                HEADER_H,
                {"title": "Apps", "text": hint % self.pager.return_after},
                "none",
            )
        ]
        pages = list(self.apps.pages.values())
        if not pages:
            look = {"label": "No apps here yet", "text": "Programs that use the touch bar show their pages here."}
            spots.append(Spot(None, "empty", 0, y0, PANEL_W, height, look, "none"))
        for i, p in enumerate(pages):
            x, y, wd, h = grid(
                0, y0, PANEL_W, height, HOME_COLUMNS, HOME_ROWS, i % HOME_COLUMNS, i // HOME_COLUMNS, 1, 1
            )
            look = {"label": p.title(), **{k: v for k, v in p.look.items() if k != "title"}}
            spots.append(Spot(None, OPEN + p.name, x, y, wd, h, look, "tap"))
        return spots

    def refresh(self):
        """Note what appeared or changed since the last look, which arms it
        ASK_ARM from now; cancel presses on widgets that changed or went.
        A card that grew or shrank over the page re-arms every widget.
        True if a press ended."""
        spots = self.spots()
        now, cover = self.clock(), self.cover()
        if cover != self.covered:
            self.since.clear()
            self.covered = cover
        keys = {s.key() for s in spots}
        self.since = {k: self.since.get(k, now) for k in keys}
        ended = False
        for p in self.presses.values():
            if p.spot is not None and p.key not in keys:
                self._cancel(p)
                p.spot = None
                ended = True
        return ended

    def _send(self, spot, kind):
        if spot.page is not None:
            self.apps.press(spot.client, spot.page, spot.wid, kind)

    def _cancel(self, p):
        if p.spot is not None:
            self._send(p.spot, "cancel")

    def handle(self, events):
        """The surface's contact events; True if what it draws changed."""
        changed = self.refresh()
        now = self.clock()
        for kind, slot, px, py in events:
            if kind == "begin":
                spot = next((s for s in self.spots() if s.on(px, py)), None)
                if spot is not None and (spot.touch == "none" or now < self.since.get(spot.key(), now) + ASK_ARM):
                    spot = None
                self.presses[slot] = Press(spot, now)
                if spot is not None:
                    self._send(spot, "down")
                    changed = True
                continue
            p = self.presses.get(slot)
            if p is None:
                continue
            if kind == "move":
                if p.spot is not None and p.spot.near(px, py) != p.inside:
                    p.inside = not p.inside
                    changed = True
                continue
            del self.presses[slot]
            if p.spot is None:
                continue
            changed = True
            if kind == "end" and p.inside and p.spot.near(px, py):
                self._lifted(p)
            else:
                self._cancel(p)
        return changed

    def _lifted(self, p):
        spot = p.spot
        self.click()
        if spot.page is not None:
            self._send(spot, "long" if p.held else "tap")
        elif spot.wid == HOME_WIDGET:
            self.open = None
        elif spot.wid.startswith(OPEN):
            self.open = spot.wid[len(OPEN) :]

    def tick(self):
        """Mark long-held "long" widgets held; True if that changed anything."""
        changed = self.refresh()
        now = self.clock()
        for p in self.presses.values():
            if p.spot is not None and p.spot.touch == "long" and not p.held and now >= p.start + LONG_PRESS:
                p.held = True
                changed = True
        return changed

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        times = [
            p.start + LONG_PRESS
            for p in self.presses.values()
            if p.spot is not None and p.spot.touch == "long" and not p.held
        ]
        return min(times) if times else None

    def release_all(self):
        for p in self.presses.values():
            self._cancel(p)
        self.presses.clear()

    def scene(self):
        """What the apps page shows (the "surface" in touchbar.json), or
        None on the touchpad page: title, page and widgets for an extension
        that draws widgets, else only the title, text and hint it draws."""
        if self.pager.page != APPS:
            return None
        if not self.can_draw():
            return {
                "title": "Apps",
                "text": "Apps need the touch bar's new extension: log out and in once.",
                "hint": "Swipe the bar to the right, or leave the panel for %d s, to go back to the touchpad."
                % self.pager.return_after,
            }
        self.refresh()
        widgets = []
        for s in self.spots():
            tile = {"id": "%s/%s" % (s.page or "surface", s.wid), "x": s.x, "y": s.y, "width": s.w, "height": s.h}
            tile.update((k, v) for k, v in s.look.items() if k != "tone")
            classes = ["tone-" + s.look.get("tone", "neutral")]
            if s.touch == "none":
                classes.append("inert")
            elif s.touch == "long":
                classes.append("long")
            for p in self.presses.values():
                if p.spot is not None and p.inside and p.key == s.key():
                    tile["pressed"] = True
                    if p.held:
                        classes.append("held")
            tile["classes"] = classes
            widgets.append(tile)
        page = self.page()
        return {"title": page.title() if page else "Apps", "page": page.name if page else None, "widgets": widgets}
