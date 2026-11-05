"""Tests for removing items with a finger: a long hold on the bar or the apps
page's home list, then a lift, takes the item out of the file, for every kind
that acts on a lift; hold items, an app's tile, the page button and the grip
stay; and the file loses exactly the chosen block. All in the daemon, with a
real config file.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import AXES, GRID_H, GRID_Y, SYN, Clock, ConfigHome, hello, lift, page_msg, touch

from screenpad_touchbar import daemon as daemon_module
from screenpad_touchbar.common import (
    APPS,
    ASK_ARM,
    HOME_COLUMNS,
    LONG_PRESS,
    PAGE_X,
    PANEL_W,
    RENDERER_NAME,
    SPOT_W,
    SPOT_X,
    TAP_TIME,
    WIDGETS_PROTOCOL,
    runtime_file,
    state_path,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.splitter import Splitter

# Blocks that start with a comment, to see that only the chosen block goes.
BLOCKS = {
    "ptt": '# Talk.\n[[bar]]\nlabel = "PTT"\nmode = "hold"\nkey = "F14"\n',
    "shot": '[[bar]]\nlabel = "Screenshot"\nkey = "print"\n',
    "run": '[[bar]]\nlabel = "Run"\ncommand = "x"\n',
    "files": '[[bar]]\napp = "files.desktop"\n',
    "agents": '[[bar]]\nlabel = "Agents"\npage = "agents"\n',
    "calls": '[[bar]]\nlabel = "Calls"\ntile = "calls"\n',
    "mute": '[[apps]]\nlabel = "Mute"\nkey = "micmute"\n',
    "talk": '# Hold.\n[[apps]]\nlabel = "Talk"\nmode = "hold"\nkey = "F14"\n',
    "lock": '[[apps]]\nlabel = "Lock"\nkey = "super+l"\n',
    "cmd": '[[apps]]\nlabel = "Cmd"\ncommand = "y"\n',
    "settings": '[[apps]]\napp = "settings.desktop"\n',
    "tile": '[[apps]]\nlabel = "Tile"\ntile = "chat"\n',
}
BAR = ["ptt", "shot", "run", "files", "agents", "calls"]
APPS_LIST = ["mute", "talk", "lock", "cmd", "settings", "tile"]


def config_text(without=()):
    return "height = 160\n\n" + "\n".join(BLOCKS[k] for k in BAR + APPS_LIST if k not in without)


def spot(i):
    """The middle of the bar's spot i, in panel pixels."""
    return SPOT_X[i] + SPOT_W // 2, 80


def cell(i):
    """The middle of the home list's cell i (two rows of four), in panel pixels."""
    w, h = PANEL_W // HOME_COLUMNS, GRID_H // 2
    return (i % HOME_COLUMNS) * w + w // 2, GRID_Y + (i // HOME_COLUMNS) * h + h // 2


class RemoveTests(unittest.TestCase):
    def setUp(self):
        home = self.enterContext(tempfile.TemporaryDirectory())
        self.path = self.enterContext(ConfigHome(home))
        os.makedirs(os.path.dirname(self.path))
        self.write(config_text())
        self.clock = Clock()
        self.daemon = d = Daemon(load_config()[0], None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = d.apps.asks.can_show = lambda: True
        self.ran = []
        d.run_command = self.ran.append  # type: ignore[method-assign]  # nothing runs
        self.keys = []
        d.bar = d.new_bar(KeyEmitter(self.keys.append))
        d.bar.clock = self.clock
        d.splitter = Splitter(AXES, 5, d._zone)
        self.enterContext(mock.patch.object(daemon_module, "write_events", lambda fd, ev: None))
        os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)), exist_ok=True)
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL, "drawn": True}, f)
        # An app gives the page "agents", so its page item can open.
        d.apps.handle(hello(d.apps), page_msg())
        d.pager.turn(APPS)
        self.published()
        self.contact = 0

    def write(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def text(self):
        with open(self.path) as f:
            return f.read()

    def frame(self, *groups):
        out = None
        for e in [e for g in groups for e in g] + [SYN]:
            out = self.daemon.splitter.feed(*e)
        assert out is not None and out != "resync"
        self.daemon.route(*out)

    def published(self):
        self.daemon.publish()
        with open(state_path()) as f:
            return json.load(f)

    def titles(self):
        c = self.daemon.config
        return [i.title() for i in c.bar], [i.title() for i in c.apps]

    def hold(self, at, seconds=LONG_PRESS, slot=0):
        """A finger down at a point (panel pixels) and held still for
        seconds, then the scene; the daemon's ticks run as its loop does."""
        self.clock.now += ASK_ARM
        self.contact += 1
        self.frame(touch(slot, self.contact, at[0] * 2, at[1] * 2))
        self.clock.now += max(seconds, TAP_TIME)
        self.daemon.settle()
        self.daemon.surface.tick()
        self.daemon.bar.tick()
        return self.published()

    def lift(self, slot=0):
        self.frame(lift(slot))
        self.daemon.check_config()

    def removing_bar(self, state):
        return [i for i, t in enumerate(state["tiles"]) if "removing" in t.get("classes", [])]

    def removing_cells(self, state):
        return [w["id"] for w in state["surface"]["widgets"] if "removing" in w["classes"]]

    def test_holding_an_item_on_the_bar_then_lifting_removes_exactly_it(self):
        # Every kind that acts on the lift: a tap key (a system item), a
        # command, an app, a page item.
        for n, key in enumerate(BAR):
            if key in ("ptt", "calls"):
                continue
            with self.subTest(key):
                self.write(config_text())
                self.daemon.check_config()
                before = self.titles()
                clicks = self.daemon.clicks
                state = self.hold(spot(n))
                # Drawn red with a bin, as an app always was.
                self.assertEqual(self.removing_bar(state), [n])
                self.assertEqual(state["tiles"][n]["icon"], "edit-delete-symbolic")
                self.lift()
                self.assertEqual(self.text(), config_text(without=[key]))
                after = self.titles()
                self.assertEqual(after[0], [t for i, t in enumerate(before[0]) if i != n])
                self.assertEqual((after[1], self.daemon.clicks), (before[1], clicks + 1))
                # Removing is not tapping: nothing fired, nothing opened.
                self.assertEqual((self.keys, self.ran), ([], []))

    def test_a_short_hold_taps_and_sliding_away_cancels(self):
        state = self.hold(spot(1), seconds=LONG_PRESS / 2)
        self.assertEqual(self.removing_bar(state), [])
        self.lift()
        self.assertEqual(self.text(), config_text())
        self.assertEqual(len(self.keys), 2)  # the tap key's press and release
        self.keys.clear()
        self.hold(spot(1))
        self.frame(touch(0, None, spot(1)[0] * 2, 500 * 2))
        self.assertEqual(self.removing_bar(self.published()), [])
        self.lift()
        self.assertEqual((self.text(), self.keys), (config_text(), []))

    def test_what_holding_is_for_is_not_removed_from_the_bar(self):
        # A hold key is down while the finger is, for as long as it is:
        # push to talk. An app's tile is its app's.
        for n in (0, 5):
            self.hold(spot(n), seconds=3 * LONG_PRESS)
            self.assertEqual(self.removing_bar(self.published()), [])
            self.lift()
            self.assertEqual(self.text(), config_text())
        self.assertEqual([k[0][2] for k in self.keys], [1, 0])

    def test_the_page_button_and_the_grip_are_never_removed(self):
        before = self.daemon.config.height
        for x in (PAGE_X + 20, 1850):
            with self.subTest(x=x):
                self.assertEqual(self.removing_bar(self.hold((x, 80), seconds=3 * LONG_PRESS)), [])
                self.lift()
                self.assertEqual(self.text(), config_text())
                self.assertEqual((self.daemon.config.height, len(self.daemon.config.bar)), (before, 6))

    def test_holding_an_item_on_the_apps_page_then_lifting_removes_exactly_it(self):
        d = self.daemon
        for n, key in enumerate(APPS_LIST):
            if key in ("talk", "tile"):
                continue
            with self.subTest(key):
                self.write(config_text())
                d.check_config()
                clicks = d.clicks
                state = self.hold(cell(n))
                self.assertEqual(self.removing_cells(state), ["surface/item:%d" % n])
                widget = next(w for w in state["surface"]["widgets"] if w["id"] == "surface/item:%d" % n)
                self.assertEqual(widget["icon"], "edit-delete-symbolic")
                self.lift()
                self.assertEqual(self.text(), config_text(without=[key]))
                self.assertEqual(len(d.config.apps), 5)
                self.assertEqual(d.clicks, clicks + 1)
                self.assertEqual((self.keys, self.ran), ([], []))

    def test_a_hold_item_and_an_apps_tile_stay_on_the_apps_page(self):
        for n in (1, 5):
            self.hold(cell(n), seconds=3 * LONG_PRESS)
            self.assertEqual(self.removing_cells(self.published()), [])
            self.lift()
            self.assertEqual(self.text(), config_text())

    def test_a_press_not_held_long_or_slid_away_removes_nothing(self):
        # Pressed (still for the tap time) and lifted: a tap, which fires it.
        state = self.hold(cell(0), seconds=TAP_TIME)
        self.assertEqual(self.removing_cells(state), [])
        self.lift()
        self.assertEqual((self.text(), len(self.keys)), (config_text(), 2))
        # Held long, then slid off the item and lifted elsewhere on the page.
        self.hold(cell(0))
        self.frame(touch(0, None, cell(7)[0] * 2, cell(7)[1] * 2))
        self.assertEqual(self.removing_cells(self.published()), [])
        self.lift()
        self.assertEqual(self.text(), config_text())

    def test_holding_then_dragging_to_the_bar_moves_instead_of_removing(self):
        # The apps page's item goes to the bar's... all six spots are used, so it asks.
        self.hold(cell(0))
        self.frame(touch(0, None, spot(1)[0] * 2, spot(1)[1] * 2))
        self.lift()
        self.assertIsNotNone(self.daemon.apps.asks.shown())
        self.assertEqual(self.titles()[1].count("Mute"), 1)

    def test_a_different_item_is_never_removed_in_its_place(self):
        d = self.daemon
        stale = d.config.bar[1]
        # The file changed under the finger: another item is now at spot 2.
        self.write(config_text(without=["shot"]))
        d.check_config()
        before = self.text()
        self.assertFalse(d.remove_item("bar", 1, stale))
        self.assertFalse(d.remove_item("bar", 9, stale))
        self.assertEqual(self.text(), before)
        self.assertTrue(d.remove_item("bar", 1, d.config.bar[1]))
        self.assertEqual(self.text(), config_text(without=["shot", "run"]))
        # While a finger holds an item through such a change, the touch ends
        # and lifting removes nothing.
        d.check_config()
        self.hold(spot(1))
        self.write(config_text(without=["shot", "run", "files"]))
        d.check_config()
        self.lift()
        self.assertEqual(self.text(), config_text(without=["shot", "run", "files"]))
