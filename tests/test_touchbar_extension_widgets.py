"""Tests for the extension's widgets.js, run under node on the apps
surface the daemon publishes: which widgets are drawn, that a press only
restyles, that every widget and its type fit on the monitor at two
scales; and the stylesheet's rules for every class a widget carries.

Run with: python3 -m unittest discover -s tests
"""

import os
import re
import tempfile
import unittest

from touchbar_support import EXT, GRID_H, GRID_Y, TOP, Clock, config, hello, js, page_msg

from screenpad_touchbar.apps import Apps
from screenpad_touchbar.common import APPS, ASK_ARM, PANEL_H
from screenpad_touchbar.pager import Pager
from screenpad_touchbar.surface import WIDGET_CLASSES, Surface


class ExtensionTests(unittest.TestCase):
    """widgets.js on what the daemon publishes, and the stylesheet."""

    MONITORS = ({"x": 0, "y": 1080, "width": 1920, "height": 1080}, {"x": 0, "y": 540, "width": 960, "height": 540})

    def scene(self):
        clock = Clock()
        apps = Apps(clock)
        pager = Pager(clock=clock)
        surface = Surface(apps, pager, lambda: TOP, lambda: TOP, lambda: True, clock)
        client = hello(apps)
        apps.handle(client, page_msg())
        pager.turn(APPS)
        surface.open = "agents"
        surface.refresh()
        clock.now += ASK_ARM
        surface.handle([("begin", 0, 1700, GRID_Y + GRID_H * 3 // 4)])
        return surface.scene()

    def test_widgets_drawn_from_the_scene(self):
        surface = self.scene()
        out = js("widgets.js", "m.surfaceWidgets(v.s)", s=surface)
        self.assertEqual([w["id"] for w in out], [w["id"] for w in surface["widgets"]])
        self.assertEqual([w["pressed"] for w in out], [False, False, False, False, True])
        # Malformed widgets are left out, unknown classes dropped; no widgets
        # (a daemon before them) is null.
        bad = {
            "widgets": [
                {"id": "a", "x": 0, "y": 0, "width": 0, "height": 5},
                {"x": 0},
                None,
                {"id": "b", "x": 0, "y": 0, "width": 5, "height": 5, "classes": ["ok", "Not OK"]},
            ]
        }
        self.assertEqual([w["classes"] for w in js("widgets.js", "m.surfaceWidgets(v.s)", s=bad)], [["ok"]])
        self.assertIsNone(js("widgets.js", "m.surfaceWidgets(v.s)", s={"title": "Apps"}))
        # A press restyles, it does not rebuild.
        keys = js(
            "widgets.js",
            "[v.s, {...v.s, widgets: v.s.widgets.map(({pressed, ...w}) => w)}]"
            ".map(s => m.widgetsKey(m.surfaceWidgets(s)))",
            s=surface,
        )
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(
            js("widgets.js", "m.widgetClasses({classes: ['tone-accept', 'long']})"),
            ["screenpad-touchbar-spot", "screenpad-touchbar-widget", "tone-accept", "long"],
        )

    def test_every_widget_fits_on_the_monitor(self):
        surface = self.scene()
        for monitor in self.MONITORS:
            out = js(
                "widgets.js",
                "m.surfaceWidgets(v.s).map(w => { const b = m.widgetBox(w, [1920, 1080], v.mon);"
                " return [b, m.widgetParts(w, b, v.mon.height / 1080)]; })",
                s=surface,
                mon=monitor,
            )
            for box, parts in out:
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], monitor["width"])
                self.assertGreaterEqual(box["y"], round(TOP * monitor["height"] / PANEL_H))
                self.assertLessEqual(box["y"] + box["height"], monitor["height"])
                self.assertGreaterEqual(min(v for k, v in parts.items() if k.endswith("Size") and v is not None), 8)
                if parts["lines"]:
                    room = box["height"] - 2 * box["padding"]
                    self.assertLessEqual(len(parts["lines"]) * parts["lineSize"] * 1.45, room + 1)

    def test_apps_on_the_home_page(self):
        # [[apps]] items on the home page: an app is drawn with its own icon
        # (unless it has one) and can be opened over D-Bus.
        clock = Clock()
        apps = Apps(clock)
        pager = Pager(clock=clock)
        items = config(
            '[[apps]]\napp = "org.gnome.Settings.desktop"\n[[apps]]\napp = "b.desktop"\nicon = "x-symbolic"\n'
        ).apps
        surface = Surface(apps, pager, lambda: TOP, lambda: TOP, lambda: True, clock, items=lambda: items)
        pager.turn(APPS)
        scene = surface.scene()
        out = js(
            "widgets.js",
            "m.surfaceWidgets(v.s).slice(1).map(w => { const b = m.widgetBox(w, [1920, 1080], v.mon);"
            " const p = m.widgetParts(w, b, 1); return [w.app, p.app, p.icon, p.iconSize > 8, p.first]; })",
            s=scene,
            mon=self.MONITORS[0],
        )
        self.assertEqual(
            out,
            [
                ["org.gnome.Settings.desktop", "org.gnome.Settings.desktop", None, True, None],
                ["b.desktop", None, "x-symbolic", True, None],
            ],
        )
        launch = js(
            "scene.js",
            "['org.gnome.Settings.desktop', 'b.desktop', 'c.desktop'].map(id => m.canLaunch(v.s, id))",
            s={"tiles": [], "surface": scene},
        )
        self.assertEqual(launch, [True, True, False])

    def test_stylesheet(self):
        rules = css_rules(os.path.join(EXT, "stylesheet.css"))
        styled = [(selectors, body) for selectors, body in rules if body]
        for name in WIDGET_CLASSES:
            self.assertTrue(
                any(name in re.findall(r"\.([\w-]+)", sel) for selectors, _ in styled for sel in selectors), name
            )
        for rule in (".screenpad-touchbar-widget", ".screenpad-touchbar-lines"):
            self.assertTrue(any(rule in selectors for selectors, _ in styled), rule)

    def test_css_rules_skip_comments_and_empty_rules(self):
        path = os.path.join(self.enterContext(tempfile.TemporaryDirectory()), "s.css")
        with open(path, "w") as f:
            f.write("/* .held { color: red; } */\n.a.long, .b {\n}\n.c > .d { color: red; /* x */ }\n")
        self.assertEqual(css_rules(path), [([".a.long", ".b"], []), ([".c > .d"], ["color: red"])])


def css_rules(path):
    """The stylesheet's rules, comments stripped: each a list of its
    selectors and a list of its declarations."""
    with open(path) as f:
        css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
    return [
        ([s.strip() for s in sel.split(",")], [d.strip() for d in body.split(";") if d.strip()])
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]


if __name__ == "__main__":
    unittest.main()
