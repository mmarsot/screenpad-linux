"""Tests for the scene's tiles, questions in the bar and app tiles in the bar.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import unittest

from touchbar_support import BAR_CONFIG, SYN, TILE_CONFIG, FakeClient, config

from screenpad_touchbar.asks import Asks
from screenpad_touchbar.bar import Bar
from screenpad_touchbar.codes import (
    EV_KEY,
)
from screenpad_touchbar.common import (
    APPS,
    ASK_ARM,
    HOME,
    PAGE_X,
    TONES,
)
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.protocol import parse_ask
from screenpad_touchbar.scene import DISMISS, TILE_CLASSES, TILE_STYLES, ask_option_at, ask_tiles, spot_tiles


class SceneTests(unittest.TestCase):
    def test_spots_as_tiles(self):
        c = config(TILE_CONFIG)
        tiles = spot_tiles(c, pressed=[0], removing=[])
        self.assertEqual(
            [(x["id"], x["x"], x["width"], x["style"]) for x in tiles],
            [
                ("item-0", 0, 272, "button"),
                ("item-1", 272, 272, "button"),
                ("item-2", 544, 272, "shortcut"),
                ("item-3", 856, 272, "shortcut"),
                ("item-4", 1128, 272, "shortcut"),
                ("item-5", 1400, 272, "shortcut"),
                ("grip", 1816, 104, "grip"),
            ],
        )
        self.assertEqual(
            tiles[0],
            {
                "id": "item-0",
                "x": 0,
                "width": 272,
                "style": "button",
                "pressed": True,
                "classes": ["hold"],
                "icon": "audio-input-microphone-symbolic",
                "label": "PTT",
            },
        )
        # No app provides "agents": its config label, dimmed.
        self.assertEqual(tiles[1]["classes"], ["tile", "offline"])
        self.assertEqual(tiles[1]["label"], "Agents")
        # An app in the third spot takes drops, like the empty spots after it.
        self.assertEqual(
            tiles[2],
            {"id": "item-2", "x": 544, "width": 272, "style": "shortcut", "drop": 3, "app": "files.desktop"},
        )
        self.assertNotIn("drop", tiles[0])
        self.assertNotIn("drop", tiles[1])
        self.assertEqual(tiles[3]["classes"], ["empty"])
        self.assertEqual((tiles[3]["icon"], tiles[3]["icon_scale"], tiles[3]["drop"]), ("list-add-symbolic", 0.6, 4))
        self.assertTrue(tiles[3]["empty"])
        self.assertEqual(tiles[6]["glyph"], "grip")

    def test_any_item_in_any_spot(self):
        c = config(
            '[[bar]]\napp = "files.desktop"\nlabel = "Files"\n'
            '[[bar]]\nlabel = "Agents page"\npage = "agents"\n'
            '[[bar]]\nlabel = "Run"\ncommand = "x"\n'
            '[[bar]]\nlabel = "Agents"\ntile = "agents"\n'
            '[[bar]]\nicon = "a-symbolic"\nkey = "micmute"\nmode = "hold"\n'
        )
        tiles = spot_tiles(c, page_to=APPS)
        self.assertEqual(
            [t.get("app") or t.get("label") or t.get("icon") for t in tiles[:6]][:5],
            ["files.desktop", "Agents page", "Run", "Agents", "a-symbolic"],
        )
        self.assertEqual(
            [t["style"] for t in tiles[:6]], ["shortcut", "button", "button", "button", "button", "shortcut"]
        )
        self.assertEqual(tiles[0]["label"], "Files")
        self.assertEqual([t.get("classes") for t in tiles[2:6]], [["tap"], ["tile", "offline"], ["hold"], ["empty"]])
        # A page item shows its page as the app gives it, while it can open.
        self.assertEqual(tiles[1]["classes"], ["tile", "offline"])
        page = {"agents": {"label": "Agents", "icon": "x-symbolic", "badge": "2", "tone": "warning"}}
        tile = spot_tiles(c, page_to=APPS, pages=page)[1]
        self.assertEqual(
            {k: tile[k] for k in ("classes", "label", "icon", "badge")},
            {"classes": ["tile", "tone-warning"], "label": "Agents", "icon": "x-symbolic", "badge": "2"},
        )
        # Drops go to an app's spot and the empty one only.
        self.assertEqual([t.get("drop") for t in tiles[:6]], [1, None, None, None, None, 6])

    def test_page_button_tile(self):
        c = config(TILE_CONFIG)
        # Only where a tap goes, as an icon: apps from the touchpad page.
        tiles = spot_tiles(c, pressed=[7], page_to=APPS)
        self.assertEqual(
            tiles[-1],
            {
                "id": "page",
                "x": 1672,
                "width": 144,
                "style": "page",
                "pressed": True,
                "glyph": "page",
                "icon": "view-app-grid-symbolic",
                "label": "Go to apps",
            },
        )
        # The touchpad from the apps page.
        self.assertEqual(
            {k: spot_tiles(c, page_to=HOME)[-1][k] for k in ("icon", "label")},
            {"icon": "input-touchpad-symbolic", "label": "Go to the touchpad"},
        )
        # Without the pager (an extension that cannot draw
        # the pages), no page button.
        self.assertNotIn("page", [t["style"] for t in spot_tiles(c)])

    def test_removing_and_app_tiles(self):
        c = config(TILE_CONFIG)
        tiles = spot_tiles(
            c, pressed=[2], removing=[2], provided={"agents": {"tone": "warning", "label": "1 blocked", "badge": "3"}}
        )
        self.assertEqual(tiles[2]["classes"], ["removing"])
        self.assertEqual(tiles[2]["icon"], "edit-delete-symbolic")
        self.assertEqual(
            tiles[1],
            {
                "id": "item-1",
                "x": 272,
                "width": 272,
                "style": "button",
                "classes": ["tile", "tone-warning"],
                "label": "1 blocked",
                "badge": "3",
            },
        )
        # An app that says nothing yet shows the config's icon or label.
        tiles = spot_tiles(c, provided={"agents": {}})
        self.assertEqual((tiles[1]["classes"], tiles[1]["label"]), (["tile", "tone-neutral"], "Agents"))

    def test_every_class_is_known(self):
        c = config(TILE_CONFIG)
        ask = parse_ask(
            None,
            {"id": "q", "title": "T", "options": [{"id": "a", "label": "A", "tone": tone} for tone in TONES[:1]]},
            0,
        )
        tiles = spot_tiles(c, removing=[2], provided={"agents": {"tone": "accept"}}, page_to=APPS) + ask_tiles(ask)
        for tile in tiles:
            self.assertIn(tile["style"], TILE_STYLES)
            for name in tile.get("classes", []):
                self.assertIn(name, TILE_CLASSES)

    def test_question_tiles(self):
        ask = parse_ask(
            None,
            {
                "id": "q",
                "title": "Run npm test?",
                "text": "in api",
                "options": [
                    {"id": "no", "label": "Deny", "tone": "reject"},
                    {"id": "yes", "label": "Allow", "tone": "accept"},
                ],
            },
            0,
        )
        tiles = ask_tiles(ask, pressed=[1], queued=2)
        self.assertEqual(
            tiles[0],
            {
                "id": "ask-text",
                "x": 0,
                "width": 544,
                "style": "ask-text",
                "title": "Run npm test?",
                "text": "in api",
                "badge": "+2",
            },
        )
        # Every card can be dismissed, from the third button spot.
        self.assertEqual(
            tiles[1],
            {
                "id": "ask-dismiss",
                "x": 544,
                "width": 272,
                "style": "option",
                "classes": ["tone-neutral"],
                "icon": "window-close-symbolic",
                "icon_scale": 0.6,
                "label": "Dismiss",
            },
        )
        self.assertEqual(
            tiles[2],
            {
                "id": "ask-option-0",
                "x": 856,
                "width": 408,
                "style": "option",
                "classes": ["tone-reject"],
                "label": "Deny",
            },
        )
        self.assertEqual(tiles[3]["pressed"], True)
        self.assertTrue(ask_tiles(ask, pressed=[DISMISS])[1]["pressed"])
        self.assertEqual(
            [ask_option_at(ask, x) for x in (0, 543, 600, 830, 856, 1200, 1263, 1264, 1671, 1672)],
            [None, None, DISMISS, None, 0, 0, 0, 1, 1, None],
        )

    def test_answers_share_the_last_three_spots(self):
        # One, two or three answers fill the spots right of Dismiss, so an
        # answer's box is as wide as the card lets it be; the text and
        # Dismiss stay where they are, and a touch hits what is drawn.
        for n, width in ((1, 816), (2, 408), (3, 272)):
            options = [{"id": "o%d" % k, "label": "O%d" % k} for k in range(n)]
            ask = parse_ask(None, {"id": "q", "title": "T", "options": options}, 0)
            tiles = ask_tiles(ask)
            self.assertEqual(
                [(t["x"], t["width"]) for t in tiles],
                [(0, 544), (544, 272)] + [(856 + k * width, width) for k in range(n)],
            )
            self.assertEqual(tiles[-1]["x"] + tiles[-1]["width"], PAGE_X)
            for k, tile in enumerate(tiles[2:]):
                for x in (tile["x"], tile["x"] + tile["width"] // 2, tile["x"] + tile["width"] - 1):
                    self.assertEqual(ask_option_at(ask, x), k, (n, x))
            self.assertIsNone(ask_option_at(ask, 855))
            self.assertIsNone(ask_option_at(ask, PAGE_X))


class AskBarTests(unittest.TestCase):
    """The bar with questions, on a fake clock."""

    def setUp(self):
        self.now = 100.0
        self.frames, self.launched, self.resized = [], [], []
        self.asks = Asks(clock=lambda: self.now)
        self.asks.set_available(True)
        self.bar = Bar(
            config(BAR_CONFIG),
            KeyEmitter(self.frames.append),
            lambda a: None,
            self.launched.append,
            resize=lambda h: self.resized.append(h) or True,
            clock=lambda: self.now,
            asks=self.asks,
        )
        self.app = FakeClient()

    def ask(self, ask_id="q", n=2):
        options = [{"id": "o%d" % k, "label": "O%d" % k} for k in range(n)]
        self.asks.add(parse_ask(self.app, {"id": ask_id, "title": "T", "options": options}, self.now))
        self.now += ASK_ARM

    def test_lifting_on_an_answer_chooses_it(self):
        self.ask()
        self.assertTrue(self.bar.handle([("begin", 0, 1300, 50)]))
        self.assertEqual(self.bar.ask_pressed(self.asks.shown()), [1])
        self.bar.handle([("end", 0, 1310, 60)])
        self.assertEqual(self.app.last("answer"), {"op": "answer", "id": "q", "option": "o1", "reason": "chosen"})
        self.assertIsNone(self.asks.shown())
        # Nothing reached the spots under the question.
        self.assertEqual((self.frames, self.launched), ([], []))

    def test_a_wide_answer_takes_a_touch_anywhere_on_it(self):
        # One answer spans the three spots: a tap at either end chooses
        # it, and sliding within it keeps it armed.
        self.ask(n=1)
        self.assertTrue(self.bar.handle([("begin", 0, 870, 50)]))
        self.bar.handle([("move", 0, 1650, 300)])
        self.bar.handle([("end", 0, 1650, 300)])
        self.assertEqual(self.app.last("answer"), {"op": "answer", "id": "q", "option": "o0", "reason": "chosen"})

    def test_too_soon_does_nothing(self):
        self.asks.add(parse_ask(self.app, {"id": "q", "title": "T", "options": [{"id": "a", "label": "A"}]}, self.now))
        self.now += ASK_ARM / 2
        self.assertFalse(self.bar.handle([("begin", 0, 1000, 50)]))
        self.now += 1
        self.bar.handle([("end", 0, 1000, 50)])
        self.assertIsNone(self.app.last("answer"))
        self.assertIsNotNone(self.asks.shown())

    def test_slide_away_and_text_do_nothing(self):
        self.ask()
        self.bar.handle([("begin", 0, 1000, 50)])
        self.assertTrue(self.bar.handle([("move", 0, 1000, 600)]))
        self.assertEqual(self.bar.ask_pressed(self.asks.shown()), [])
        self.bar.handle([("end", 0, 1000, 600)])
        # The question's text, over the button spots, is not a button.
        self.assertFalse(self.bar.handle([("begin", 1, 100, 50)]))
        self.bar.handle([("end", 1, 100, 50)])
        self.assertEqual((self.app.last("answer"), self.frames), (None, []))

    def test_the_grip_still_works(self):
        self.ask()
        self.bar.handle([("begin", 0, 1850, 100), ("move", 0, 1850, 180), ("end", 0, 1850, 180)])
        self.assertEqual(self.resized, [240])

    def test_a_held_button_stays_held(self):
        self.bar.handle([("begin", 0, 100, 50)])
        self.assertEqual(self.frames, [[(EV_KEY, 184, 1), SYN]])
        self.ask()
        self.bar.handle([("end", 0, 100, 50)])
        self.assertEqual(self.frames[-1], [(EV_KEY, 184, 0), SYN])
        self.assertIsNone(self.app.last("answer"))

    def test_an_answer_that_went_away_does_nothing(self):
        self.ask()
        self.bar.handle([("begin", 0, 1000, 50)])
        self.now += 100
        self.asks.tick()
        self.assertEqual(self.app.last("answer")["reason"], "timeout")
        self.bar.handle([("end", 0, 1000, 50)])
        self.assertEqual(len([m for m in self.app.sent if m["op"] == "answer"]), 1)

    def test_queue_shows_the_next_armed_again(self):
        self.ask("first")
        self.ask("second")
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.app.last("answer")["id"], "first")
        # The next one just appeared: a tap now is too soon.
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.asks.shown().id, "second")
        self.now += ASK_ARM
        self.bar.handle([("begin", 0, 1000, 50), ("end", 0, 1000, 50)])
        self.assertEqual(self.app.last("answer")["id"], "second")


class TileBarTests(unittest.TestCase):
    def setUp(self):
        self.events, self.on = [], {"agents"}
        bar_self = self

        class Tiles:
            def online(self, name):
                return name in bar_self.on

            def event(self, name, kind, inside=None):
                bar_self.events.append((name, kind, inside))

        self.frames = []
        self.bar = Bar(config(TILE_CONFIG), KeyEmitter(self.frames.append), lambda a: None, tiles=Tiles())

    def test_down_and_up(self):
        self.assertTrue(self.bar.handle([("begin", 0, 400, 50)]))
        self.assertEqual(self.bar.pressed(), [1])
        self.bar.handle([("end", 0, 410, 60)])
        self.assertEqual(self.events, [("agents", "down", None), ("agents", "up", True)])
        self.bar.handle([("begin", 0, 400, 50), ("move", 0, 400, 600), ("end", 0, 400, 600)])
        self.assertEqual(self.events[-1], ("agents", "up", False))
        self.assertEqual(self.frames, [])

    def test_offline_tile_does_nothing(self):
        self.on.clear()
        self.assertFalse(self.bar.handle([("begin", 0, 400, 50), ("end", 0, 400, 50)]))
        self.assertEqual(self.events, [])

    def test_reload_release_and_app_gone(self):
        self.bar.handle([("begin", 0, 400, 50)])
        self.bar.set_config(config(TILE_CONFIG))  # same tile, same spot: still down
        self.assertEqual(self.events, [("agents", "down", None)])
        self.bar.release_all()
        self.assertEqual(self.events[-1], ("agents", "up", False))
        self.bar.handle([("begin", 0, 400, 50)])
        self.bar.tile_gone("agents")
        self.bar.handle([("end", 0, 400, 50)])
        self.assertEqual(self.events[-1], ("agents", "down", None))
        self.assertEqual(self.bar.pressed(), [])


if __name__ == "__main__":
    unittest.main()
