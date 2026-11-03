"""The bar: what touches on its spots, the grip and a question do."""

import time

from .common import (
    ASK_ARM,
    BUTTON,
    CARD_HEIGHT,
    DOTS,
    DOTS_X,
    GRIP,
    HEIGHT_STEP,
    LONG_PRESS,
    MAX_HEIGHT,
    MIN_HEIGHT,
    SHORTCUT,
    SPOT_W,
)
from .config import near
from .scene import DISMISS, ask_option_at, option_x


class Touch:
    __slots__ = ("armed", "removing", "spot", "start", "start_height", "start_y")

    def __init__(self, spot, start, start_y=0, start_height=0):
        self.spot, self.armed, self.start, self.removing = spot, True, start, False
        self.start_y, self.start_height = start_y, start_height


class AskTouch:
    """A contact on a question: on one of its answers, or inert (option None)."""

    __slots__ = ("armed", "ask", "option")

    def __init__(self, ask, option):
        self.ask, self.option, self.armed = ask, option, option is not None


class Bar:
    """Turns bar contacts into button presses, shortcut taps and resizes.

    - button: "hold" sends its keys down on touch and up on lift; "tap" sends
      them when the finger lifts on (or near) the button; a "tile" button
      belongs to an app, which gets "down" on touch and "up" on lift, with
      whether the finger lifted on (or near) it;
    - shortcut: a tap opens the app; held for LONG_PRESS it turns to
      "remove", and lifting then removes it; sliding away cancels either;
    - grip: dragging it up or down resizes the bar, saved on lift;
    - dots: a tap turns the page (pager.tap()), while the pager is
      available (pager.available()); otherwise the spot is empty;
    - while a question shows (asks.shown()), the bar is CARD_HEIGHT tall
      (touch_height()) and the question covers the button and shortcut
      spots: lifting on an answer chooses it, lifting on Dismiss closes
      it with no answer. Touches that start within ASK_ARM of it showing,
      or on its text, do nothing. The dots and the grip stay.
    A touch keeps what it started on: a button held when a question appears
    stays held, and a touch on a question that goes away does nothing.
    launch(app), remove(index) and resize(height) do the saving and
    launching; remove and resize return whether they did. click() is
    called once for each press the bar accepts, when its action happens: a
    hold button's touch, and the lift on (or near) a tap button, an app's
    tile, a shortcut, the dots or an answer; never a slide away, a cancel
    or the grip. tiles has online(name) and event(name, kind, inside); asks
    has shown(), the shown question's shown_at, choose(ask, option) and
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
    ):
        self.config = config
        self.keys = keys  # KeyEmitter
        self.run = run  # run(argv)
        self.launch = launch or (lambda app: None)
        self.remove = remove or (lambda index: False)
        self.resize = resize or (lambda height: False)
        self.clock = clock
        self.tiles = tiles
        self.asks = asks
        self.pager = pager
        self.click = click or (lambda: None)
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
                near = self.config.near_spot(t.spot, px, py, self.touch_height())
                if (s.kind in (SHORTCUT, DOTS) or s.item.mode == "tap") and near != t.armed:
                    t.armed = near
                    changed = True
                continue
            del self.touch[slot]
            changed = True
            lifted = kind == "end" and t.armed and self.config.near_spot(t.spot, px, py, self.touch_height())
            if lifted and s.kind != GRIP and not (s.kind == BUTTON and s.item.mode == "hold"):
                self.click()
            if s.kind == GRIP:
                if not (kind == "end" and self.preview != self.config.height and self.resize(self.preview)):
                    self.preview = None
            elif s.kind == DOTS:
                if lifted:
                    self.pager.tap()
            elif s.kind == SHORTCUT:
                if lifted and t.removing:
                    self.remove(s.index)
                elif lifted:
                    self.launch(s.item)
            elif s.item.tile:
                self.tiles.event(s.item.tile, "up", bool(lifted))
            elif s.item.mode == "hold":
                self._fire(s.item, press=False)
            elif lifted:
                self._fire(s.item, press=True)
                self._fire(s.item, press=False)
        return changed

    def touch_height(self):
        """How far down touches belong to the bar now: the config's height,
        or CARD_HEIGHT while a question shows on a shorter bar."""
        if self.asks and self.asks.shown():
            return max(self.config.height, CARD_HEIGHT)
        return self.config.height

    def _begin(self, slot, px, py):
        ask = self.asks.shown() if self.asks else None
        if ask and 0 <= py < self.touch_height() and px < DOTS_X:
            # Too soon after the question appeared (with the bar grown
            # under it), or on its text: the contact is the question's,
            # and does nothing.
            fresh = self.clock() < ask.shown_at + ASK_ARM
            option = None if fresh else ask_option_at(ask, px)
            self.ask_touch[slot] = AskTouch(ask, option)
            return option is not None
        i = self.config.spot_at(px, py, self.touch_height())
        if i is None:
            return False
        s = self.config.spots[i]
        if s.kind == DOTS:
            if not (self.pager and self.pager.available()):
                return False
            self.touch[slot] = Touch(i, self.clock())
            return True
        if s.item is None and s.kind != GRIP:
            return False
        if s.kind == GRIP:
            if any(self.spot(t).kind == GRIP for t in self.touch.values()):
                return False
            self.preview = self.config.height
            self.touch[slot] = Touch(i, self.clock(), py, self.config.height)
            return True
        if s.kind == BUTTON and s.item.tile:
            if not (self.tiles and self.tiles.online(s.item.tile)):
                return False
            self.touch[slot] = Touch(i, self.clock())
            self.tiles.event(s.item.tile, "down")
            return True
        self.touch[slot] = Touch(i, self.clock())
        if s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=True)
            self.click()
        return True

    def _ask_event(self, kind, slot, px, py):
        t = self.ask_touch[slot]
        if t.option is None:
            if kind != "move":
                del self.ask_touch[slot]
            return False
        here = near(option_x(t.option), SPOT_W, max(self.config.height, CARD_HEIGHT), px, py)
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

    def tick(self):
        """Turn long-held shortcuts to "remove"; True if that changed anything."""
        now, changed = self.clock(), False
        for t in self.touch.values():
            if self.spot(t).kind == SHORTCUT and not t.removing and now >= t.start + LONG_PRESS:
                t.removing = True
                changed |= t.armed
        return changed

    def deadline(self):
        """When tick() next has something to do, on the clock; or None."""
        times = [t.start + LONG_PRESS for t in self.touch.values() if self.spot(t).kind == SHORTCUT and not t.removing]
        return min(times) if times else None

    def _fire(self, b, press):
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
            return max(self.base_height(), CARD_HEIGHT)
        return self.base_height()

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
        s = self.spot(t)
        if s.kind == BUTTON and s.item.tile:
            self.tiles.event(s.item.tile, "up", False)
        elif s.kind == BUTTON and s.item.mode == "hold":
            self._fire(s.item, press=False)

    def set_config(self, config):
        """Take a reloaded config. Buttons held through it stay held if the
        same button is still in the same spot; every other touch ends."""
        for slot, t in list(self.touch.items()):
            s, n = self.spot(t), config.spots[t.spot]
            if not (s.kind == BUTTON and n.kind == BUTTON and n.item and n.item.action() == s.item.action()):
                self._end(slot)
        self.config = config
        self.preview = None

    def tile_gone(self, name):
        """An app stopped providing a tile: touches on it end, unannounced."""
        for slot, t in list(self.touch.items()):
            s = self.spot(t)
            if s.kind == BUTTON and s.item.tile == name:
                del self.touch[slot]

    def release_all(self):
        for slot in list(self.touch):
            self._end(slot)
        self.ask_touch.clear()
        self.preview = None
        self.keys.release_all()
