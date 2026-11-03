// The apps page's widgets as the extension draws them: which widgets the
// scene has, when the page must be rebuilt, and where each widget, its
// icon, text and fixed-width lines go, in logical pixels.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node on
// scenes the daemon builds. Like the bar's tiles, a widget is drawn without
// knowing what it is: a rectangle in panel pixels and what to draw in it.

import {CLASS_NAME} from './scene.js';

// Sizes in panel pixels (16 px a mm), scaled to the monitor: the gap
// around each widget, and the largest icon and type.
export const WIDGET_GAP = 12;
export const WIDGET_PAD = 20;
export const MAX_WIDGET_ICON = 96;
export const LABEL_SIZE = 44;
export const TEXT_SIZE = 30;
export const LINE_SIZE = 26;
// A line's height, at most, for its font size: the lines are one label, and
// shrink until they all fit.
export const LINE_HEIGHT = 1.45;

const text = value => (typeof value === 'string' && value ? value : null);

// The widgets of a scene's surface, the malformed left out; null when it
// has none (a daemon from before widgets: the page shows its text).
export function surfaceWidgets(surface) {
    if (!Array.isArray(surface?.widgets)) return null;
    return surface.widgets
        .filter(
            w =>
                w &&
                typeof w === 'object' &&
                typeof w.id === 'string' &&
                ['x', 'y', 'width', 'height'].every(k => Number.isFinite(w[k])) &&
                w.width > 0 &&
                w.height > 0,
        )
        .map(w => ({
            id: w.id,
            x: w.x,
            y: w.y,
            width: w.width,
            height: w.height,
            classes: Array.isArray(w.classes) ? w.classes.filter(c => CLASS_NAME.test(c)) : [],
            icon: text(w.icon),
            title: text(w.title),
            label: text(w.label),
            text: text(w.text),
            lines: Array.isArray(w.lines) ? w.lines.filter(l => typeof l === 'string') : null,
            badge: text(w.badge),
            pressed: w.pressed === true,
        }));
}

// What the surface is built from: a finger going down or up only restyles
// a widget, so "pressed" is left out.
export function widgetsKey(widgets) {
    return JSON.stringify((widgets ?? []).map(({pressed, ...rest}) => rest));
}

// The CSS classes a widget is drawn with.
export function widgetClasses(widget) {
    return ['screenpad-touchbar-spot', 'screenpad-touchbar-widget', ...widget.classes];
}

// A widget's rectangle from the monitor's top left, in logical pixels,
// with a gap around it, and its corner radius. The touch area is the whole
// rectangle the daemon gave.
export function widgetBox(widget, panel, monitor) {
    const sx = monitor.width / panel[0];
    const sy = monitor.height / panel[1];
    const gap = Math.max(Math.round(WIDGET_GAP * sy), 1);
    const x = Math.round(widget.x * sx);
    const y = Math.round(widget.y * sy);
    const width = Math.max(Math.round((widget.x + widget.width) * sx) - x - 2 * gap, 1);
    const height = Math.max(Math.round((widget.y + widget.height) * sy) - y - 2 * gap, 1);
    return {
        x: x + gap,
        y: y + gap,
        width,
        height,
        radius: Math.round(Math.min(width, height) * 0.12),
        padding: Math.max(Math.round(Math.min(WIDGET_PAD * sy, height * 0.1)), 1),
    };
}

// What a widget shows, top to bottom, with its sizes in px for a box of
// box's size at scale (logical pixels per panel pixel): an icon, a title
// (to the left) or a label (centered), a text that wraps, fixed-width
// lines (fitted under the title or label; give a widget with lines no icon
// or text), and a badge. Type shrinks to fit a small widget, never below
// 8 px.
export function widgetParts(widget, box, scale) {
    const fit = (size, share) =>
        Math.max(Math.min(Math.round(size * scale), Math.round(box.height * share)), 8);
    const first = widget.title ?? widget.label;
    const lines = widget.lines?.length ? widget.lines : null;
    const firstSize = fit(LABEL_SIZE, lines ? 0.16 : 0.3);
    // The lines take the room the title or label leaves.
    const room = box.height - 2 * box.padding - (first ? firstSize * LINE_HEIGHT : 0);
    const lineSize = lines
        ? Math.max(
              Math.min(
                  Math.round(LINE_SIZE * scale),
                  Math.floor(room / (lines.length * LINE_HEIGHT)),
              ),
              8,
          )
        : null;
    return {
        icon: widget.icon,
        iconSize: widget.icon
            ? Math.max(
                  Math.min(
                      Math.round(MAX_WIDGET_ICON * scale),
                      Math.round(box.height * (first ? 0.4 : 0.6)),
                      Math.round(box.width * 0.5),
                  ),
                  8,
              )
            : null,
        first,
        start: Boolean(widget.title) || Boolean(lines),
        firstSize,
        text: widget.text,
        textSize: fit(TEXT_SIZE, 0.2),
        lines,
        lineSize,
        badge: widget.badge,
        badgeSize: fit(TEXT_SIZE, 0.2),
    };
}
