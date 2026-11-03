// The scene in touchbar.json as the extension draws it: which states it
// draws, when the bar must be rebuilt, and where each tile, its icon, text,
// badge and the grip go, in logical pixels; and the pages
// below the bar: the black screensaver and the apps page.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node on
// scenes the daemon builds. It never asks what a tile is: it reads a tile's
// geometry and what to draw (glyph, icon, app, text), nothing else.
// What the pages show below the bar comes from the scene the same way.

// Visual gap around each tile, as a fraction of the bar height. The touch
// area is the whole tile rectangle.
export const INSET = 0.1;
export const ICON = 0.5;
// At most this fraction of the narrowest tile's width, so a tall bar keeps
// every tile, the narrow grip included, drawn at every height.
export const MAX_INSET = 0.15;
export const MAX_ICON = 0.8;
// CSS class names a tile may carry; anything else is dropped.
export const CLASS_NAME = /^[a-z][a-z0-9-]{0,31}$/;

// The state to draw from touchbar.json, or null. A daemon from before tiles
// publishes only version 1 spots; its bar is not drawn until it restarts.
export function sceneFrom(state) {
    return Array.isArray(state?.tiles) ? state : null;
}

// What the bar is built from. A finger going down or up only restyles a
// tile, so "pressed" is left out: anything else rebuilds the bar.
export function layoutKey(state, monitor) {
    const shape = state.tiles.map(({pressed, ...rest}) => rest);
    return JSON.stringify([
        state.panel,
        state.height,
        state.strut,
        shape,
        state.divider,
        monitor.x,
        monitor.y,
        monitor.width,
        monitor.height,
    ]);
}

// Only an app shown as a tile, or as a widget of the apps page, can be
// opened over D-Bus.
export function canLaunch(state, id) {
    const shown = list => (Array.isArray(list) ? list : []).some(t => t?.app === id);
    return shown(state?.tiles) || shown(state?.surface?.widgets);
}

// The bar's size on the monitor. Panel pixels become logical pixels; the
// ScreenPad's monitor is rotated so that it reads upright, which makes its
// top the bar's edge. "own" is the bar's own height (the strut), which
// insets, icons and text are sized by, so a card's extra height is room,
// not bigger type.
export function barGeometry(state, monitor) {
    const [panelWidth, panelHeight] = state.panel;
    const sx = monitor.width / panelWidth;
    const height = Math.round((state.height * monitor.height) / panelHeight);
    const strutRows = Number.isFinite(state.strut) ? state.strut : state.height;
    const own = Math.min(Math.round((strutRows * monitor.height) / panelHeight), height);
    const narrowest = Math.min(...state.tiles.map(t => t.width)) * sx;
    const inset = Math.min(Math.round(own * INSET), Math.round(narrowest * MAX_INSET));
    // The narrow tiles (a glyph: the grip, the page button) do not limit
    // the icons; the page button's own icon is fitted to it (iconSizeFor).
    const iconWidth = Math.min(...state.tiles.filter(t => !t.glyph).map(t => t.width)) * sx;
    const iconSize = Math.max(
        Math.min(Math.round(own * ICON), Math.round((iconWidth - 2 * inset) * MAX_ICON)),
        8,
    );
    return {sx, height, own, inset, iconSize};
}

// A tile's rectangle within the bar, and its corner radius.
export function tileBox(tile, geometry) {
    const {sx, height, own, inset} = geometry;
    const width = Math.max(Math.round(tile.width * sx) - 2 * inset, 1);
    return {
        x: Math.round(tile.x * sx) + inset,
        y: inset,
        width,
        height: Math.max(height - 2 * inset, 1),
        radius: Math.round(Math.min(own * 0.16, width * 0.25)),
    };
}

// The line between the two groups, or null when the scene has none.
export function dividerBox(state, geometry) {
    if (!Number.isFinite(state.divider)) return null;
    const {sx, height, inset} = geometry;
    return {
        x: Math.round(state.divider * sx) - 1,
        y: inset * 2,
        width: 2,
        height: Math.max(height - 4 * inset, 1),
    };
}

// A tile's badge: where it goes and its font size in px, or null.
export function badgeBox(tile, box, iconSize) {
    if (typeof tile.badge !== 'string' || !tile.badge) return null;
    return {
        x: box.x + box.width - Math.round(iconSize * 0.75),
        y: box.y,
        fontSize: Math.max(Math.round(iconSize * 0.3), 8),
    };
}

// The CSS classes a tile is drawn with: its style and classes, as long as
// they are plain class names.
export function tileClasses(tile) {
    const classes = ['screenpad-touchbar-spot'];
    if (CLASS_NAME.test(tile.style ?? '')) classes.push(`screenpad-touchbar-${tile.style}`);
    for (const name of Array.isArray(tile.classes) ? tile.classes : []) {
        if (CLASS_NAME.test(name)) classes.push(name);
    }
    return classes;
}

// What a tile shows, in this order of preference: the grip (drawn by its
// own code), an icon, an app's icon, or text. Any other glyph (the page
// button) is drawn from its icon.
export function tileContent(tile) {
    if (tile.glyph === 'grip') return 'grip';
    if (tile.icon) return 'icon';
    if (tile.app) return 'app';
    return 'text';
}

// An icon tile's icon size in px, never wider than MAX_ICON of the tile's
// drawn width, so a narrow tile's icon fits at every bar height.
export function iconSizeFor(tile, iconSize, width) {
    const scale = Number.isFinite(tile.icon_scale) ? tile.icon_scale : 1;
    return Math.max(Math.min(Math.round(iconSize * scale), Math.round(width * MAX_ICON)), 8);
}

// A text tile's lines: the title or label, and a smaller text under it,
// which wraps when the tile has room for more lines (a card). Null when it
// has neither.
export function textLines(tile, iconSize, height) {
    const first = tile.title ?? tile.label ?? null;
    if (!first && !tile.text) return null;
    const titleSize = Math.round(iconSize * 0.5);
    const textSize = Math.round(iconSize * 0.36);
    return {
        first,
        text: tile.text || null,
        // A title starts at the left; a label is centered.
        start: Boolean(tile.title),
        titleSize,
        textSize,
        wrap: height > 2 * (iconSize * 0.5 + textSize * 2),
    };
}

// The name a screen reader says for a tile.
export function accessibleName(tile) {
    const name = tile.label ?? tile.title;
    if (name) return name;
    return tile.glyph === 'grip' ? 'Resize the touch bar' : null;
}

// The resize grip in a tile of this size: an up arrow, a grab bar and a
// down arrow, sized to fit at any bar height so it never disappears.
export function gripSizes(width, height) {
    const bar = Math.max(Math.round(Math.min(width, height) * 0.06), 3);
    const gap = Math.max(Math.round(bar * 0.7), 2);
    const arrow = Math.max(
        Math.min(Math.round(width * 0.75), Math.round((height - bar - 2 * gap) / 2.4), 48),
        8,
    );
    return {bar, gap, arrow, barWidth: Math.max(Math.round(width * 0.6), arrow)};
}

// Whether the bar grows into a card from what was drawn before: taller over
// an unchanged own height. It then unrolls downwards instead of jumping.
export function grows(previous, drawn) {
    return Boolean(previous) && previous.own === drawn.own && previous.height < drawn.height;
}

// Which tile (index into tiles) an app dropped at panel x px goes to: the
// drop tile under it, else the first empty one; -1 if none.
export function dropTile(tiles, px) {
    const under = tiles.findIndex(t => t.drop && t.x <= px && px < t.x + t.width);
    if (under >= 0) return under;
    return tiles.findIndex(t => t.drop && t.empty);
}

// The arguments after 'shortcut add APP' for a drop on tiles[index]: over
// that spot, put it there; elsewhere on the bar, the first free spot.
export function dropArgs(tiles, index, px) {
    const tile = tiles[index];
    return tile.x <= px && px < tile.x + tile.width ? ['--spot', String(tile.drop)] : [];
}

// What the pages draw below the bar, from the scene: the touchpad page's
// black screensaver (null when the scene has none), with or without a
// clock, and the apps page's surface (null on the touchpad page), with its
// title, text and the hint on how to go back.
export function pagesFrom(state) {
    const text = value => (typeof value === 'string' ? value : '');
    const saver = state?.screensaver;
    const surface = state?.surface;
    return {
        screensaver: saver && typeof saver === 'object' ? {clock: saver.clock === true} : null,
        surface:
            surface && typeof surface === 'object'
                ? {title: text(surface.title), text: text(surface.text), hint: text(surface.hint)}
                : null,
    };
}

// The font sizes in px the pages use on a monitor this many logical pixels
// tall: the screensaver's clock (and its battery level, drawn like it) and
// the apps page's title, text and hint.
export function pageSizes(monitorHeight) {
    const size = fraction => Math.max(Math.round(monitorHeight * fraction), 8);
    return {clock: size(0.06), title: size(0.045), text: size(0.03), hint: size(0.026)};
}

// How long until the clock next changes, in ms, at a Date: the next minute.
export function msToNextMinute(date) {
    return 60000 - (date.getSeconds() * 1000 + date.getMilliseconds());
}

// The states of UPower's devices (UPower.DeviceState) on external power:
// charging, fully charged, and plugged in but not charging (held at a
// charge threshold).
const ON_POWER = new Set([1, 4, 5]);

// The mark after the level on external power: the high voltage sign in its
// text presentation (U+FE0E), so it is drawn in the label's colour, not as
// a colour emoji.
export const CHARGING_MARK = '\u26a1\ufe0e';

// What the screensaver's bottom left corner shows, from UPower's display
// device (the one GNOME Shell's power indicator reads; its IsPresent,
// Percentage and State): the level in whole percent, with CHARGING_MARK on
// external power. '' (nothing shown) without a battery, or before UPower
// answers. It changes only with the shown level or the power, so the black
// is redrawn only then.
export function batteryText(device) {
    const percent = device?.Percentage;
    if (device?.IsPresent !== true || typeof percent !== 'number' || !Number.isFinite(percent))
        return '';
    const level = `${Math.min(Math.max(Math.round(percent), 0), 100)}%`;
    return ON_POWER.has(device.State) ? `${level} ${CHARGING_MARK}` : level;
}
