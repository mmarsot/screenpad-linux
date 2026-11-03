"""The scene in touchbar.json: the tiles the extension draws, for the spots and for a question."""

from .common import GRIP, GROUP_GAP, SHORTCUT, SHORTCUTS_X, SPOT_W, TONES

# What touchbar.json shows the extension: tiles, each a rectangle in the bar
# (x and width in panel pixels, as tall as the bar) and what to draw in it.
# The extension draws tiles without knowing what they are, so a new kind of
# tile needs no extension update (GNOME loads extension code only at login).
#
# Every tile has "id", "x", "width" and "style" (its CSS class is
# screenpad-touchbar-<style>). It may have "classes" (more CSS classes),
# "pressed", "glyph" ("grip": drawn by the extension's own code), "icon" (an
# icon name or an image path) and "icon_scale", "app" (a desktop file id,
# drawn as that app's icon; the extension opens only apps shown this way),
# "label", "title" and "text" (text lines), "badge" (a short text in a
# corner), "drop" (the shortcut spot number an app icon dropped there takes)
# and "empty". "divider" is the x of the line between the two groups.
# "height" is the bar's height drawn (CARD_HEIGHT while a card shows) and
# "strut" its own height, which windows keep clear of; the extension
# animates a growth of height over an unchanged strut.

TILE_STYLES = ("button", "shortcut", "grip", "ask-text", "option")
# Every CSS class a tile can carry besides its style. The stylesheet has a
# rule for each but "tap", which is the plain button (tests/test_touchbar.py
# checks it).
TILE_CLASSES = ("hold", "tap", "empty", "removing", "tile", "offline") + tuple("tone-" + tone for tone in TONES)
DIVIDER_X = SHORTCUTS_X - GROUP_GAP // 2
# A question (an attention card): its title and text over the first two
# button spots, Dismiss in the third, its answers in the three shortcut
# spots. The grip stays.
ASK_TEXT_W = DISMISS_X = 2 * SPOT_W
DISMISS = "dismiss"


def _looks(button):
    """A config button's icon and label, those it has."""
    return {k: v for k, v in (("icon", button.icon), ("label", button.label)) if v}


def spot_tiles(config, pressed=(), removing=(), provided=None):
    """The tiles of the config's spots: buttons, shortcuts and the grip.

    provided maps the names of the tiles apps provide now to what they draw.
    """
    provided = provided or {}
    tiles = []
    for i, s in enumerate(config.spots):
        tile = {"id": "%s-%d" % (s.kind, s.index), "x": s.x, "width": s.width, "style": s.kind}
        if i in pressed:
            tile["pressed"] = True
        if s.kind == GRIP:
            tile["id"], tile["glyph"] = GRIP, GRIP
        elif s.kind == SHORTCUT:
            tile["drop"] = s.index + 1
            if s.item is None:
                tile.update(classes=["empty"], icon="list-add-symbolic", icon_scale=0.6, empty=True)
            else:
                tile["app"] = s.item
                if i in removing:
                    tile.update(classes=["removing"], icon="edit-delete-symbolic")
        elif s.item is None:
            tile["classes"] = ["empty"]
        elif s.item.tile:
            look = provided.get(s.item.tile)
            if look is None:
                tile["classes"] = ["tile", "offline"]
                look = {"icon": s.item.icon, "label": s.item.label}
            else:
                tile["classes"] = ["tile", "tone-" + look.get("tone", "neutral")]
            for k in ("icon", "label", "text", "badge"):
                if look.get(k):
                    tile[k] = look[k]
            if "icon" not in tile and "label" not in tile:
                tile.update(_looks(s.item))
        else:
            tile["classes"] = [s.item.mode]
            tile.update(_looks(s.item))
        tiles.append(tile)
    return tiles


def ask_option_x(k):
    return SHORTCUTS_X + k * SPOT_W


def option_x(option):
    """Where an answer's tile (an index, or DISMISS) starts."""
    return DISMISS_X if option == DISMISS else ask_option_x(option)


def ask_option_at(ask, px):
    """The answer under a touch at px (an index, or DISMISS), or None."""
    if DISMISS_X <= px < DISMISS_X + SPOT_W:
        return DISMISS
    for k in range(len(ask.options)):
        if ask_option_x(k) <= px < ask_option_x(k) + SPOT_W:
            return k
    return None


def ask_tiles(ask, pressed=(), queued=0):
    """The tiles of a question: its text, Dismiss, then one tile per answer."""
    text = {"id": "ask-text", "x": 0, "width": ASK_TEXT_W, "style": "ask-text", "title": ask.title}
    if ask.text:
        text["text"] = ask.text
    if queued:
        text["badge"] = "+%d" % queued
    dismiss = {
        "id": "ask-dismiss",
        "x": DISMISS_X,
        "width": SPOT_W,
        "style": "option",
        "classes": ["tone-neutral"],
        "icon": "window-close-symbolic",
        "icon_scale": 0.6,
        "label": "Dismiss",
    }
    if DISMISS in pressed:
        dismiss["pressed"] = True
    tiles = [text, dismiss]
    for k, o in enumerate(ask.options):
        tile = {
            "id": "ask-option-%d" % k,
            "x": ask_option_x(k),
            "width": SPOT_W,
            "style": "option",
            "classes": ["tone-" + o["tone"]],
            "label": o["label"],
        }
        if k in pressed:
            tile["pressed"] = True
        tiles.append(tile)
    return tiles
