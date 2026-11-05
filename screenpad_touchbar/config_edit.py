"""Editing touchbar.toml in place: the height, the items added and removed, items moved between the two lists."""

import fcntl
import json
import os
import re

from .common import APP, BAR_ITEMS, COMMAND, KEY, ConfigError, state_path
from .config import Config, config_path, parse_config
from .defaults import EXAMPLE_CONFIG
from .items import Item

# The bar changes four things in the user's file: the height line, the
# apps among the items (it adds, moves and removes app items), keys and
# commands it adds (the picker's, picker.py), items of any kind removed
# (remove_item(): an item's block goes), and items of any kind moved from
# the bar to the apps page or back (their block goes from one list to the
# other as it is). It edits
# those lines and leaves every other line, comments included, as it was,
# then checks that the result parses to the old config with only that
# change before it replaces the file.

TABLE = re.compile(r"\s*\[\[?\s*([A-Za-z0-9_.\"' -]+?)\s*\]\]?\s*(#.*)?")
HEIGHT_LINE = re.compile(r"\s*height\s*=.*")


def with_height(text, height):
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if TABLE.fullmatch(line.rstrip("\n")):
            break
        if HEIGHT_LINE.fullmatch(line.rstrip("\n")):
            lines[i] = "height = %d\n" % height
            return "".join(lines)
    else:
        i = len(lines)
    lines.insert(i, "height = %d\n\n" % height)
    return "".join(lines)


def _comment_or_blank(line):
    return not line.strip() or line.lstrip().startswith("#")


def blocks(lines, name):
    """The [[name]] blocks of a file's lines, in order, as (lead, start,
    end): start is the header's line and end the line after its last
    setting, so the comments and blank lines after a block are left to what
    comes next; lead is where the comment lines right above its header
    begin, which belong to it."""
    tables = [(k, TABLE.fullmatch(line.rstrip("\n"))) for k, line in enumerate(lines)]
    headers = [(k, m.group(1)) for k, m in tables if m]
    out = []
    for n, (h, table) in enumerate(headers):
        if table != name or not lines[h].lstrip().startswith("[["):
            continue
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        while end > h + 1 and _comment_or_blank(lines[end - 1]):
            end -= 1
        lead = h
        while lead > 0 and lines[lead - 1].lstrip().startswith("#"):
            lead -= 1
        out.append((lead, h, end))
    return out


def _toml(value):
    # A JSON string is a TOML basic string: the same quotes and escapes.
    return json.dumps(value)


def app_block(name, item):
    """The lines the bar writes for an item it adds: an app, a key or a command."""
    lines = ["[[%s]]\n" % name]
    if item.kind == APP:
        lines.append("app = %s\n" % _toml(item.name))
    elif item.kind == KEY:
        lines.append("key = %s\n" % _toml(item.key))
    elif item.kind == COMMAND:
        # A string for sh as it was given, else the list of arguments (a JSON
        # list of strings is a TOML array).
        sh = item.command[:2] == ["/bin/sh", "-c"] and len(item.command) == 3
        lines.append("command = %s\n" % _toml(item.command[2] if sh else item.command))
    else:
        raise ConfigError(ONLY)
    if item.mode and item.mode != "tap":
        lines.append("mode = %s\n" % _toml(item.mode))
    if item.icon:
        lines.append("icon = %s\n" % _toml(item.icon))
    if item.label:
        lines.append("label = %s\n" % _toml(item.label))
    return lines


def retitled(lines, name):
    """A block's lines (its header first) as a [[name]] block."""
    m = TABLE.fullmatch(lines[0].rstrip("\n"))
    assert m is not None  # blocks() found it as a header
    return [lines[0][: m.start(1)] + name + lines[0][m.end(1) :]] + lines[1:]


ONLY = "the bar only adds and removes items, adds keys and commands, and moves items between the bar and the apps page"


def with_items(text, name, old, new, carried=None, added=(), removed=()):
    """text with its [[name]] items changed from old to new (lists of
    items.Item, old those text has).

    carried maps id() of the items moving between the two lists to the
    lines of their block where they come from (its header to its last
    setting, as blocks() finds it). The items new keeps from old (the same
    objects) keep their blocks as they are, and stay in the same order;
    the others in new must be apps, carried, or keys and commands whose
    id() is in added (new items, in neither list before), and get a block
    (one written anew, a carried one's lines under a [[name]] header), before
    the next item kept or after the last block; the ones new drops must
    be apps, carried or in removed (id()s of items taken away of any kind),
    and lose their block (the comments above it stay). Raises ConfigError
    for any other change."""
    carried = carried or {}
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    found = blocks(lines, name)
    if len(found) != len(old):
        raise ConfigError("its [[%s]] blocks are not all on lines of their own" % name)
    where = [next((i for i, o in enumerate(old) if o is x), None) for x in new]
    kept = [i for i in where if i is not None]
    if kept != sorted(set(kept)):
        raise ConfigError(ONLY)
    for i, o in enumerate(old):
        if i not in kept and o.kind != APP and id(o) not in carried and id(o) not in removed:
            raise ConfigError(ONLY)

    def block(item):
        if id(item) in carried:
            return retitled(carried[id(item)], name)
        if item.kind != APP and id(item) not in added:
            raise ConfigError(ONLY)
        return app_block(name, item)

    inserts: dict[int, list[str]] = {}
    pending: list[Item] = []
    for x, w in zip(new, where, strict=True):
        if w is None:
            pending.append(x)
        elif pending:
            at = inserts.setdefault(found[w][0], [])
            for p in pending:
                at += block(p) + ["\n"]
            pending = []
    if pending:
        at_line = found[-1][2] if found else len(lines)
        at = inserts.setdefault(at_line, [])
        for n, p in enumerate(pending):
            # After the last block, or at the end of the file, a blank line first.
            if found or n or (lines and lines[-1].strip()):
                at.append("\n")
            at += block(p)
    deleted: set[int] = set()
    for i, (_lead, start, end) in enumerate(found):
        if i in kept:
            continue
        deleted.update(range(start, end))
        # The blank line before it goes too when a blank line or the end of
        # the file follows it, so blank lines do not pile up.
        if start > 0 and not lines[start - 1].strip() and (end >= len(lines) or not lines[end].strip()):
            deleted.add(start - 1)
    out = []
    for k in range(len(lines) + 1):
        out += inserts.get(k, [])
        if k < len(lines) and k not in deleted:
            out.append(lines[k])
    return "".join(out)


def _lock():
    path = os.path.join(os.path.dirname(state_path()), "config.lock")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def write_config(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def edit_config(change, path=None):
    """Apply change(config) to the user's file and return the new Config.

    change returns a dict of what changes: "height", and "bar" or "apps",
    the new list of that place's items (as with_items() takes it); an item
    of one list (the same object) in the other's new list moves there,
    with its block. Writes the default first if there is no file.
    """
    path = path or config_path()
    fd = _lock()
    try:
        try:
            with open(path) as f:
                text = f.read()
        except FileNotFoundError:
            text = EXAMPLE_CONFIG
        try:
            old = parse_config(text)
        except ConfigError as e:
            raise ConfigError("%s: fix this first: %s" % (path, e))
        target = change(old)
        height = target.get("height", old.height)
        lists = {name: target.get(name, old.items(name)) for name in ("bar", "apps")}
        new_text = text
        if height != old.height:
            new_text = with_height(new_text, height)
        try:
            placed = [id(i) for items in lists.values() for i in items]
            if len(placed) != len(set(placed)):
                raise ConfigError("an item cannot be in two places")
            carried = _carried(text, old, lists)
            before = {id(i) for i in old.bar + old.apps}
            added = set(placed) - before  # in neither list before: new items
            removed = before - set(placed)  # in neither list now: taken away
            for name, items in lists.items():
                if [id(i) for i in items] != [id(i) for i in old.items(name)]:
                    new_text = with_items(new_text, name, old.items(name), items, carried, added, removed)
            new = parse_config(new_text)
        except ConfigError as e:
            raise ConfigError("%s: could not edit it safely (%s); change it by hand" % (path, e))
        if new.spec() != Config(height, lists["bar"], lists["apps"], old.notifications, old.pages).spec():
            raise ConfigError("%s: could not edit it safely; change it by hand" % path)
        if new_text != text or not os.path.exists(path):
            write_config(path, new_text)
        return new
    finally:
        os.close(fd)


def _carried(text, old, lists):
    """The block lines, in text, of the items of old (a Config) that lists
    puts in the other list, by id(): what with_items() carries."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    carried = {}
    for name, other in (("bar", "apps"), ("apps", "bar")):
        moving = {id(i) for i in lists[other]}
        found = blocks(lines, name)
        if len(found) == len(old.items(name)):
            for item, (_lead, start, end) in zip(old.items(name), found, strict=True):
                if id(item) in moving:
                    carried[id(item)] = lines[start:end]
    return carried


def add_app(items, app, spot=None, limit=BAR_ITEMS):
    """items with app added: at spot (0-based), else at the end.

    An app already there moves (with its icon and label). Items after the
    spot move along; when the place is full, an app added at an app's spot
    replaces that one, and there is no room otherwise.
    """
    old = next((i for i in items if i.kind == APP and i.name == app), None)
    item = Item.app(app, old.label, old.icon) if old else Item.app(app)
    rest = [i for i in items if i is not old]
    if spot is None:
        if len(rest) >= limit:
            raise ConfigError("there is no free spot; remove an item first")
        out = rest + [item]
    elif not 0 <= spot < limit:
        raise ConfigError("spot must be 1 to %d" % limit)
    elif len(rest) >= limit:
        if rest[spot].kind != APP:
            raise ConfigError("spot %d holds %s, not an app; change it in the file" % (spot + 1, rest[spot].describe()))
        out = rest[:spot] + [item] + rest[spot + 1 :]
    else:
        out = rest[:spot] + [item] + rest[spot:]
    # Put back where it was: nothing to write.
    return items if [i.spec() for i in out] == [i.spec() for i in items] else out


def append_item(items, item, limit):
    """items with item added at the end: the picker's choice, or a key or
    command added from the command line."""
    if len(items) >= limit:
        raise ConfigError("there is no free spot; remove an item first")
    if item.kind == APP and any(i.kind == APP and i.name == item.name for i in items):
        raise ConfigError("%s is already there" % item.name)
    return items + [item]


def remove_item(items, index, item=None):
    """items without the item at index (0-based), of any kind.

    item, if given, is what the finger or the caller saw there: the file
    may have changed since, and a different item is never removed in its
    place."""
    if not 0 <= index < len(items):
        raise ConfigError("spot %d is empty" % (index + 1))
    if item is not None and items[index].spec() != item.spec():
        raise ConfigError("the items changed since; try again")
    return items[:index] + items[index + 1 :]
