// The custom shortcut window's logic (custom.js draws it): which key
// combination a key press makes, and the command that adds the item.
//
// The daemon's picker asks for the window with AddItem(place), "bar" or
// "apps". A combination is sent as evdev key codes ("29+56+20"), which
// 'screenpad-touchbar shortcut add-key' writes in the config by name, so
// the names are the daemon's alone; a command is a line for sh.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// and hand what it builds to the real command line.

export const PLACES = {bar: 'Add to the bar', apps: 'Add to the apps page'};

// Clutter's hardware key codes are XKB's: the evdev code plus 8.
export const XKB_OFFSET = 8;

// The modifiers, as evdev codes, by Clutter's modifier mask bits
// (CONTROL_MASK, SHIFT_MASK, MOD1_MASK for Alt, SUPER_MASK / MOD4_MASK).
export const CTRL = 29;
export const SHIFT = 42;
export const ALT = 56;
export const SUPER = 125;
// Every modifier key's code, left and right (and AltGr): pressed alone it
// makes no combination yet.
export const MODIFIER_CODES = [29, 97, 42, 54, 56, 100, 125, 126];

// A key press while capturing: mods says which modifiers are down
// ({ctrl, shift, alt, super}), keycode is Clutter's hardware key code.
// The combination's evdev codes, modifiers first, or null while only a
// modifier is pressed.
export function combo(mods, keycode) {
    const code = keycode - XKB_OFFSET;
    if (!(code > 0) || MODIFIER_CODES.includes(code)) return null;
    const out = [];
    if (mods.ctrl) out.push(CTRL);
    if (mods.shift) out.push(SHIFT);
    if (mods.alt) out.push(ALT);
    if (mods.super) out.push(SUPER);
    out.push(code);
    return out;
}

// What the capture button shows: the keys' names joined by "+", Clutter's
// names as given (names), e.g. ['Control_L', 't'] -> 'Ctrl+T'.
const SHOWN = {
    Control_L: 'Ctrl',
    Control_R: 'Ctrl',
    Shift_L: 'Shift',
    Shift_R: 'Shift',
    Alt_L: 'Alt',
    Alt_R: 'Alt',
    Super_L: 'Super',
    Super_R: 'Super',
};
export function comboText(mods, keyName) {
    const parts = [];
    if (mods.ctrl) parts.push('Ctrl');
    if (mods.shift) parts.push('Shift');
    if (mods.alt) parts.push('Alt');
    if (mods.super) parts.push('Super');
    const name = SHOWN[keyName] ?? String(keyName ?? '');
    parts.push(name.length === 1 ? name.toUpperCase() : name);
    return parts.join('+');
}

// {args}: the arguments of 'screenpad-touchbar' that add the item (after
// the program), or {error}: what is missing, a name, and a combination
// (codes) or a command. A combination wins over a command.
export function addArgs(place, {label, codes, command}) {
    const name = (label ?? '').trim();
    const line = (command ?? '').trim();
    if (!Object.hasOwn(PLACES, place)) return {error: `no place ${place}`};
    if (!name) return {error: 'Give it a name.'};
    let args;
    if (codes?.length) args = ['shortcut', 'add-key', name, codes.join('+')];
    else if (line) args = ['shortcut', 'add-command', name, line];
    else return {error: 'Press a key combination, or type a command.'};
    if (place === 'apps') args.push('--apps');
    return {args};
}
