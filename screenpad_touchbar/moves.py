"""Moving items between the bar and the apps page by dragging them: where a drag lands, the confirmation, the edit.

A touch on an item in one place (a spot of the bar, or one of the apps
page's [[apps]] items on its home list) that lifts over the other place
moves the item there. The touch stays what it started as (Bar or Surface
follows it, and leaving its item cancels its own action, as always); only
where it lifts makes it a move:

- over an empty spot of the bar, or anywhere on the home list but on an
  item, while that list has room: the item goes at its end (the bar's first
  empty spot), at once;
- over an item, or anywhere on a full home list (then its last item): a
  card asks first, naming what would be replaced, with Cancel and Replace.
  Replace swaps the two, the replaced item going where the dragged one
  was, so both lists keep their length; Cancel, Dismiss, the card's
  timeout, or a card that cannot show leave both lists as they were.

A lift over the bar while a card covers it, over the home list's header,
on an app's page, or that would put an app or a page twice in one list,
is no move. While a drag is over the other place, the scene marks the
spot or cell it would land on ("target") and the item it moves ("moving");
that is the only drawing a drag adds, and only when the mark changes.
"""

from .common import APP, BAR_ITEMS, PAGE_ITEM, ConfigError
from .config import LISTS
from .protocol import Ask

BAR_LIST, APPS_LIST = "bar", "apps"
OTHER = {BAR_LIST: APPS_LIST, APPS_LIST: BAR_LIST}
# How long the Replace card waits for an answer, in seconds.
MOVE_TIMEOUT = 20
OPTIONS = [
    {"id": "cancel", "label": "Cancel", "tone": "neutral"},
    {"id": "replace", "label": "Replace", "tone": "warning"},
]
PLACE_NAMES = {BAR_LIST: "the bar", APPS_LIST: "the apps page"}


class Target:
    """Where a drag lands: index in place's list, and the item there it
    replaces (swapped to the source's spot), or None to go at the end."""

    __slots__ = ("index", "place", "replaced")

    def __init__(self, place, index, replaced=None):
        self.place, self.index, self.replaced = place, index, replaced

    def key(self):
        return (self.place, self.index, self.replaced is None)


def moved(config, source, target):
    """The two lists, {"bar": .., "apps": ..}, once source's item (a
    (place, index, item) the config has) goes to target."""
    place, i, item = source
    lists = {name: list(config.items(name)) for name in LISTS}
    if target.replaced is None:
        del lists[place][i]
        lists[target.place].append(item)
    else:
        lists[place][i] = target.replaced
        lists[target.place][target.index] = item
    return lists


def once_each(items):
    """Whether no app and no page is twice in a list (the config's rule)."""
    named = [(i.kind, i.name) for i in items if i.kind in (APP, PAGE_ITEM)]
    return len(named) == len(set(named))


def short(text, limit=32):
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Asker:
    """The Ask's client for a Replace card: the answer does the move."""

    name = None

    def __init__(self, replace):
        self.replace = replace

    def send(self, msg):
        if msg.get("op") == "answer" and msg.get("option") == "replace":
            self.replace()


class Mover:
    """Follows drags from one place to the other and moves what they drop.

    config() is the config shown; home_cell(px, py) where on the apps
    page's home list a lift lands (Surface.drop_cell(): an [[apps]] index,
    len(apps) elsewhere on the list, None off it); asks the Asks a Replace
    card goes to; edit(change, what) the daemon's edit of the file;
    click() is called for a move made at once (a Replace is the card's
    click). A source is (place, index, item): the item a touch started on,
    which must still be there when it lifts.
    """

    def __init__(self, config, home_cell, asks, edit, click=None):
        self.config, self.home_cell, self.asks, self.edit = config, home_cell, asks, edit
        self.click = click or (lambda: None)
        self.hover = {}  # source -> Target, while a drag is over the other place
        self.asked = 0

    def target(self, source, px, py):
        """Where source's item lands if lifted at px, py; None for no move."""
        place, i, item = source
        config = self.config()
        items = config.items(place)
        if i >= len(items) or items[i] is not item:
            return None  # the config changed under the finger
        to = OTHER[place]
        j = self._at(config, to, px, py)
        if j is None:
            return None
        there = config.items(to)
        if j < len(there):
            target = Target(to, j, there[j])
        elif len(there) < LISTS[to]:
            target = Target(to, len(there))
        else:
            target = Target(to, len(there) - 1, there[-1])
        if not all(once_each(new) for new in moved(config, source, target).values()):
            return None
        return target

    def _at(self, config, place, px, py):
        if place == APPS_LIST:
            return self.home_cell(px, py)
        if self.asks.shown():
            return None  # a card covers the spots
        spot = config.spot_at(px, py)
        return spot if spot is not None and spot < BAR_ITEMS else None

    def over(self, source, px, py):
        """A drag moved: mark where it would land. True if the mark changed."""
        target = self.target(source, px, py)
        old = self.hover.pop(source, None)
        if target is not None:
            self.hover[source] = target
        return (old and old.key()) != (target and target.key())

    def leave(self, source):
        """The touch ended without a move: True if it had a mark."""
        return self.hover.pop(source, None) is not None

    def drop(self, source, px, py):
        """The touch lifted at px, py: move, or ask first. True if it was a
        move (made, or asked about)."""
        self.leave(source)
        target = self.target(source, px, py)
        if target is None:
            return False
        if target.replaced is None:
            if self.edit(lambda c: self.change(c, source, target), "move the item"):
                self.click()
            return True
        place, _, item = source
        name, replaced = short(item.title()), short(target.replaced.title())
        self.asked += 1
        self.asks.add(
            Ask(
                Asker(lambda: self.edit(lambda c: self.change(c, source, target), "move the item")),
                "move-%d" % self.asked,
                "Replace %s?" % replaced,
                "%s takes its place on %s, and %s goes to %s."
                % (name, PLACE_NAMES[target.place], replaced, PLACE_NAMES[place]),
                OPTIONS,
                self.asks.clock() + MOVE_TIMEOUT,
            )
        )
        return True

    @staticmethod
    def change(config, source, target):
        """edit_config()'s change: the move, on the file as it is now
        (config), if it still has the same items where they were."""
        place, i, item = source
        items, there = config.items(place), config.items(target.place)
        same = i < len(items) and items[i].spec() == item.spec()
        if target.replaced is None:
            same = same and len(there) == target.index
        else:
            same = same and target.index < len(there) and there[target.index].spec() == target.replaced.spec()
        if not same:
            raise ConfigError("the items changed since; drag it again")
        now = Target(target.place, target.index, there[target.index] if target.replaced is not None else None)
        return moved(config, (place, i, items[i]), now)

    def marks(self, place):
        """{index: class} for place's spots or cells: "moving" on an item a
        drag over the other place moves, "target" where one would land (on
        the apps page, only on an item it would replace)."""
        out = {}
        for (p, i, _), t in self.hover.items():
            if p == place:
                out[i] = "moving"
            if t.place == place and (place == BAR_LIST or t.replaced is not None):
                out[t.index] = "target"
        return out

    def hint(self):
        """What the apps page's header says while a drag from the bar is
        over its home list, or None."""
        for (_, _, item), t in self.hover.items():
            if t.place == APPS_LIST:
                name = short(item.title())
                if t.replaced is None:
                    return "Lift to add %s to the apps page." % name
                return "Lift to replace %s with %s; you will be asked first." % (short(t.replaced.title()), name)
        return None
