"""The screenpad-touchbar commands and their arguments."""

import argparse
import ast
import json
import os
import select
import shlex
import signal
import socket
import subprocess

from . import __doc__ as PACKAGE_DOC
from .codes import INPUT_EVENT
from .common import (
    ACTIVE_FLAG,
    ASK_TIMEOUT,
    CARD_HEIGHT,
    GRIP,
    MAX_OPTIONS,
    MAX_SHORTCUTS,
    PANEL_H,
    SHORTCUT,
    SOCKET_NAME,
    SYS_INPUT,
    TONES,
    UNIT,
    UUID,
    ConfigError,
    TouchbarError,
    log,
    runtime_file,
    state_path,
)
from .config import app_id, config_path, load_config
from .config_edit import add_shortcut, edit_config, migrate_config
from .daemon import Daemon
from .defaults import EXAMPLE_CONFIG
from .devices import Source, find_source
from .splitter import BAR, Splitter


def cmd_run(args):
    try:
        migrate_config(args.config)
        config, path = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    log("config: %s" % (path or "built-in default"))
    return Daemon(config, args.config, args.device, args.flag).main()


def cmd_check(args):
    try:
        config, path = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    print("%s: ok" % (path or "built-in default"))
    print(
        "bar: %d of %d px high (%.1f mm); %d px while a card shows"
        % (config.height, PANEL_H, config.height / 16, max(config.height, CARD_HEIGHT))
    )
    print("notifications: %s" % config.notifications.describe())
    for s in config.spots:
        where = "  %-8s %d  x %4d..%-4d " % (s.kind, s.index + 1, s.x, s.x + s.width)
        if s.kind == GRIP:
            print(where + "drag to resize")
        elif s.item is None:
            print(where + "empty")
        elif s.kind == SHORTCUT:
            print(where + s.item)
        else:
            b = s.item
            if b.tile:
                print(where + "%s, icon %s, tile %s (an app draws it)" % (b.label or "-", b.icon or "none", b.tile))
                continue
            action = (
                "key %s (codes %s)" % (b.key, "+".join(str(k) for k in b.keys))
                if b.keys
                else "command %s" % shlex.join(b.command)
            )
            print(where + "%s, icon %s, %s %s" % (b.label or "-", b.icon or "none", b.mode, action))
    return 0


def systemctl(*argv):
    return subprocess.run(["systemctl", "--user"] + list(argv)).returncode


def enable_extension():
    """Enable the GNOME Shell extension; a new one loads at the next login."""
    if subprocess.run(["gnome-extensions", "enable", UUID], capture_output=True).returncode == 0:
        return True
    r = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"], capture_output=True, text=True)
    if r.returncode != 0:
        log("could not read the enabled GNOME Shell extensions: %s" % r.stderr.strip())
        return False
    value = r.stdout.strip()
    if value.startswith("@as "):
        value = value[4:]
    enabled = ast.literal_eval(value)
    if UUID not in enabled:
        enabled.append(UUID)
        subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(enabled)], check=True)
    log("enabled %s; GNOME Shell loads it at your next login (log out and in once)" % UUID)
    return False


def cmd_on(args):
    path = config_path()
    migrate_config(path)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(EXAMPLE_CONFIG)
        log("wrote the default bar to %s" % path)
    try:
        load_config()
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    enable_extension()
    return systemctl("enable", "--now", UNIT)


def cmd_off(args):
    return systemctl("disable", "--now", UNIT)


def cmd_status(args):
    try:
        config, path = load_config(args.config)
        print(
            "config: %s, bar %d px, %d button(s), shortcuts: %s"
            % (path or "built-in default", config.height, len(config.buttons), ", ".join(config.shortcuts) or "none")
        )
    except (OSError, ConfigError) as e:
        print("config: %s" % e)
    source = find_source()
    if source and not os.access(source, os.R_OK):
        source += " (not readable: add your user to group input and log in again)"
    print("touchpad: %s" % (source or "not found"))
    print(
        "screenpad: %s"
        % ("on (%s)" % ACTIVE_FLAG if os.path.exists(ACTIVE_FLAG) else "off; the bar waits for 'screenpad on'")
    )
    print("uinput: %s" % ("writable" if os.access("/dev/uinput", os.W_OK) else "not writable"))
    r = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
    print("unit: %s %s" % (UNIT, r.stdout.strip() or "unknown"))
    try:
        with open(state_path()) as f:
            state = json.load(f)
        print(
            "daemon: pid %d, %s, pressed %s"
            % (state["pid"], "touchpad grabbed" if state["active"] else "idle", state["pressed"])
        )
        print("apps: %s" % (", ".join(state.get("apps") or []) or "none"))
    except (OSError, ValueError, KeyError):
        print("daemon: not running")
    return 0


def cmd_probe(args):
    """Print where touches land, without grabbing; for checking the orientation."""
    path = args.device or find_source()
    if not path:
        log("ScreenPad touchpad not found")
        return 1
    try:
        config, _ = load_config(args.config)
    except (OSError, ConfigError) as e:
        log(str(e))
        return 2
    source = Source(path)
    splitter = Splitter(source.axes(), source.nslots, lambda px, py: py < config.height)
    others = [
        p
        for p in (os.path.join("/dev/input", n) for n in os.listdir(SYS_INPUT) if n.startswith("event"))
        if p != path and _same_hid(p, path)
    ]
    other_fds = {}
    for p in others:
        try:
            other_fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p
        except OSError:
            pass
    print(
        "reading %s (%s), not grabbing; bar %d px high. Touch the panel; Ctrl+C stops."
        % (path, source.name, config.height)
    )
    seen: dict[int, tuple] = {}  # slot -> Contact as last printed
    try:
        while True:
            ready, _, _ = select.select([source.fd] + list(other_fds), [], [])
            for fd in ready:
                if fd in other_fds:
                    n = len(os.read(fd, 4096)) // INPUT_EVENT.size
                    print("  %s (same digitizer) sent %d event(s)" % (other_fds[fd], n))
                    continue
                for ev in source.read():
                    if splitter.feed(*ev) is None:
                        continue
                    now = splitter.contacts
                    for s, c in sorted(seen.items()):
                        if s not in now or now[s].id != c[0]:
                            print("slot %d lift   at panel %4.0f,%4.0f" % ((s,) + c[1]))
                            del seen[s]
                    for s, c in sorted(now.items()):
                        px, py = splitter.axes.panel(c.x, c.y)
                        if s not in seen:
                            i = config.spot_at(px, py) if c.kind == BAR else None
                            spot = config.spots[i] if i is not None else None
                            print(
                                "slot %d touch  at panel %4.0f,%4.0f (device %d,%d): %s%s"
                                % (
                                    s,
                                    px,
                                    py,
                                    c.x,
                                    c.y,
                                    c.kind,
                                    "" if spot is None else ", %s %d" % (spot.kind, spot.index + 1),
                                )
                            )
                        seen[s] = (c.id, (px, py))
    except KeyboardInterrupt:
        return 0
    finally:
        source.close()
        for fd in other_fds:
            os.close(fd)


def poke_daemon():
    """Make the running daemon reload its config now (else it does within POLL)."""
    try:
        with open(state_path()) as f:
            pid = json.load(f)["pid"]
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmdline = f.read().split(b"\0")
        if any(a.endswith((b"screenpad-touchbar", b"touchbar.py")) for a in cmdline):
            os.kill(pid, signal.SIGHUP)
    except (OSError, ValueError, KeyError, TypeError):
        pass


def cmd_shortcut(args):
    """List, add or remove the bar's shortcuts (the extension adds by drag and drop)."""
    try:
        migrate_config(args.config)
        if args.action == "list":
            config, _path = load_config(args.config)
            for i in range(MAX_SHORTCUTS):
                print("%d  %s" % (i + 1, config.shortcuts[i] if i < len(config.shortcuts) else "(empty)"))
            return 0
        if args.action == "add":
            app = app_id(args.app)
            spot = None if args.spot is None else args.spot - 1

            def change(c):
                return c.height, add_shortcut(c.shortcuts, app, spot)
        else:
            if not 1 <= args.spot <= MAX_SHORTCUTS:
                raise ConfigError("spot must be 1 to %d" % MAX_SHORTCUTS)

            def change(c):
                if args.spot > len(c.shortcuts):
                    raise ConfigError("shortcut spot %d is empty" % args.spot)
                return c.height, c.shortcuts[: args.spot - 1] + c.shortcuts[args.spot :]

        config = edit_config(change, args.config)
    except ConfigError as e:
        log(str(e))
        return 2
    poke_daemon()
    print("shortcuts: %s" % (", ".join(config.shortcuts) or "none"))
    return 0


def cmd_ask(args):
    """Ask a question in the bar; print the chosen answer's id."""
    options, tones = [], {}
    for spec in args.tone:
        key, sep, tone = spec.partition("=")
        if not sep:
            log("--tone takes ID=TONE, such as allow=accept")
            return 2
        tones[key] = tone
    for spec in args.option:
        key, sep, label = spec.partition("=")
        if not sep:
            log('--option takes ID=LABEL, such as allow="Allow once"')
            return 2
        option = {"id": key, "label": label}
        if key in tones:
            option["tone"] = tones[key]
        options.append(option)
    request = {"op": "ask", "id": "ask", "title": args.title, "options": options, "timeout": args.timeout}
    if args.text:
        request["text"] = args.text
    path = runtime_file(SOCKET_NAME)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.settimeout(args.timeout + 10)
    try:
        sock.connect(path)
        for msg in ({"op": "hello", "app": "ask"}, request):
            sock.sendall((json.dumps(msg) + "\n").encode())
        buf = b""
        while True:
            data = sock.recv(65536)
            if not data:
                log("the touch bar went away")
                return 1
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if msg.get("op") == "error":
                    log(msg.get("message", "error"))
                    return 2
                if msg.get("op") == "answer" and msg.get("id") == "ask":
                    if msg.get("option") is None:
                        log("no answer: %s" % msg.get("reason"))
                        return 1
                    print(msg["option"])
                    return 0
    except (OSError, ValueError) as e:
        log("the touch bar is not reachable at %s: %s" % (path, e))
        return 1
    finally:
        sock.close()


def _same_hid(a, b):
    def parent(p):
        return os.path.realpath(os.path.join(SYS_INPUT, os.path.basename(p), "device", "..", ".."))

    return parent(a) == parent(b)


COMMANDS = {
    "run": cmd_run,
    "on": cmd_on,
    "off": cmd_off,
    "status": cmd_status,
    "check": cmd_check,
    "probe": cmd_probe,
    "shortcut": cmd_shortcut,
    "ask": cmd_ask,
}


def parse_args(argv):
    p = argparse.ArgumentParser(prog="screenpad-touchbar", description=PACKAGE_DOC.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="the daemon (the systemd user unit runs this)")
    sub.add_parser(
        "on",
        help="write the default config if there is none, enable and start the user unit and the GNOME Shell extension",
    )
    sub.add_parser("off", help="stop and disable the user unit; the plain touchpad comes back")
    status = sub.add_parser("status", help="show the config, the touchpad and the daemon")
    check = sub.add_parser("check", help="validate the config")
    probe = sub.add_parser("probe", help="print where touches land, without grabbing")
    shortcut = sub.add_parser("shortcut", help="list, add or remove the bar's app shortcuts")
    actions = shortcut.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="the shortcut spots and their apps")
    add = actions.add_parser(
        "add", help="add an app, at a spot or in the first free one; on a full bar, an app added at a spot replaces it"
    )
    add.add_argument("app", help="desktop file id, such as org.gnome.Nautilus.desktop")
    add.add_argument("--spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    remove = actions.add_parser("remove", help="remove the shortcut in a spot")
    remove.add_argument("spot", type=int, metavar="N", help="shortcut spot 1 to %d" % MAX_SHORTCUTS)
    ask = sub.add_parser(
        "ask",
        help="ask a question in the bar and print the chosen answer's "
        "id; exits 1 without an answer (a timeout, or the bar "
        "cannot show it)",
    )
    ask.add_argument("title", help="the question, at most 80 characters")
    ask.add_argument("--text", help="a second line, at most 200 characters")
    ask.add_argument(
        "--option",
        action="append",
        default=[],
        metavar="ID=LABEL",
        required=True,
        help="an answer, up to %d: its id (printed when chosen) and label" % MAX_OPTIONS,
    )
    ask.add_argument(
        "--tone", action="append", default=[], metavar="ID=TONE", help="an answer's color: %s" % ", ".join(TONES)
    )
    ask.add_argument(
        "--timeout",
        type=float,
        default=ASK_TIMEOUT,
        metavar="SECONDS",
        help="give up after this long (default %d)" % ASK_TIMEOUT,
    )
    for s in (run, status, check, probe, shortcut):
        s.add_argument("--config", metavar="FILE", help="config file (default ~/.config/screenpad/touchbar.toml)")
    for s in (run, probe):
        s.add_argument("--device", metavar="PATH", help="touchpad evdev node (default: find it)")
    run.add_argument("--flag", default=ACTIVE_FLAG, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (OSError, TouchbarError, subprocess.CalledProcessError) as e:
        log(str(e))
        return 1
