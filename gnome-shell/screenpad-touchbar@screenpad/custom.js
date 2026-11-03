// The custom shortcut window: the picker's "Custom shortcut…" asks for it
// over D-Bus (AddItem), and it opens on the main screen as a GNOME dialog.
// It records a key combination as you press it, or takes a command, and a
// name; Add runs 'screenpad-touchbar shortcut add-key' or 'add-command',
// which writes the item in the config and reloads the bar. What a key
// press makes and the command's arguments are shortcut.js's.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';

import {CLI, CLI_FALLBACK} from './contract.js';
import {PLACES, addArgs, combo, comboText} from './shortcut.js';

const RECORD = 'Record a key combination';

export class CustomShortcut {
    constructor(place) {
        this._place = place;
        this._codes = null;
        this._recording = false;
        this._dialog = new ModalDialog.ModalDialog({
            styleClass: 'screenpad-touchbar-custom',
            destroyOnClose: true,
        });
        this._dialog.connect('destroy', () => (this._dialog = null));
        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'screenpad-touchbar-custom-box',
        });
        box.add_child(
            new St.Label({
                text: `Custom shortcut: ${PLACES[place].toLowerCase()}`,
                style_class: 'screenpad-touchbar-custom-title',
            }),
        );
        this._name = new St.Entry({hint_text: 'Name', can_focus: true});
        box.add_child(this._name);
        this._key = new St.Button({label: RECORD, can_focus: true, style_class: 'button'});
        this._key.connect('clicked', () => this._record());
        this._key.connect('key-press-event', (_actor, event) => this._keyPress(event));
        box.add_child(this._key);
        this._command = new St.Entry({
            hint_text: 'Or a command, such as notify-send hello',
            can_focus: true,
        });
        box.add_child(this._command);
        this._error = new St.Label({text: '', style_class: 'screenpad-touchbar-custom-error'});
        box.add_child(this._error);
        this._dialog.contentLayout.add_child(box);
        this._dialog.setButtons([
            {label: 'Cancel', action: () => this.close(), key: Clutter.KEY_Escape},
            {label: 'Add', action: () => this._add(), default: true},
        ]);
    }

    open() {
        this._dialog?.open();
        global.stage.set_key_focus(this._name);
    }

    close() {
        this._dialog?.close();
    }

    destroy() {
        this._dialog?.destroy();
    }

    _record() {
        this._recording = true;
        this._codes = null;
        this._key.label = 'Press the keys…';
        this._key.grab_key_focus();
    }

    _keyPress(event) {
        if (!this._recording) return Clutter.EVENT_PROPAGATE;
        const state = event.get_state();
        const M = Clutter.ModifierType;
        const mods = {
            ctrl: Boolean(state & M.CONTROL_MASK),
            shift: Boolean(state & M.SHIFT_MASK),
            alt: Boolean(state & M.MOD1_MASK),
            super: Boolean(state & (M.SUPER_MASK | M.MOD4_MASK)),
        };
        const codes = combo(mods, event.get_key_code());
        if (codes) {
            this._codes = codes;
            this._recording = false;
            this._key.label = comboText(mods, Clutter.keyval_name(event.get_key_symbol()));
        }
        // Every key goes to the recording, Escape and Enter too.
        return Clutter.EVENT_STOP;
    }

    _add() {
        const {args, error} = addArgs(this._place, {
            label: this._name.get_text(),
            codes: this._codes,
            command: this._command.get_text(),
        });
        if (!args) {
            this._error.text = error;
            return;
        }
        const argv = [GLib.find_program_in_path(CLI) ?? CLI_FALLBACK, ...args];
        try {
            const proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDERR_PIPE);
            proc.communicate_utf8_async(null, null, (p, result) => {
                try {
                    const [, , stderr] = p.communicate_utf8_finish(result);
                    if (p.get_successful()) this.close();
                    else if (this._dialog) this._error.text = stderr.trim() || 'Could not add it.';
                } catch (e) {
                    logError(e, 'screenpad-touchbar shortcut add-key');
                }
            });
        } catch (e) {
            logError(e, 'screenpad-touchbar shortcut add-key');
            this._error.text = 'Could not run screenpad-touchbar.';
        }
    }
}
