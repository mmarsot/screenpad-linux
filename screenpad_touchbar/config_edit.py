"""Editing touchbar.toml in place: the height, and the apps among the bar's and the apps page's items."""

import fcntl
import json
import os
import re

from .common import APP, BAR_ITEMS, ConfigError, state_path
from .config import Config, config_path, parse_config
from .defaults import EXAMPLE_CONFIG
from .items import Item

# The bar changes two things in the user's file: the height line, and the
# apps among the items (it adds, moves and removes app items, never any
# other). It edits those lines and leaves every other line, comments
# included, as it was, then checks that the result parses to the old config
# with only that change before it replaces the file.

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
    """The lines the bar writes for an app item."""
    lines = ["[[%s]]\n" % name, "app = %s\n" % _toml(item.name)]
    if item.icon:
        lines.append("icon = %s\n" % _toml(item.icon))
    if item.label:
        lines.append("label = %s\n" % _toml(item.label))
    return lines


def with_items(text, name, old, new):
    """text with its [[name]] items changed from old to new (lists of
    items.Item, old those text has).

    The items new keeps from old (the same objects) keep their blocks as
    they are, and stay in the same order; the others in new must be apps,
    and get a block, before the next item kept or after the last block;
    the ones new drops must be apps too, and lose their block. Raises
    ConfigError for any other change."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    found = blocks(lines, name)
    if len(found) != len(old):
        raise ConfigError("its [[%s]] blocks are not all on lines of their own" % name)
    where = [next((i for i, o in enumerate(old) if o is x), None) for x in new]
    kept = [i for i in where if i is not None]
    if kept != sorted(set(kept)):
        raise ConfigError("the bar only adds, moves and removes apps")
    for i, o in enumerate(old):
        if i not in kept and o.kind != APP:
            raise ConfigError("the bar only adds, moves and removes apps")
    inserts: dict[int, list[str]] = {}
    pending: list[Item] = []
    for x, w in zip(new, where, strict=True):
        if w is None:
            if x.kind != APP:
                raise ConfigError("the bar only adds, moves and removes apps")
            pending.append(x)
        elif pending:
            at = inserts.setdefault(found[w][0], [])
            for p in pending:
                at += app_block(name, p) + ["\n"]
            pending = []
    if pending:
        at_line = found[-1][2] if found else len(lines)
        at = inserts.setdefault(at_line, [])
        for n, p in enumerate(pending):
            # After the last block, or at the end of the file, a blank line first.
            if found or n or (lines and lines[-1].strip()):
                at.append("\n")
            at += app_block(name, p)
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
    the new list of that place's items (as with_items() takes it). Writes
    the default first if there is no file.
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
            for name, items in lists.items():
                if [id(i) for i in items] != [id(i) for i in old.items(name)]:
                    new_text = with_items(new_text, name, old.items(name), items)
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


def remove_app(items, index):
    """items without the app at index (0-based)."""
    if not 0 <= index < len(items):
        raise ConfigError("spot %d is empty" % (index + 1))
    if items[index].kind != APP:
        raise ConfigError("spot %d holds %s, not an app; change it in the file" % (index + 1, items[index].describe()))
    return items[:index] + items[index + 1 :]
