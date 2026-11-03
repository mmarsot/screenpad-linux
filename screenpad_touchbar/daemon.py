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
    GRIP,
    HOME,
    LAUNCH,
    LIFT_WAIT,
    PAGE,
    PAGES_PROTOCOL,
    PANEL_H,
    PANEL_W,
    POLL,
    RENDERER_NAME,
    SCENE_VERSION,
    SOCKET_NAME,
    WIDGETS_PROTOCOL,
    ConfigError,
    TouchbarError,
    log,
    monitor_identity,
    read_renderer,
    runtime_file,
    state_path,
)
from .config import config_path, load_config
from .config_edit import edit_config, remove_app
from .devices import Source, create_keys, create_pad, find_source, uinput_destroy, write_events
from .fnkey import Panel, Toggle, TouchpadSetting
from .items import app_label
from .keys import KeyEmitter
from .pager import Pager
from .scene import ask_tiles, page_look, spot_tiles
from .splitter import BAR, PAD, RESYNC, SURFACE, Splitter
from .surface import Surface


class Daemon:
    def __init__(self, config, config_file, device=None, flag=ACTIVE_FLAG, toggle=None):
        self.config = config
        self.config_file = config_file
        self.config_mtime = self._mtime()
        self.device = device
        self.flag = flag
        # Fn+F6's cycle (fnkey.py); main() makes it, the tests pass their own.
        self.toggle: Toggle | None = toggle
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
            return_after=config.pages.return_after,
            can_show=lambda: self.grabbed() and self.renderer_protocol() >= PAGES_PROTOCOL,
        )
        # The apps page's widgets, below the bar's own height; a card
        # covers their top while it shows. Touches act on them only while
        # the extension draws them.
        self.surface = Surface(
            self.apps,
            self.pager,
            top=lambda: self.bar.base_height() if self.bar else self.config.height,
            cover=lambda: self.bar.touch_height() if self.bar else self.config.height,
            can_draw=lambda: self.renderer_protocol() >= WIDGETS_PROTOCOL,
            click=self._click,
            items=lambda: self.config.apps,
            act=self.item_act,
        )
        self.page_button_shown = False
        # Presses the bar and the surface accepted since the daemon started:
        # the extension plays a click each time this goes up.
        self.clicks = 0
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

    def _click(self):
        self.clicks += 1

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
            self.renderer = (mtime, read_renderer(path))
        return self.renderer[1]

    def renderer_ready(self):
        """Whether the extension draws questions and has the bar on screen."""
        return self.renderer_protocol() >= ASKS_PROTOCOL

    def scene(self):
        """The tiles to draw: a question over the spots while one shows."""
        pressed = self.bar.pressed() if self.bar else []
        removing = self.bar.removing() if self.bar else []
        page_to = self.pager.target() if self.pager.available() else None
        # Page items open their page only while the apps page can show.
        pages = {n: page_look(p) for n, p in self.apps.pages.items()} if page_to is not None else {}
        tiles = spot_tiles(self.config, pressed, removing, self.apps.provided(), page_to, pages)
        ask = self.apps.asks.shown()
        if ask is not None:
            tiles = ask_tiles(ask, self.bar.ask_pressed(ask) if self.bar else (), len(self.apps.asks.queue) - 1) + [
                t for t in tiles if t["style"] in (GRIP, PAGE)
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
            # No line between groups of spots: any item goes in any spot.
            "divider": None,
            "apps": self.apps.names(),
            # The page shown, black below the bar on the touchpad page (and
            # whether with a clock), and what the apps page shows there.
            "page": self.pager.name(),
            "screensaver": {"clock": self.config.pages.clock},
            "surface": self.surface.scene(),
            # Presses accepted so far (Bar and Surface click()): the
            # extension plays its click when this goes up.
            "clicks": self.clicks,
            # Fn+F6 (Toggle.shown()): the extension shows "ScreenPad on".
            "fnkey": self.toggle.shown() if self.toggle else None,
            # Version 1: the extension loaded before tiles keeps drawing the
            # bar from these until the next login (GNOME loads new extension
            # code only then). It shows no app tiles and no questions.
            "spots": [s.public() for s in self.config.spots if s.kind != PAGE],
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
        """Remove the app in the bar's spot index (a long press on it)."""
        return self.edit(lambda c: {"bar": remove_app(c.bar, index)}, "remove the app")

    def resize(self, height):
        return self.edit(lambda c: {"height": height}, "resize the bar")

    def item_act(self, item, kind):
        """A touch on an item of the apps page (Bar.act())."""
        if self.bar is not None:
            self.bar.act(item, kind)

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
            click=self._click,
            open_page=self.surface.open_page,
        )

    def reap(self):
        self.children = [c for c in self.children if c.poll() is None]

    def want_active(self):
        """The ScreenPad on ('screenpad on') and its display not turned off with Fn+F6."""
        return os.path.exists(self.flag) and (self.toggle is None or self.toggle.display_on())

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
            "grabbed %s (%s), %d slots, x 0..%d, y 0..%d, %d units/mm; %s"
            % (path, source.name, source.nslots, x[2], y[2], x[5], self.config.describe())
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
            self.surface.release_all()
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
        # An app installed since shows its name.
        app_label.cache_clear()
        self.apps.set_placed(config.tiles())
        self.apps.notify_policy = config.notifications
        self.pager.configure(config.pages)
        if self.bar:
            self.bar.set_config(config)
        log("loaded %s: %s" % (path or "the built-in default", config.describe()))
        self.publish()

    def loop(self):
        self.publish()
        retry_at = 0.0
        while not self.stop:
            self.check_config()
            self.reap()
            timeout = POLL
            if self.toggle:
                self.toggle.tick()
                # A press read last round, or one settled: Fn+F6's part of the
                # scene. Held means lit, so it changes only while not held.
                if self.source is None and self.toggle.shown() != (self.published or {}).get("fnkey"):
                    self.publish()
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
                self.deactivate("the ScreenPad is off (screenpad off, or its display off with Fn+F6)")
                self.publish()
            self.apps.asks.refresh()
            self.apps.asks.tick()
            # Back to the touchpad page after a while without a touch, and
            # the page button shown or hidden as the extension comes and goes.
            if self.pager.tick() or self.pager.available() != self.page_button_shown:
                self.page_button_shown = self.pager.available()
                self.publish()
            if self.surface.tick():
                self.publish()
            for deadline in (
                self.bar.deadline() if self.bar else None,
                self.surface.deadline(),
                self.apps.asks.deadline(),
                self.pager.deadline(),
                self.toggle.deadline() if self.toggle else None,
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
                + (self.toggle.fds() if self.toggle else [])
            )
            ready, _, _ = select.select(fds, self.server.wfds() if self.server else [], [], timeout)
            if self.wakeup is not None and self.wakeup in ready:
                try:
                    os.read(self.wakeup, 64)
                except BlockingIOError:
                    pass
            if self.toggle:
                self.toggle.readable(ready)
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
        if self.toggle is None:
            self.toggle = Toggle(TouchpadSetting(), Panel(), flag=self.flag)
        try:
            self.loop()
        finally:
            self.toggle.close()
            self.deactivate("stopping")
            if self.server:
                self.server.close()
            self.unpublish()
        return 0
