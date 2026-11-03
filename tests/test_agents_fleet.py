"""The fleet as the touch bar shows it: the agents page and tiles, checked
against the touch bar daemon's own validation of app messages."""

import unittest

from agents_support import SESSION, task
from touchbar_support import FakeClient

from screenpad_agents.firstmate import Bearings
from screenpad_agents.fleet import Fleet, widget_id
from screenpad_agents.herdr import Agent
from screenpad_touchbar.apps import Apps


def fleet(agents=(), firstmate=True, calls=None, tasks=None):
    f = Fleet(SESSION, firstmate)
    f.set_agents([Agent(*a) for a in agents])
    if calls is not None:
        f.set_bearings(Bearings(calls, False, tasks or {}))
    return f


def widgets(f):
    return {w["id"]: w for w in f.page()["widgets"]}


class FleetTests(unittest.TestCase):
    def accepted(self, f):
        """What the daemon's Apps makes of the fleet's messages."""
        apps, client = Apps(), FakeClient()
        apps.connect(client)
        apps.set_placed(f.tiles())
        apps.handle(client, {"op": "hello", "app": "agents", "tiles": f.tiles()})
        for msg in f.messages():
            apps.handle(client, msg)
        self.assertEqual([m for m in client.sent if m["op"] == "error"], [])
        return apps

    def test_one_widget_per_agent_in_herdr_order(self):
        f = fleet(
            [
                ("w1:p1", "claude", "blocked", "/w/api"),
                ("w2:p1", "codex", "working", "/w/web/"),
                ("w3:p2", "pi", "idle"),
            ],
            calls=2,
            tasks={"lab:w1:p1": task("fix-api", "waiting on tests")},
        )
        page = f.page()
        self.assertEqual(
            (page["title"], page["text"], page["tone"], page["badge"]),
            ("Agents", "3 agents · 1 blocked · 1 working", "warning", "1"),
        )
        got = [(w["id"], w["col"], w["row"], w.get("label"), w.get("tone")) for w in page["widgets"]]
        self.assertEqual(
            got,
            [
                ("calls", 0, 0, "Captain's Call", "warning"),
                ("agent.w1.p1", 1, 0, "fix-api", "warning"),
                ("agent.w2.p1", 2, 0, "web", "accept"),
                ("agent.w3.p2", 3, 0, "pi", "neutral"),
            ],
        )
        self.assertEqual(widgets(f)["agent.w1.p1"]["text"], "claude · blocked · waiting on tests")
        self.assertEqual(widgets(f)["calls"]["badge"], "2")
        apps = self.accepted(f)
        self.assertEqual([w.id for w in apps.pages["agents"].widgets], [w["id"] for w in page["widgets"]])
        self.assertEqual(
            apps.provided()["agents"],
            {"label": "3 agents", "text": "1 blocked", "badge": "1", "tone": "warning"},
        )
        self.assertEqual(apps.provided()["calls"]["badge"], "2")

    def test_taps_map_to_panes(self):
        f = fleet([("w1:p1", "claude", "idle"), ("w2:p1", "codex", "blocked"), ("w3:p1", "pi", "blocked")])
        f.page()
        self.assertEqual(f.pane_of("agent.w2.p1"), "w2:p1")
        self.assertIsNone(f.pane_of("calls"))
        self.assertEqual(f.first_blocked(), "w2:p1")
        self.assertIsNone(fleet([("w1:p1", "claude", "idle")]).first_blocked())

    def test_odd_pane_ids_get_a_digest(self):
        self.assertEqual(widget_id("w12:p3"), "agent.w12.p3")
        long = widget_id("workspace-with-a-long-name:pane-7")
        self.assertRegex(long, r"^agent\.h[0-9a-f]{20}$")
        self.assertNotEqual(widget_id("a b:c"), widget_id("a c:c"))
        f = fleet([("a b:c", "claude", "idle")])
        self.accepted(f)
        self.assertEqual(f.pane_of(widget_id("a b:c")), "a b:c")

    def test_the_grid_grows_then_says_how_many_more(self):
        many = [("w%d:p1" % i, "claude", "idle") for i in range(30)]
        for n, rows in ((0, 2), (7, 2), (8, 3), (15, 4), (30, 4)):
            with self.subTest(n=n):
                f = fleet(many[:n], calls=0)
                page = f.page()
                self.assertEqual(page["rows"], rows)
                self.accepted(f)
        page = fleet(many, calls=0).page()
        self.assertEqual(len(page["widgets"]), 16)
        self.assertEqual(page["widgets"][-1], {"id": "more", "label": "+16 more", "touch": "none", "col": 3, "row": 3})
        f = fleet(many, calls=0)
        f.page()
        self.assertIsNone(f.pane_of("agent.w14.p1"))
        self.assertEqual(f.pane_of("agent.w13.p1"), "w13:p1")

    def test_the_tile_says_the_most_urgent_state(self):
        for states, text in ((("working", "idle"), "1 working"), (("idle", "idle"), "2 idle"), (("done",), None)):
            f = fleet([("w%d:p1" % i, "claude", s) for i, s in enumerate(states)])
            self.assertEqual(f.fleet_tile().get("text"), text)

    def test_without_firstmate(self):
        f = fleet([("w1:p1", "claude", "working")], firstmate=False)
        self.assertEqual(f.tiles(), ["agents"])
        self.assertEqual([w["id"] for w in f.page()["widgets"]], ["agent.w1.p1"])
        self.assertEqual([m.get("tile") for m in f.messages()], [None, "agents"])
        self.accepted(f)

    def test_herdr_says_nothing_or_fails(self):
        f = Fleet(SESSION, True)
        self.assertEqual(widgets(f)["herdr"]["label"], "Reading Herdr…")
        self.assertEqual(widgets(f)["calls"]["text"], "reading firstmate…")
        self.accepted(f)
        f.set_agents([])
        self.assertEqual(widgets(f)["herdr"]["text"], "Herdr reports no agent in session lab")
        f.set_agents(None, "herdr agent list: Error: Os { code: 2 }")
        self.assertEqual(
            widgets(f)["herdr"],
            {
                "id": "herdr",
                "label": "Herdr unavailable",
                "text": "herdr agent list: Error: Os { code: 2 }",
                "touch": "none",
                "col": 1,
                "row": 0,
            },
        )
        self.assertEqual(
            f.fleet_tile(),
            {"op": "set", "tile": "agents", "label": "Agents", "tone": "neutral", "text": "Herdr unavailable"},
        )
        self.accepted(f)

    def test_captain_calls(self):
        f = fleet(calls=0)
        self.assertEqual(
            f.calls_tile(), {"op": "set", "tile": "calls", "label": "Calls", "text": "none waiting", "tone": "neutral"}
        )
        f.set_bearings(Bearings(20, True, {}))
        self.assertEqual(f.calls_tile()["badge"], "20+")
        self.assertEqual(f.calls_tile()["text"], "20+ waiting")
        f.set_bearings(None, "firstmate's reader failed")
        self.assertEqual(f.calls_tile()["text"], "20+ waiting (stale)")
        f = fleet()
        f.set_bearings(None, "firstmate's reader failed")
        self.assertEqual(f.calls_tile()["text"], "firstmate unreadable")
        self.accepted(f)

    def test_long_names_are_clipped_to_the_limits(self):
        name = "a-task-with-a-really-long-name-that-goes-on"
        f = fleet([("w1:p1", "claude", "working")], calls=0, tasks={"lab:w1:p1": task(name, "x" * 300)})
        w = widgets(f)["agent.w1.p1"]
        self.assertEqual(len(w["label"]), 32)
        self.assertTrue(w["label"].endswith("…"))
        self.assertEqual(len(w["text"]), 200)
        self.accepted(f)


if __name__ == "__main__":
    unittest.main()
