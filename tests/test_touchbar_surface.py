"""Tests for the apps surface: apps' pages and their validation, the home
page's list, what touches on widgets do (tap, long press, sliding away, the
arming delay, a widget changing or going under a finger), the surface only
while the extension draws widgets, the daemon's routing to it, and what it
publishes.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from touchbar_support import (
    AXES,
    BAR_CONFIG,
    GRID_H,
    GRID_Y,
    SYN,
    TOP,
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
from screenpad_touchbar.apps import Apps
from screenpad_touchbar.common import (
    APPS,
    ASK_ARM,
    HEADER_H,
    HOME,
    HOME_W,
    LONG_PRESS,
    MAX_PAGES,
    PANEL_W,
    RENDERER_NAME,
    WIDGETS_PROTOCOL,
    runtime_file,
    state_path,
)
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.pager import Pager
from screenpad_touchbar.protocol import ProtocolError
from screenpad_touchbar.splitter import Splitter
from screenpad_touchbar.surface import Surface
from screenpad_touchbar.widgets import parse_page


class PageTests(unittest.TestCase):
    """The page message's validation."""

    def test_a_page(self):
        page = parse_page(None, page_msg())
        self.assertEqual((page.name, page.title(), page.columns, page.rows), ("agents", "Agents", 3, 2))
        self.assertEqual(page.look, {"tone": "warning", "title": "Agents", "text": "1 blocked"})
        prompt, allow, deny = page.widgets
        self.assertEqual((prompt.cols, prompt.rows, prompt.touch), (3, 1, "none"))
        self.assertEqual(prompt.look["lines"], ["Run npm test?", "1. Yes"])
        self.assertEqual((allow.touch, deny.touch, deny.look["tone"]), ("long", "tap", "reject"))
        # Defaults: a 4 by 2 grid, no widgets.
        page = parse_page(None, {"op": "page", "page": "x"})
        self.assertEqual((page.columns, page.rows, page.widgets, page.title()), (4, 2, [], "x"))

    def test_errors(self):
        def widget(**kw):
            return page_msg(widgets=[{"id": "w", "col": 0, "row": 0, "label": "W", **kw}])

        bad = [
            page_msg(page="Agents"),
            page_msg(columns=7),
            page_msg(rows=0),
            page_msg(columns=True),
            page_msg(colour="red"),
            page_msg(title="x" * 33),
            page_msg(widgets="none"),
            page_msg(widgets=[{"id": "w", "col": 0, "row": 0, "label": str(i)} for i in range(25)]),
            widget(col=3),
            widget(row=2),
            widget(cols=4),
            widget(rows=3),
            widget(id="no spaces"),
            widget(touch="press"),
            widget(label=None),
            widget(lines=[]),
            widget(lines=["x" * 121]),
            widget(lines=["tab\there"]),
            widget(lines=["a"] * 17),
            widget(text="x" * 201),
            widget(icon="../icon"),
            widget(size=3),
            page_msg(
                widgets=[{"id": "w", "col": 0, "row": 0, "label": "a"}, {"id": "w", "col": 1, "row": 0, "label": "b"}]
            ),
            page_msg(
                widgets=[
                    {"id": "a", "col": 0, "row": 0, "cols": 2, "label": "a"},
                    {"id": "b", "col": 1, "row": 0, "label": "b"},
                ]
            ),
        ]
        for msg in bad:
            with self.subTest(msg=msg), self.assertRaises(ProtocolError):
                parse_page(None, msg)


class AppPagesTests(unittest.TestCase):
    """Pages through the app protocol."""

    def setUp(self):
        self.apps = Apps()

    def test_page_unpage_and_disconnect(self):
        client = hello(self.apps)
        self.apps.handle(client, page_msg())
        self.assertEqual(list(self.apps.pages), ["agents"])
        self.assertTrue(self.apps.take_changed())
        # The same page again changes nothing; a new version replaces it.
        self.apps.handle(client, page_msg())
        self.assertFalse(self.apps.take_changed())
        self.apps.handle(client, page_msg(text="2 blocked"))
        self.assertTrue(self.apps.take_changed())
        self.assertEqual(self.apps.pages["agents"].look["text"], "2 blocked")
        self.apps.handle(client, {"op": "unpage", "page": "agents"})
        self.assertEqual(self.apps.pages, {})
        self.apps.handle(client, page_msg())
        self.apps.disconnect(client)
        self.assertEqual(self.apps.pages, {})
        self.assertIsNone(client.last("error"))

    def test_refused(self):
        other = hello(self.apps, "other")
        self.apps.handle(other, page_msg())
        client = hello(self.apps)
        for msg, why in (
            (page_msg(), "another app's"),
            ({"op": "unpage", "page": "agents"}, "not yours"),
            ({"op": "unpage", "page": ["agents"]}, "not yours"),
            (page_msg(page="x", rows=9), "rows"),
        ):
            self.apps.handle(client, msg)
            self.assertIn(why, client.last("error")["message"])
        for i in range(MAX_PAGES):
            self.apps.handle(client, page_msg(page="p%d" % i))
        self.apps.handle(client, page_msg(page="one-more"))
        self.assertIn("at most %d pages" % MAX_PAGES, client.last("error")["message"])
        # Before hello, nothing.
        stranger = FakeClient()
        self.apps.connect(stranger)
        self.apps.handle(stranger, page_msg(page="y"))
        self.assertIn("hello", stranger.last("error")["message"])

    def test_press_goes_to_the_owner(self):
        client = hello(self.apps)
        self.apps.handle(client, page_msg())
        self.apps.press(client, "agents", "allow", "tap")
        self.apps.disconnect(client)
        self.apps.press(client, "agents", "allow", "tap")
        self.assertEqual(
            [m for m in client.sent if m["op"] == "event"],
            [{"op": "event", "page": "agents", "widget": "allow", "type": "tap"}],
        )


class SurfaceTests(unittest.TestCase):
    """What touches on the surface do."""

    def setUp(self):
        self.clock = Clock()
        self.apps = Apps(self.clock)
        self.pager = Pager(clock=self.clock)
        self.cover = TOP
        self.draws = True
        self.surface = Surface(
            self.apps,
            self.pager,
            top=lambda: TOP,
            cover=lambda: self.cover,
            can_draw=lambda: self.draws,
            clock=self.clock,
        )
        self.client = hello(self.apps)
        self.apps.handle(self.client, page_msg())
        self.pager.turn(APPS)

    def widgets(self):
        return {w["id"]: w for w in self.surface.scene()["widgets"]}

    def events(self):
        return [(m["widget"], m["type"]) for m in self.client.sent if m["op"] == "event"]

    def tap(self, x, y, slot=0, hold=0.0, to=None):
        self.surface.handle([("begin", slot, x, y)])
        self.clock.now += hold
        self.surface.tick()
        x, y = to or (x, y)
        self.surface.handle([("move", slot, x, y), ("end", slot, x, y)])

    def settle(self):
        """Let the widgets drawn now arm."""
        self.surface.refresh()
        self.clock.now += ASK_ARM

    def open_agents(self):
        self.settle()
        home = self.widgets()["surface/open:agents"]
        self.tap(home["x"] + 10, home["y"] + 10)
        self.assertEqual(self.surface.scene()["page"], "agents")
        self.settle()

    def test_the_home_page_lists_the_apps(self):
        w = self.widgets()
        self.assertEqual(self.surface.scene()["page"], None)
        self.assertEqual(w["surface/title"]["title"], "Apps")
        self.assertIn("8 s", w["surface/title"]["text"])
        self.assertEqual((w["surface/title"]["y"], w["surface/title"]["height"]), (TOP, HEADER_H))
        tile = w["surface/open:agents"]
        self.assertEqual((tile["label"], tile["text"], tile["classes"]), ("Agents", "1 blocked", ["tone-warning"]))
        self.assertEqual((tile["x"], tile["y"], tile["width"], tile["height"]), (0, GRID_Y, PANEL_W // 4, GRID_H // 2))
        # No apps: it says so.
        self.apps.disconnect(self.client)
        w = self.widgets()
        self.assertEqual(w["surface/empty"]["label"], "No apps here yet")
        self.assertIn("inert", w["surface/empty"]["classes"])

    def test_open_a_page_and_go_home(self):
        self.open_agents()
        w = self.widgets()
        self.assertEqual(w["surface/title"]["title"], "Agents")
        self.assertEqual(w["surface/title"]["text"], "fleet")
        self.assertEqual((w["surface/home"]["x"], w["surface/home"]["width"]), (0, HOME_W))
        # The grid: three columns, two rows below the header.
        third = PANEL_W // 3
        self.assertEqual(
            [
                (w[k]["x"], w[k]["y"], w[k]["width"], w[k]["height"])
                for k in ("agents/prompt", "agents/allow", "agents/deny")
            ],
            [
                (0, GRID_Y, PANEL_W, GRID_H // 2),
                (0, GRID_Y + GRID_H // 2, third, GRID_H - GRID_H // 2),
                (2 * third, GRID_Y + GRID_H // 2, third, GRID_H - GRID_H // 2),
            ],
        )
        self.assertEqual(w["agents/prompt"]["lines"], ["Run npm test?", "1. Yes"])
        self.assertEqual(w["agents/allow"]["classes"], ["tone-accept", "long"])
        # Opening it told the app nothing; Home goes back to the list.
        self.assertEqual(self.events(), [])
        self.tap(100, TOP + 50)
        self.assertEqual(self.surface.scene()["page"], None)
        # The page stays chosen while the apps page is away.
        self.open_agents()
        self.pager.turn(HOME)
        self.assertIsNone(self.surface.scene())
        self.pager.turn(APPS)
        self.assertEqual(self.surface.scene()["page"], "agents")

    def test_tap_long_press_and_sliding_away(self):
        self.open_agents()
        deny_y = GRID_Y + GRID_H * 3 // 4
        self.tap(1700, deny_y)
        self.assertEqual(self.events(), [("deny", "down"), ("deny", "tap")])
        self.client.sent.clear()
        # Allow takes a long press: drawn held once LONG_PRESS has passed.
        self.surface.handle([("begin", 0, 300, deny_y)])
        self.assertTrue(self.widgets()["agents/allow"]["pressed"])
        self.assertEqual(self.surface.deadline(), self.clock.now + LONG_PRESS)
        self.clock.now += LONG_PRESS
        self.assertTrue(self.surface.tick())
        self.assertIn("held", self.widgets()["agents/allow"]["classes"])
        self.assertIsNone(self.surface.deadline())
        self.surface.handle([("end", 0, 300, deny_y)])
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "long")])
        self.client.sent.clear()
        # Lifted before LONG_PRESS it is a tap; a tap widget held long is a tap.
        self.tap(300, deny_y, hold=LONG_PRESS - 0.1)
        self.tap(1700, deny_y, hold=LONG_PRESS * 2)
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "tap"), ("deny", "down"), ("deny", "tap")])
        self.client.sent.clear()
        # Sliding away cancels, even after a long hold; sliding back in time
        # (within the slop) still counts.
        self.tap(300, deny_y, hold=LONG_PRESS, to=(1700, deny_y))
        self.surface.handle([("begin", 1, 1700, deny_y), ("move", 1, 1000, deny_y)])
        self.assertNotIn("pressed", self.widgets()["agents/deny"])
        self.surface.handle([("move", 1, 1300, deny_y), ("end", 1, 1300, deny_y)])
        self.surface.handle([("begin", 2, 1700, deny_y), ("cancel", 2, 1700, deny_y)])
        self.assertEqual(
            self.events(),
            [
                ("allow", "down"),
                ("allow", "cancel"),
                ("deny", "down"),
                ("deny", "tap"),
                ("deny", "down"),
                ("deny", "cancel"),
            ],
        )
        # Touches on a widget that takes none, and between widgets, do nothing.
        self.client.sent.clear()
        self.tap(900, GRID_Y + 10)
        self.tap(900, deny_y)
        self.assertEqual(self.events(), [])

    def test_a_touch_too_soon_after_a_widget_appears_does_nothing(self):
        self.surface.refresh()
        self.clock.now += ASK_ARM - 0.01
        home = self.widgets()["surface/open:agents"]
        self.tap(home["x"] + 10, home["y"] + 10)
        self.assertEqual(self.surface.scene()["page"], None)
        self.clock.now += 0.01
        self.tap(home["x"] + 10, home["y"] + 10)
        # The page just opened: its widgets arm ASK_ARM from now.
        deny_y = GRID_Y + GRID_H * 3 // 4
        self.tap(1700, deny_y)
        self.assertEqual(self.events(), [])
        self.clock.now += ASK_ARM
        self.tap(1700, deny_y)
        self.assertEqual(self.events(), [("deny", "down"), ("deny", "tap")])
        self.client.sent.clear()
        # A widget the app changes is armed anew; the others stay armed.
        self.apps.handle(
            self.client,
            page_msg(widgets=page_msg()["widgets"][:2] + [{"id": "deny", "col": 2, "row": 1, "label": "No"}]),
        )
        self.tap(1700, deny_y)
        self.tap(300, deny_y)
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "tap")])
        # A card growing over the page, or shrinking, re-arms every widget.
        self.client.sent.clear()
        self.clock.now += ASK_ARM
        self.cover = 456
        self.tap(300, deny_y)
        self.clock.now += ASK_ARM
        self.cover = TOP
        self.tap(300, deny_y)
        self.assertEqual(self.events(), [])
        # The apps page turned away and back: armed anew.
        self.clock.now += ASK_ARM
        self.pager.turn(HOME)
        self.surface.refresh()
        self.pager.turn(APPS)
        self.tap(300, deny_y)
        self.assertEqual(self.events(), [])

    def test_a_widget_that_changes_or_goes_under_a_finger_chooses_nothing(self):
        self.open_agents()
        deny_y = GRID_Y + GRID_H * 3 // 4
        self.surface.handle([("begin", 0, 1700, deny_y)])
        self.apps.handle(
            self.client,
            page_msg(widgets=page_msg()["widgets"][:2] + [{"id": "deny", "col": 2, "row": 1, "label": "No"}]),
        )
        self.assertTrue(self.surface.refresh())
        self.surface.handle([("end", 0, 1700, deny_y)])
        self.assertEqual(self.events(), [("deny", "down"), ("deny", "cancel")])
        # The page goes: back to the list, and the press is cancelled.
        self.client.sent.clear()
        self.clock.now += ASK_ARM
        self.surface.handle([("begin", 0, 300, deny_y)])
        self.apps.handle(self.client, {"op": "unpage", "page": "agents"})
        self.surface.handle([("end", 0, 300, deny_y)])
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "cancel")])
        self.assertEqual(self.surface.scene()["page"], None)
        # The page turning to the touchpad under a finger cancels it too.
        self.apps.handle(self.client, page_msg())
        self.open_agents()
        self.client.sent.clear()
        self.surface.handle([("begin", 0, 300, deny_y)])
        self.pager.turn(HOME)
        self.surface.handle([("end", 0, 300, deny_y)])
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "cancel")])
        # Letting go cancels what is held.
        self.pager.turn(APPS)
        self.settle()
        self.client.sent.clear()
        self.surface.handle([("begin", 0, 300, deny_y)])
        self.surface.release_all()
        self.assertEqual(self.events(), [("allow", "down"), ("allow", "cancel")])

    def test_not_drawn_without_an_extension_that_draws_widgets(self):
        self.draws = False
        scene = self.surface.scene()
        self.assertNotIn("widgets", scene)
        self.assertIn("log out and in", scene["text"])
        self.clock.now += 10
        self.tap(100, GRID_Y + 10)
        self.draws = True
        self.assertEqual(self.surface.scene()["page"], None)


class DaemonTests(unittest.TestCase):
    """The surface in the daemon: touches routed to it, what it publishes."""

    def setUp(self):
        self.enterContext(ConfigHome(self.enterContext(tempfile.TemporaryDirectory())))
        self.clock = Clock()
        self.daemon = d = Daemon(config(BAR_CONFIG), None)
        d.pager.clock = d.surface.clock = d.apps.asks.clock = self.clock
        d.pager.can_show = lambda: True
        d.bar = d.new_bar(KeyEmitter(lambda ev: None))
        d.bar.clock = self.clock
        d.splitter = Splitter(AXES, 5, d._zone)
        self.sent = []
        self.enterContext(mock.patch.object(daemon_module, "write_events", lambda fd, ev: self.sent.extend(ev)))
        os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)), exist_ok=True)
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL, "drawn": True}, f)
        self.client = hello(d.apps)
        d.apps.handle(self.client, page_msg())

    def frame(self, *groups):
        self.sent.clear()
        out = None
        for e in [e for g in groups for e in g] + [SYN]:
            out = self.daemon.splitter.feed(*e)
        assert out is not None and out != "resync"
        self.daemon.route(*out)
        return list(self.sent)

    def published(self):
        self.daemon.publish()
        with open(state_path()) as f:
            return json.load(f)

    def test_touches_on_the_apps_page_reach_the_widgets(self):
        d = self.daemon
        d.pager.turn(APPS)
        state = self.published()
        tile = next(w for w in state["surface"]["widgets"] if w["id"] == "surface/open:agents")
        self.clock.now += ASK_ARM
        # Device units are twice panel pixels.
        x, y = (tile["x"] + 20) * 2, (tile["y"] + 20) * 2
        self.assertEqual(self.frame(touch(0, 1, x, y)), [])
        self.assertEqual(self.frame(lift(0)), [])
        self.assertEqual(self.published()["surface"]["page"], "agents")
        self.clock.now += ASK_ARM
        deny_y = (GRID_Y + GRID_H * 3 // 4) * 2
        self.frame(touch(0, 2, 3400, deny_y))
        self.assertTrue(next(w for w in self.published()["surface"]["widgets"] if w["id"] == "agents/deny")["pressed"])
        self.frame(lift(0))
        self.assertEqual(
            [m["type"] for m in self.client.sent if m["op"] == "event"],
            ["down", "tap"],
        )
        # The touchpad got none of it.
        self.assertEqual(self.sent, [])
        self.assertEqual(d.splitter.sent, {})

    def test_published_widgets_need_an_extension_that_draws_them(self):
        self.daemon.pager.turn(APPS)
        self.assertEqual(self.published()["surface"]["widgets"][0]["id"], "surface/title")
        with open(runtime_file(RENDERER_NAME), "w") as f:
            json.dump({"protocol": WIDGETS_PROTOCOL - 1, "drawn": True}, f)
        os.utime(runtime_file(RENDERER_NAME), ns=(0, 1000))
        self.assertNotIn("widgets", self.published()["surface"])


if __name__ == "__main__":
    unittest.main()
