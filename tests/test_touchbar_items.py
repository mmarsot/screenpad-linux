"""Tests for items of every kind in both places: the apps page's [[apps]]
items (keys, commands, apps, apps' tiles and pages) on the home page and
what touches on them do, and a page item on the bar opening its page, all
in the daemon.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import (
    AXES,
    GRID_H,
    GRID_Y,
    SYN,
    Clock,
    ConfigHome,
    FakeClient,
    config,
    hello,
    lift,
    page_msg,
    touch,
)

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
    TAP_TIME,
    WIDGETS_PROTOCOL,
    runtime_file,
    state_path,
)
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.splitter import Splitter

ITEMS = """
[[bar]]
label = "PTT"
mode = "hold"
key = "F14"
[[bar]]
label = "Agents page"
page = "agents"
[[apps]]
app = "org.gnome.Settings.desktop"
[[apps]]
label = "Talk"
mode = "hold"
key = "F14"
[[apps]]
label = "Mute"
key = "micmute"
[[apps]]
label = "Run"
command = "x"
[[apps]]
label = "Calls"
tile = "calls"
[[apps]]
label = "Later"
page = "later"
[[apps]]
page = "agents"
"""


class ItemsTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(ConfigHome(self.enterContext(tempfile.TemporaryDirectory())))
        self.clock = Clock()
        self.daemon = d = Daemon(config(ITEMS), None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = lambda: True
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
        self.client = hello(d.apps)
        d.apps.handle(self.client, page_msg())
        d.apps.handle(self.client, page_msg(page="other", title="Other"))

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

    def widgets(self):
        return {w["id"]: w for w in self.published()["surface"]["widgets"]}

    def press(self, widget, slot=0, hold=False):
        """A touch on a widget, in device units (twice panel pixels), held
        still for the tap time if hold; returns the lift."""
        self.clock.now += ASK_ARM
        x, y = (widget["x"] + 20) * 2, (widget["y"] + 20) * 2
        self.frame(touch(slot, 1, x, y))
        if hold:
            self.clock.now += TAP_TIME
            self.daemon.settle()
        return lambda: self.frame(lift(slot))

    def test_the_home_page_lists_the_items_then_the_pages(self):
        self.daemon.pager.turn(APPS)
        w = self.published()["surface"]["widgets"]
        cells = [x for x in w if x["id"] not in ("surface/title",)]
        self.assertEqual(
            [(x["id"], x.get("label")) for x in cells],
            [
                # An app without a label: the extension names it.
                ("surface/item:0", None),
                ("surface/item:1", "Talk"),
                ("surface/item:2", "Mute"),
                ("surface/item:3", "Run"),
                ("surface/item:4", "Calls"),
                ("surface/item:5", "Later"),
                ("surface/open:agents", "Agents"),
                # The page no item places, after the items.
                ("surface/open:other", "Other"),
            ],
        )
        self.assertEqual(cells[0]["app"], "org.gnome.Settings.desktop")
        # Nothing provides the tile, nor the page "later": dimmed, inert.
        self.assertIn("inert", cells[4]["classes"])
        self.assertIn("inert", cells[5]["classes"])
        self.assertNotIn("inert", cells[1]["classes"])
        # Eight cells: the usual 4 by 2; a ninth takes a third row.
        self.assertEqual((cells[0]["width"], cells[0]["height"]), (PANEL_W // HOME_COLUMNS, GRID_H // 2))
        self.daemon.apps.handle(self.client, page_msg(page="third"))
        cells = [x for x in self.published()["surface"]["widgets"] if x["id"] != "surface/title"]
        self.assertEqual((len(cells), cells[8]["x"], cells[8]["y"]), (9, 0, GRID_Y + round(2 * GRID_H / 3)))
        self.assertAlmostEqual(cells[0]["height"], GRID_H / 3, delta=1)

    def test_touches_do_what_the_items_do(self):
        d = self.daemon
        d.pager.turn(APPS)
        w = self.widgets()
        # An app opens on the lift.
        self.press(w["surface/item:0"])()
        self.assertEqual(self.ran, [LAUNCH + ["org.gnome.Settings.desktop"]])
        # A hold key is down while the finger is, once it is a press (still
        # for the tap time), and clicks as it goes down.
        clicks = d.clicks
        up = self.press(w["surface/item:1"])
        self.assertEqual((self.keys, d.clicks), ([], clicks))
        self.clock.now += TAP_TIME
        self.assertTrue(d.settle())
        self.assertEqual((self.keys, d.clicks), ([[(EV_KEY, 184, 1), SYN]], clicks + 1))
        up()
        self.assertEqual((self.keys[-1], d.clicks), ([(EV_KEY, 184, 0), SYN], clicks + 1))
        # A quick tap on it presses and releases it on the lift, one click.
        self.keys.clear()
        self.press(w["surface/item:1"])()
        self.assertEqual((self.keys, d.clicks), ([[(EV_KEY, 184, 1), SYN], [(EV_KEY, 184, 0), SYN]], clicks + 2))
        clicks += 1
        # A tap key on the lift; a command runs.
        self.keys.clear()
        self.press(w["surface/item:2"])()
        self.assertEqual(self.keys, [[(EV_KEY, 248, 1), SYN], [(EV_KEY, 248, 0), SYN]])
        self.press(w["surface/item:3"])()
        self.assertEqual(self.ran[-1], ["/bin/sh", "-c", "x"])
        self.assertEqual(d.clicks, clicks + 3)
        # An inert item (the tile no app provides) does nothing.
        self.press(w["surface/item:4"])()
        self.assertEqual((len(self.ran), d.clicks), (2, clicks + 3))
        # A page item opens its page.
        self.press(w["surface/open:agents"])()
        self.assertEqual(self.published()["surface"]["page"], "agents")

    def test_a_hold_key_lets_go_when_the_finger_slides_away_or_the_bar_goes(self):
        d = self.daemon
        d.pager.turn(APPS)
        w = self.widgets()
        talk = w["surface/item:1"]
        self.press(talk, hold=True)
        self.frame(touch(0, None, 3800, 2100))
        self.frame(lift(0))
        self.assertEqual(self.keys[-1], [(EV_KEY, 184, 0), SYN])
        self.keys.clear()
        self.press(talk, hold=True)
        d.surface.release_all()
        self.assertEqual(self.keys[-1], [(EV_KEY, 184, 0), SYN])

    def test_an_apps_tile_on_the_apps_page(self):
        d = self.daemon
        calls = FakeClient()
        d.apps.connect(calls)
        d.apps.handle(calls, {"op": "hello", "app": "calls", "tiles": ["calls"]})
        self.assertEqual(calls.last("welcome")["placed"], ["calls"])
        d.apps.handle(calls, {"op": "set", "tile": "calls", "label": "2 calls", "tone": "accept"})
        d.pager.turn(APPS)
        tile = self.widgets()["surface/item:4"]
        self.assertEqual((tile["label"], tile["classes"]), ("2 calls", ["tone-accept"]))
        up = self.press(tile)
        up()
        self.assertEqual(
            [(m["type"], m.get("inside")) for m in calls.sent if m["op"] == "event"], [("down", None), ("up", True)]
        )

    def test_a_page_item_on_the_bar_opens_its_page(self):
        d = self.daemon
        tile = self.published()["tiles"][1]
        self.assertEqual((tile["label"], tile["classes"]), ("Agents", ["tile", "tone-warning"]))
        x = (tile["x"] + 20) * 2
        self.frame(touch(0, 1, x, 40))
        self.frame(lift(0))
        state = self.published()
        self.assertEqual((state["page"], state["surface"]["page"]), ("apps", "agents"))
        # Without the page, or without the pages, it is dimmed and does nothing.
        d.pager.turn(HOME)
        d.surface.open = None
        d.apps.handle(self.client, {"op": "unpage", "page": "agents"})
        self.assertEqual(self.published()["tiles"][1]["classes"], ["tile", "offline"])
        self.frame(touch(0, 2, x, 40))
        self.frame(lift(0))
        self.assertEqual(self.published()["page"], "touchpad")
        d.apps.handle(self.client, page_msg())
        d.pager.can_show = lambda: False
        self.assertEqual(self.published()["tiles"][1]["classes"], ["tile", "offline"])
        self.frame(touch(0, 3, x, 40))
        self.frame(lift(0))
        self.assertEqual(self.published()["page"], "touchpad")


if __name__ == "__main__":
    unittest.main()
