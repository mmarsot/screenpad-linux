// Keeps the desktop off the ScreenPad, which GNOME's display settings join
// to the main screen as one more monitor (docs/touchbar.md, "Keeping the
// desktop off the ScreenPad"). While the extension knows which monitor is
// the ScreenPad, it:
// - puts a pointer barrier on each edge the ScreenPad shares with another
//   monitor, so the pointer, and with it any drag, cannot go onto it. The
//   bar takes no pointer input: the daemon reads its touches from the
//   touchpad itself. The barriers come down while an app is dragged from
//   the dock or the app grid, so it can still be dropped on the bar;
// - moves any window that ends up on the ScreenPad back to the primary
//   monitor (moved there with the keyboard, opened there, left there by a
//   monitor change), once mutter is done placing it;
// - leaves the ScreenPad's view out of the overview, so it shows no
//   workspace or window there and nothing can be dropped into it.
// It reacts to events only, no timer. What goes where is fence.js.

import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {WorkspacesDisplay} from 'resource:///org/gnome/shell/ui/workspacesView.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

import {evictFrame, evicts, fenceFor, hidesView} from './fence.js';

const rect = ({x, y, width, height}) => ({x, y, width, height});
const typeName = type => Object.keys(Meta.WindowType).find(k => Meta.WindowType[k] === type);

export class KeepOut {
    constructor() {
        this._fence = null;
        this._key = 'null';
        this._barriers = [];
        this._dragging = false;
        this._grabbed = null;
        this._later = 0;
        this._display = null;

        // Each of these may leave a window on the ScreenPad; the move waits
        // for mutter to finish (_queue), never runs inside its signal.
        global.display.connectObject(
            'window-created',
            () => this._queue(),
            'window-entered-monitor',
            () => this._queue(),
            'grab-op-begin',
            (_display, window) => (this._grabbed = window),
            'grab-op-end',
            () => {
                this._grabbed = null;
                this._queue();
            },
            this,
        );
        Main.overview.connectObject(
            'item-drag-begin',
            () => this._drag(true),
            'item-drag-end',
            () => this._drag(false),
            'item-drag-cancelled',
            () => this._drag(false),
            this,
        );
        // The overview builds a view per monitor each time it opens and on
        // a monitor change; the ScreenPad's is hidden as soon as it is built.
        const keepOut = this;
        this._injections = new InjectionManager();
        this._injections.overrideMethod(
            WorkspacesDisplay.prototype,
            '_updateWorkspacesViews',
            original =>
                function (...args) {
                    original.apply(this, args);
                    keepOut._display = this;
                    keepOut._syncViews();
                },
        );
    }

    // The ScreenPad's monitor (Main.layoutManager's) while the daemon names
    // it, else null.
    update(monitor) {
        const monitors = Main.layoutManager.monitors.map(rect);
        const fence = monitor
            ? fenceFor(monitor.index, monitors, Main.layoutManager.primaryIndex)
            : null;
        const key = JSON.stringify(fence);
        if (key === this._key) return;
        this._key = key;
        this._fence = fence;
        this._raise();
        this._syncViews();
        this._queue();
    }

    destroy() {
        global.display.disconnectObject(this);
        Main.overview.disconnectObject(this);
        if (this._later) global.compositor.get_laters().remove(this._later);
        this._later = 0;
        this._injections.clear();
        this._fence = null;
        this._syncViews();
        this._display = null;
        this._lower();
        this._grabbed = null;
    }

    _drag(dragging) {
        this._dragging = dragging;
        this._raise();
    }

    // The barriers the fence has, unless an app is being dragged.
    _raise() {
        this._lower();
        if (!this._fence || this._dragging) return;
        for (const {direction, ...line} of this._fence.barriers) {
            try {
                this._barriers.push(
                    new Meta.Barrier({
                        backend: global.backend,
                        directions: Meta.BarrierDirection[direction],
                        ...line,
                    }),
                );
            } catch (e) {
                logError(e, 'screenpad-touchbar: barrier');
            }
        }
    }

    _lower() {
        this._barriers.forEach(b => b.destroy());
        this._barriers = [];
    }

    _syncViews() {
        const views = this._display?._workspacesViews ?? [];
        views.forEach((view, i) => {
            if (i !== this._display._primaryIndex) view.visible = !hidesView(this._fence, i);
        });
    }

    // One move of every window off the ScreenPad, before the next frame.
    _queue() {
        if (!this._fence || this._later) return;
        this._later = global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
            this._later = 0;
            for (const window of global.display.list_all_windows()) {
                try {
                    this._evict(window);
                } catch (e) {
                    logError(e, 'screenpad-touchbar: moving a window off the ScreenPad');
                }
            }
            return false;
        });
    }

    _evict(window) {
        const plain = {
            type: typeName(window.get_window_type()),
            skipTaskbar: window.is_skip_taskbar(),
            attached: window.is_attached_dialog(),
            grabbed: window === this._grabbed,
            monitor: window.get_monitor(),
        };
        if (!evicts(plain, this._fence)) return;
        const primary = this._fence.primary;
        // Mutter keeps a maximized or fullscreen window so on the new monitor.
        const to =
            window.is_maximized() || window.is_fullscreen()
                ? null
                : evictFrame(
                      rect(window.get_frame_rect()),
                      rect(Main.layoutManager.getWorkAreaForMonitor(primary)),
                  );
        if (to) window.move_resize_frame(true, to.x, to.y, to.width, to.height);
        else window.move_to_monitor(primary);
    }
}
