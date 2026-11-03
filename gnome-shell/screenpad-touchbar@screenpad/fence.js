// What keeps the desktop off the ScreenPad (keepout.js does it in GNOME
// Shell): where the pointer barriers go, which windows are moved back to
// the primary monitor, and where to. A plain module the tests run: no GNOME
// imports, only rectangles ({x, y, width, height}) and plain values.

// The window types moved off the ScreenPad: the ones a person opens and
// moves. Desktop, dock, menu, tooltip and notification windows stay where
// their owner puts them (the desktop icons' window on each monitor, say).
export const EVICTED_TYPES = ['NORMAL', 'DIALOG', 'MODAL_DIALOG', 'UTILITY'];

const overlap = (a1, a2, b1, b2) => [Math.max(a1, b1), Math.min(a2, b2)];

// The fence around the ScreenPad's monitor, or null when there is none to
// put up: no ScreenPad, no other monitor, or the ScreenPad is the primary
// one (where windows go back to). monitors are every monitor's rectangle
// by index, index the ScreenPad's and primary the primary monitor's.
//
// One barrier per edge the ScreenPad shares with another monitor, as long
// as the part they share. Each barrier stops the pointer going onto the
// ScreenPad and lets it leave: `direction` is the Meta.BarrierDirection the
// pointer may still cross it in, away from the ScreenPad. Mutter stops the
// pointer on the barrier's line itself, so the line is the other monitor's
// last or first row or column of pixels along the edge, never the
// ScreenPad's: the pointer stops on the other monitor.
export function fenceFor(index, monitors, primary) {
    const pad = monitors[index];
    if (!pad || index === primary || !monitors[primary]) return null;
    const right = pad.x + pad.width;
    const bottom = pad.y + pad.height;
    const barriers = [];
    monitors.forEach((m, i) => {
        if (i === index) return;
        const [x1, x2] = overlap(pad.x, right, m.x, m.x + m.width);
        const [y1, y2] = overlap(pad.y, bottom, m.y, m.y + m.height);
        const above = pad.y - 1;
        const left = pad.x - 1;
        if (x2 > x1 && m.y + m.height === pad.y)
            barriers.push({x1, y1: above, x2, y2: above, direction: 'NEGATIVE_Y'});
        if (x2 > x1 && m.y === bottom)
            barriers.push({x1, y1: bottom, x2, y2: bottom, direction: 'POSITIVE_Y'});
        if (y2 > y1 && m.x + m.width === pad.x)
            barriers.push({x1: left, y1, x2: left, y2, direction: 'NEGATIVE_X'});
        if (y2 > y1 && m.x === right)
            barriers.push({x1: right, y1, x2: right, y2, direction: 'POSITIVE_X'});
    });
    return {index, primary, barriers};
}

// Whether a window goes back to the primary monitor: it is on the
// ScreenPad, of a type a person opens (EVICTED_TYPES), shows in the
// overview and the app switcher (not skip-taskbar), is not a dialog
// attached to its parent (it moves with the parent) and is not being moved
// or resized by hand right now (it goes back when that ends). window is
// {type: a Meta.WindowType name, skipTaskbar, attached, grabbed, monitor}.
export function evicts(window, fence) {
    return (
        fence !== null &&
        window.monitor === fence.index &&
        EVICTED_TYPES.includes(window.type) &&
        !window.skipTaskbar &&
        !window.attached &&
        !window.grabbed
    );
}

// Where a window that is not maximized or fullscreen goes on the primary
// monitor's work area: a window partly over it (dragged down until most of
// it hung over the ScreenPad) moves up just enough to fit, shrunk only if
// larger than the work area. A window with nothing on it (moved there with
// the keyboard, or opened there) gets null: mutter's own move to a monitor
// places it as it was placed on the ScreenPad.
export function evictFrame(frame, area) {
    const [x1, x2] = overlap(frame.x, frame.x + frame.width, area.x, area.x + area.width);
    const [y1, y2] = overlap(frame.y, frame.y + frame.height, area.y, area.y + area.height);
    if (x2 <= x1 || y2 <= y1) return null;
    const width = Math.min(frame.width, area.width);
    const height = Math.min(frame.height, area.height);
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    return {
        x: clamp(frame.x, area.x, area.x + area.width - width),
        y: clamp(frame.y, area.y, area.y + area.height - height),
        width,
        height,
    };
}

// Whether the overview leaves out monitor i's view: the ScreenPad's.
export function hidesView(fence, i) {
    return fence !== null && i === fence.index && i !== fence.primary;
}
