"""Editing touchbar.toml in place (the height and the shortcuts) and migrating earlier defaults."""

import fcntl
import hashlib
import os
import re

from .common import MAX_SHORTCUTS, ConfigError, log, state_path
from .config import config_path, parse_config
from .defaults import EXAMPLE_CONFIG, OLD_BLOCKS, OLD_DEFAULTS

# The bar changes two things in the user's file: the height line and the
# [[shortcut]] blocks. It edits those lines and leaves every other line,
# comments included, as it was, then checks that the result parses to the
# old config with only that change before it replaces the file.

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


def with_shortcuts(text, apps):
    """text with its [[shortcut]] blocks replaced by these, at the end."""
    lines = text.splitlines(keepends=True)
    out, block = [], None
    for line in lines + [None]:
        header = line is not None and TABLE.fullmatch(line.rstrip("\n"))
        if block is not None and (line is None or header):
            # A block's trailing comments and blank lines introduce what
            # comes next, so they stay.
            k = len(block)
            while k > 1 and (not block[k - 1].strip() or block[k - 1].lstrip().startswith("#")):
                k -= 1
            out += block[k:]
            block = None
        if line is None:
            break
        if header and header.group(1) == "shortcut":
            block = [line]
        elif block is not None:
            block.append(line)
        else:
            out.append(line)
    text = "".join(out).rstrip()
    if text:
        text += "\n"
    for app in apps:
        text += '\n[[shortcut]]\napp = "%s"\n' % app
    return text


def _lock():
    path = os.path.join(os.path.dirname(state_path()), "config.lock")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def edit_config(change, path=None):
    """Apply change(config) -> (height, shortcuts) to the user's file.

    Writes the default first if there is no file. Returns the new Config.
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
        height, shortcuts = change(old)
        new_text = text
        if height != old.height:
            new_text = with_height(new_text, height)
        if shortcuts != old.shortcuts:
            new_text = with_shortcuts(new_text, shortcuts)
        new = parse_config(new_text)
        if (
            (new.height, new.shortcuts) != (height, shortcuts)
            or [b.public() for b in new.buttons] != [b.public() for b in old.buttons]
            or [b.action() for b in new.buttons] != [b.action() for b in old.buttons]
        ):
            raise ConfigError("%s: could not edit it safely; change it by hand" % path)
        if new_text != text or not os.path.exists(path):
            _write(path, new_text)
        return new
    finally:
        os.close(fd)


def add_shortcut(shortcuts, app, spot=None):
    """shortcuts with app added: at spot (0-based), else at the end.

    On a full bar, an app dropped on a spot replaces the one there. An app
    already on the bar moves.
    """
    apps = [a for a in shortcuts if a != app]
    if spot is None:
        if len(apps) >= MAX_SHORTCUTS:
            raise ConfigError("the bar has no free shortcut spot; remove one first")
        return apps + [app]
    if not 0 <= spot < MAX_SHORTCUTS:
        raise ConfigError("spot must be 1 to %d" % MAX_SHORTCUTS)
    if len(apps) >= MAX_SHORTCUTS:
        apps[spot] = app
        return apps
    apps.insert(min(spot, len(apps)), app)
    return apps


def migrate_config(path=None):
    """Replace a config that is still an earlier version's default."""
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    if hashlib.sha256(data).hexdigest() in OLD_DEFAULTS:
        _write(path, EXAMPLE_CONFIG)
        log("%s was an earlier default; replaced it with the current one" % path)
        return True
    text = data.decode("utf-8", "replace")
    changed = False
    for old, new in OLD_BLOCKS:
        if text.count(old) == 1:
            text = text.replace(old, new)
            changed = True
            # Updated block by block, an older default can become a later one.
            if hashlib.sha256(text.encode()).hexdigest() in OLD_DEFAULTS:
                text = EXAMPLE_CONFIG
                break
    if not changed:
        return False
    _write(path, text)
    log("%s had an earlier default button; updated it to the current one" % path)
    return True
