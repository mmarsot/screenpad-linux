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
    ASK_ARM,
    TONES,
)
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.protocol import parse_ask
from screenpad_touchbar.scene import DISMISS, DIVIDER_X, TILE_CLASSES, TILE_STYLES, ask_option_at, ask_tiles, spot_tiles


class SceneTests(unittest.TestCase):
    def test_spots_as_tiles(self):
        c = config(TILE_CONFIG)
        tiles = spot_tiles(c, pressed=[0], removing=[])
        self.assertEqual(
            [(x["id"], x["x"], x["width"], x["style"]) for x in tiles],
            [
                ("button-0", 0, 296, "button"),
                ("button-1", 296, 296, "button"),
                ("button-2", 592, 296, "button"),
                ("shortcut-0", 928, 296, "shortcut"),
                ("shortcut-1", 1224, 296, "shortcut"),
                ("shortcut-2", 1520, 296, "shortcut"),
                ("grip", 1816, 104, "grip"),
            ],
        )
        self.assertEqual(
            tiles[0],
            {
                "id": "button-0",
                "x": 0,
                "width": 296,
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
        self.assertEqual(tiles[2]["classes"], ["empty"])
        self.assertEqual(
            tiles[3],
            {"id": "shortcut-0", "x": 928, "width": 296, "style": "shortcut", "drop": 1, "app": "files.desktop"},
        )
        self.assertEqual(tiles[4]["classes"], ["empty"])
        self.assertEqual((tiles[4]["icon"], tiles[4]["icon_scale"], tiles[4]["drop"]), ("list-add-symbolic", 0.6, 2))
        self.assertTrue(tiles[4]["empty"])
        self.assertEqual(tiles[6]["glyph"], "grip")
        self.assertEqual(DIVIDER_X, 908)

    def test_removing_and_app_tiles(self):
        c = config(TILE_CONFIG)
        tiles = spot_tiles(
            c, pressed=[3], removing=[3], provided={"agents": {"tone": "warning", "label": "1 blocked", "badge": "3"}}
        )
        self.assertEqual(tiles[3]["classes"], ["removing"])
        self.assertEqual(tiles[3]["icon"], "edit-delete-symbolic")
        self.assertEqual(
            tiles[1],
            {
                "id": "button-1",
                "x": 296,
                "width": 296,
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
        tiles = spot_tiles(c, removing=[3], provided={"agents": {"tone": "accept"}}) + ask_tiles(ask)
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
                "width": 592,
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
                "x": 592,
                "width": 296,
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
                "x": 928,
                "width": 296,
                "style": "option",
                "classes": ["tone-reject"],
                "label": "Deny",
            },
        )
        self.assertEqual(tiles[3]["pressed"], True)
        self.assertTrue(ask_tiles(ask, pressed=[DISMISS])[1]["pressed"])
        self.assertEqual(
            [ask_option_at(ask, x) for x in (0, 591, 700, 900, 928, 1300, 1600)],
            [None, None, DISMISS, None, 0, 1, None],
        )


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
