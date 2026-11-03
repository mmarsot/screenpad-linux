"""The scene in touchbar.json: the tiles the extension draws, for the spots and for a question."""

from .common import (
    APP,
    CARD_HEIGHT,
    GRIP,
    GROUP_GAP,
    PAGE,
    PAGE_ICONS,
    PAGE_ITEM,
    PAGE_LABELS,
    PAGE_X,
    SPOT_W,
    TILE,
    TONES,
)

# What touchbar.json shows the extension: tiles, each a rectangle in the bar
# (x and width in panel pixels, as tall as the bar) and what to draw in it.
# The extension draws tiles without knowing what they are, so a new kind of
# tile needs no extension update (GNOME loads extension code only at login).
#
# Every tile has "id", "x", "width" and "style" (its CSS class is
# screenpad-touchbar-<style>). It may have "classes" (more CSS classes),
# "pressed", "glyph" (a narrow tile, which does not limit the other tiles'
# icon size: "grip", drawn by the extension's own code, "page", the page
# button, or "dismiss", a card's Dismiss, both drawn as their icon fitted
# to the tile), "icon" (an icon name
# or an image path) and "icon_scale", "app" (a desktop file id,
# drawn as that app's icon; the extension opens only apps shown this way),
# "label", "title" and "text" (text lines), "badge" (a short text in a
# corner), "drop" (the spot number an app icon dropped there takes) and
# "empty". "divider" (the x of a line between two groups of spots) is
# null: any item goes in any spot.
# "height" is the bar's height drawn (CARD_HEIGHT while a card shows) and
# "strut" its own height, which windows keep clear of; the extension
# animates a growth of height over an unchanged strut.

TILE_STYLES = ("button", "shortcut", "grip", "page", "ask-text", "option")
# Every CSS class a tile can carry besides its style. The stylesheet has a
# rule for each but "tap", which is the plain button
# (tests/test_touchbar_scene.py checks it).
TILE_CLASSES = ("hold", "tap", "empty", "removing", "tile", "offline", "moving", "target") + tuple(
    "tone-" + tone for tone in TONES
)
# A question (an attention card): its title and text, a narrow Dismiss,
# then its answers up to the page button, GROUP_GAP after Dismiss. The text
# and the answers share the rest: an answer is as wide as the card is tall
# while the text keeps at least one and a half spots (one or two answers),
# else (three) the answers share what the text leaves. The page button and
# the grip stay.
DISMISS_W = SPOT_W // 2
ASK_TEXT_MIN = 3 * SPOT_W // 2
ASK_SPACE = PAGE_X - DISMISS_W - GROUP_GAP
DISMISS = "dismiss"


def app_look(item, look):
    """What an app's tile or page item shows: the look its app gives it
    (provided), else its own icon and label, dimmed. Its classes and fields."""
    if look is None:
        return ["tile", "offline"], item.looks()
    shown = {k: look[k] for k in ("icon", "label", "text", "badge") if look.get(k)}
    if "icon" not in shown and "label" not in shown:
        shown.update(item.looks())
    return ["tile", "tone-" + look.get("tone", "neutral")], shown


def page_look(page):
    """What a page item shows of an app's page (widgets.Page): its title, icon, badge and tone."""
    return {"label": page.title(), **{k: v for k, v in page.look.items() if k != "title"}}


def spot_tiles(config, pressed=(), removing=(), provided=None, page_to=None, pages=None, marks=None):
    """The tiles of the config's spots: its items, the grip and the page
    button.

    provided maps the names of the tiles apps provide now to what they draw,
    pages the names of the apps' pages that can be opened now to theirs
    (page_look()). page_to is the page a tap on the page button turns to
    (the pager's target()) while the pages turn; without it, no page button.
    An app dropped on an empty spot or on an app's spot goes there.
    marks maps spot indexes to a class a drag gives them (Mover.marks()):
    "moving" on the item it moves, "target" where it would land.
    """
    provided = provided or {}
    pages = pages or {}
    tiles = []
    for i, s in enumerate(config.spots):
        if s.kind == PAGE and page_to is None:
            continue
        item = s.item
        tile = {"id": "%s-%d" % (s.kind, s.index), "x": s.x, "width": s.width, "style": "button"}
        if i in pressed:
            tile["pressed"] = True
        if s.kind == GRIP:
            tile.update(id=GRIP, style=GRIP, glyph=GRIP)
        elif s.kind == PAGE:
            # Only where a tap goes, as an icon. An extension from before
            # the page button draws it as any icon tile.
            tile.update(id=PAGE, style=PAGE, glyph=PAGE, icon=PAGE_ICONS[page_to], label=PAGE_LABELS[page_to])
        elif item is None:
            tile.update(
                style="shortcut", classes=["empty"], icon="list-add-symbolic", icon_scale=0.6, empty=True, drop=i + 1
            )
        elif item.kind == APP:
            tile.update(style="shortcut", app=item.name, drop=i + 1, **item.looks())
            if i in removing:
                tile.update(classes=["removing"], icon="edit-delete-symbolic")
        elif item.kind in (TILE, PAGE_ITEM):
            classes, shown = app_look(item, (provided if item.kind == TILE else pages).get(item.name))
            tile.update(classes=classes, **shown)
        else:
            tile.update(classes=[item.mode], **item.looks())
        if marks and s.kind not in (GRIP, PAGE) and i in marks:
            tile["classes"] = tile.get("classes", []) + [marks[i]]
        tiles.append(tile)
    return tiles


def ask_answer_width(ask):
    """The width of each of a question's answers."""
    return min(CARD_HEIGHT, (ASK_SPACE - ASK_TEXT_MIN) // len(ask.options))


def ask_text_width(ask):
    """The width of a question's text tile, at the left of the bar."""
    return ASK_SPACE - len(ask.options) * ask_answer_width(ask)


def ask_option_box(ask, option):
    """Where an answer's tile (an index, or DISMISS) is: its x and width."""
    if option == DISMISS:
        return ask_text_width(ask), DISMISS_W
    width = ask_answer_width(ask)
    return ask_text_width(ask) + DISMISS_W + GROUP_GAP + option * width, width


def ask_option_at(ask, px):
    """The answer under a touch at px (an index, or DISMISS), or None."""
    for option in (DISMISS, *range(len(ask.options))):
        x, width = ask_option_box(ask, option)
        if x <= px < x + width:
            return option
    return None


def ask_tiles(ask, pressed=(), queued=0):
    """The tiles of a question: its text, Dismiss, then one tile per answer."""
    text = {"id": "ask-text", "x": 0, "width": ask_text_width(ask), "style": "ask-text", "title": ask.title}
    if ask.text:
        text["text"] = ask.text
    if queued:
        text["badge"] = "+%d" % queued
    x, width = ask_option_box(ask, DISMISS)
    dismiss = {
        "id": "ask-dismiss",
        "x": x,
        "width": width,
        "style": "option",
        "glyph": DISMISS,
        "classes": ["tone-neutral"],
        "icon": "window-close-symbolic",
        "icon_scale": 0.6,
        "label": "Dismiss",
    }
    if DISMISS in pressed:
        dismiss["pressed"] = True
    tiles = [text, dismiss]
    for k, o in enumerate(ask.options):
        x, width = ask_option_box(ask, k)
        tile = {
            "id": "ask-option-%d" % k,
            "x": x,
            "width": width,
            "style": "option",
            "classes": ["tone-" + o["tone"]],
            "label": o["label"],
        }
        if k in pressed:
            tile["pressed"] = True
        tiles.append(tile)
    return tiles
