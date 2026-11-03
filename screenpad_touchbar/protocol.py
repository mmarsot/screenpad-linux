"""The app socket's messages: validation, and questions (asks) as apps send them."""

from .common import ASK_TIMEOUT, MAX_ASK_TIMEOUT, MAX_OPTIONS, OPTION_ID, TONES

# Programs use the bar through a Unix socket next to touchbar.json
# (SOCKET_NAME, in the user's own runtime directory), one JSON object per
# line each way. docs/app-api.md is the reference. In short, an app sends:
#
#   {"op": "hello", "app": NAME, "tiles": [TILE, ...]}   the tiles it draws
#   {"op": "set", "tile": TILE, "icon": .., "label": .., "text": ..,
#    "badge": .., "tone": ..}                              what a tile shows
#   {"op": "ask", "id": ID, "title": .., "text": .., "timeout": SECONDS,
#    "options": [{"id": .., "label": .., "tone": ..}, ...]}  a question
#   {"op": "page", "page": NAME, ..., "widgets": [...]}  a page of widgets
#                                    for the apps page (widgets.py)
#   {"op": "unpage", "page": NAME}
#   {"op": "cancel", "id": ID}  and  {"op": "ping"}
#   {"op": "notify", ...}   a GNOME notification (the extension; see below)
#
# and gets back "welcome" (which of its tiles have a spot), "event" (a touch
# on its tile: "down", then "up" with "inside" when the finger lifted on
# it; on a widget of its page: "down", then "tap", "long" or "cancel"),
# "answer" (the chosen option id, or null with the reason: "dismissed" when
# the user closed the card, "timeout", "unavailable" when the bar
# cannot show questions, "cancelled", or "ignored" with "why" for a
# notification the bar does not mirror), "pong" and "error".


class ProtocolError(Exception):
    pass


def text_field(value, what, limit, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise ProtocolError("%s must be printable text of 1 to %d characters" % (what, limit))
    return value.strip()


def known_fields(msg, allowed):
    unknown = set(msg) - allowed - {"op"}
    if unknown:
        raise ProtocolError("unknown field(s): %s" % ", ".join(sorted(unknown)))


def tone_field(value):
    if value is None:
        return "neutral"
    if value not in TONES:
        raise ProtocolError("tone must be one of %s" % ", ".join(TONES))
    return value


class Ask:
    """A question from an app, with up to MAX_OPTIONS answers; or a GNOME
    notification shown the same way (notification True)."""

    __slots__ = ("client", "deadline", "id", "notification", "options", "shown_at", "text", "title")

    def __init__(self, client, id, title, text, options, deadline, notification=False):
        self.client, self.id, self.title, self.text = client, id, title, text
        self.options, self.deadline, self.shown_at = options, deadline, None
        self.notification = notification


def parse_ask(client, msg, now):
    """An Ask from an "ask" message; ProtocolError if it is not one."""
    known_fields(msg, {"id", "title", "text", "options", "timeout"})
    ask_id = msg.get("id")
    if not isinstance(ask_id, str) or not OPTION_ID.fullmatch(ask_id):
        raise ProtocolError('id must be a name like "q1" (letters, digits, . _ -, at most 32)')
    title = text_field(msg.get("title"), "title", 80)
    text = text_field(msg.get("text"), "text", 200, required=False)
    options = msg.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        raise ProtocolError("options must be a list of 1 to %d answers" % MAX_OPTIONS)
    clean: list[dict] = []
    for o in options:
        if not isinstance(o, dict):
            raise ProtocolError("each option is an object with id and label")
        known_fields(o, {"id", "label", "tone"})
        if not isinstance(o.get("id"), str) or not OPTION_ID.fullmatch(o["id"]):
            raise ProtocolError('option id must be a name like "allow"')
        if o["id"] in [c["id"] for c in clean]:
            raise ProtocolError("option id %s appears twice" % o["id"])
        clean.append(
            {"id": o["id"], "label": text_field(o.get("label"), "option label", 24), "tone": tone_field(o.get("tone"))}
        )
    timeout = msg.get("timeout", ASK_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= MAX_ASK_TIMEOUT:
        raise ProtocolError("timeout must be 1 to %d seconds" % MAX_ASK_TIMEOUT)
    return Ask(client, ask_id, title, text, clean, now + timeout)
