// Shows "ScreenPad on" in GNOME's on-screen indicator when Fn+F6 lights the
// ScreenPad's display, on the primary monitor, in place of GNOME's
// touchpad-off indicator for that press (when and which: fnkey.js).
//
// GNOME's indicators reach the shell as ShowOSD calls, which end in the
// OsdWindowManager's showAll or showOne; both are wrapped while the
// extension is enabled, and pass every other indicator through. Nothing
// here runs but on a scene the daemon publishes or an indicator GNOME
// shows: no timer.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {OsdWindowManager} from 'resource:///org/gnome/shell/ui/osdWindow.js';
import {InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

import {SCREENPAD_ON_ICON, SCREENPAD_ON_LABEL, ScreenpadOn} from './fnkey.js';

const iconNames = icon => (icon instanceof Gio.ThemedIcon ? icon.get_names() : []);

export class FnKeyOsd {
    constructor() {
        this._indicator = new ScreenpadOn(() => GLib.get_monotonic_time() / 1000);
        this._icon = new Gio.ThemedIcon({name: SCREENPAD_ON_ICON});
        const osd = this;
        this._injections = new InjectionManager();
        this._injections.overrideMethod(
            OsdWindowManager.prototype,
            'showAll',
            original =>
                function (icon, ...rest) {
                    if (osd._replaces(icon)) osd._show(this);
                    else original.call(this, icon, ...rest);
                },
        );
        this._injections.overrideMethod(
            OsdWindowManager.prototype,
            'showOne',
            original =>
                function (monitorIndex, icon, ...rest) {
                    if (osd._replaces(icon)) osd._show(this);
                    else original.call(this, monitorIndex, icon, ...rest);
                },
        );
    }

    // Each scene the extension reads, or null once it is gone.
    update(state) {
        if (this._indicator.update(state)) this._show(Main.osdWindowManager);
    }

    // A failure deciding must not lose GNOME's own indicator.
    _replaces(icon) {
        try {
            return this._indicator.replaces(iconNames(icon));
        } catch (e) {
            logError(e, 'screenpad-touchbar: indicator');
            return false;
        }
    }

    // Through the wrapped showOne, which passes this icon on.
    _show(manager) {
        manager.showOne(Main.layoutManager.primaryIndex, this._icon, SCREENPAD_ON_LABEL, null);
    }

    destroy() {
        this._injections.clear();
        this._injections = null;
    }
}
