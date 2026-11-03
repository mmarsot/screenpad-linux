// The repaint safeguard's GNOME Shell side: damages the whole ScreenPad
// monitor when repaint.js says, so mutter copies all of it again into both
// NVIDIA scanout buffers (docs/touchbar.md, "The repaint safeguard").
//
// It owns one empty, transparent, non-reactive St.Widget exactly over the
// ScreenPad's monitor, at the bottom of the shell's UI group, under the
// windows: it draws nothing and never takes input. Queuing a redraw on it
// adds its whole box to the stage's redraw clip, which for that monitor is
// its whole view: mutter then paints and copies the whole view. It also
// tells which stage view is the ScreenPad's (the views the actor is on),
// and so which painted frames are the ScreenPad's. It exists only while
// the bar is drawn.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {CANCEL, RepaintPlan} from './repaint.js';

const now = () => GLib.get_monotonic_time() / 1000;

export class RepaintSafeguard {
    constructor() {
        this._plan = new RepaintPlan();
        this._actor = null;
        this._views = [];
        this._where = null;
        this._paintId = 0;
        this._timerId = 0;
        // Resume: logind says PrepareForSleep(false). A failure here only
        // loses this trigger.
        this._sleepId = 0;
        try {
            this._sleepId = Gio.DBus.system.signal_subscribe(
                'org.freedesktop.login1',
                'org.freedesktop.login1.Manager',
                'PrepareForSleep',
                '/org/freedesktop/login1',
                null,
                Gio.DBusSignalFlags.NONE,
                (_connection, _sender, _path, _iface, _signal, params) => {
                    const [sleeping] = params.deep_unpack();
                    if (!sleeping) this.request();
                },
            );
        } catch (e) {
            logError(e, 'screenpad-touchbar: PrepareForSleep');
        }
    }

    // The ScreenPad's monitor while the bar is drawn on it, else null.
    update(monitor) {
        const where = monitor ? [monitor.x, monitor.y, monitor.width, monitor.height] : null;
        if (JSON.stringify(where) !== JSON.stringify(this._where)) {
            this._destroyActor();
            if (where) this._build(monitor);
            this._where = where;
        }
        this._apply(this._plan.setOn(this._actor !== null, now()));
    }

    // A display event (resume, monitors changed).
    request() {
        this._apply(this._plan.request(now()));
    }

    destroy() {
        if (this._sleepId) Gio.DBus.system.signal_unsubscribe(this._sleepId);
        this._sleepId = 0;
        this._plan.setOn(false, now());
        this._removeTimer();
        this._destroyActor();
        this._where = null;
    }

    _build(monitor) {
        this._actor = new St.Widget({
            name: 'screenpad-touchbar-repaint',
            reactive: false,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
        });
        // The views it is on are known after the next layout.
        this._actor.connect('stage-views-changed', actor => {
            this._views = actor.peek_stage_views();
        });
        Main.layoutManager.uiGroup.insert_child_at_index(this._actor, 0);
        this._paintId = global.stage.connect('after-paint', (_stage, view) => {
            if (this._views.includes(view)) this._apply(this._plan.painted(now()));
        });
    }

    _destroyActor() {
        if (this._paintId) global.stage.disconnect(this._paintId);
        this._paintId = 0;
        this._views = [];
        if (this._actor) {
            this._actor.get_parent()?.remove_child(this._actor);
            this._actor.destroy();
        }
        this._actor = null;
    }

    _apply(step) {
        if (step.redraw) this._actor?.queue_redraw();
        if (step.timer === null) return;
        this._removeTimer();
        if (step.timer === CANCEL) return;
        this._timerId = GLib.timeout_add(GLib.PRIORITY_LOW, step.timer, () => {
            this._timerId = 0;
            this._apply(this._plan.wake(now()));
            return GLib.SOURCE_REMOVE;
        });
    }

    _removeTimer() {
        if (this._timerId) GLib.source_remove(this._timerId);
        this._timerId = 0;
    }
}
