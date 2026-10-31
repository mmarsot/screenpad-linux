// Draws the ScreenPad touch bar on the ScreenPad output.
//
// screenpad-touchbar (the daemon) handles the touches; it publishes the bar
// layout (button spots, shortcut spots, the resize grip), its height and
// which spots are pressed in $XDG_RUNTIME_DIR/screenpad/touchbar.json. This
// extension draws that along the top of the ScreenPad's monitor, only while
// the daemon holds the touchpad. It also:
// - takes the bar off the ScreenPad monitor's work area (a strut), so
//   windows there open and maximize below it;
// - takes apps dropped on the bar (from the dock or the app grid) and has
//   'screenpad-touchbar shortcut add' put them in a shortcut spot;
// - opens a shortcut when the daemon asks over D-Bus (Launch), raising the
//   app if it is already open, as the dock does.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as DND from 'resource:///org/gnome/shell/ui/dnd.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const STATE_NAME = 'touchbar.json';
const CLI = 'screenpad-touchbar';
const CLI_FALLBACK = '/usr/local/bin/screenpad-touchbar';
// Visual gap around each spot, as a fraction of the bar height. The touch
// area is the whole spot rectangle.
const INSET = 0.1;
const ICON = 0.5;
// At most this fraction of the narrowest spot's width, so a tall bar keeps
// every spot, the narrow grip included, drawn at every height.
const MAX_INSET = 0.15;
const MAX_ICON = 0.8;

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
        this._spots = [];
        this._layout = null;
        this._drop = -1;

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
        this._load();
    }

    disable() {
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
    Launch(id) {
        const onBar = this._state?.spots?.some(s => s.kind === 'shortcut' && s.app === id);
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
        this._state = Array.isArray(state?.spots) ? state : null;
        this._sync();
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
            return;
        }
        const layout = JSON.stringify([state.panel, state.height, state.spots,
            state.removing, monitor.x, monitor.y, monitor.width, monitor.height]);
        if (layout !== this._layout) {
            this._destroyBar();
            this._build(state, monitor);
            this._layout = layout;
        }
        this._spots.forEach((actor, i) => {
            if (state.pressed.includes(i))
                actor.add_style_pseudo_class('active');
            else
                actor.remove_style_pseudo_class('active');
        });
        this._highlight(this._drop);
    }

    _build(state, monitor) {
        // Panel pixels to logical pixels; the ScreenPad's monitor is rotated
        // so that it reads upright, which makes its top the bar's edge.
        const [panelWidth, panelHeight] = state.panel;
        this._sx = monitor.width / panelWidth;
        const height = Math.round(state.height * monitor.height / panelHeight);
        const narrowest = Math.min(...state.spots.map(s => s.width)) * this._sx;
        const inset = Math.min(Math.round(height * INSET), Math.round(narrowest * MAX_INSET));
        const spotWidth = Math.min(...state.spots.filter(s => s.kind !== 'grip')
            .map(s => s.width)) * this._sx;
        const iconSize = Math.max(Math.min(Math.round(height * ICON),
            Math.round((spotWidth - 2 * inset) * MAX_ICON)), 8);
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
        const firstShortcut = state.spots.find(s => s.kind === 'shortcut');
        if (firstShortcut) {
            const gap = firstShortcut.x - Math.max(...state.spots
                .filter(s => s.kind === 'button').map(s => s.x + s.width));
            bar.add_child(new St.Widget({
                style_class: 'screenpad-touchbar-separator',
                x: Math.round((firstShortcut.x - gap / 2) * this._sx) - 1,
                y: inset * 2,
                width: 2,
                height: Math.max(height - 4 * inset, 1),
            }));
        }
        state.spots.forEach((spot, i) => {
            const width = Math.max(Math.round(spot.width * this._sx) - 2 * inset, 1);
            const innerHeight = Math.max(height - 2 * inset, 1);
            const actor = spot.kind === 'grip'
                ? this._grip(width, innerHeight)
                : this._spot(spot, state.removing.includes(i), iconSize);
            actor.set_position(Math.round(spot.x * this._sx) + inset, inset);
            actor.set_size(width, innerHeight);
            actor.style = `border-radius: ${Math.round(Math.min(height * 0.16, width * 0.25))}px;`;
            bar.add_child(actor);
            this._spots.push(actor);
        });
        // affectsStruts: the bar spans the top of the ScreenPad's monitor,
        // so GNOME takes it off that monitor's work area and places and
        // maximizes windows there below it. It is rebuilt at each new
        // height, which keeps the work area in step. The strut lies within
        // the ScreenPad's monitor (there are monitors above it, so mutter
        // does not stretch it to the top of the screen), so no other
        // monitor's work area changes.
        // GNOME 50's addChrome takes only trackFullscreen and affectsStruts
        // and throws on any other key. It adds the actor before it checks
        // them, so a throw would leave a bar drawn while the shell, seeing
        // the extension fail, unloads stylesheet.css: plain labels, no
        // strip, no pressed state. Hence no bar left behind on a failure.
        try {
            Main.layoutManager.addChrome(bar, {affectsStruts: true});
        } catch (e) {
            if (bar.get_parent())
                bar.get_parent().remove_child(bar);
            bar.destroy();
            this._spots = [];
            throw e;
        }
        this._bar = bar;
    }

    _spot(spot, removing, iconSize) {
        const classes = [`screenpad-touchbar-spot screenpad-touchbar-${spot.kind}`];
        let child;
        if (spot.kind === 'button') {
            if (spot.mode)
                classes.push(spot.mode);
            if (spot.icon)
                child = new St.Icon({gicon: iconFor(spot.icon), icon_size: iconSize});
            else if (spot.label)
                child = new St.Label({text: spot.label, style: `font-size: ${Math.round(iconSize * 0.5)}px;`});
            else
                classes.push('empty');
        } else if (removing) {
            classes.push('removing');
            child = new St.Icon({icon_name: 'edit-delete-symbolic', icon_size: iconSize});
        } else if (spot.app) {
            const app = Shell.AppSystem.get_default().lookup_app(spot.app);
            child = app
                ? app.create_icon_texture(iconSize)
                : new St.Icon({icon_name: 'image-missing', icon_size: iconSize});
        } else {
            classes.push('empty');
            child = new St.Icon({icon_name: 'list-add-symbolic', icon_size: Math.round(iconSize * 0.6)});
        }
        const actor = new St.Bin({style_class: classes.join(' '), child: child ?? null});
        if (spot.kind === 'button' && spot.label)
            actor.accessible_name = spot.label;
        return actor;
    }

    // The resize grip: an up arrow, a grab bar and a down arrow, sized to
    // fit the spot at any bar height so it never disappears.
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
        const actor = new St.Bin({
            style_class: 'screenpad-touchbar-spot screenpad-touchbar-grip',
            child: box,
        });
        actor.accessible_name = 'Resize the touch bar';
        return actor;
    }

    // Which shortcut spot (index into spots) an app dropped at bar x goes
    // to: the one under it, else the first empty one; -1 if none.
    _dropSpot(x) {
        const spots = this._state?.spots ?? [];
        const px = x / this._sx;
        const under = spots.findIndex(s => s.kind === 'shortcut' && s.x <= px && px < s.x + s.width);
        if (under >= 0)
            return under;
        return spots.findIndex(s => s.kind === 'shortcut' && !s.app);
    }

    _dragOver(source, x) {
        const spot = source?.app?.get_id?.() ? this._dropSpot(x) : -1;
        this._highlight(spot);
        return spot >= 0 ? DND.DragMotionResult.COPY_DROP : DND.DragMotionResult.NO_DROP;
    }

    _acceptDrop(source, x) {
        const id = source?.app?.get_id?.();
        const spots = this._state?.spots ?? [];
        const spot = id ? this._dropSpot(x) : -1;
        this._highlight(-1);
        if (spot < 0)
            return false;
        // Over a shortcut spot: put it there. Elsewhere on the bar: the
        // first free spot.
        const px = x / this._sx;
        const s = spots[spot];
        const argv = [GLib.find_program_in_path(CLI) ?? CLI_FALLBACK, 'shortcut', 'add', id];
        if (s.x <= px && px < s.x + s.width) {
            const n = spots.filter(o => o.kind === 'shortcut').indexOf(s) + 1;
            argv.push('--spot', String(n));
        }
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
        this._spots.forEach((actor, i) => {
            if (i === index)
                actor.add_style_class_name('drop');
            else
                actor.remove_style_class_name('drop');
        });
    }

    _destroyBar() {
        if (this._bar) {
            Main.layoutManager.removeChrome(this._bar);
            this._bar.destroy();
        }
        this._bar = null;
        this._spots = [];
        this._layout = null;
    }
}
