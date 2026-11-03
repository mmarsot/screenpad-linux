// Fn+F6's "ScreenPad on" indicator.
//
// Fn+F6 goes round three states: (1) touchpad and ScreenPad display off,
// (2) the touchpad on, (3) both on (screenpad_touchbar/fnkey.py). GNOME's
// on-screen indicator (gsd-media-keys) knows only the touchpad, and flips
// it on every press, so for (2) to (3), where the daemon then turns the
// touchpad back on, it says the touchpad is off. The daemon says in the
// scene ("fnkey") how many presses lit the display ("lit", up once per
// such press) and whether the next press will ("lights_next"). From those
// this decides when the extension shows "ScreenPad on", and which of
// GNOME's indicators it shows in place of: the touchpad-off one for that
// same press, whichever of the two the shell hears of first.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// on the scenes the daemon publishes, with a stand-in clock.

export const TOUCHPAD_OFF_ICON = 'touchpad-disabled-symbolic';
export const SCREENPAD_ON_ICON = 'video-display-symbolic';
export const SCREENPAD_ON_LABEL = 'ScreenPad on';
// How long after one of the two (the daemon's scene, GNOME's indicator)
// the other may come and still be that same press, in ms. The daemon waits
// as long for GNOME's flip of the setting (FLIP_WAIT).
export const SAME_PRESS_MS = 1000;

// The scene's Fn+F6 part, or null: an older daemon has none.
function fnkeyOf(state) {
    const fnkey = state?.fnkey;
    if (!Number.isInteger(fnkey?.lit)) return null;
    return {pid: state.pid, lit: fnkey.lit, lightsNext: fnkey.lights_next === true};
}

// Whether a press lit the display between two scenes: the same daemon's
// count went up. A first scene, or a daemon (re)started, shows nothing.
export function lit(before, after) {
    return before !== null && after !== null && before.pid === after.pid && after.lit > before.lit;
}

// now() is the time in ms.
export class ScreenpadOn {
    constructor(now) {
        this._now = now;
        this._last = null;
        // When the scene said a press lit the display before GNOME's
        // indicator for it came, and when GNOME's came first.
        this._owed = null;
        this._replaced = null;
    }

    _within(time) {
        return time !== null && this._now() - time < SAME_PRESS_MS;
    }

    // Each scene the extension reads, or null once it is gone. True:
    // show "ScreenPad on" now.
    update(state) {
        const before = this._last;
        this._last = fnkeyOf(state);
        if (!lit(before, this._last)) return false;
        if (this._within(this._replaced)) {
            // GNOME's indicator for this press already showed as ours.
            this._replaced = null;
            return false;
        }
        this._owed = this._now();
        return true;
    }

    // GNOME is about to show its indicator with these icon names. True:
    // show "ScreenPad on" in its place. Only the touchpad-off indicator of
    // the press that lights the display, once.
    replaces(names) {
        if (!names.includes(TOUCHPAD_OFF_ICON)) return false;
        if (this._within(this._owed)) {
            this._owed = null;
            return true;
        }
        if (this._last?.lightsNext) {
            this._replaced = this._now();
            return true;
        }
        return false;
    }
}
