"""The grub command: the drm.edid_firmware boot parameter in /etc/default/grub."""

import os
import re

from .common import (
    EDID_FIRMWARE_PARAM,
    FIRMWARE_NAME,
    GRUB_BACKUP,
    GRUB_DEFAULT,
    GRUB_DEFAULT_D,
    GRUB_VAR,
    ScreenpadError,
    log,
)
from .discovery import discover
from .lockdown import boot_param, firmware_path
from .system import check_dmi, read_text, require_root, write_file

GRUB_LINE = re.compile(r"""^(\s*%s=)(["']?)(.*?)\2(\s*(?:#.*)?)$""" % GRUB_VAR)


def grub_assignments(lines):
    """[(index, match)] for each GRUB_CMDLINE_LINUX_DEFAULT assignment line."""
    found = []
    for i, line in enumerate(lines):
        m = GRUB_LINE.match(line.rstrip("\n"))
        if m:
            found.append((i, m))
    return found


def grub_set_value(lines, i, m, value):
    quote = m.group(2) or '"'
    end = "\n" if lines[i].endswith("\n") else ""
    lines[i] = "%s%s%s%s%s%s" % (m.group(1), quote, value, quote, m.group(4), end)


def grub_add(text, token):
    """/etc/default/grub text with token added to GRUB_CMDLINE_LINUX_DEFAULT."""
    lines = text.splitlines(keepends=True)
    found = grub_assignments(lines)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + '%s="%s"\n' % (GRUB_VAR, token)
    i, m = found[-1]  # the shell keeps the last assignment
    words = m.group(3).split()
    if token in words:
        return text
    name = token.split("=", 1)[0] + "="
    other = [w for w in words if w.startswith(name)]
    if other:
        raise ScreenpadError("%s already sets %s; edit it by hand" % (GRUB_VAR, " ".join(other)))
    value = m.group(3).rstrip()
    grub_set_value(lines, i, m, value + " " + token if value else token)
    return "".join(lines)


# Ours for any connector name: the name can change between boots.
GRUB_TOKEN = re.compile(r"%s=[^\s:]*:%s" % (re.escape(EDID_FIRMWARE_PARAM), re.escape(FIRMWARE_NAME)))


def grub_has_ours(text):
    return any(GRUB_TOKEN.fullmatch(w) for _, m in grub_assignments(text.splitlines()) for w in m.group(3).split())


def grub_remove(text):
    """/etc/default/grub text with our drm.edid_firmware word removed from GRUB_CMDLINE_LINUX_DEFAULT."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(r"\s*(?<!\S)%s(?!\S)" % GRUB_TOKEN.pattern)
    for i, m in grub_assignments(lines):
        value = m.group(3)
        new = pattern.sub("", value)
        if new == value:
            continue
        if not value[:1].isspace():
            new = new.lstrip()
        grub_set_value(lines, i, m, new)
    return "".join(lines)


def grub_overrides(env):
    """grub.d files that replace GRUB_CMDLINE_LINUX_DEFAULT instead of extending it."""
    d = env.path(GRUB_DEFAULT_D)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".cfg"))
    except OSError:
        return []
    found = []
    for name in names:
        for line in (read_text(os.path.join(d, name)) or "").splitlines():
            if re.match(r"\s*%s=" % GRUB_VAR, line) and not re.search(r"\$\{?%s\b" % GRUB_VAR, line):
                found.append(os.path.join(d, name))
                break
    return found


def grub_connector(env, args):
    if args.connector:
        return re.sub(r"^card\d+-", "", args.connector)
    target = discover(env)
    if not target:
        raise ScreenpadError("no nvidia-drm HDMI connector found; name it with --connector HDMI-A-2")
    return target.short_connector


def changed_line(old, new):
    """The first line of new that differs from old (grub_add/grub_remove change one)."""
    old_lines = old.splitlines()
    for i, line in enumerate(new.splitlines()):
        if i >= len(old_lines) or line != old_lines[i]:
            return line
    return None


def cmd_grub(env, args):
    check_dmi(env)
    token = boot_param(grub_connector(env, args))
    path = env.path(GRUB_DEFAULT)
    with open(path) as f:
        text = f.read()
    present = grub_has_ours(text)

    if args.action == "show":
        found = grub_assignments(text.splitlines())
        print("parameter:     %s" % token)
        print("file:          %s" % path)
        print("now:           %s" % (found[-1][1].group(0) if found else "(no %s)" % GRUB_VAR))
        if present:
            print("after remove:  %s" % changed_line(text, grub_remove(text)))
        else:
            try:
                print("after apply:   %s" % changed_line(text, grub_add(text, token)))
            except ScreenpadError as e:
                print("cannot apply:  %s" % e)
        print("apply:         sudo screenpad grub apply   (edits that line, runs update-grub)")
        print("remove:        sudo screenpad grub remove  (deletes exactly that parameter, runs update-grub)")
        print("Either one takes effect at the next boot.")
        for f in grub_overrides(env):
            print("warning: %s sets %s itself and may override this" % (f, GRUB_VAR))
        return 0

    require_root(env)
    if args.action == "apply":
        if not os.path.exists(firmware_path(env)):
            raise ScreenpadError(
                "%s missing, so the kernel would have nothing to load; "
                "run capture-edid or install.sh first" % firmware_path(env)
            )
        new = grub_add(text, token)
        if new == text:
            log("%s already has %s" % (path, token))
            return 0
        backup = env.path(GRUB_BACKUP)
        if not os.path.exists(backup):
            write_file(backup, text.encode())
            log("saved a copy of %s as %s" % (path, backup))
    else:
        new = grub_remove(text)
        if new == text:
            log("%s has no %s=…:%s; nothing to remove" % (path, EDID_FIRMWARE_PARAM, FIRMWARE_NAME))
            return 0
    write_file(path, new.encode(), mode=os.stat(path).st_mode & 0o7777)
    log("%s: %s" % (path, changed_line(text, new)))
    for f in grub_overrides(env):
        log("warning: %s sets %s itself and may override this" % (f, GRUB_VAR))
    env.run(["update-grub"])
    log("reboot for it to take effect; 'screenpad status' then shows the boot parameter")
    return 0
