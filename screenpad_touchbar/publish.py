"""The scene the daemon publishes in touchbar.json, which the extension draws."""

import os

from .common import (
    CARD_HEIGHT,
    GRIP,
    PAGE,
    PANEL_H,
    PANEL_W,
    SCENE_VERSION,
)
from .moves import BAR_LIST
from .scene import ask_tiles, page_look, spot_tiles


def scene(d):
    """The tiles to draw: a question over the spots while one shows."""
    pressed, removing = (d.bar.pressed(), d.bar.removing()) if d.bar else ([], [])
    page_to = d.pager.target() if d.pager.available() else None
    # Page items open their page only while the apps page can show.
    pages = {n: page_look(p) for n, p in d.apps.pages.items()} if page_to is not None else {}
    marks = d.mover.marks(BAR_LIST)  # an item dragged from or to the bar
    tiles = spot_tiles(d.config, pressed, removing, d.apps.provided(), page_to, pages, marks)
    ask = d.apps.asks.shown()
    if ask is not None:
        height = d.bar.card_height() if d.bar else max(d.config.height, CARD_HEIGHT)
        pressed_ask = d.bar.ask_pressed(ask) if d.bar else ()
        tiles = ask_tiles(ask, height, pressed_ask, len(d.apps.asks.queue) - 1) + [
            t for t in tiles if t["style"] in (GRIP, PAGE)
        ]
    return tiles


def state(d):
    """touchbar.json: everything the extension draws, from the daemon d."""
    return {
        "version": SCENE_VERSION,
        "pid": os.getpid(),
        "active": d.source is not None and d.source.grabbed,
        "monitor": d.monitor,
        "panel": [PANEL_W, PANEL_H],
        # Drawn: CARD_HEIGHT while a card shows. The strut (the work
        # area windows avoid) is the bar's own height: a card covers
        # windows for a moment rather than resizing them.
        "height": d.bar.height() if d.bar else d.config.height,
        "strut": d.bar.base_height() if d.bar else d.config.height,
        "tiles": d.scene(),
        # No line between groups of spots: any item goes in any spot.
        "divider": None,
        "apps": d.apps.names(),
        # The page shown, black below the bar on the touchpad page (and
        # whether with a clock), and what the apps page shows there.
        "page": d.pager.name(),
        "screensaver": {"clock": d.config.pages.clock},
        "surface": d.surface.scene(),
        # Presses accepted so far (Bar and Surface click()): the
        # extension plays its click when this goes up.
        "clicks": d.clicks,
        # Fn+F6 (Toggle.shown()): the extension shows "ScreenPad on".
        "fnkey": d.toggle.shown() if d.toggle else None,
        # Version 1: the extension loaded before tiles keeps drawing the
        # bar from these until the next login (GNOME loads new extension
        # code only then). It shows no app tiles and no questions.
        "spots": [s.public() for s in d.config.spots if s.kind != PAGE],
        "pressed": d.bar.pressed() if d.bar else [],
        "removing": d.bar.removing() if d.bar else [],
    }
