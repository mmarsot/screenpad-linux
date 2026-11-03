"""Tests for dragging items between the bar and the apps page's home list:
where a drag lands, what it marks while over the other place, the Replace
card and its answers, the swap, and the file edited in place, all in the
daemon.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import AXES, GRID_H, GRID_Y, SYN, Clock, ConfigHome, FakeClient, hello, lift, page_msg, touch

from screenpad_touchbar import daemon as daemon_module
from screenpad_touchbar.codes import EV_KEY
from screenpad_touchbar.common import (
    APPS,
    ASK_ARM,
    HOME,
    HOME_COLUMNS,
    LAUNCH,
    PANEL_W,
    RENDERER_NAME,
    SPOT_W,
    SPOT_X,
    WIDGETS_PROTOCOL,
    runtime_file,
    state_path,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.moves import MOVE_TIMEOUT
from screenpad_touchbar.protocol import Ask
from screenpad_touchbar.scene import ask_option_box
from screenpad_touchbar.splitter import Splitter

CONFIG = """\
height = 160

# Talk.
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"  # mine

[[bar]]
app = "files.desktop"
label = "Files"

# From the agents app.
[[bar]]
label = "Agents"
tile = "agents"

[notifications]
mirror = false

# The apps page.
[[apps]]
app = "settings.desktop"

[[apps]]
label = "Mute"
key = "micmute"
"""


def spot(i):
    """The middle of the bar's spot i, in panel pixels."""
    return SPOT_X[i] + SPOT_W // 2, 80


def cell(i):
    """The middle of the home list's cell i (two rows of four), in panel pixels."""
    w, h = PANEL_W // HOME_COLUMNS, GRID_H // 2
    return (i % HOME_COLUMNS) * w + w // 2, GRID_Y + (i // HOME_COLUMNS) * h + h // 2


class MovesTests(unittest.TestCase):
    def setUp(self):
        home = self.enterContext(tempfile.TemporaryDirectory())
        self.path = self.enterContext(ConfigHome(home))
        # Apps' names come from desktop files there only.
        data = os.path.join(home, "data")
        self.enterContext(mock.patch.dict(os.environ, {"XDG_DATA_HOME": data, "XDG_DATA_DIRS": data}))
        os.makedirs(os.path.join(data, "applications"))
        with open(os.path.join(data, "applications", "settings.desktop"), "w") as f:
            f.write("[Desktop Entry]\nName=Settings\nName[fr]=Paramètres\n[Desktop Action x]\nName=Other\n")
        self.write(CONFIG)
        self.clock = Clock()
        self.daemon = d = Daemon(load_config()[0], None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = lambda: True
        d.apps.asks.can_show = lambda: True
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
        d.pager.turn(APPS)
        # The apps page has been shown a while: its widgets are armed.
        self.published()
        self.contact = 0

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
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

    def down(self, at, slot=0):
        """A finger down at a point, in panel pixels (twice that in device units)."""
        self.clock.now += ASK_ARM
        self.contact += 1
        self.frame(touch(slot, self.contact, at[0] * 2, at[1] * 2))

    def move(self, at, slot=0):
        self.frame(touch(slot, None, at[0] * 2, at[1] * 2))

    def drag(self, start, end, slot=0):
        """A finger down at start, moved to end and lifted there; returns
        the scene while it was over end."""
        self.down(start, slot)
        self.move(end, slot)
        state = self.published()
        self.frame(lift(slot))
        self.daemon.check_config()
        return state

    def lists(self):
        c = self.daemon.config
        return [i.title() for i in c.bar], [i.title() for i in c.apps]

    def widget(self, state, wid):
        return next(w for w in state["surface"]["widgets"] if w["id"] == "surface/" + wid)

    def answer(self, option):
        """Tap an answer of the card shown (0 Cancel, 1 Replace)."""
        self.clock.now += ASK_ARM
        x, width = ask_option_box(self.daemon.apps.asks.shown(), option, self.daemon.bar.card_height())
        x += width // 2
        self.down((x, 200))
        self.frame(lift(0))
        self.daemon.check_config()

    def test_a_bar_item_to_a_free_cell_moves_at_once(self):
        d = self.daemon
        clicks = d.clicks
        state = self.drag(spot(1), cell(5))
        # While over the list: the item is marked moving, and the header
        # says where it goes; no cell is the target (it goes at the end).
        self.assertIn("moving", state["tiles"][1]["classes"])
        self.assertEqual(self.widget(state, "title")["text"], "Lift to add Files to the apps page.")
        self.assertFalse(any("target" in w["classes"] for w in state["surface"]["widgets"]))
        self.assertEqual(self.lists(), (["PTT", "Agents"], ["Settings", "Mute", "Files"]))
        # Not opened, one click for the move, and no card.
        self.assertEqual((self.ran, d.clicks, d.apps.asks.shown()), ([], clicks + 1, None))
        # Its block moved as it was, the comments stayed where they were.
        self.assertEqual(
            self.text(),
            CONFIG.replace('[[bar]]\napp = "files.desktop"\nlabel = "Files"\n\n', "")
            + '\n[[apps]]\napp = "files.desktop"\nlabel = "Files"\n',
        )
        # And back: from the list to the bar's first empty spot, which is marked.
        state = self.drag(cell(2), spot(5))
        self.assertIn("target", state["tiles"][2]["classes"])
        self.assertIn("moving", self.widget(state, "item:2")["classes"])
        self.assertEqual(self.lists(), (["PTT", "Agents", "Files"], ["Settings", "Mute"]))
        self.assertEqual(self.published()["tiles"][2].get("classes"), None)

    def test_a_drop_on_an_item_asks_then_swaps(self):
        d = self.daemon
        state = self.drag(spot(1), cell(1))
        self.assertIn("target", self.widget(state, "item:1")["classes"])
        self.assertIn("replace Mute with Files", self.widget(state, "title")["text"])
        ask = d.apps.asks.shown()
        self.assertEqual(
            (ask.title, ask.text, [o["label"] for o in ask.options]),
            (
                "Replace Mute?",
                "Files takes its place on the apps page, and Mute goes to the bar.",
                ["Cancel", "Replace"],
            ),
        )
        # Nothing changes until it is answered; Cancel leaves both lists.
        self.assertEqual(self.text(), CONFIG)
        self.answer(0)
        self.assertEqual((d.apps.asks.shown(), self.text()), (None, CONFIG))
        # Replace swaps them: each takes the other's place.
        self.drag(spot(1), cell(1))
        self.answer(1)
        self.assertEqual(self.lists(), (["PTT", "Mute", "Agents"], ["Settings", "Files"]))
        self.assertIn('# Talk.\n[[bar]]\nlabel = "PTT"', self.text())
        self.assertIn('[[bar]]\nlabel = "Mute"\nkey = "micmute"\n\n# From the agents app.', self.text())
        self.assertTrue(self.text().endswith('[[apps]]\napp = "files.desktop"\nlabel = "Files"\n'))

    def test_the_card_going_unanswered_changes_nothing(self):
        d = self.daemon
        self.drag(cell(0), spot(0))
        # An app without a label by its desktop file's name.
        self.assertEqual(
            (d.apps.asks.shown().title, d.apps.asks.shown().text),
            ("Replace PTT?", "Settings takes its place on the bar, and PTT goes to the apps page."),
        )
        self.clock.now += MOVE_TIMEOUT
        d.apps.asks.tick()
        self.assertEqual((d.apps.asks.shown(), self.text()), (None, CONFIG))
        # Dismiss too.
        self.drag(cell(0), spot(0))
        self.clock.now += ASK_ARM
        self.down((2 * SPOT_W + SPOT_W // 2, 200))
        self.frame(lift(0))
        self.assertEqual((d.apps.asks.shown(), self.text()), (None, CONFIG))
        # A card that cannot show: the asker hears "unavailable", nothing moves.
        d.apps.asks.can_show = lambda: False
        self.drag(cell(0), spot(0))
        self.assertEqual((d.apps.asks.shown(), self.text()), (None, CONFIG))

    def test_a_full_bar_asks_to_replace_the_spot_under_the_finger(self):
        self.write(CONFIG.replace("# The apps page.", "".join('[[bar]]\napp = "%s.desktop"\n\n' % a for a in "xyz")))
        self.daemon.reload = True
        self.daemon.check_config()
        self.assertEqual(len(self.daemon.config.bar), 6)
        self.drag(cell(1), spot(4))
        self.assertEqual(self.daemon.apps.asks.shown().title, "Replace y?")
        self.answer(1)
        self.assertEqual(self.lists(), (["PTT", "Files", "Agents", "x", "Mute", "z"], ["Settings", "y"]))

    def test_a_full_list_asks_to_replace_its_last_item(self):
        apps = "".join('[[apps]]\napp = "a%d.desktop"\n\n' % n for n in range(6))
        self.write(CONFIG + "\n" + apps)
        self.daemon.reload = True
        self.daemon.check_config()
        # On a free place of a full list (a page no item places, here).
        self.daemon.apps.handle(hello(self.daemon.apps), page_msg())
        page = self.widget(self.published(), "open:agents")
        self.clock.now += ASK_ARM
        state = self.drag(spot(1), (page["x"] + 20, page["y"] + 20))
        self.assertIn("target", self.widget(state, "item:7")["classes"])
        self.assertEqual(self.daemon.apps.asks.shown().title, "Replace a5?")
        self.answer(1)
        self.assertEqual(self.lists()[1][-1], "Files")
        self.assertEqual(self.lists()[0], ["PTT", "a5", "Agents"])

    def test_what_is_no_move(self):
        d = self.daemon
        # Lifted back on the bar, or on the header: nothing, and a tap item
        # slid away does not fire.
        self.drag(spot(1), spot(4))
        self.drag(spot(1), (900, 200))
        self.drag(cell(1), (900, 200))
        self.assertEqual((self.ran, self.keys, self.text()), ([], [], CONFIG))
        # On an app's page, the list is not shown.
        client = hello(d.apps)
        d.apps.handle(client, page_msg())
        d.surface.open = "agents"
        self.drag(spot(1), cell(5))
        self.assertEqual(self.text(), CONFIG)
        d.surface.open = None
        # On the touchpad page a bar touch sliding down is still nothing.
        d.pager.turn(HOME)
        self.drag(spot(1), cell(5))
        self.assertEqual(self.text(), CONFIG)
        d.pager.turn(APPS)
        # An app the other list has already: no target.
        self.write(CONFIG.replace('app = "files.desktop"', 'app = "settings.desktop"'))
        d.reload = True
        d.check_config()
        state = self.drag(spot(1), cell(5))
        self.assertEqual(self.widget(state, "title")["text"][:4], "Tap ")
        self.assertEqual(len(d.config.bar), 3)
        # While a card covers the bar, the spots take no drop.
        d.apps.asks.add(Ask(FakeClient(), "q", "A question", None, [{"id": "a", "label": "A", "tone": "neutral"}], 1e9))
        self.clock.now += ASK_ARM
        self.drag(cell(1), spot(4))
        self.assertEqual(len(d.config.apps), 2)

    def test_a_drop_on_a_cell_that_just_changed_is_no_move(self):
        d = self.daemon
        self.write(CONFIG.replace('label = "Mute"\nkey = "micmute"', 'label = "Calls"\ntile = "calls"'))
        d.reload = True
        d.check_config()
        calls = FakeClient()
        d.apps.connect(calls)
        d.apps.handle(calls, {"op": "hello", "app": "calls", "tiles": ["calls"]})
        self.published()
        for wait, asked in ((0, False), (ASK_ARM, True)):
            self.down(spot(1))
            self.move(cell(1))
            # The tile under the finger changes: a drop on it waits ASK_ARM.
            d.apps.handle(calls, {"op": "set", "tile": "calls", "label": "%d calls" % wait})
            self.clock.now += wait
            self.move((cell(1)[0] + 4, cell(1)[1]))
            self.frame(lift(0))
            self.assertEqual(d.apps.asks.shown() is not None, asked)
        self.assertEqual(d.apps.asks.shown().title, "Replace Calls?")

    def test_the_config_reloading_under_a_drag_ends_it(self):
        d = self.daemon
        self.down(spot(1))
        self.move(cell(5))
        d.reload = True
        d.check_config()
        self.move((cell(5)[0] + 4, cell(5)[1]))
        self.frame(lift(0))
        d.check_config()
        self.assertEqual((self.text(), self.ran), (CONFIG, []))

    def test_a_held_key_and_an_offline_tile(self):
        d = self.daemon
        # The key is down from the touch to the lift, as in a swipe, and no more.
        self.drag(spot(0), cell(5))
        self.assertEqual(self.keys, [[(EV_KEY, 184, 1), SYN], [(EV_KEY, 184, 0), SYN]])
        self.assertEqual(self.lists(), (["Files", "Agents"], ["Settings", "Mute", "PTT"]))
        # A tile no app provides does nothing, but moves.
        self.drag(spot(1), cell(5))
        self.assertEqual(self.lists(), (["Files"], ["Settings", "Mute", "PTT", "Agents"]))
        self.assertEqual(d.apps.placed, {"agents"})
        # And the file holds both, comments kept.
        self.assertIn('[[apps]]\nlabel = "PTT"\nmode = "hold"\nkey = "F14"  # mine\n', self.text())
        self.assertIn("# Talk.\n", self.text())

    def test_the_config_changing_before_the_answer_moves_nothing(self):
        self.drag(spot(1), cell(1))
        changed = CONFIG.replace('label = "Mute"', 'label = "Quiet"')
        self.write(changed)
        self.answer(1)
        self.assertEqual(self.text(), changed)

    def test_slide_away_and_open_still_work(self):
        # A tap on an app opens it; a touch on an item that lifts on it is
        # its own, not a move.
        self.down(spot(1))
        self.frame(lift(0))
        self.assertEqual(self.ran, [LAUNCH + ["files.desktop"]])
        self.down(cell(1))
        self.frame(lift(0))
        self.assertEqual(self.keys, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])
        self.assertEqual(self.text(), CONFIG)


if __name__ == "__main__":
    unittest.main()
