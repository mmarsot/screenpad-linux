"""The apps surface: what the apps page shows below the bar, and what its touches do.

The apps page shows either the home page, a list of the config's [[apps]]
items and then the apps' pages no item places, or one app's page of widgets
(widgets.py). The contacts that start below the bar on the
apps page and are not the pointer's (decide.py hands the surface only taps
and presses, begun where and when they went down) act on the widget under
it:

- a touch that starts within ASK_ARM of that widget appearing, moving or
  changing does nothing, so a tap already on its way when a page turned or
  a widget changed lands on nothing;
- a tap is a lift on the widget (or within TAP_SLOP of it); a "long" widget
  held for LONG_PRESS is drawn held, and a lift then is a long press.
  Sliding away and lifting cancels. A widget that changes or goes while a
  finger is on it cancels that press: the finger chooses nothing;
- the app gets "down", then "tap", "long" or "cancel" (Apps.press). The
  surface's own widgets (Home, the home page's pages) open pages instead,
  and the home page's items do what they do on the bar (act()).
A touch keeps what it started on, like the bar's: one that started on a
widget stays that widget's (or nothing) until it lifts. One that started on
an [[apps]] item and lifts over the bar moves the item there instead
(moves.py); its own action is cancelled, as when sliding away.
"""

import json
import time

from .common import (
    APP,
    APPS,
    ASK_ARM,
    HEADER_H,
    HOME_COLUMNS,
    HOME_ROWS,
    HOME_W,
    LONG_PRESS,
    PAGE_ITEM,
    PANEL_H,
    PANEL_W,
    TAP_SLOP,
    TILE,
    TONES,
)
from .items import Item
from .moves import APPS_LIST
from .scene import page_look

# The surface's own widgets: Home, a page in the home page's list, and an
# item there.
HOME_WIDGET = "home"
OPEN = "open:"
ITEM = "item:"
# The picker's (picker.py): a + on a free cell of the home list, Back, and
# a choice.
ADD = "add:"
BACK = "back"
PICK = "pick:"
PICK_COLUMNS = 6
# How to go back, under the apps page's title.
BACK_HINT = (
    "Tap the touchpad button, swipe the bar to the right, or leave the panel for %d s, to go back to the touchpad."
)
# Every CSS class a widget can carry; the stylesheet has a rule for each
# (tests/test_touchbar_surface.py checks it).
WIDGET_CLASSES = ("inert", "long", "held", "moving", "target", "empty") + tuple("tone-" + tone for tone in TONES)


class Spot:
    """A widget as drawn: where (panel pixels), what, and whose."""

    __slots__ = ("client", "entry", "h", "item", "look", "page", "touch", "w", "wid", "x", "y")

    def __init__(self, page, wid, x, y, w, h, look, touch, client=None, item=None, entry=None):
        # page and client are None for the surface's own widgets; item is
        # the config's item a home page widget acts as, entry the index in
        # the [[apps]] items of the one it shows (a page item's too)
        self.page, self.wid, self.x, self.y, self.w, self.h = page, wid, x, y, w, h
        self.look, self.touch, self.client, self.item, self.entry = look, touch, client, item, entry

    def key(self):
        action = self.item.action() if self.item else None
        return json.dumps(
            [self.page, self.wid, self.x, self.y, self.w, self.h, self.touch, self.look, action], sort_keys=True
        )

    def near(self, px, py):
        return (
            self.x - TAP_SLOP <= px < self.x + self.w + TAP_SLOP
            and self.y - TAP_SLOP <= py < self.y + self.h + TAP_SLOP
        )

    def on(self, px, py):
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


class Press:
    """A contact on the surface: on a widget (spot), or inert (spot None);
    source is the [[apps]] item it can drag to the bar (moves.py)."""

    __slots__ = ("held", "inside", "key", "source", "spot", "start")

    def __init__(self, spot, start, source=None):
        self.spot, self.start, self.inside, self.held = spot, start, spot is not None, False
        self.key = spot.key() if spot else None
        self.source = source


def grid(x0, y0, width, height, columns, rows, col, row, cols, nrows):
    """A widget's rectangle in a grid of columns by rows cells."""
    x = x0 + round(col * width / columns)
    y = y0 + round(row * height / rows)
    return x, y, x0 + round((col + cols) * width / columns) - x, y0 + round((row + nrows) * height / rows) - y


class Surface:
    """The apps page's widgets and contacts.

    apps has pages (name -> widgets.Page), provided() (the tiles apps give
    now and their looks) and press(client, page, widget, kind); items() is
    the config's [[apps]] items, and act(item, kind) does what a touch on
    one does: "down" as it starts, then "tap" or "cancel" (the daemon's
    item_act());
    pager says which page shows (page, return_after); top() is the bar's
    own height (where the surface starts), cover() the height the bar
    covers now (taller while a card shows); can_draw() whether the renderer
    draws widgets (WIDGETS_PROTOCOL): without it the page shows only its
    text, and touches on it do nothing. click() is called for each tap or
    long press, as the finger lifts on the widget, and as it goes down on
    a hold item; never for a cancel.
    """

    def __init__(self, apps, pager, top, cover, can_draw, clock=time.monotonic, click=None, items=None, act=None):
        self.apps, self.pager, self.clock = apps, pager, clock
        self.click = click or (lambda: None)
        self.items = items or list
        self.act = act or (lambda item, kind: None)
        self.top, self.cover, self.can_draw = top, cover, can_draw
        self.open = None  # the app page shown, or None for the home page
        self.presses = {}  # slot -> Press
        self.since = {}  # Spot.key() -> when it appeared as it is
        self.covered = None  # cover() when the widgets were last drawn
        self.mover = None  # moves.Mover: drags between the home list and the bar
        self.picker = None  # picker.Picker: what a + adds, while it shows

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
        if self.picker is not None and self.picker.place is not None:
            return self._picker_spots(top, y0, height)
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
        hint = BACK_HINT
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
        cells = self.home_cells()
        adding = self.picker is not None and self.picker.room(APPS_LIST)
        if not cells and not adding:
            look = {
                "label": "No apps here yet",
                "text": "Items from touchbar.toml's [[apps]] blocks show here, then the pages programs give.",
            }
            spots.append(Spot(None, "empty", 0, y0, PANEL_W, height, look, "none"))
        rows = max(HOME_ROWS, -(-len(cells) // HOME_COLUMNS))
        for i, (wid, look, touch, item) in enumerate(cells):
            x, y, wd, h = grid(0, y0, PANEL_W, height, HOME_COLUMNS, rows, i % HOME_COLUMNS, i // HOME_COLUMNS, 1, 1)
            entry = i if i < len(self.items()) else None
            spots.append(Spot(None, wid, x, y, wd, h, look, touch, item=item, entry=entry))
        # A + on each free cell while the [[apps]] items have room.
        for i in range(len(cells), rows * HOME_COLUMNS if adding else 0):
            x, y, wd, h = grid(0, y0, PANEL_W, height, HOME_COLUMNS, rows, i % HOME_COLUMNS, i // HOME_COLUMNS, 1, 1)
            spots.append(Spot(None, ADD + str(i), x, y, wd, h, {"icon": "list-add-symbolic"}, "tap"))
        return spots

    def _picker_spots(self, top, y0, height):
        """The picker: Back and its title, then what it offers."""
        assert self.picker is not None
        spots = [
            Spot(None, BACK, 0, top, HOME_W, HEADER_H, {"icon": "go-previous-symbolic", "label": "Back"}, "tap"),
            Spot(None, "title", HOME_W, top, PANEL_W - HOME_W, HEADER_H, {"title": self.picker.title()}, "none"),
        ]
        choices = self.picker.choices()
        rows = max(2, -(-len(choices) // PICK_COLUMNS))
        for i, (look, _item) in enumerate(choices):
            x, y, wd, h = grid(0, y0, PANEL_W, height, PICK_COLUMNS, rows, i % PICK_COLUMNS, i // PICK_COLUMNS, 1, 1)
            spots.append(Spot(None, PICK + str(i), x, y, wd, h, look, "tap"))
        return spots

    def open_picker(self, place):
        """A + tapped: the picker, adding to place ("bar" or "apps"), on the apps page."""
        if self.picker is not None and self.pager.available():
            self.open = None
            self.picker.open(place)
            self.pager.turn(APPS)

    def home_cells(self):
        """The home page's list: (widget id, look, touch, item) for each
        [[apps]] item, in order, then each app's page that no item places."""
        cells: list[tuple[str, dict, str, Item | None]] = []
        items: list[Item] = self.items()
        provided = self.apps.provided()
        for n, item in enumerate(items):
            if item.kind == PAGE_ITEM:
                page = self.apps.pages.get(item.name)
                if page is None:
                    cells.append((ITEM + str(n), item.looks() or {"label": item.name}, "none", None))
                else:
                    cells.append((OPEN + item.name, page_look(page), "tap", None))
            elif item.kind == TILE:
                look = provided.get(item.name)
                if look is None:
                    cells.append((ITEM + str(n), item.looks() or {"label": item.name}, "none", item))
                else:
                    shown = {k: look[k] for k in ("icon", "label", "text", "badge", "tone") if look.get(k)}
                    if "icon" not in shown and "label" not in shown:
                        shown.update(item.looks())
                    cells.append((ITEM + str(n), shown, "tap", item))
            elif item.kind == APP:
                cells.append((ITEM + str(n), {"app": item.name, **item.looks()}, "tap", item))
            else:
                cells.append((ITEM + str(n), item.looks(), "tap", item))
        placed = {i.name for i in items if i.kind == PAGE_ITEM}
        for p in self.apps.pages.values():
            if p.name not in placed:
                cells.append((OPEN + p.name, page_look(p), "tap", None))
        return cells

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

    def drop_cell(self, px, py):
        """Where on the home list an item dragged from the bar lands if
        lifted at px, py: the index of the [[apps]] item under it, or
        len(items) elsewhere on the list (a page, a free cell); None while
        the home list does not show, off it (the header, under a card), or
        on an item that appeared or changed within ASK_ARM."""
        if self.pager.page != APPS or not (max(self.top() + HEADER_H, self.cover()) <= py < PANEL_H):
            return None
        if not 0 <= px < PANEL_W or not self.shown() or self.page() is not None:
            return None
        spot = next((s for s in self.spots() if s.entry is not None and s.on(px, py)), None)
        if spot is None:
            return len(self.items())
        if self.clock() < self.since.get(spot.key(), self.clock()) + ASK_ARM:
            return None
        return spot.entry

    def open_page(self, name):
        """A page item's tap on the bar: the apps page, on that app's page."""
        if self.pager.available() and name in self.apps.pages:
            self.open = name
            self.pager.turn(APPS)

    def _send(self, spot, kind):
        if spot.page is not None:
            self.apps.press(spot.client, spot.page, spot.wid, kind)
        elif spot.item is not None:
            self.act(spot.item, kind)

    def _cancel(self, p):
        if p.spot is not None:
            self._send(p.spot, "cancel")

    def handle(self, events):
        """The surface's contact events; True if what it draws changed."""
        changed = self.refresh()
        now = self.clock()
        for ev in events:
            kind, slot, px, py = ev[:4]
            if kind == "begin":
                # When it went down (decide.py holds a touch back until it
                # is a press): the arming counts from then.
                at = ev[4] if len(ev) > 4 else now
                spot, fresh = self._under(px, py, at)
                source = None
                if self.mover and spot is not None and spot.entry is not None and not fresh:
                    source = (APPS_LIST, spot.entry, self.items()[spot.entry])
                if spot is not None and (spot.touch == "none" or fresh):
                    spot = None
                self.presses[slot] = Press(spot, at, source)
                if spot is not None:
                    self._send(spot, "down")
                    if spot.item is not None and spot.item.hold():
                        # A hold item acts as the finger goes down.
                        self.click()
                    changed = True
                continue
            p = self.presses.get(slot)
            if p is None:
                continue
            if kind == "move":
                if p.source is not None and self.mover:
                    changed |= self.mover.over(p.source, px, py)
                if p.spot is not None and p.spot.near(px, py) != p.inside:
                    p.inside = not p.inside
                    changed = True
                continue
            del self.presses[slot]
            if p.source is not None and self.mover:
                if kind == "end" and self.mover.drop(p.source, px, py):
                    self._cancel(p)
                    changed = True
                    continue
                changed |= self.mover.leave(p.source)
            if p.spot is None:
                continue
            changed = True
            if kind == "end" and p.inside and p.spot.near(px, py):
                self._lifted(p)
            else:
                self._cancel(p)
        return changed

    def _under(self, px, py, at):
        """The widget under px, py, and whether it appeared or changed
        within ASK_ARM before at (then a touch from at does nothing)."""
        spot = next((s for s in self.spots() if s.on(px, py)), None)
        return spot, spot is not None and at < self.since.get(spot.key(), at) + ASK_ARM

    def holdable(self, px, py, at):
        """Whether a touch that went down at px, py at time at presses
        something there: a widget that takes touches, or an item that can
        be dragged to the bar, armed. A widget not seen yet (refresh()) is
        newer than the touch."""
        spot, fresh = self._under(px, py, at)
        movable = self.mover is not None and spot is not None and spot.entry is not None
        return spot is not None and not fresh and (spot.touch != "none" or movable)

    def _lifted(self, p):
        spot = p.spot
        if not (spot.item is not None and spot.item.hold()):
            self.click()
        if spot.page is not None:
            self._send(spot, "long" if p.held else "tap")
        elif spot.item is not None:
            self._send(spot, "tap")
        elif spot.wid == HOME_WIDGET:
            self.open = None
        elif spot.wid.startswith(ADD):
            self.open_picker(APPS_LIST)
        elif self.picker is not None and spot.wid == BACK:
            self.picker.close()
        elif self.picker is not None and spot.wid.startswith(PICK):
            self.picker.choose(int(spot.wid[len(PICK) :]))
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
            if p.source is not None and self.mover:
                self.mover.leave(p.source)
        self.presses.clear()

    def scene(self):
        """What the apps page shows (the "surface" in touchbar.json), or
        None on the touchpad page: title, page and widgets for an extension
        that draws widgets, else only the title, text and hint it draws."""
        if self.pager.page != APPS:
            if self.picker is not None:
                self.picker.close()  # it does not wait for the next visit
            return None
        if not self.can_draw():
            return {
                "title": "Apps",
                "text": "Apps need the touch bar's new extension: log out and in once.",
                "hint": BACK_HINT % self.pager.return_after,
            }
        self.refresh()
        marks = self.mover.marks(APPS_LIST) if self.mover else {}
        hint = self.mover.hint() if self.mover else None
        widgets = []
        for s in self.spots():
            tile = {"id": "%s/%s" % (s.page or "surface", s.wid), "x": s.x, "y": s.y, "width": s.w, "height": s.h}
            tile.update((k, v) for k, v in s.look.items() if k != "tone")
            classes = ["tone-" + s.look.get("tone", "neutral")]
            if s.wid.startswith(ADD):
                classes.append("empty")
            elif s.touch == "none":
                classes.append("inert")
            elif s.touch == "long":
                classes.append("long")
            for p in self.presses.values():
                if p.spot is not None and p.inside and p.key == s.key():
                    tile["pressed"] = True
                    if p.held:
                        classes.append("held")
            if s.page is None and s.entry in marks:
                classes.append(marks[s.entry])
            if hint and s.page is None and s.wid == "title":
                tile["text"] = hint
            tile["classes"] = classes
            widgets.append(tile)
        page = self.page()
        if self.picker is not None and self.picker.place is not None:
            return {"title": self.picker.title(), "page": None, "widgets": widgets}
        return {"title": page.title() if page else "Apps", "page": page.name if page else None, "widgets": widgets}
