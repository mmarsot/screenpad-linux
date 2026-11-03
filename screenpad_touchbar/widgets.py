"""Apps' pages on the apps surface: validating the "page" message and its widgets.

An app gives the apps page a page of widgets with {"op": "page", ...}; each
one replaces that page's previous version. docs/app-api.md is the reference:

  {"op": "page", "page": NAME, "title": .., "text": .., "icon": ..,
   "badge": .., "tone": .., "columns": 1-6, "rows": 1-4,
   "widgets": [{"id": .., "col": .., "row": .., "cols": .., "rows": ..,
                "label": .., "text": .., "lines": [..], "icon": ..,
                "badge": .., "tone": .., "touch": "tap"|"long"|"none"}, ...]}

The page's title, text, icon, badge and tone are what the home page's list
shows for it. A widget covers a rectangle of the page's grid (col, row and
its size in cells), and no two widgets overlap.
"""

import json
import re

from .common import (
    MAX_COLUMNS,
    MAX_LINES,
    MAX_ROWS,
    MAX_WIDGETS,
    OPTION_ID,
    TILE_NAME,
)
from .protocol import ProtocolError, known_fields, text_field, tone_field

TOUCHES = ("tap", "long", "none")
PAGE_FIELDS = {"page", "title", "text", "icon", "badge", "tone", "columns", "rows", "widgets"}
WIDGET_FIELDS = {"id", "col", "row", "cols", "rows", "label", "text", "lines", "icon", "badge", "tone", "touch"}


class Widget:
    """One widget of a page: its cells, what it draws and what a touch does."""

    __slots__ = ("col", "cols", "id", "look", "row", "rows", "touch")

    def __init__(self, id, col, row, cols, rows, look, touch):
        self.id, self.col, self.row, self.cols, self.rows = id, col, row, cols, rows
        self.look, self.touch = look, touch

    def key(self):
        """Everything that makes it what it is; a change re-arms it."""
        return json.dumps([self.id, self.col, self.row, self.cols, self.rows, self.touch, self.look], sort_keys=True)


class Page:
    """An app's page: what the home page lists for it and its widgets."""

    __slots__ = ("client", "columns", "look", "name", "rows", "widgets")

    def __init__(self, client, name, look, columns, rows, widgets):
        self.client, self.name, self.look = client, name, look
        self.columns, self.rows, self.widgets = columns, rows, widgets

    def title(self):
        return self.look.get("title") or self.name


def icon_field(value):
    """An icon name from the theme or an absolute image path, or None."""
    if value is None:
        return None
    if not isinstance(value, str) or not (
        re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value)
        or (value.startswith("/") and len(value) <= 4096 and value.isprintable())
    ):
        raise ProtocolError("icon must be an icon name or an absolute image path")
    return value


def int_field(value, what, low, high, default=None):
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProtocolError("%s must be a whole number from %d to %d" % (what, low, high))
    return value


def look_fields(msg, limits):
    """The tone, icon and text fields (limits: name -> length) a message has."""
    look = {"tone": tone_field(msg.get("tone"))}
    icon = icon_field(msg.get("icon"))
    if icon:
        look["icon"] = icon
    for key, limit in limits.items():
        value = text_field(msg.get(key), key, limit, required=False)
        if value:
            look[key] = value
    return look


def lines_field(value):
    """A widget's preformatted lines (drawn in a fixed-width font), or None."""
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_LINES:
        raise ProtocolError("lines must be a list of 1 to %d lines" % MAX_LINES)
    for line in value:
        if not isinstance(line, str) or len(line) > 120 or not line.isprintable():
            raise ProtocolError("each line must be printable text of at most 120 characters")
    return [line.rstrip() for line in value]


def parse_widget(msg, columns, rows):
    if not isinstance(msg, dict):
        raise ProtocolError("each widget is an object with an id")
    known_fields(msg, WIDGET_FIELDS)
    wid = msg.get("id")
    if not isinstance(wid, str) or not OPTION_ID.fullmatch(wid):
        raise ProtocolError('widget id must be a name like "allow" (letters, digits, . _ -, at most 32)')
    col = int_field(msg.get("col"), "col", 0, columns - 1)
    row = int_field(msg.get("row"), "row", 0, rows - 1)
    cols = int_field(msg.get("cols"), "cols", 1, columns - col, default=1)
    nrows = int_field(msg.get("rows"), "rows", 1, rows - row, default=1)
    look = look_fields(msg, {"label": 32, "text": 200, "badge": 4})
    lines = lines_field(msg.get("lines"))
    if lines:
        look["lines"] = lines
    if len(look) == 1:
        raise ProtocolError("widget %s shows nothing: give it a label, text, lines or an icon" % wid)
    touch = msg.get("touch", "tap")
    if touch not in TOUCHES:
        raise ProtocolError("touch must be one of %s" % ", ".join(TOUCHES))
    return Widget(wid, col, row, cols, nrows, look, touch)


def parse_page(client, msg):
    """A Page from a "page" message; ProtocolError if it is not one."""
    known_fields(msg, PAGE_FIELDS)
    name = msg.get("page")
    if not isinstance(name, str) or not TILE_NAME.fullmatch(name):
        raise ProtocolError('page must be a name like "agents" (lower case, at most 32)')
    look = look_fields(msg, {"title": 32, "text": 48, "badge": 4})
    columns = int_field(msg.get("columns"), "columns", 1, MAX_COLUMNS, default=4)
    rows = int_field(msg.get("rows"), "rows", 1, MAX_ROWS, default=2)
    widgets = msg.get("widgets", [])
    if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS:
        raise ProtocolError("widgets must be a list of at most %d widgets" % MAX_WIDGETS)
    clean: list[Widget] = []
    taken: set[tuple[int, int]] = set()
    for w in widgets:
        widget = parse_widget(w, columns, rows)
        if widget.id in [c.id for c in clean]:
            raise ProtocolError("widget id %s appears twice" % widget.id)
        cells = {
            (c, r)
            for c in range(widget.col, widget.col + widget.cols)
            for r in range(widget.row, widget.row + widget.rows)
        }
        if cells & taken:
            raise ProtocolError("widget %s overlaps another" % widget.id)
        taken |= cells
        clean.append(widget)
    return Page(client, name, look, columns, rows, clean)
