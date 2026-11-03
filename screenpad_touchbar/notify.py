"""GNOME notifications as the extension forwards them.

Normalizing them, the mirror rules, and the question a mirrored one becomes.
"""

from .common import MAX_OPTIONS, OPTION_ID, URGENCIES, ConfigError
from .config import app_id
from .protocol import Ask, ProtocolError, known_fields

# The extension forwards each notification GNOME's message tray asks a banner
# for, normalized, as one "notify" message:
#
#   {"op": "notify", "id": ID, "app": "org.gnome.Calendar.desktop" | null,
#    "source": "Calendar", "title": .., "text": .., "urgency": "normal",
#    "actions": ["Join", "Snooze"], "banners": true, "dnd": false,
#    "acknowledged": false}
#
# "banners" is whether GNOME shows banners for that app (false under Do Not
# Disturb too), "dnd" whether Do Not Disturb is on. The rules of what the bar
# mirrors are here, not in the extension, so they are tested here.


def clip(value, limit):
    """value as one line of printable text, at most limit characters."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def mirror_refusal(card, policy):
    """Why the bar does not mirror this notification, or None if it does.

    Only notifications GNOME would show a banner for (not low urgency, not
    already seen, banners on for the app and Do Not Disturb off, except for
    critical ones, as GNOME does), and of those only the ones with buttons
    or that are urgent (high or critical). Then the config's say.
    """
    urgency = card["urgency"]
    if not policy.mirror:
        return "mirroring is off in the config"
    if card["app"] in policy.deny:
        return "the config denies %s" % card["app"]
    if policy.allow is not None and card["app"] not in policy.allow:
        return "%s is not in the config's allow list" % (card["app"] or "an app with no id")
    if card["acknowledged"]:
        return "already seen"
    if urgency == "low":
        return "low urgency"
    if urgency != "critical" and card["dnd"]:
        return "do not disturb"
    if urgency != "critical" and not card["banners"]:
        return "banners are off for the app"
    if not card["actions"] and urgency not in ("high", "critical"):
        return "no buttons and not urgent"
    return None


def parse_notify(msg):
    """The normalized notification of a "notify" message."""
    known_fields(msg, {"id", "app", "source", "title", "text", "urgency", "actions", "banners", "dnd", "acknowledged"})
    if not isinstance(msg.get("id"), str) or not OPTION_ID.fullmatch(msg["id"]):
        raise ProtocolError('id must be a name like "n1" (letters, digits, . _ -, at most 32)')
    app = msg.get("app")
    try:
        app = app_id(app) if app is not None else None
    except ConfigError:
        app = None
    if msg.get("urgency", "normal") not in URGENCIES:
        raise ProtocolError("urgency must be one of %s" % ", ".join(URGENCIES))
    actions = msg.get("actions", [])
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        raise ProtocolError("actions must be a list of button labels")
    flags = {}
    for key, default in (("banners", True), ("dnd", False), ("acknowledged", False)):
        flags[key] = msg.get(key, default)
        if not isinstance(flags[key], bool):
            raise ProtocolError("%s must be true or false" % key)
    return dict(
        flags,
        id=msg["id"],
        app=app,
        source=clip(msg.get("source"), 80),
        title=clip(msg.get("title"), 80),
        text=clip(msg.get("text"), 400),
        urgency=msg.get("urgency", "normal"),
        actions=[clip(a, 24) or "Action" for a in actions],
    )


def notification_ask(client, card, now, timeout):
    """The question that shows a notification: its first MAX_OPTIONS buttons
    as answers "action-0".., the title (else the app's name) and the text."""
    title = card["title"] or card["source"] or "Notification"
    text = " \u00b7 ".join(p for p in (card["source"] if card["source"] != title else "", card["text"]) if p)
    options = [
        {"id": "action-%d" % k, "label": label, "tone": "neutral"}
        for k, label in enumerate(card["actions"][:MAX_OPTIONS])
    ]
    return Ask(client, card["id"], title, clip(text, 200) or None, options, now + timeout, notification=True)
