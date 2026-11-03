// The click a press plays: a soft tick confirming a tap without looking,
// in place of the haptic feedback the ScreenPad does not have.
//
// The daemon counts the presses it accepts in the scene ("clicks"): a hold
// button as the finger goes down, and a tap button, an app's tile, a
// shortcut, the page button, a card's answer or an apps page widget as the
// finger lifts on it, which is when their action happens. A slide away, a
// cancel, a swipe, the grip and the touchpad count nothing. The extension
// plays click.wav (made by gnome-shell/make_click.py) each time the count
// goes up, through GNOME's sound player, which plays it only while the
// desktop's event sounds are on (org.gnome.desktop.sound event-sounds), at
// the system sounds' volume.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// with a stand-in for the sound player.

export const CLICK_NAME = 'click.wav';
export const CLICK_DESCRIPTION = 'Touch bar press';

// Whether a press was accepted between two scenes: the same daemon's count
// went up. A first scene, or a daemon (re)started, plays nothing.
export function clicked(before, after) {
    return (
        Number.isInteger(before?.clicks) &&
        Number.isInteger(after?.clicks) &&
        before.pid === after.pid &&
        after.clicks > before.clicks
    );
}

// Plays the click on the scenes the extension reads. player is GNOME's
// sound player (global.display.get_sound_player()), file the click's
// Gio.File. Presses closer together than the extension reads the scene
// play one click.
export class Clicker {
    constructor(player, file) {
        this._player = player;
        this._file = file;
        this._last = null;
    }

    // Each scene read, or null once it is gone. True if it played.
    update(state) {
        const before = this._last;
        this._last = state ? {pid: state.pid, clicks: state.clicks} : null;
        if (!clicked(before, this._last)) return false;
        this._player.play_from_file(this._file, CLICK_DESCRIPTION, null);
        return true;
    }
}
