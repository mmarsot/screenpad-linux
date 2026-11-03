// One tile of the bar as GNOME Shell actors: the grip, the page dots, an
// icon, an app's icon or text lines, with the tile's classes and accessible name. Sizes
// and choices come from scene.js; this only builds the actors.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {
    accessibleName,
    dotsSizes,
    gripSizes,
    iconSizeFor,
    textLines,
    tileClasses,
    tileContent,
} from './scene.js';

export function iconFor(name) {
    if (name.startsWith('/')) return Gio.FileIcon.new(Gio.File.new_for_path(name));
    return Gio.ThemedIcon.new(name);
}

// One tile: the grip, the page dots, an icon, an app's icon, or text
// (tileContent).
// The style and classes come from the daemon.
export function tileActor(tile, width, height, iconSize) {
    let child;
    switch (tileContent(tile)) {
        case 'grip':
            child = gripActor(width, height);
            break;
        case 'dots':
            child = dotsActor(tile, width, height, iconSize);
            break;
        case 'icon':
            child = new St.Icon({
                gicon: iconFor(tile.icon),
                icon_size: iconSizeFor(tile, iconSize),
            });
            break;
        case 'app': {
            const app = Shell.AppSystem.get_default().lookup_app(tile.app);
            child = app
                ? app.create_icon_texture(iconSize)
                : new St.Icon({icon_name: 'image-missing', icon_size: iconSize});
            break;
        }
        default:
            child = textActor(tile, iconSize, height);
    }
    const actor = new St.Bin({style_class: tileClasses(tile).join(' '), child});
    const name = accessibleName(tile);
    if (name) actor.accessible_name = name;
    return actor;
}

function textActor(tile, iconSize, height) {
    const lines = textLines(tile, iconSize, height);
    if (!lines) return null;
    const box = new St.BoxLayout({
        orientation: Clutter.Orientation.VERTICAL,
        x_align: Clutter.ActorAlign.CENTER,
        y_align: Clutter.ActorAlign.CENTER,
    });
    const align = lines.start ? Clutter.ActorAlign.START : Clutter.ActorAlign.CENTER;
    if (lines.first) {
        box.add_child(
            new St.Label({
                text: lines.first,
                x_align: align,
                style_class: 'screenpad-touchbar-title',
                style: `font-size: ${lines.titleSize}px;`,
            }),
        );
    }
    if (lines.text) {
        const text = new St.Label({
            text: lines.text,
            x_align: align,
            style_class: 'screenpad-touchbar-text',
            style: `font-size: ${lines.textSize}px;`,
        });
        if (lines.wrap) {
            text.clutter_text.line_wrap = true;
            text.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        }
        box.add_child(text);
    }
    return box;
}

// The page dots, sized by dotsSizes, with the page's name under them.
function dotsActor(tile, width, height, iconSize) {
    const {pages, page, dot, current, gap, textSize} = dotsSizes(tile, width, height, iconSize);
    const box = new St.BoxLayout({
        orientation: Clutter.Orientation.VERTICAL,
        x_align: Clutter.ActorAlign.CENTER,
        y_align: Clutter.ActorAlign.CENTER,
        style: `spacing: ${gap}px;`,
    });
    const row = new St.BoxLayout({
        x_align: Clutter.ActorAlign.CENTER,
        style: `spacing: ${gap}px;`,
    });
    for (let i = 0; i < pages; i++) {
        const size = i === page ? current : dot;
        row.add_child(
            new St.Widget({
                style_class:
                    i === page ? 'screenpad-touchbar-dot current' : 'screenpad-touchbar-dot',
                style: `border-radius: ${Math.ceil(size / 2)}px;`,
                width: size,
                height: size,
                y_align: Clutter.ActorAlign.CENTER,
            }),
        );
    }
    box.add_child(row);
    if (typeof tile.text === 'string' && tile.text) {
        box.add_child(
            new St.Label({
                text: tile.text,
                x_align: Clutter.ActorAlign.CENTER,
                style_class: 'screenpad-touchbar-text',
                style: `font-size: ${textSize}px;`,
            }),
        );
    }
    return box;
}

// The resize grip, sized by gripSizes.
function gripActor(width, height) {
    const {bar, gap, arrow, barWidth} = gripSizes(width, height);
    const box = new St.BoxLayout({
        orientation: Clutter.Orientation.VERTICAL,
        x_align: Clutter.ActorAlign.CENTER,
        y_align: Clutter.ActorAlign.CENTER,
        style: `spacing: ${gap}px;`,
    });
    box.add_child(
        new St.Icon({
            icon_name: 'pan-up-symbolic',
            icon_size: arrow,
            x_align: Clutter.ActorAlign.CENTER,
        }),
    );
    box.add_child(
        new St.Widget({
            style_class: 'screenpad-touchbar-grip-bar',
            style: `border-radius: ${bar}px;`,
            width: barWidth,
            height: bar,
            x_align: Clutter.ActorAlign.CENTER,
        }),
    );
    box.add_child(
        new St.Icon({
            icon_name: 'pan-down-symbolic',
            icon_size: arrow,
            x_align: Clutter.ActorAlign.CENTER,
        }),
    );
    return box;
}
