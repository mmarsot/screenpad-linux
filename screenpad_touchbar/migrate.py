"""Bringing an earlier version's touchbar.toml up to date.

Its default replaced, its default blocks updated, its buttons and shortcuts
turned into items.
"""

import hashlib
import re
import tomllib

from .common import ConfigError, log
from .config import config_path, parse_config, parse_data
from .config_edit import blocks, with_items, write_config
from .defaults import EXAMPLE_CONFIG, OLD_BLOCKS, OLD_DEFAULTS
from .items import Item

# The first version placed buttons with these; the spots do it since.
OLD_PLACEMENT = re.compile(r"\s*(x|width)\s*=\s*\d+\s*(#.*)?")


def _rename(line, old, new):
    return line.replace(old, new, 1)


def convert_items(text):
    """text with its [[button]] and [[shortcut]] blocks turned into [[bar]]
    items, buttons first, each block's lines kept; None when it has none.

    The first version's x and width lines go. Shortcut blocks that all come
    after the last button are renamed where they are; otherwise they go,
    and the same apps are added after the last button. Raises ConfigError
    when the result would not be the same bar.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    if "button" not in data and "shortcut" not in data:
        return None
    buttons, shortcuts = data.pop("button", []), data.pop("shortcut", [])
    if "bar" in data or not isinstance(buttons, list) or not isinstance(shortcuts, list):
        raise ConfigError("it has [[bar]] blocks and an earlier version's [[button]] or [[shortcut]] blocks")
    data["bar"] = [
        {k: v for k, v in b.items() if k not in ("x", "width")} if isinstance(b, dict) else b for b in buttons
    ] + shortcuts
    expected = parse_data(data)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    old_buttons, old_shortcuts = blocks(lines, "button"), blocks(lines, "shortcut")
    if (len(old_buttons), len(old_shortcuts)) != (len(buttons), len(shortcuts)):
        raise ConfigError("its [[button]] and [[shortcut]] blocks are not all on lines of their own")
    in_order = not old_buttons or not old_shortcuts or old_shortcuts[0][1] > old_buttons[-1][1]
    drop: set[int] = set()
    for _lead, start, end in old_buttons:
        lines[start] = _rename(lines[start], "button", "bar")
        drop.update(k for k in range(start + 1, end) if OLD_PLACEMENT.fullmatch(lines[k].rstrip("\n")))
    for _lead, start, end in old_shortcuts:
        if in_order:
            lines[start] = _rename(lines[start], "shortcut", "bar")
        else:
            drop.update(range(start, end))
    out = "".join(line for k, line in enumerate(lines) if k not in drop)
    if not in_order:
        bar = parse_config(out).bar
        out = with_items(out, "bar", bar, bar + [Item.app(i.name) for i in expected.bar[len(bar) :]])
    if parse_config(out).spec() != expected.spec():
        raise ConfigError("the converted file would not be the same bar")
    return out


def migrate_config(path=None):
    """Bring a config from an earlier version up to date: one still that
    version's default is replaced by the current default; otherwise the
    earlier default blocks it still has, word for word, are updated, and
    its buttons and shortcuts become [[bar]] items. True if it changed."""
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    if hashlib.sha256(data).hexdigest() in OLD_DEFAULTS:
        write_config(path, EXAMPLE_CONFIG)
        log("%s was an earlier default; replaced it with the current one" % path)
        return True
    text = original = data.decode("utf-8", "replace")
    for old, new in OLD_BLOCKS:
        if text.count(old) == 1:
            text = text.replace(old, new)
            # Updated block by block, an older default can become a later one.
            if hashlib.sha256(text.encode()).hexdigest() in OLD_DEFAULTS:
                text = EXAMPLE_CONFIG
                break
    updated = text != original
    try:
        converted = convert_items(text)
    except ConfigError as e:
        # Left as it is: the daemon says what to change.
        log("%s: could not turn its [[button]] and [[shortcut]] blocks into [[bar]] items: %s" % (path, e))
        return False
    if converted is not None:
        text = converted
    if text == original:
        return False
    write_config(path, text)
    if updated:
        log("%s had an earlier default block; updated it to the current one" % path)
    if converted is not None:
        log("%s: its [[button]] and [[shortcut]] blocks are [[bar]] items now" % path)
    return True
