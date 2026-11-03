"""The daemon: grabbing the touchpad, the event loop, routing contacts, publishing the scene, reloading the config."""

import json
import os
import select
import shlex
import signal
import subprocess
import time

from .apps import Apps, AppServer
from .bar import Bar
from .codes import ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_SLOT
from .common import (
    ACTIVE_FLAG,
    ASKS_PROTOCOL,
    CARD_HEIGHT,
    DOTS,
    GRIP,
    HOME,
    LAUNCH,
    LIFT_WAIT,
    PAGES_PROTOCOL,
    PANEL_H,
    PANEL_W,
    POLL,
    RENDERER_NAME,
    SCENE_VERSION,
    SOCKET_NAME,
    ConfigError,
    TouchbarError,
    log,
    monitor_identity,
    runtime_file,
    state_path,
)
from .config import config_path, load_config
from .config_edit import edit_config
from .devices import Source, create_keys, create_pad, find_source, uinput_destroy, write_events
from .keys import KeyEmitter
from .pager import Pager, Surface
from .scene import DIVIDER_X, ask_tiles, spot_tiles
from .splitter import BAR, PAD, RESYNC, SURFACE, Splitter


class Daemon:
    def __init__(self, config, config_file, device=None, flag=ACTIVE_FLAG):
        self.config = config
        self.config_file = config_file
        self.config_mtime = self._mtime()
        self.device = device
        self.flag = flag
        self.source: Source | None = None
        self.pad: int | None = None
        self.keys_fd: int | None = None
        self.splitter: Splitter | None = None
        self.bar: Bar | None = None
        self.children = []
        self.stop = False
        self.reload = False
        self.published = None
        self.monitor = monitor_identity()
        self.wakeup: int | None = None
        self.apps = Apps()
        self.apps.set_placed(config.tiles())
        self.apps.on_tile_gone = self._tile_gone
        # Questions show only while the bar is on screen, drawn by an
        # extension that knows them; otherwise whoever asked hears
        # "unavailable".
        self.apps.asks.can_show = lambda: self.grabbed() and self.renderer_ready()
        # The bar grows for a card only when no touchpad finger is in the
        # band it would cover (or after GROW_WAIT).
        self.apps.asks.band_clear = lambda: (
            not (self.splitter and self.splitter.pad_in_band(self.config.height, CARD_HEIGHT))
        )
        self.apps.notify_policy = config.notifications
        # The apps page shows only while the bar is on screen, drawn by an
        # extension that knows the pages.
        self.pager = Pager(
            enabled=config.pages.pager,
            return_after=config.pages.return_after,
            can_show=lambda: self.grabbed() and self.renderer_protocol() >= PAGES_PROTOCOL,
        )
        self.surface = Surface()
        self.dots_shown = False
        self.server = None
        # renderer.json's mtime, and the protocol it says it draws while
        # the bar is on screen (0 when it is not)
        self.renderer: tuple[int | None, int] = (None, 0)

    def _mtime(self):
        p = self.config_file or config_path()
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    def _tile_gone(self, name):
        if self.bar:
            self.bar.tile_gone(name)

    def grabbed(self):
        return self.source is not None and self.source.grabbed

    def renderer_protocol(self):
        """The protocol the extension draws while it has the bar on screen
        (renderer.json), else 0."""
        path = runtime_file(RENDERER_NAME)
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            self.renderer = (None, 0)
            return 0
        if self.renderer[0] != mtime:
            try:
                with open(path) as f:
                    said = json.load(f)
                protocol = said.get("protocol")
                if not (isinstance(protocol, int) and not isinstance(protocol, bool) and said.get("drawn") is True):
                    protocol = 0
            except (OSError, ValueError, AttributeError):
                protocol = 0
            self.renderer = (mtime, max(protocol, 0))
        return self.renderer[1]

    def renderer_ready(self):
        """Whether the extension draws questions and has the bar on screen."""
        return self.renderer_protocol() >= ASKS_PROTOCOL

    def scene(self):
        """The tiles to draw: a question over the spots while one shows."""
        pressed = self.bar.pressed() if self.bar else []
        removing = self.bar.removing() if self.bar else []
        dots = self.pager.dots() if self.pager.available() else None
        tiles = spot_tiles(self.config, pressed, removing, self.apps.provided(), dots)
        ask = self.apps.asks.shown()
        if ask is not None:
            tiles = ask_tiles(ask, self.bar.ask_pressed(ask) if self.bar else (), len(self.apps.asks.queue) - 1) + [
                t for t in tiles if t["style"] in (GRIP, DOTS)
            ]
        return tiles

    # The state file the GNOME Shell extension draws from.
    def publish(self):
        state = {
            "version": SCENE_VERSION,
            "pid": os.getpid(),
            "active": self.source is not None and self.source.grabbed,
            "monitor": self.monitor,
            "panel": [PANEL_W, PANEL_H],
            # Drawn: CARD_HEIGHT while a card shows. The strut (the work
            # area windows avoid) is the bar's own height: a card covers
            # windows for a moment rather than resizing them.
            "height": self.bar.height() if self.bar else self.config.height,
            "strut": self.bar.base_height() if self.bar else self.config.height,
            "tiles": self.scene(),
            "divider": DIVIDER_X,
            "apps": self.apps.names(),
            # The page shown, black below the bar on the touchpad page (and
            # whether with a clock), and what the apps page shows there.
            "page": self.pager.name(),
            "screensaver": {"clock": self.config.pages.clock} if self.config.pages.screensaver else None,
            "surface": self.pager.surface(),
            # Version 1: the extension loaded before tiles keeps drawing the
            # bar from these until the next login (GNOME loads new extension
            # code only then). It shows no app tiles and no questions.
            "spots": [s.public() for s in self.config.spots if s.kind != DOTS],
            "pressed": self.bar.pressed() if self.bar else [],
            "removing": self.bar.removing() if self.bar else [],
        }
        if state == self.published:
            return
        path = state_path()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        self.published = state

    def unpublish(self):
        try:
            os.unlink(state_path())
        except FileNotFoundError:
            pass

    def run_command(self, argv):
        try:
            self.children.append(subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True))
        except OSError as e:
            log("%s: %s" % (shlex.join(argv), e))

    def launch(self, app):
        self.run_command(LAUNCH + [app])

    def edit(self, change, what):
        try:
            edit_config(change, self.config_file)
        except (OSError, ConfigError) as e:
            log("could not %s: %s" % (what, e))
            return False
        self.reload = True
        return True

    def remove_shortcut(self, index):
        def change(c):
            return c.height, c.shortcuts[:index] + c.shortcuts[index + 1 :]

        return self.edit(change, "remove the shortcut")

    def resize(self, height):
        return self.edit(lambda c: (height, c.shortcuts), "resize the bar")

    def new_bar(self, keys):
        return Bar(
            self.config,
            keys,
            self.run_command,
            self.launch,
            self.remove_shortcut,
            self.resize,
            tiles=self.apps,
            asks=self.apps.asks,
            pager=self.pager,
        )

    def reap(self):
        self.children = [c for c in self.children if c.poll() is None]

    def want_active(self):
        return os.path.exists(self.flag)

    def activate(self):
        """True once grabbed; False while a finger is down; None if there is no pad."""
        path = self.device or find_source()
        if not path:
            return None
        source = Source(path)
        try:
            # Grab only between touches, so the desktop never keeps a touch
            # whose end it will not see.
            if source.touching():
                source.close()
                return False
            self.pad = create_pad(source)
            self.keys_fd = create_keys()
            source.grab(True)
        except BaseException:
            source.close()
            self.destroy_uinput()
            raise
        self.source = source
        x, y = source.abs[ABS_MT_POSITION_X], source.abs[ABS_MT_POSITION_Y]
        # New contacts take the zone of now (the bar's height, taller while
        # a card shows, and the page); contacts already down keep their kind.
        self.splitter = Splitter(source.axes(), source.nslots, self._zone, source.abs[ABS_MT_SLOT][0])
        self.bar = self.new_bar(KeyEmitter(lambda ev: write_events(self.keys_fd, ev)))
        log(
            "grabbed %s (%s), %d slots, x 0..%d, y 0..%d, %d units/mm; bar %d px high, "
            "%d button(s), %d shortcut(s)"
            % (
                path,
                source.name,
                source.nslots,
                x[2],
                y[2],
                x[5],
                self.config.height,
                len(self.config.buttons),
                len(self.config.shortcuts),
            )
        )
        return True

    def _zone(self, px, py):
        """What a new contact is: the bar's above its height, else the
        touchpad's on the touchpad page and the surface's on the apps page."""
        assert self.bar is not None  # made right after the splitter, before any event
        if py < self.bar.touch_height():
            return BAR
        return PAD if self.pager.page == HOME else SURFACE

    def destroy_uinput(self):
        for attr in ("pad", "keys_fd"):
            fd = getattr(self, attr)
            setattr(self, attr, None)
            if fd is not None:
                try:
                    uinput_destroy(fd)
                except OSError as e:
                    log("destroying a uinput device: %s" % e)

    def deactivate(self, why, wait=True):
        source = self.source
        if source is None:
            return
        assert self.splitter is not None and self.bar is not None
        try:
            pad, bar, surface = self.splitter.release_all()
            self.bar.handle(bar)
            self.bar.release_all()
            self.surface.handle(surface)
            write_events(self.pad, pad)
        except OSError as e:
            log("releasing: %s" % e)
        deadline = time.monotonic() + (LIFT_WAIT if wait else 0)
        try:
            while source.grabbed and source.touching() and time.monotonic() < deadline:
                time.sleep(0.05)
                source.read()
            source.grab(False)
        except OSError:
            pass
        source.close()
        self.source = self.splitter = self.bar = None
        self.pager.reset()
        self.destroy_uinput()
        log("released the touchpad: %s" % why)

    def process(self):
        assert self.source is not None and self.splitter is not None and self.bar is not None
        try:
            events = self.source.read()
        except OSError as e:
            self.deactivate("%s: %s" % (self.source.path, e), wait=False)
            return
        changed = False
        for ev in events:
            out = self.splitter.feed(*ev)
            if out is None:
                continue
            if out == RESYNC:
                out = self.splitter.resync(*self.source.state())
            changed |= self.route(*out)
        changed |= self.pager.take_changed()
        if changed and self.bar:
            self.publish()

    def route(self, pad, bar, surface):
        """One frame's contacts: the touchpad's to the virtual touchpad, the
        bar's through the pager (which takes swipes) to the bar, the apps
        surface's to it. True if the bar changed."""
        assert self.splitter is not None and self.bar is not None
        write_events(self.pad, pad)
        changed = self.bar.handle(self.pager.watch(bar))
        changed |= self.surface.handle(surface)
        self.pager.touching(bool(self.splitter.contacts))
        return changed

    def check_config(self):
        mtime = self._mtime()
        if not self.reload and mtime == self.config_mtime:
            return
        self.reload = False
        self.config_mtime = mtime
        try:
            config, path = load_config(self.config_file)
        except (OSError, ConfigError) as e:
            log("keeping the current bar: %s" % e)
            return
        self.config = config
        self.apps.set_placed(config.tiles())
        self.apps.notify_policy = config.notifications
        self.pager.configure(config.pages)
        if self.bar:
            self.bar.set_config(config)
        log(
            "loaded %s: bar %d px high, %d button(s), %d shortcut(s)"
            % (path or "the built-in default", config.height, len(config.buttons), len(config.shortcuts))
        )
        self.publish()

    def loop(self):
        self.publish()
        retry_at = 0.0
        while not self.stop:
            self.check_config()
            self.reap()
            timeout = POLL
            if self.source is None and self.want_active() and time.monotonic() >= retry_at:
                try:
                    taken = self.activate()
                except OSError as e:
                    log("cannot take the touchpad: %s" % e)
                    retry_at = time.monotonic() + 5
                    taken = None
                if taken:
                    self.publish()
                elif taken is False:
                    timeout = 0.2  # a finger is down, or the pad is not there yet
            elif self.source is not None and not self.want_active():
                self.deactivate("%s is gone (screenpad off)" % self.flag)
                self.publish()
            self.apps.asks.refresh()
            self.apps.asks.tick()
            # Back to the touchpad page after a while without a touch, and
            # the dots shown or hidden as the extension comes and goes.
            if self.pager.tick() or self.pager.available() != self.dots_shown:
                self.dots_shown = self.pager.available()
                self.publish()
            for deadline in (
                self.bar.deadline() if self.bar else None,
                self.apps.asks.deadline(),
                self.pager.deadline(),
            ):
                if deadline is not None:
                    timeout = min(timeout, max(deadline - time.monotonic(), 0))
            if self.apps.take_changed():
                self.publish()
            if self.server:
                self.server.flush()
            fds = (
                ([self.source.fd] if self.source else [])
                + ([self.wakeup] if self.wakeup is not None else [])
                + (self.server.rfds() if self.server else [])
            )
            ready, _, _ = select.select(fds, self.server.wfds() if self.server else [], [], timeout)
            if self.wakeup is not None and self.wakeup in ready:
                try:
                    os.read(self.wakeup, 64)
                except BlockingIOError:
                    pass
            if self.source and self.source.fd in ready:
                self.process()
                if self.source is None:
                    self.publish()
            for fd in ready:
                if self.server and self.server.owns(fd):
                    self.server.readable(fd)
            if self.bar and self.bar.tick():
                self.publish()
            if self.apps.take_changed():
                self.publish()
            if self.server:
                self.server.flush()

    def main(self):
        def on_term(signum, frame):
            self.stop = True

        def on_hup(signum, frame):
            self.reload = True

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        signal.signal(signal.SIGHUP, on_hup)
        # Signals wake the select() at once: a reload ('screenpad-touchbar
        # shortcut' sends SIGHUP) shows on the bar without waiting for POLL.
        r, w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        signal.set_wakeup_fd(w)
        self.wakeup = r
        try:
            self.server = AppServer(runtime_file(SOCKET_NAME), self.apps)
        except (OSError, TouchbarError) as e:
            log("apps cannot use the bar: %s" % e)
        try:
            self.loop()
        finally:
            self.deactivate("stopping")
            if self.server:
                self.server.close()
            self.unpublish()
        return 0
