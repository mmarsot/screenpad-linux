"""Tests for the GNOME Shell extension's plain modules, run under node.

contract.js against the daemon's constants and its renderer.json check, and
scene.js on scenes the daemon publishes: what is rebuilt, where tiles, the
grip, text and badges go, drops and launches. The stylesheet is checked
against every style and class a tile can carry. Only names that cross from
the daemon or GNOME Shell into extension.js are read from its source.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from touchbar_support import EXT, TILE_CONFIG, ConfigHome, FakeClient, config, js, read

from screenpad_touchbar.cli import main
from screenpad_touchbar.common import (
    ADD_ITEM,
    APPS,
    ASK_ARM,
    CARD_HEIGHT,
    DBUS_IFACE,
    DBUS_PATH,
    HEIGHT_STEP,
    HOME,
    LAUNCH,
    MAX_HEIGHT,
    MIN_HEIGHT,
    PANEL_H,
    PANEL_W,
    RENDERER_NAME,
    RENDERER_PROTOCOL,
    SOCKET_NAME,
    runtime_file,
    state_path,
)
from screenpad_touchbar.config_edit import with_height
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.scene import TILE_CLASSES, TILE_STYLES

# GNOME 50's LayoutManager chrome options (layout.js defaultParams). Params.parse
# throws on any other key, after addChrome has already put the actor on the
# stage; the shell then unloads the extension's stylesheet, leaving bare
# labels with no bar and no pressed state.
CHROME_OPTIONS = frozenset({"trackFullscreen", "affectsStruts"})
# The ScreenPad's monitor, upright, at scale 1 and at 2 (logical pixels).
MONITORS = ({"x": 0, "y": 1080, "width": 1920, "height": 1080}, {"x": 0, "y": 540, "width": 960, "height": 540})


def published(text=TILE_CONFIG, ask=None, page=HOME):
    """The scene the daemon publishes for a config, with a question showing,
    on a page (the page button shown)."""
    with tempfile.TemporaryDirectory() as d, ConfigHome(d):
        daemon = Daemon(config(text), None)
        daemon.apps.asks.can_show = lambda: True
        daemon.pager.can_show = lambda: True
        daemon.pager.turn(page)
        daemon.bar = daemon.new_bar(KeyEmitter(lambda ev: None))
        if ask:
            app = FakeClient()
            daemon.apps.connect(app)
            daemon.apps.handle(app, {"op": "hello", "app": "fleet"})
            daemon.apps.handle(app, dict(ask, op="ask"))
            daemon.apps.asks.tick()
        daemon.publish()
        with open(state_path()) as f:
            return json.load(f)


QUESTION = {
    "id": "q",
    "title": "Run it?",
    "text": "fleet wants to run make check",
    "options": [{"id": "y", "label": "Yes"}],
}


class ContractTests(unittest.TestCase):
    """contract.js against the daemon and GNOME Shell 50."""

    def setUp(self):
        self.c = js("contract.js", "m")

    def test_names_match_the_daemon(self):
        c = self.c
        self.assertEqual(
            (c["STATE_NAME"], c["RENDERER_NAME"], c["RENDERER_PROTOCOL"], c["SOCKET_NAME"]),
            (os.path.basename(state_path()), RENDERER_NAME, RENDERER_PROTOCOL, SOCKET_NAME),
        )

    def test_dbus_interface_matches_the_daemon(self):
        self.assertEqual(self.c["DBUS_PATH"], DBUS_PATH)
        node = ET.fromstring(self.c["DBUS_XML"])
        [iface] = node.findall("interface")
        self.assertEqual(iface.get("name"), DBUS_IFACE)
        methods = iface.findall("method")
        self.assertEqual([m.get("name") for m in methods], ["Launch", "AddItem"])
        for method in methods:
            self.assertEqual([(a.get("type"), a.get("direction")) for a in method.findall("arg")], [("s", "in")])
        # The daemon calls them so.
        self.assertIn("--dest org.gnome.Shell --object-path %s --method %s.Launch" % (DBUS_PATH, DBUS_IFACE), LAUNCH[2])
        self.assertIn(
            "--dest org.gnome.Shell --object-path %s --method %s.AddItem" % (DBUS_PATH, DBUS_IFACE), ADD_ITEM[2]
        )

    def test_card_growth_is_animated_within_the_arming_delay(self):
        # Fully drawn before a tap on it counts.
        self.assertLess(self.c["GROW_MS"], ASK_ARM * 1000)

    def test_chrome_options_exist_in_gnome_50(self):
        self.assertLessEqual(set(self.c["BAR_CHROME"]), CHROME_OPTIONS)
        self.assertEqual(self.c["STRUT_CHROME"], {"affectsStruts": True})
        # Source check of the names crossing into GNOME Shell: every chrome
        # call passes one of those two.
        calls = re.findall(r"(?:add|addTop|track)Chrome\(([^;]*?)\);", read("extension.js"))
        self.assertEqual(sorted(a.split(",")[-1].strip() for a in calls), ["BAR_CHROME", "STRUT_CHROME"])

    def test_renderer_json_is_what_the_daemon_reads(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = Daemon(config(), None)
            os.makedirs(os.path.dirname(runtime_file(RENDERER_NAME)))
            for i, drawn in enumerate((True, False, True)):
                with open(runtime_file(RENDERER_NAME), "w") as f:
                    f.write(js("contract.js", "m.rendererJson(v.drawn)", drawn=drawn))
                os.utime(runtime_file(RENDERER_NAME), ns=(0, 1000 + i))
                self.assertIs(daemon.renderer_ready(), drawn)


class SceneTests(unittest.TestCase):
    """scene.js on scenes the daemon publishes."""

    def test_only_tile_scenes_are_drawn(self):
        state = published()
        self.assertEqual(js("scene.js", "m.sceneFrom(v.s)", s=state), state)
        v1 = {k: state[k] for k in ("version", "pid", "active", "monitor", "panel", "height", "spots")}
        self.assertIsNone(js("scene.js", "m.sceneFrom(v.s)", s=v1))
        self.assertIsNone(js("scene.js", "m.sceneFrom(null)"))

    def test_a_press_restyles_without_rebuilding(self):
        state = published()
        pressed = json.loads(json.dumps(state))
        pressed["tiles"][0]["pressed"] = True
        relabelled = json.loads(json.dumps(state))
        relabelled["tiles"][0]["label"] = "Other"
        keys = js(
            "scene.js",
            "[v.base, v.pressed, v.relabelled, {...v.base, height: v.base.height + 8}]"
            ".map(s => m.layoutKey(s, v.monitor)).concat([m.layoutKey(v.base, {...v.monitor, y: 0})])",
            base=state,
            pressed=pressed,
            relabelled=relabelled,
            monitor=MONITORS[0],
        )
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), 4)

    def test_only_apps_on_the_bar_launch(self):
        state = published()
        self.assertEqual(
            js("scene.js", "['files.desktop', 'other.desktop'].map(id => m.canLaunch(v.s, id))", s=state), [True, False]
        )
        self.assertFalse(js("scene.js", "m.canLaunch(null, 'files.desktop')"))

    def test_classes_and_content(self):
        state = published(ask=QUESTION)
        drawn = js("scene.js", "v.s.tiles.map(t => [m.tileClasses(t), m.tileContent(t), m.accessibleName(t)])", s=state)
        for tile, (classes, content, name) in zip(state["tiles"], drawn, strict=True):
            self.assertEqual(
                classes, ["screenpad-touchbar-spot", "screenpad-touchbar-" + tile["style"]] + tile.get("classes", [])
            )
            if tile.get("glyph") == "grip":
                self.assertEqual((content, name), ("grip", "Resize the touch bar"))
            if tile.get("glyph") == "page":
                # Drawn as its icon, the page a tap goes to.
                self.assertEqual((content, name), ("icon", "Go to apps"))
                self.assertEqual(tile["icon"], "view-app-grid-symbolic")
        self.assertIn(["text", "Run it?"], [d[1:] for d in drawn])
        self.assertIn("page", [t.get("glyph") for t in state["tiles"]])
        # Only plain class names: the rest is dropped.
        odd = {"style": "bad style", "classes": ["ok-1", "Bad", "x;y", 3], "glyph": "grip", "icon": "x"}
        self.assertEqual(
            js("scene.js", "[m.tileClasses(v.t), m.tileContent(v.t)]", t=odd),
            [["screenpad-touchbar-spot", "ok-1"], "grip"],
        )
        self.assertEqual(
            js("scene.js", "[{icon: 'a', app: 'b'}, {app: 'b'}, {label: 'c'}].map(m.tileContent)"),
            ["icon", "app", "text"],
        )

    def test_every_tile_and_the_grip_drawn_at_every_height(self):
        # The grip is narrow (104 px): insets and icons scaled by the height
        # alone once left it 1 px wide on a tall bar.
        base = with_height(TILE_CONFIG, MIN_HEIGHT)
        for height in range(MIN_HEIGHT, MAX_HEIGHT + 1, HEIGHT_STEP):
            state = published(with_height(base, height))
            for monitor in MONITORS:
                out = js(
                    "scene.js",
                    "(() => { const g = m.barGeometry(v.s, v.monitor);"
                    " return [g, v.s.tiles.map(t => { const b = m.tileBox(t, g);"
                    " return [b, t.glyph === 'grip' ? m.gripSizes(b.width, b.height)"
                    " : t.glyph === 'page' ? m.iconSizeFor(t, g.iconSize, b.width) : null]; }),"
                    " m.barGeometry({...v.s, tiles: v.s.tiles.filter(t => t.glyph !== 'page')}, v.monitor).iconSize];"
                    " })()",
                    s=state,
                    monitor=monitor,
                )
                geometry, boxes, without_page = out
                # The narrow page button does not shrink the others' icons.
                self.assertEqual(geometry["iconSize"], without_page)
                sx = monitor["width"] / PANEL_W
                self.assertEqual(geometry["height"], round(height * monitor["height"] / PANEL_H))
                self.assertGreaterEqual(geometry["iconSize"], 8)
                self.assertEqual(state["tiles"][-1]["glyph"], "page")
                for tile, (box, grip) in zip(state["tiles"], boxes, strict=True):
                    msg = (height, monitor["width"], tile["id"])
                    self.assertGreaterEqual(box["width"], 0.6 * tile["width"] * sx, msg)
                    self.assertGreaterEqual(box["height"], 0.75 * geometry["height"], msg)
                    if grip is None:
                        self.assertLessEqual(geometry["iconSize"], box["width"], msg)
                        continue
                    if tile["glyph"] == "page":
                        # The page button's icon fits it at every height,
                        # as big as the others' while it has room.
                        icon = grip
                        self.assertGreaterEqual(icon, 8, msg)
                        self.assertLessEqual(icon, min(box["width"], box["height"]), msg)
                        if geometry["iconSize"] <= 0.8 * box["width"]:
                            self.assertEqual(icon, geometry["iconSize"], msg)
                        continue
                    self.assertGreaterEqual(grip["arrow"], 8, msg)
                    self.assertLessEqual(2 * grip["arrow"] + grip["bar"] + 2 * grip["gap"], box["height"], msg)
                    self.assertLessEqual(grip["barWidth"], box["width"], msg)

    def test_a_card_grows_over_the_bar_own_height(self):
        bar, card = published(), published(ask=QUESTION)
        self.assertEqual((card["height"], card["strut"]), (CARD_HEIGHT, bar["strut"]))
        out = js(
            "scene.js",
            "(() => { const [b, c, t] = [v.bar, v.card, {...v.bar, height: 240, strut: 240}]"
            ".map(s => m.barGeometry(s, v.monitor));"
            " return [b.own === c.own, m.grows(b, c), m.grows(c, b), m.grows(b, t), m.grows(null, c)]; })()",
            bar=bar,
            card=card,
            monitor=MONITORS[0],
        )
        self.assertEqual(out, [True, True, False, False, False])

    def test_card_text_wraps_and_labels_center(self):
        # On a card both lines wrap, the title and an answer's label too,
        # so neither is cut; on the bar a label stays one line.
        card = published(ask=QUESTION)
        bar = published()
        [text] = [t for t in card["tiles"] if t.get("title")]
        [answer] = [t for t in card["tiles"] if t["id"] == "ask-option-0"]
        [item] = [t for t in bar["tiles"] if t.get("label") and not t.get("icon")][:1]
        out = js(
            "scene.js",
            "(() => { const lines = (s, t) => { const g = m.barGeometry(s, v.monitor);"
            " return m.textLines(t, g.iconSize, m.tileBox(t, g).height); };"
            " return [lines(v.card, v.t), lines(v.card, v.a), lines(v.bar, v.i),"
            " m.textLines({}, 80, 28)]; })()",
            card=card,
            bar=bar,
            t=text,
            a=answer,
            i=item,
            monitor=MONITORS[0],
        )
        lines, label, on_bar, empty = out
        self.assertEqual(
            (lines["first"], lines["text"], lines["start"], lines["wrap"]), ("Run it?", text["text"], True, True)
        )
        self.assertEqual((label["first"], label["text"], label["start"], label["wrap"]), ("Yes", None, False, True))
        self.assertEqual((on_bar["first"], on_bar["start"], on_bar["wrap"]), (item["label"], False, False))
        self.assertIsNone(empty)

    def test_card_text_keeps_the_default_card_size_on_a_taller_bar(self):
        # The default card (160 px bar on a 1920x1080 monitor at scale 1)
        # draws a 40 px title and 29 px text (an 80 px icon size: half the
        # bar). On a taller bar a card keeps those sizes and wraps, instead
        # of growing them until nothing fits; a bar item's label still grows.
        lines = (
            "(() => { const lines = (s, id) => { const g = m.barGeometry(s, v.monitor);"
            " const t = s.tiles.find(t => t.id === id);"
            " return m.textLines(t, g.iconSize, m.tileBox(t, g).height, g.sy); };"
            " return [lines(v.card, 'ask-text'), lines(v.card, 'ask-option-0'), lines(v.bar, v.item),"
            " m.barGeometry(v.bar, v.monitor).iconSize]; })()"
        )
        for height in (160, 300, MAX_HEIGHT):
            card = published(with_height(TILE_CONFIG, height), ask=QUESTION)
            bar = published(with_height(TILE_CONFIG, height))
            [item] = [t["id"] for t in bar["tiles"] if t.get("label") and not t.get("icon")][:1]
            text, answer, on_bar, icon = js("scene.js", lines, card=card, bar=bar, item=item, monitor=MONITORS[0])
            for got in (text, answer):
                self.assertEqual((got["titleSize"], got["textSize"], got["wrap"]), (40, 29, True), height)
            # Outside a card the label is sized by the bar, as before: half
            # its icon size, which grows past the card's at a taller bar.
            self.assertEqual(on_bar["titleSize"], round(icon * 0.5), height)
            if height > 160:
                self.assertGreater(on_bar["titleSize"], 40, height)

    def test_the_pages(self):
        home, apps = published(), published(page=APPS)
        off = published(TILE_CONFIG + "[screensaver]\nclock = false\n")
        out = js(
            "scene.js",
            "[v.home, v.apps, v.off, null, {surface: {title: 3}}].map(m.pagesFrom)",
            home=home,
            apps=apps,
            off=off,
        )
        self.assertEqual(out[0], {"screensaver": {"clock": True}, "surface": None})
        self.assertEqual(out[1]["surface"], apps["surface"])
        self.assertEqual(set(out[1]["surface"]), {"title", "text", "hint"})
        self.assertEqual(out[2], {"screensaver": {"clock": False}, "surface": None})
        self.assertEqual(out[3], {"screensaver": None, "surface": None})
        self.assertEqual(out[4]["surface"], {"title": "", "text": "", "hint": ""})
        # The page button shows where a tap goes: apps from the touchpad
        # page, the touchpad from the apps page. It changes with the page,
        # which rebuilds the bar.
        self.assertEqual(
            [(s["tiles"][-1]["icon"], s["tiles"][-1]["label"]) for s in (home, apps)],
            [("view-app-grid-symbolic", "Go to apps"), ("input-touchpad-symbolic", "Go to the touchpad")],
        )
        self.assertNotEqual(
            *js(
                "scene.js",
                "[v.home, v.apps].map(s => m.layoutKey(s, v.monitor))",
                home=home,
                apps=apps,
                monitor=MONITORS[0],
            )
        )
        # The clock is redrawn once a minute, at the minute.
        self.assertEqual(
            js("scene.js", "[new Date(2026, 0, 1, 12, 0, 59, 500), new Date(2026, 0, 1, 12, 1)].map(m.msToNextMinute)"),
            [500, 60000],
        )
        sizes = js("scene.js", "m.pageSizes(v.h)", h=MONITORS[0]["height"])
        self.assertLess(sizes["hint"], sizes["text"])
        self.assertLess(sizes["text"], sizes["title"])

    def test_battery(self):
        # From UPower's display device, as GNOME Shell's power indicator reads
        # it: State 1 charging, 2 discharging, 4 fully charged, 5 plugged in
        # but not charging, 6 waiting to discharge.
        mark = js("scene.js", "m.CHARGING_MARK")
        self.assertEqual(mark, "\u26a1\ufe0e")
        device = {"IsPresent": True, "Percentage": 84.4, "State": 2}
        cases = [
            (device, "84%"),
            ({**device, "State": 1}, "84% " + mark),
            ({**device, "Percentage": 100.0, "State": 4}, "100% " + mark),
            ({**device, "Percentage": 80.0, "State": 5}, "80% " + mark),
            ({**device, "State": 6}, "84%"),
            ({**device, "Percentage": 84.6}, "85%"),
            ({**device, "Percentage": 0.2}, "0%"),
            ({**device, "Percentage": 120}, "100%"),
            # No battery, or before UPower answers: nothing shown.
            ({**device, "IsPresent": False}, ""),
            ({"IsPresent": None, "Percentage": None, "State": None}, ""),
            ({**device, "Percentage": "84"}, ""),
            (None, ""),
        ]
        out = js("scene.js", "v.devices.map(m.batteryText)", devices=[d for d, _ in cases])
        self.assertEqual(out, [text for _, text in cases])

    def test_divider_and_badge(self):
        state = published()
        out = js(
            "scene.js",
            "(() => { const g = m.barGeometry(v.s, v.monitor); const b = {x: 10, y: 4, width: 100};"
            " return [m.dividerBox({...v.s, divider: 836}, g), m.dividerBox(v.s, g),"
            " m.badgeBox({badge: '3'}, b, g.iconSize), m.badgeBox({badge: ''}, b, g.iconSize), g.iconSize]; })()",
            s=state,
            monitor=MONITORS[1],
        )
        divider, none, badge, no_badge, icon = out
        self.assertEqual(divider["x"], round(836 * MONITORS[1]["width"] / PANEL_W) - 1)
        # The daemon draws none: any item goes in any spot.
        self.assertIsNone(state["divider"])
        self.assertIsNone(none)
        self.assertEqual(badge, {"x": 110 - round(icon * 0.75), "y": 4, "fontSize": max(round(icon * 0.3), 8)})
        self.assertIsNone(no_badge)

    def test_drops_reach_the_shortcut_command(self):
        def drops(state, *xs):
            return js(
                "scene.js",
                "v.xs.map(px => { const i = m.dropTile(v.s.tiles, px);"
                " return i < 0 ? null : [v.s.tiles[i].drop, m.dropArgs(v.s.tiles, i, px)]; })",
                s=state,
                xs=list(xs),
            )

        def apps(state):
            return [t.get("app") for t in state["tiles"] if t.get("drop")]

        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as f:
                f.write(TILE_CONFIG)

            def scene():
                with open(path) as f:
                    return published(f.read())

            def add(app, args):
                with contextlib.redirect_stdout(io.StringIO()):
                    return main(["shortcut", "add", app, *args])

            state = scene()
            spot = {t["drop"]: t["x"] + 10 for t in state["tiles"] if t.get("drop")}
            # The app in spot 3 and the empty spots take drops; the key and
            # the app's tile in spots 1 and 2 do not.
            self.assertEqual(sorted(spot), [3, 4, 5, 6])
            key, tile, grip = state["tiles"][0]["x"] + 10, state["tiles"][1]["x"] + 10, PANEL_W - 10
            # Over the app: there, before it. Over the key or the grip: the
            # first free spot, 4.
            self.assertEqual(drops(state, spot[3], key, grip), [[3, ["--spot", "3"]], [4, []], [4, []]])
            # Those arguments put the app where the drop said.
            [(_, args)] = drops(state, spot[3])
            self.assertEqual(add("a.desktop", args), 0)
            [(_, args)] = drops(scene(), key)
            self.assertEqual(add("b.desktop", args), 0)
            [(_, args)] = drops(scene(), tile)
            self.assertEqual(add("c.desktop", args), 0)
            state = scene()
            self.assertEqual(apps(state), ["a.desktop", "files.desktop", "b.desktop", "c.desktop"])
            self.assertEqual(state["tiles"][1]["label"], "Agents")
            # A full bar takes a drop only on an app, which it replaces.
            self.assertEqual(drops(state, key, spot[4]), [None, [4, ["--spot", "4"]]])
            [(_, args)] = drops(state, spot[4])
            self.assertEqual(add("d.desktop", args), 0)
            self.assertEqual(apps(scene()), ["a.desktop", "d.desktop", "b.desktop", "c.desktop"])


class StylesheetTests(unittest.TestCase):
    def test_styles_for_every_tile_style_and_class(self):
        with open(os.path.join(EXT, "stylesheet.css")) as f:
            css = f.read()
        for style in TILE_STYLES:
            self.assertIn(".screenpad-touchbar-%s {" % style, css)
        # A tap button is the plain button style; every other class has a rule.
        for name in set(TILE_CLASSES) - {"tap"}:
            self.assertRegex(css, r"\.%s[ {:.,]" % re.escape(name), name)
        for rule in (
            ".screenpad-touchbar-button.hold:active",
            ".screenpad-touchbar-shortcut.removing",
            ".screenpad-touchbar-spot.removing",
            ".screenpad-touchbar-shortcut.drop",
            ".screenpad-touchbar-spot.empty",
            ".screenpad-touchbar-badge",
            ".screenpad-touchbar-text",
            ".screenpad-touchbar-grip-bar",
            ".screenpad-touchbar-screensaver",
            ".screenpad-touchbar-clock",
            ".screenpad-touchbar-battery",
            ".screenpad-touchbar-surface",
            ".screenpad-touchbar-surface-title",
            ".screenpad-touchbar-surface-text",
            ".screenpad-touchbar-surface-hint",
        ):
            self.assertIn(rule + " {", css)


if __name__ == "__main__":
    unittest.main()
