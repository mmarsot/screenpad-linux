"""The bar: what touches on its spots, the grip and a question do."""

import time

from .common import (
    APP,
    ASK_ARM,
    CARD_HEIGHT,
    GRIP,
    HEIGHT_STEP,
    LONG_PRESS,
    MAX_HEIGHT,
    MIN_HEIGHT,
    PAGE,
    PAGE_ITEM,
    PAGE_X,
    TILE,
)
from .config import near
from .moves import BAR_LIST
from .scene import DISMISS, ask_option_at, ask_option_box


class Touch:
    """A contact on a spot. item is the item pressed there, source the
    item it can drag to the apps page (moves.py), and an inert touch (on a
    tile no app provides, or a page that cannot open) does nothing but
    that."""

    __slots__ = ("armed", "inert", "item", "removing", "source", "spot", "start", "start_height", "start_y")

    def __init__(self, spot, start, start_y=0, start_height=0, source=None, inert=False, item=None):
        self.spot, self.armed, self.start, self.removing = spot, not inert, start, False
        self.start_y, self.start_height = start_y, start_height
        self.source, self.inert, self.item = source, inert, item


class AskTouch:
    """A contact on a question: on one of its answers, or inert (option None)."""

    __slots__ = ("armed", "ask", "option")

    def __init__(self, ask, option):
        self.ask, self.option, self.armed = ask, option, option is not None


class Bar:
    """Turns bar contacts into what the items do, page turns and resizes.

    - a key or command item: "hold" sends its keys down (or runs its
      command) on touch and up (release_command) on lift; "tap" sends them
      when the finger lifts on (or near) it;
    - a tile item belongs to an app, which gets "down" on touch and "up" on
      lift, with whether the finger lifted on (or near) it;
    - an app item: a tap opens the app; held for LONG_PRESS it turns to
      "remove", and lifting then removes it; sliding away cancels either.
      So does any item that acts on the lift (Item.removable(): a tap key
      or command, a page item); a hold item and an app's tile never do;
    - a page item: a tap opens that app's page on the apps page
      (open_page(name)), while the pages turn and an app gives it
      (tiles.pages);
    - an empty spot: a tap opens the picker (add_spot(), picker.py) while
      the pages turn; otherwise it does nothing;
    - grip: dragging it up or down resizes the bar, saved on lift;
    - page: a tap turns the page (pager.tap()), while the pager is
      available (pager.available()); otherwise the spot is empty;
    - while a question shows (asks.shown()), the bar is CARD_HEIGHT tall
      (touch_height()) and the question covers the item spots: lifting on
      an answer chooses it, lifting on Dismiss closes it with no answer.
      Touches that start within ASK_ARM of it showing, or on its text, do
      nothing. The page button and the grip stay.
    A touch keeps what it started on: an item held when a question appears
    stays held, and a touch on a question that goes away does nothing.
    With a mover (moves.Mover), a touch on an item that lifts over the
    apps page's home list moves the item there instead: its own action
    is cancelled, as when sliding away (a held key goes up on the lift).
    launch(app), remove(index, item), resize(height) and open_page(name) do
    the saving, launching and opening; remove and resize return whether
    they did. click() is called once for each press the bar accepts, when
    its action happens: a hold item's touch, and the lift on (or near) a
    tap item, an app's tile, an app, a page, the page button or an answer;
    never a slide away, a cancel or the grip. tiles has online(name),
    event(name, kind, inside) and pages (the apps' pages by name); asks has
    shown(), the shown question's shown_at, choose(ask, option) and
    dismiss(ask).
    """

    def __init__(
        self,
        config,
        keys,
        run,
        launch=None,
        remove=None,
        resize=None,
        clock=time.monotonic,
        tiles=None,
        asks=None,
        pager=None,
        click=None,
        open_page=None,
        mover=None,
        add_spot=None,
    ):
        self.config = config
        self.keys = keys  # KeyEmitter
        self.run = run  # run(argv)
        self.launch = launch or (lambda app: None)
        self.remove = remove or (lambda index, item: False)
        self.resize = resize or (lambda height: False)
        self.clock = clock
        self.tiles = tiles
        self.asks = asks
        self.pager = pager
        self.click = click or (lambda: None)
        self.open_page = open_page or (lambda name: None)
        self.mover = mover
        self.add_spot = add_spot or (lambda: None)
        self.touch = {}  # slot -> Touch
        self.ask_touch = {}  # slot -> AskTouch
        self.preview = None  # the height while the grip moves

    def spot(self, t):
        return self.config.spots[t.spot]

    def handle(self, events):
        changed = False
        for kind, slot, px, py in events:
            if kind == "begin":
                changed |= self._begin(slot, px, py)
                continue
            if slot in self.ask_touch:
                changed |= self._ask_event(kind, slot, px, py)
                continue
            t = self.touch.get(slot)
            if t is None:
                continue
            s = self.spot(t)
            if kind == "move":
                if s.kind == GRIP:
                    h = t.start_height + (py - t.start_y)
                    h = round(h / HEIGHT_STEP) * HEIGHT_STEP
                    h = min(max(h, MIN_HEIGHT), MAX_HEIGHT)
                    if h != self.preview:
                        self.preview = h
                        changed = True
                    continue
                if t.source is not None:
                    changed |= self.mover.over(t.source, px, py)
                if t.inert:
                    continue
                near = self.config.near_spot(t.spot, px, py, self.touch_height())
                if (s.item is None or not s.item.hold()) and near != t.armed:
                    t.armed = near
                    changed = True
                continue
            del self.touch[slot]
            changed = True
            if t.source is not None:
                if kind == "end" and self.mover.drop(t.source, px, py):
                    self._release(t)
                    continue
                self.mover.leave(t.source)
            if t.inert:
                continue
            lifted = kind == "end" and t.armed and self.config.near_spot(t.spot, px, py, self.touch_height())
            if lifted and s.kind != GRIP and not (s.item and s.item.hold()):
                self.click()
            if s.kind == GRIP:
                if not (kind == "end" and self.preview != self.config.height and self.resize(self.preview)):
                    self.preview = None
            elif s.kind == PAGE:
                if lifted:
                    self.pager.tap()
            elif s.item is None:
                if lifted:
                    self.add_spot()
            elif lifted and t.removing:
                self.remove(s.index, t.item)
            elif s.item.kind == APP:
                if lifted:
                    self.launch(s.item.name)
            elif s.item.kind == PAGE_ITEM:
                if lifted:
                    self.open_page(s.item.name)
            elif s.item.kind == TILE:
                self.tiles.event(s.item.name, "up", bool(lifted))
            elif s.item.hold():
                self.fire(s.item, press=False)
            elif lifted:
                self.fire(s.item, press=True)
                self.fire(s.item, press=False)
        return changed

    def touch_height(self):
        """How far down touches belong to the bar now: the config's height,
        or CARD_HEIGHT while a question shows on a shorter bar."""
        if self.asks and self.asks.shown():
            return max(self.config.height, CARD_HEIGHT)
        return self.config.height

    def _begin(self, slot, px, py):
        ask = self.asks.shown() if self.asks else None
        if ask and 0 <= py < self.touch_height() and px < PAGE_X:
            # Too soon after the question appeared (with the bar grown
            # under it), or on its text: the contact is the question's,
            # and does nothing.
            fresh = self.clock() < ask.shown_at + ASK_ARM
            option = None if fresh else ask_option_at(ask, px, self.card_height())
            self.ask_touch[slot] = AskTouch(ask, option)
            return option is not None
        i = self.config.spot_at(px, py, self.touch_height())
        if i is None:
            return False
        s = self.config.spots[i]
        if s.kind == PAGE or (s.item is None and s.kind != GRIP):
            # The page button, or an empty spot's +: only while the pages turn.
            if not (self.pager and self.pager.available()):
                return False
            self.touch[slot] = Touch(i, self.clock())
            return True
        if s.kind == GRIP:
            if any(self.spot(t).kind == GRIP for t in self.touch.values()):
                return False
            self.preview = self.config.height
            self.touch[slot] = Touch(i, self.clock(), py, self.config.height)
            return True
        source = (BAR_LIST, s.index, s.item) if self.mover else None
        if (s.item.kind == TILE and not (self.tiles and self.tiles.online(s.item.name))) or (
            s.item.kind == PAGE_ITEM and not self.page_open(s.item.name)
        ):
            # It does nothing, but can still be dragged to the apps page.
            if source:
                self.touch[slot] = Touch(i, self.clock(), source=source, inert=True)
            return False
        self.touch[slot] = Touch(i, self.clock(), source=source, item=s.item)
        if s.item.kind == TILE:
            self.tiles.event(s.item.name, "down")
            return True
        if s.item.hold():
            self.fire(s.item, press=True)
            self.click()
        return True

    def page_open(self, name):
        """Whether a page item can open its page now: the pages turn, and an app gives it."""
        return bool(self.pager and self.pager.available() and self.tiles and name in self.tiles.pages)

    def _ask_event(self, kind, slot, px, py):
        t = self.ask_touch[slot]
        if t.option is None:
            if kind != "move":
                del self.ask_touch[slot]
            return False
        height = self.card_height()
        x, width = ask_option_box(t.ask, t.option, height)
        here = near(x, width, height, px, py)
        if kind == "move":
            if here != t.armed:
                t.armed = here
                return True
            return False
        del self.ask_touch[slot]
        if kind == "end" and t.armed and here and self.asks.shown() is t.ask:
            self.click()
            if t.option == DISMISS:
                self.asks.dismiss(t.ask)
            else:
                self.asks.choose(t.ask, t.option)
        return True

    def _removable(self, t):
        s = self.spot(t)
        return not t.inert and s.item is not None and s.item.removable()

    def tick(self):
        """Turn long-held items to "remove"; True if that changed anything."""
        now, changed = self.clock(), False
        for t in self.touch.values():
            if self._removable(t) and not t.removing and now >= t.start + LONG_PRESS:
                t.removing = True
                changed |= t.armed
        return changed

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        times = [t.start + LONG_PRESS for t in self.touch.values() if self._removable(t) and not t.removing]
        return min(times) if times else None

    def act(self, item, kind):
        """A touch on an item elsewhere (the apps page): "down" as it
        starts, then "tap" (lifted on it) or "cancel". It does what it does
        on the bar; a page item's page the surface opens itself."""
        if item.kind == TILE:
            self.tiles.event(item.name, *(("down",) if kind == "down" else ("up", kind == "tap")))
        elif item.kind == APP:
            if kind == "tap":
                self.launch(item.name)
        elif item.hold():
            self.fire(item, press=kind == "down")
        elif kind == "tap":
            self.fire(item, press=True)
            self.fire(item, press=False)

    def fire(self, b, press):
        """A key or command item's press (press) or release."""
        if b.keys:
            (self.keys.press if press else self.keys.release)(b.keys)
        elif press and b.command:
            self.run(b.command)
        elif not press and b.release_command:
            self.run(b.release_command)

    def base_height(self):
        """The bar's own height: the config's, or the grip's while it moves."""
        return self.config.height if self.preview is None else self.preview

    def height(self):
        """The height drawn: the bar's own, or CARD_HEIGHT while a question
        shows on a shorter bar."""
        if self.asks and self.asks.shown():
            return self.card_height()
        return self.base_height()

    def card_height(self):
        """A card's height, drawn and touched: CARD_HEIGHT, or the bar's own
        height (the grip's while it moves) if taller."""
        return max(self.base_height(), CARD_HEIGHT)

    def pressed(self):
        return sorted({t.spot for t in self.touch.values() if t.armed})

    def removing(self):
        return sorted({t.spot for t in self.touch.values() if t.armed and t.removing})

    def ask_pressed(self, ask):
        """The answers of this question a finger is on (DISMISS first)."""
        on = {t.option for t in self.ask_touch.values() if t.ask is ask and t.armed and t.option is not None}
        return ([DISMISS] if DISMISS in on else []) + sorted(o for o in on if o != DISMISS)

    def _end(self, slot):
        t = self.touch.pop(slot)
        if t.source is not None:
            self.mover.leave(t.source)
        self._release(t)

    def _release(self, t):
        """End what a touch holds: a tile's touch (not inside), a held key."""
        s = self.spot(t)
        if t.inert:
            return
        if s.item is not None and s.item.kind == TILE:
            self.tiles.event(s.item.name, "up", False)
        elif s.item is not None and s.item.hold():
            self.fire(s.item, press=False)

    def set_config(self, config):
        """Take a reloaded config. Keys, commands and tiles held through it
        stay held if the same item is still in the same spot; every other
        touch ends, and so does one about to remove its item."""
        for slot, t in list(self.touch.items()):
            s, n = self.spot(t), config.spots[t.spot]
            same = s.item and n.item and s.item.kind not in (APP, PAGE_ITEM) and n.item.action() == s.item.action()
            if t.removing or not same:
                self._end(slot)
            elif t.source is not None:
                self.mover.leave(t.source)
                t.source = (BAR_LIST, n.index, n.item)
        self.config = config
        self.preview = None

    def tile_gone(self, name):
        """An app stopped providing a tile: touches on it end, unannounced."""
        for slot, t in list(self.touch.items()):
            s = self.spot(t)
            if s.item is not None and s.item.kind == TILE and s.item.name == name:
                del self.touch[slot]
                if t.source is not None:
                    self.mover.leave(t.source)

    def release_all(self):
        for slot in list(self.touch):
            self._end(slot)
        self.ask_touch.clear()
        self.preview = None
        self.keys.release_all()
