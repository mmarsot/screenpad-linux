// The pages below the bar on the ScreenPad's monitor, as GNOME Shell
// actors:
// - the touchpad page's black screensaver: drawn behind windows, just above
//   the desktop background, with a faint clock in the corner. It is black
//   pixels only; the panel's brightness is never touched. It stays while
//   the bar is drawn, under the apps page too;
// - the apps page's surface: over windows and under the bar, the widgets
//   of the page shown (the home page's list of apps, or an app's page),
//   or, from a daemon before widgets, the page's title, text and the hint
//   on how to go back. The daemon keeps every touch below the bar away
//   from the pointer while it shows, and decides what a touch on a widget
//   does.
// What they show comes from the scene (pagesFrom in scene.js, the widgets
// from widgets.js); this only builds the actors.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {BAR_CHROME} from './contract.js';
import {msToNextMinute, pageSizes, pagesFrom} from './scene.js';
import {iconFor} from './tiles.js';
import {surfaceWidgets, widgetBox, widgetClasses, widgetParts, widgetsKey} from './widgets.js';

export class Pages {
    constructor() {
        this._saver = null;
        this._clock = null;
        this._clockId = 0;
        this._interface = null;
        this._surface = null;
        this._widgets = [];
        this._key = null;
    }

    // Draws the pages of state on monitor, below a bar of own logical
    // pixels; nothing when monitor is null (no bar drawn).
    update(state, monitor, bar, own) {
        const pages = monitor ? pagesFrom(state) : {screensaver: null, surface: null};
        const widgets = pages.surface ? surfaceWidgets(state.surface) : null;
        const where = monitor ? [monitor.x, monitor.y, monitor.width, monitor.height, own] : null;
        // A finger going down or up only restyles a widget.
        const key = JSON.stringify([
            pages,
            widgetsKey(widgets),
            widgets !== null,
            where,
            state?.panel,
        ]);
        if (key !== this._key) {
            this.destroy();
            if (pages.screensaver) this._buildSaver(pages.screensaver, monitor);
            if (widgets) this._buildWidgets(widgets, state.panel, monitor);
            else if (pages.surface) this._buildSurface(pages.surface, monitor, own);
            this._key = key;
        }
        this._widgets.forEach((actor, i) => {
            if (widgets[i].pressed) actor.add_style_pseudo_class('active');
            else actor.remove_style_pseudo_class('active');
        });
        // The bar is rebuilt on its own; the surface stays under it.
        const parent = this._surface?.get_parent();
        if (parent && bar?.get_parent() === parent)
            parent.set_child_below_sibling(this._surface, bar);
    }

    destroy() {
        if (this._clockId) GLib.source_remove(this._clockId);
        this._clockId = 0;
        this._clock = null;
        this._interface = null;
        if (this._saver) {
            this._saver.get_parent()?.remove_child(this._saver);
            this._saver.destroy();
        }
        this._saver = null;
        if (this._surface) {
            Main.layoutManager.removeChrome(this._surface);
            this._surface.destroy();
        }
        this._surface = null;
        this._widgets = [];
        this._key = null;
    }

    _buildSaver(saver, monitor) {
        this._saver = new St.Widget({
            style_class: 'screenpad-touchbar-screensaver',
            layout_manager: new Clutter.BinLayout(),
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
        });
        // Behind every window, over the desktop background; at the very
        // bottom (under it, unseen) rather than over windows if GNOME
        // ever moves the background elsewhere.
        const background = Main.layoutManager._backgroundGroup;
        if (background?.get_parent() === global.window_group)
            global.window_group.insert_child_above(this._saver, background);
        else global.window_group.insert_child_at_index(this._saver, 0);
        if (!saver.clock) return;
        this._clock = new St.Label({
            style_class: 'screenpad-touchbar-clock',
            style: `font-size: ${pageSizes(monitor.height).clock}px;`,
            x_align: Clutter.ActorAlign.END,
            y_align: Clutter.ActorAlign.END,
            x_expand: true,
            y_expand: true,
        });
        this._saver.add_child(this._clock);
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._tick();
    }

    // Shows the time, then again at the next minute: the screen is redrawn
    // once a minute, not more.
    _tick() {
        const twelve = this._interface.get_string('clock-format') === '12h';
        this._clock.text = GLib.DateTime.new_now_local()
            .format(twelve ? '%l:%M' : '%H:%M')
            .trim();
        this._clockId = GLib.timeout_add(GLib.PRIORITY_LOW, msToNextMinute(new Date()) + 50, () => {
            this._clockId = 0;
            this._tick();
            return GLib.SOURCE_REMOVE;
        });
    }

    _buildSurface(surface, monitor, own) {
        const sizes = pageSizes(monitor.height);
        this._surface = new St.Widget({
            style_class: 'screenpad-touchbar-surface',
            layout_manager: new Clutter.BinLayout(),
            // A mouse click lands on it, not on a window hidden under it.
            reactive: true,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
            style: `padding-top: ${own}px;`,
        });
        const box = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            y_expand: true,
            style: `spacing: ${Math.round(sizes.text * 0.8)}px;`,
        });
        for (const [name, size] of [
            ['title', sizes.title],
            ['text', sizes.text],
            ['hint', sizes.hint],
        ]) {
            if (!surface[name]) continue;
            box.add_child(
                new St.Label({
                    text: surface[name],
                    x_align: Clutter.ActorAlign.CENTER,
                    style_class: `screenpad-touchbar-surface-${name}`,
                    style: `font-size: ${size}px;`,
                }),
            );
        }
        this._surface.add_child(box);
        Main.layoutManager.addChrome(this._surface, BAR_CHROME);
    }

    // The surface with widgets: each at the rectangle the daemon gave, in
    // the monitor's logical pixels.
    _buildWidgets(widgets, panel, monitor) {
        this._surface = new St.Widget({
            style_class: 'screenpad-touchbar-surface',
            reactive: true,
            x: monitor.x,
            y: monitor.y,
            width: monitor.width,
            height: monitor.height,
        });
        const scale = monitor.height / panel[1];
        for (const widget of widgets) {
            const box = widgetBox(widget, panel, monitor);
            const actor = this._widgetActor(widget, box, widgetParts(widget, box, scale));
            this._surface.add_child(actor);
            this._widgets.push(actor);
        }
        Main.layoutManager.addChrome(this._surface, BAR_CHROME);
    }

    _widgetActor(widget, box, parts) {
        const actor = new St.Widget({
            style_class: widgetClasses(widget).join(' '),
            layout_manager: new Clutter.BinLayout(),
            x: box.x,
            y: box.y,
            width: box.width,
            height: box.height,
            style: `border-radius: ${box.radius}px; padding: ${box.padding}px;`,
        });
        const align = parts.start ? Clutter.ActorAlign.START : Clutter.ActorAlign.CENTER;
        const column = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: parts.start ? Clutter.ActorAlign.FILL : Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
            x_expand: true,
            y_expand: true,
            style: `spacing: ${Math.round(parts.textSize * 0.3)}px;`,
        });
        if (parts.icon) {
            column.add_child(
                new St.Icon({
                    gicon: iconFor(parts.icon),
                    icon_size: parts.iconSize,
                    x_align: align,
                }),
            );
        }
        const label = (value, styleClass, size, wrap) => {
            const l = new St.Label({
                text: value,
                x_align: align,
                style_class: styleClass,
                style: `font-size: ${size}px;`,
            });
            if (wrap) {
                l.clutter_text.line_wrap = true;
                l.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
            }
            return l;
        };
        if (parts.first)
            column.add_child(label(parts.first, 'screenpad-touchbar-title', parts.firstSize));
        if (parts.text)
            column.add_child(label(parts.text, 'screenpad-touchbar-text', parts.textSize, true));
        if (parts.lines)
            column.add_child(
                label(parts.lines.join('\n'), 'screenpad-touchbar-lines', parts.lineSize),
            );
        actor.add_child(column);
        if (parts.badge) {
            actor.add_child(
                new St.Label({
                    text: parts.badge,
                    style_class: 'screenpad-touchbar-badge',
                    style: `font-size: ${parts.badgeSize}px;`,
                    x_align: Clutter.ActorAlign.END,
                    y_align: Clutter.ActorAlign.START,
                    x_expand: true,
                    y_expand: true,
                }),
            );
        }
        const name = parts.first ?? parts.text;
        if (name) actor.accessible_name = name;
        return actor;
    }
}
