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
// - takes apps dropped on the bar (from the dock or the app grid) and has
//   'screenpad-touchbar shortcut add' put them in a shortcut spot;
// - opens a shortcut when the daemon asks over D-Bus (Launch), raising the
//   app if it is already open, as the dock does.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {NotificationMirror} from './notifications.js';

const STATE_NAME = 'touchbar.json';
// The handshake: the daemon reads it (touchbar.py RENDERER_NAME and
// RENDERER_PROTOCOL must match).
const RENDERER_NAME = 'renderer.json';
const RENDERER_PROTOCOL = 2;
const SOCKET_NAME = 'touchbar.sock';
// How long the bar takes to grow into a card, in ms. Less than the daemon's
// ASK_ARM (0.3 s), so a card is fully drawn before it takes a tap.
const GROW_MS = 150;
const CLI = 'screenpad-touchbar';
const CLI_FALLBACK = '/usr/local/bin/screenpad-touchbar';
// Visual gap around each tile, as a fraction of the bar height. The touch
// area is the whole tile rectangle.
const INSET = 0.1;
const ICON = 0.5;
// At most this fraction of the narrowest tile's width, so a tall bar keeps
// every tile, the narrow grip included, drawn at every height.
const MAX_INSET = 0.15;
const MAX_ICON = 0.8;
// CSS class names a tile may carry; anything else is dropped.
const CLASS_NAME = /^[a-z][a-z0-9-]{0,31}$/;

const DBUS_PATH = '/org/screenpad/Touchbar';
const DBUS_XML = `<node>
  <interface name="org.screenpad.Touchbar">
    <method name="Launch">
      <arg type="s" direction="in" name="app"/>
    </method>
  </interface>
</node>`;

function iconFor(name) {
    if (name.startsWith('/'))
        return Gio.FileIcon.new(Gio.File.new_for_path(name));
    return Gio.ThemedIcon.new(name);
}

export default class ScreenpadTouchbar extends Extension {
    enable() {
        this._dir = Gio.File.new_for_path(
            GLib.build_filenamev([GLib.get_user_runtime_dir(), 'screenpad']));
        try {
            this._dir.make_directory_with_parents(null);
        } catch (e) {
            if (!e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.EXISTS))
                throw e;
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

        this._fileMonitor = this._dir.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
        this._fileMonitor.connect('changed', (_monitor, file, other) => {
            if ([file, other].some(f => f && f.get_basename() === STATE_NAME))
                this._load();
        });
        this._monitorsChangedId = Main.layoutManager.connect('monitors-changed',
            () => this._sync());
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
                if (!this._bar || !this._bar.contains(event.targetActor))
                    this._highlight(-1);
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
            'item-drag-end', () => this._highlight(-1),
            'item-drag-cancelled', () => this._highlight(-1), this);
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
        this._destroyBar();
        this._state = null;
        this._dir = null;
    }

    // D-Bus: open a shortcut on the bar (the daemon calls this on a tap).
    // Only an app the bar shows as a tile can be opened this way.
    Launch(id) {
        const onBar = this._state?.tiles?.some(t => t.app === id);
        const app = onBar ? Shell.AppSystem.get_default().lookup_app(id) : null;
        if (!app)
            throw new Error(`${id} is not a shortcut on the touch bar`);
        if (Main.overview.visible)
            Main.overview.hide();
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
        // A daemon from before tiles publishes only version 1 spots; its
        // bar is not drawn until it is restarted.
        this._state = Array.isArray(state?.tiles) ? state : null;
        this._sync();
        // A daemon (re)started: connect to its socket.
        if (this._state)
            this._mirror?.wake();
    }

    // The ScreenPad's monitor, by the EDID identity the daemon read from the
    // EDID 'screenpad on' loads: vendor, product and serial must all match,
    // and exactly one active monitor may match. Never by connector name,
    // position or resolution, so another display never gets the bar.
    _screenpad(id) {
        if (!Array.isArray(id) || id.length !== 3)
            return null;
        const manager = global.backend.get_monitor_manager();
        const matches = manager.get_monitors().filter(m => m.is_active() &&
            m.get_vendor() === id[0] && m.get_product() === id[1] && m.get_serial() === id[2]);
        if (matches.length !== 1)
            return null;
        const index = manager.get_monitor_for_connector(matches[0].get_connector());
        return index >= 0 ? Main.layoutManager.monitors[index] ?? null : null;
    }

    _sync() {
        const state = this._state;
        const monitor = state?.active ? this._screenpad(state.monitor) : null;
        if (!monitor) {
            this._destroyBar();
            this._announce();
            return;
        }
        // A finger going down or up only restyles a tile; anything else
        // rebuilds the bar.
        const shape = state.tiles.map(({pressed, ...rest}) => rest);
        const layout = JSON.stringify([state.panel, state.height, state.strut, shape, state.divider,
            monitor.x, monitor.y, monitor.width, monitor.height]);
        if (layout !== this._layout) {
            const previous = this._drawn;
            this._destroyBar();
            this._build(state, monitor, previous);
            this._layout = layout;
        }
        this._tiles.forEach((actor, i) => {
            if (state.tiles[i].pressed)
                actor.add_style_pseudo_class('active');
            else
                actor.remove_style_pseudo_class('active');
        });
        this._highlight(this._drop);
        this._announce();
    }

    // renderer.json: the protocol this extension draws, and whether the bar
    // is on screen now. Rewritten only when that changes.
    _announce() {
        const drawn = this._bar !== null;
        if (drawn === this._announced)
            return;
        try {
            this._renderer.replace_contents(
                new TextEncoder().encode(JSON.stringify({protocol: RENDERER_PROTOCOL, drawn})),
                null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
            this._announced = drawn;
        } catch (e) {
            logError(e, 'screenpad-touchbar: renderer.json');
        }
    }

    _build(state, monitor, previous) {
        // Panel pixels to logical pixels; the ScreenPad's monitor is rotated
        // so that it reads upright, which makes its top the bar's edge.
        const [panelWidth, panelHeight] = state.panel;
        this._sx = monitor.width / panelWidth;
        const height = Math.round(state.height * monitor.height / panelHeight);
        // The bar's own height: the strut, and what insets, icons and text
        // are sized by, so a card's extra height is room, not bigger type.
        const strutRows = Number.isFinite(state.strut) ? state.strut : state.height;
        const own = Math.min(Math.round(strutRows * monitor.height / panelHeight), height);
        const narrowest = Math.min(...state.tiles.map(t => t.width)) * this._sx;
        const inset = Math.min(Math.round(own * INSET), Math.round(narrowest * MAX_INSET));
        const iconWidth = Math.min(...state.tiles.filter(t => t.glyph !== 'grip')
            .map(t => t.width)) * this._sx;
        const iconSize = Math.max(Math.min(Math.round(own * ICON),
            Math.round((iconWidth - 2 * inset) * MAX_ICON)), 8);
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
        if (Number.isFinite(state.divider)) {
            bar.add_child(new St.Widget({
                style_class: 'screenpad-touchbar-separator',
                x: Math.round(state.divider * this._sx) - 1,
                y: inset * 2,
                width: 2,
                height: Math.max(height - 4 * inset, 1),
            }));
        }
        state.tiles.forEach(tile => {
            const width = Math.max(Math.round(tile.width * this._sx) - 2 * inset, 1);
            const innerHeight = Math.max(height - 2 * inset, 1);
            const actor = this._tile(tile, width, innerHeight, iconSize);
            const x = Math.round(tile.x * this._sx) + inset;
            actor.set_position(x, inset);
            actor.set_size(width, innerHeight);
            actor.style = `border-radius: ${Math.round(Math.min(own * 0.16, width * 0.25))}px;`;
            bar.add_child(actor);
            this._tiles.push(actor);
            if (typeof tile.badge === 'string' && tile.badge) {
                const badge = new St.Label({
                    style_class: 'screenpad-touchbar-badge',
                    text: tile.badge,
                    style: `font-size: ${Math.max(Math.round(iconSize * 0.3), 8)}px;`,
                });
                bar.add_child(badge);
                badge.set_position(x + width - Math.round(iconSize * 0.75), inset);
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
        const strut = new St.Widget({x: monitor.x, y: monitor.y, width: monitor.width,
            height: own});
        // GNOME 50's addChrome takes only trackFullscreen and affectsStruts
        // and throws on any other key. It adds the actor before it checks
        // them, so a throw would leave a bar drawn while the shell, seeing
        // the extension fail, unloads stylesheet.css: plain labels, no
        // strip, no pressed state. Hence no bar left behind on a failure.
        try {
            Main.layoutManager.addChrome(strut, {affectsStruts: true});
            Main.layoutManager.addChrome(bar);
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
        if (previous && previous.own === own && previous.height < height) {
            bar.clip_to_allocation = true;
            bar.height = previous.height;
            bar.ease({height, duration: GROW_MS, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        }
    }

    // One tile: the grip, an icon, an app's icon, or text, in that order of
    // preference. The style and classes come from the daemon.
    _tile(tile, width, height, iconSize) {
        const classes = ['screenpad-touchbar-spot'];
        if (CLASS_NAME.test(tile.style ?? ''))
            classes.push(`screenpad-touchbar-${tile.style}`);
        for (const name of Array.isArray(tile.classes) ? tile.classes : []) {
            if (CLASS_NAME.test(name))
                classes.push(name);
        }
        let child = null;
        if (tile.glyph === 'grip') {
            child = this._grip(width, height);
        } else if (tile.icon) {
            const scale = Number.isFinite(tile.icon_scale) ? tile.icon_scale : 1;
            child = new St.Icon({gicon: iconFor(tile.icon),
                icon_size: Math.max(Math.round(iconSize * scale), 8)});
        } else if (tile.app) {
            const app = Shell.AppSystem.get_default().lookup_app(tile.app);
            child = app
                ? app.create_icon_texture(iconSize)
                : new St.Icon({icon_name: 'image-missing', icon_size: iconSize});
        } else {
            child = this._text(tile, iconSize, height);
        }
        const actor = new St.Bin({style_class: classes.join(' '), child});
        const name = tile.label ?? tile.title;
        if (name)
            actor.accessible_name = name;
        else if (tile.glyph === 'grip')
            actor.accessible_name = 'Resize the touch bar';
        return actor;
    }

    // Text lines: the title or label, and a smaller text under it, which
    // wraps when the tile has room for more lines (a card).
    _text(tile, iconSize, height) {
        const first = tile.title ?? tile.label;
        if (!first && !tile.text)
            return null;
        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const align = tile.title ? Clutter.ActorAlign.START : Clutter.ActorAlign.CENTER;
        if (first) {
            box.add_child(new St.Label({text: first, x_align: align,
                style_class: 'screenpad-touchbar-title',
                style: `font-size: ${Math.round(iconSize * 0.5)}px;`}));
        }
        if (tile.text) {
            const size = Math.round(iconSize * 0.36);
            const text = new St.Label({text: tile.text, x_align: align,
                style_class: 'screenpad-touchbar-text',
                style: `font-size: ${size}px;`});
            if (height > 2 * (iconSize * 0.5 + size * 2)) {
                text.clutter_text.line_wrap = true;
                text.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
            }
            box.add_child(text);
        }
        return box;
    }

    // The resize grip: an up arrow, a grab bar and a down arrow, sized to
    // fit the tile at any bar height so it never disappears.
    _grip(width, height) {
        const bar = Math.max(Math.round(Math.min(width, height) * 0.06), 3);
        const gap = Math.max(Math.round(bar * 0.7), 2);
        const arrow = Math.max(Math.min(Math.round(width * 0.75),
            Math.round((height - bar - 2 * gap) / 2.4), 48), 8);
        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            style: `spacing: ${gap}px;`,
        });
        box.add_child(new St.Icon({icon_name: 'pan-up-symbolic', icon_size: arrow,
            x_align: Clutter.ActorAlign.CENTER}));
        box.add_child(new St.Widget({
            style_class: 'screenpad-touchbar-grip-bar',
            style: `border-radius: ${bar}px;`,
            width: Math.max(Math.round(width * 0.6), arrow),
            height: bar,
            x_align: Clutter.ActorAlign.CENTER,
        }));
        box.add_child(new St.Icon({icon_name: 'pan-down-symbolic', icon_size: arrow,
            x_align: Clutter.ActorAlign.CENTER}));
        return box;
    }

    // Which tile (index into tiles) an app dropped at bar x goes to: the
    // drop tile under it, else the first empty one; -1 if none.
    _dropTile(x) {
        const tiles = this._state?.tiles ?? [];
        const px = x / this._sx;
        const under = tiles.findIndex(t => t.drop && t.x <= px && px < t.x + t.width);
        if (under >= 0)
            return under;
        return tiles.findIndex(t => t.drop && t.empty);
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
        if (index < 0)
            return false;
        // Over a shortcut spot: put it there. Elsewhere on the bar: the
        // first free spot.
        const px = x / this._sx;
        const tile = tiles[index];
        const argv = [GLib.find_program_in_path(CLI) ?? CLI_FALLBACK, 'shortcut', 'add', id];
        if (tile.x <= px && px < tile.x + tile.width)
            argv.push('--spot', String(tile.drop));
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
            if (i === index)
                actor.add_style_class_name('drop');
            else
                actor.remove_style_class_name('drop');
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
