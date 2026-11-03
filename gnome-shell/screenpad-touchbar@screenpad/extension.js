// Draws the ScreenPad touch bar on the ScreenPad output.
//
// screenpad-touchbar (the daemon) handles the touches; it publishes the bar
// as a scene of tiles in $XDG_RUNTIME_DIR/screenpad/touchbar.json: each tile
// a rectangle in the bar and what to draw in it (an icon, an app's icon,
// text, the resize grip), its CSS classes and whether a finger is on it.
// This extension draws those tiles along the top of the ScreenPad's monitor
// without knowing what they are, only while the daemon holds the touchpad,
// so a new kind of tile needs no new extension. It also:
// - writes renderer.json next to the scene while it is enabled, saying
//   whether the bar is on screen now; the daemon shows questions only
//   while it is, so a touch never answers a question nobody can see;
// - takes the bar off the ScreenPad monitor's work area (a strut), so
//   windows there open and maximize below it. The strut is the bar's own
//   height: while an attention card grows the bar ("height" above
//   "strut"), the card covers windows for a moment instead of resizing
//   them, and its growth is animated;
// - mirrors GNOME's notifications to the daemon (notifications.js), which
//   shows those with buttons as cards;
// - draws the pages below the bar (pages.js): the touchpad page's black
//   screensaver and the apps page, as the scene says;
// - takes apps dropped on the bar (from the dock or the app grid) and has
//   'screenpad-touchbar shortcut add' put them in a shortcut spot;
// - opens a shortcut when the daemon asks over D-Bus (Launch), raising the
//   app if it is already open, as the dock does.
//
// What it agrees on with the daemon is in contract.js, and where tiles go
// in scene.js; both are plain modules the tests run. tiles.js builds one
// tile's actors, pages.js the pages below the bar. This file does the rest of what needs GNOME Shell: the
// bar and its strut, files, D-Bus, drag and drop.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {
    BAR_CHROME,
    CLI,
    CLI_FALLBACK,
    DBUS_PATH,
    DBUS_XML,
    GROW_MS,
    RENDERER_NAME,
    SOCKET_NAME,
    STATE_NAME,
    STRUT_CHROME,
    rendererJson,
} from './contract.js';
import {NotificationMirror} from './notifications.js';
import {Pages} from './pages.js';
import {
    badgeBox,
    barGeometry,
    canLaunch,
    dividerBox,
    dropArgs,
    dropTile,
    grows,
    layoutKey,
    sceneFrom,
    tileBox,
} from './scene.js';
import {tileActor} from './tiles.js';

export default class ScreenpadTouchbar extends Extension {
    enable() {
        this._dir = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_runtime_dir(), 'screenpad']),
        );
        try {
            this._dir.make_directory_with_parents(null);
        } catch (e) {
            if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS)) throw e;
        }
        this._state = null;
        this._bar = null;
        this._strut = null;
        this._drawn = null;
        this._tiles = [];
        this._layout = null;
        this._drop = -1;
        this._renderer = this._dir.get_child(RENDERER_NAME);
        this._announced = null;
        this._pages = new Pages();

        this._fileMonitor = this._dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._fileMonitor.connect('changed', (_monitor, file, other) => {
            if ([file, other].some(f => f && f.get_basename() === STATE_NAME)) this._load();
        });
        this._monitorsChangedId = Main.layoutManager.connect('monitors-changed', () =>
            this._sync(),
        );
        // The daemon's unit removes the file when it stops; this catches a
        // daemon killed outside systemd.
        this._aliveId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 3, () => {
            if (this._state && !GLib.file_test(`/proc/${this._state.pid}`, GLib.FileTest.EXISTS)) {
                this._state = null;
                this._sync();
            }
            return GLib.SOURCE_CONTINUE;
        });
        // Clears the drop highlight once a drag leaves the bar or ends.
        this._dragMonitor = {
            dragMotion: event => {
                if (!this._bar || !this._bar.contains(event.targetActor)) this._highlight(-1);
                return DND.DragMotionResult.CONTINUE;
            },
            dragDrop: () => {
                this._highlight(-1);
                return DND.DragDropResult.CONTINUE;
            },
        };
        DND.addDragMonitor(this._dragMonitor);
        // Dock and app grid icons end their drags through the overview,
        // also when cancelled, which the drag monitor does not see.
        Main.overview.connectObject(
            'item-drag-end',
            () => this._highlight(-1),
            'item-drag-cancelled',
            () => this._highlight(-1),
            this,
        );
        // Without it, the daemon opens shortcuts with gtk-launch instead;
        // a failure here must not fail the extension (see _build).
        this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
        try {
            this._dbus.export(Gio.DBus.session, DBUS_PATH);
        } catch (e) {
            logError(e, 'screenpad-touchbar: D-Bus');
            this._dbus = null;
        }
        // A failure here must not fail the bar: no notifications, then.
        try {
            this._mirror = new NotificationMirror(this._dir.get_child(SOCKET_NAME));
        } catch (e) {
            logError(e, 'screenpad-touchbar: notifications');
            this._mirror = null;
        }
        this._load();
    }

    disable() {
        // First, so the daemon stops showing questions before the bar goes.
        // GNOME disables extensions on the lock screen: no question shows,
        // or takes an answer, while the screen is locked.
        try {
            this._renderer.delete(null);
        } catch {
            // Already gone.
        }
        this._renderer = null;
        this._mirror?.destroy();
        this._mirror = null;
        this._dbus?.unexport();
        this._dbus = null;
        Main.overview.disconnectObject(this);
        DND.removeDragMonitor(this._dragMonitor);
        this._dragMonitor = null;
        GLib.source_remove(this._aliveId);
        Main.layoutManager.disconnect(this._monitorsChangedId);
        this._fileMonitor.cancel();
        this._fileMonitor = null;
        this._pages.release();
        this._pages = null;
        this._destroyBar();
        this._state = null;
        this._dir = null;
    }

    // D-Bus: open a shortcut on the bar (the daemon calls this on a tap).
    // Only an app the bar shows as a tile can be opened this way.
    Launch(id) {
        const app = canLaunch(this._state, id)
            ? Shell.AppSystem.get_default().lookup_app(id)
            : null;
        if (!app) throw new Error(`${id} is not a shortcut on the touch bar`);
        if (Main.overview.visible) Main.overview.hide();
        app.activate();
    }

    _load() {
        let state = null;
        try {
            const [, bytes] = this._dir.get_child(STATE_NAME).load_contents(null);
            state = JSON.parse(new TextDecoder().decode(bytes));
        } catch {
            // Gone: the daemon stopped. A half-written file cannot happen
            // (the daemon renames it into place).
        }
        this._state = sceneFrom(state);
        this._sync();
        // A daemon (re)started: connect to its socket.
        if (this._state) this._mirror?.wake();
    }

    // The ScreenPad's monitor, by the EDID identity the daemon read from the
    // EDID 'screenpad on' loads: vendor, product and serial must all match,
    // and exactly one active monitor may match. Never by connector name,
    // position or resolution, so another display never gets the bar.
    _screenpad(id) {
        if (!Array.isArray(id) || id.length !== 3) return null;
        const manager = global.backend.get_monitor_manager();
        const matches = manager
            .get_monitors()
            .filter(
                m =>
                    m.is_active() &&
                    m.get_vendor() === id[0] &&
                    m.get_product() === id[1] &&
                    m.get_serial() === id[2],
            );
        if (matches.length !== 1) return null;
        const index = manager.get_monitor_for_connector(matches[0].get_connector());
        return index >= 0 ? (Main.layoutManager.monitors[index] ?? null) : null;
    }

    _sync() {
        const state = this._state;
        const monitor = state?.active ? this._screenpad(state.monitor) : null;
        if (!monitor) {
            this._pages.update(null, null, null, 0);
            this._destroyBar();
            this._announce();
            return;
        }
        // A finger going down or up only restyles a tile; anything else
        // rebuilds the bar.
        const layout = layoutKey(state, monitor);
        if (layout !== this._layout) {
            const previous = this._drawn;
            this._destroyBar();
            this._build(state, monitor, previous);
            this._layout = layout;
        }
        this._tiles.forEach((actor, i) => {
            if (state.tiles[i].pressed) actor.add_style_pseudo_class('active');
            else actor.remove_style_pseudo_class('active');
        });
        this._highlight(this._drop);
        this._pages.update(state, monitor, this._bar, this._drawn?.own ?? 0);
        this._announce();
    }

    // renderer.json: the protocol this extension draws, and whether the bar
    // is on screen now. Rewritten only when that changes.
    _announce() {
        const drawn = this._bar !== null;
        if (drawn === this._announced) return;
        try {
            this._renderer.replace_contents(
                new TextEncoder().encode(rendererJson(drawn)),
                null,
                false,
                Gio.FileCreateFlags.REPLACE_DESTINATION,
                null,
            );
            this._announced = drawn;
        } catch (e) {
            logError(e, 'screenpad-touchbar: renderer.json');
        }
    }

    _build(state, monitor, previous) {
        const geometry = barGeometry(state, monitor);
        const {sx, height, own, iconSize} = geometry;
        this._sx = sx;
        const bar = new St.Widget({
            style_class: 'screenpad-touchbar',
            layout_manager: new Clutter.FixedLayout(),
            // Pointer clicks on the bar land on it, not on a window hidden under it.
            reactive: true,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height,
        });
        // A drop target for apps dragged from the dock or the app grid.
        bar._delegate = {
            handleDragOver: (source, _actor, x) => this._dragOver(source, x),
            acceptDrop: (source, _actor, x) => this._acceptDrop(source, x),
        };
        const divider = dividerBox(state, geometry);
        if (divider) {
            bar.add_child(new St.Widget({style_class: 'screenpad-touchbar-separator', ...divider}));
        }
        state.tiles.forEach(tile => {
            const box = tileBox(tile, geometry);
            const actor = tileActor(tile, box.width, box.height, iconSize);
            actor.set_position(box.x, box.y);
            actor.set_size(box.width, box.height);
            actor.style = `border-radius: ${box.radius}px;`;
            bar.add_child(actor);
            this._tiles.push(actor);
            const place = badgeBox(tile, box, iconSize);
            if (place) {
                const badge = new St.Label({
                    style_class: 'screenpad-touchbar-badge',
                    text: tile.badge,
                    style: `font-size: ${place.fontSize}px;`,
                });
                bar.add_child(badge);
                badge.set_position(place.x, place.y);
            }
        });
        // The strut: an empty actor as tall as the bar's own height, along
        // the top of the ScreenPad's monitor, with affectsStruts, so GNOME
        // takes it off that monitor's work area and places and maximizes
        // windows there below it. It is rebuilt at each new height, which
        // keeps the work area in step; a card's extra height is not in it.
        // The strut lies within the ScreenPad's monitor (there are monitors
        // above it, so mutter does not stretch it to the top of the
        // screen), so no other monitor's work area changes.
        const strut = new St.Widget({
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: own,
        });
        // GNOME 50's addChrome takes only trackFullscreen and affectsStruts
        // and throws on any other key. It adds the actor before it checks
        // them, so a throw would leave a bar drawn while the shell, seeing
        // the extension fail, unloads stylesheet.css: plain labels, no
        // strip, no pressed state. Hence no bar left behind on a failure.
        try {
            Main.layoutManager.addChrome(strut, STRUT_CHROME);
            Main.layoutManager.addChrome(bar, BAR_CHROME);
        } catch (e) {
            for (const actor of [strut, bar]) {
                actor.get_parent()?.remove_child(actor);
                actor.destroy();
            }
            this._tiles = [];
            throw e;
        }
        this._bar = bar;
        this._strut = strut;
        this._drawn = {height, own};
        // Grown over an unchanged own height (a card appearing): the bar
        // unrolls downwards from where it was, instead of jumping.
        if (grows(previous, this._drawn)) {
            bar.clip_to_allocation = true;
            bar.height = previous.height;
            bar.ease({height, duration: GROW_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    // Which tile an app dropped at bar x goes to (dropTile); -1 if none.
    _dropTile(x) {
        return dropTile(this._state?.tiles ?? [], x / this._sx);
    }

    _dragOver(source, x) {
        const tile = source?.app?.get_id?.() ? this._dropTile(x) : -1;
        this._highlight(tile);
        return tile >= 0 ? DND.DragMotionResult.COPY_DROP : DND.DragMotionResult.NO_DROP;
    }

    _acceptDrop(source, x) {
        const id = source?.app?.get_id?.();
        const tiles = this._state?.tiles ?? [];
        const index = id ? this._dropTile(x) : -1;
        this._highlight(-1);
        if (index < 0) return false;
        const argv = [
            GLib.find_program_in_path(CLI) ?? CLI_FALLBACK,
            'shortcut',
            'add',
            id,
            ...dropArgs(tiles, index, x / this._sx),
        ];
        try {
            const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE);
            proc.communicate_utf8_async(null, null, (p, result) => {
                try {
                    const [, , stderr] = p.communicate_utf8_finish(result);
                    if (!p.get_successful())
                        Main.notify('ScreenPad touch bar', stderr.trim() || `could not add ${id}`);
                } catch (e) {
                    logError(e, 'screenpad-touchbar shortcut add');
                }
            });
        } catch (e) {
            logError(e, 'screenpad-touchbar shortcut add');
            return false;
        }
        return true;
    }

    _highlight(index) {
        this._drop = index;
        this._tiles.forEach((actor, i) => {
            if (i === index) actor.add_style_class_name('drop');
            else actor.remove_style_class_name('drop');
        });
    }

    _destroyBar() {
        for (const actor of [this._bar, this._strut]) {
            if (actor) {
                Main.layoutManager.removeChrome(actor);
                actor.destroy();
            }
        }
        this._bar = null;
        this._strut = null;
        this._drawn = null;
        this._tiles = [];
        this._layout = null;
    }
}
