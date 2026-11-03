"""The blocked card: the screen lines it draws, the per-harness keymap, its
widgets (checked against the touch bar daemon's own validation), and the
guard that sends keys only to the screen drawn. Herdr is a fake."""

import unittest

from agents_support import CURSOR, SESSION, FakeHerdr, dialog, task
from touchbar_support import FakeClient

from screenpad_agents.card import Card
from screenpad_agents.firstmate import Bearings
from screenpad_agents.fleet import Fleet
from screenpad_agents.herdr import Agent
from screenpad_agents.keymap import offered
from screenpad_agents.screen import Screen, guarded_send, read_screen, screen_lines
from screenpad_touchbar.apps import Apps

BLOCKED = Agent("w1:p1", "claude", "blocked", "/work/api", 7)
LINES = screen_lines(dialog())


def ids(card, agent=BLOCKED, interrupt=(True, None)):
    return [w["id"] for w in card.widgets("fix-api", agent, interrupt)]


def drawn(command="npm test", agent=BLOCKED, always=True):
    return Screen(agent.pane, agent, screen_lines(dialog(command, always)))


class ScreenLinesTests(unittest.TestCase):
    def test_the_frame_and_blank_lines_go(self):
        self.assertEqual(
            LINES,
            [
                "some earlier output",
                "Bash command",
                "npm test",
                "Run the command",
                "Do you want to proceed?",
                CURSOR + " 1. Yes",
                "2. Yes, and don't ask again for this command",
                "3. No, and tell Claude what to do differently (esc)",
            ],
        )

    def test_the_last_sixteen_lines_each_clipped(self):
        lines = screen_lines("\n".join("line %d" % i for i in range(40)) + "\n" + "x" * 300 + "\x1b\x07")
        self.assertEqual(len(lines), 16)
        self.assertEqual(lines[0], "line 25")
        self.assertEqual(lines[-1], "x" * 119 + "…")
        self.assertEqual(screen_lines(""), [])


class KeymapTests(unittest.TestCase):
    def test_claude_code_permission_dialog(self):
        self.assertEqual(offered("claude", LINES), {"once": ("1",), "always": ("2",), "deny": ("esc",)})
        two = screen_lines(dialog(always=False))
        self.assertEqual(offered("claude", two), {"once": ("1",), "deny": ("esc",)})

    def test_never_a_dialog_it_does_not_know(self):
        self.assertEqual(offered("codex", LINES), {})
        self.assertEqual(offered("claude", ["$ npm test", "1. Yes"]), {})
        for refused in (
            [
                "Quick safety check: Is this a project you created or one you trust?",
                "Do you want to",
                "1. Yes, I trust this folder",
            ],
            ["Allow external CLAUDE.md file imports?", "Do you want to allow", "1. Yes, allow external imports"],
            ["WARNING: Claude Code running in Bypass Permissions mode", "Do you want to", "1. Yes, I accept"],
        ):
            self.assertEqual(offered("claude", refused), {}, refused)
        # Always alone (no plain yes) is not the dialog the keymap knows.
        self.assertEqual(offered("claude", ["Do you want to proceed?", "2. Yes, always", "3. No"]), {})

    def test_only_the_options_right_under_the_question(self):
        # A question in earlier text over another dialog's numbered choices
        # (Claude Code's plan approval) is not a permission dialog.
        plan = [
            "Do you want to keep the old API?",
            "Would you like to proceed?",
            "> 1. Yes, and auto-accept edits",
            "2. Yes, and manually approve edits",
            "3. No, keep planning",
        ]
        self.assertEqual(offered("claude", plan), {})
        self.assertEqual(offered("claude", ["Do you want to proceed?", "some text", "1. Yes", "2. No"]), {})
        self.assertEqual(offered("claude", ["Do you want to proceed?", "1. Yes, and auto-accept edits"]), {})
        ascii_cursor = ["Do you want to make this edit to api.py?", "> 1. Yes", "2. No, and tell Claude (esc)"]
        self.assertEqual(offered("claude", ascii_cursor), {"once": ("1",), "deny": ("esc",)})
        # Option 2 that is not a yes is not Always.
        self.assertNotIn("always", offered("claude", ["Do you want to proceed?", "1. Yes", "2. No"]))


class CardTests(unittest.TestCase):
    def test_a_blocked_dialog_gets_its_answers(self):
        card = Card("w1:p1")
        self.assertEqual(ids(card), ["card.screen", "card.back", "card.focus", "card.interrupt", "card.state"])
        card.screen = drawn()
        token = card.screen.token()
        self.assertEqual(
            ids(card),
            ["card.screen", "card.back", "card.focus", "card.interrupt", "answer.once." + token]
            + ["answer.deny." + token, "card.state"],
        )
        widgets = {w["id"]: w for w in card.widgets("fix-api", BLOCKED, (True, None))}
        self.assertEqual(widgets["card.screen"]["lines"], LINES)
        self.assertEqual(widgets["answer.once." + token]["touch"], "long")
        self.assertEqual(widgets["card.state"]["text"], "Claude Code · blocked")

    def test_touches_map_to_answers_of_the_screen_drawn(self):
        card = Card("w1:p1")
        card.screen = drawn()
        once, deny = "answer.once." + card.screen.token(), "answer.deny." + card.screen.token()
        self.assertEqual(card.answer_for(BLOCKED, once, "tap"), ("once", ("1",)))
        self.assertEqual(card.answer_for(BLOCKED, once, "long"), ("always", ("2",)))
        self.assertEqual(card.answer_for(BLOCKED, deny, "tap"), ("deny", ("esc",)))
        self.assertIsNone(card.answer_for(BLOCKED, deny, "long"))
        self.assertIsNone(card.answer_for(BLOCKED, "answer.once.000000000000", "tap"))
        self.assertIsNone(card.answer_for(Agent("w1:p1", "claude", "working", None, 8), once, "tap"))
        card.screen = drawn(always=False)
        once = "answer.once." + card.screen.token()
        self.assertIsNone(card.answer_for(BLOCKED, once, "long"))
        self.assertEqual(card.answer_for(BLOCKED, once, "tap"), ("once", ("1",)))
        card.pending = True
        self.assertIsNone(card.answer_for(BLOCKED, once, "tap"))

    def test_a_changed_screen_is_new_buttons(self):
        first, second = drawn("npm test"), drawn("rm -rf build")
        self.assertNotEqual(first.token(), second.token())
        reseq = Screen("w1:p1", Agent("w1:p1", "claude", "blocked", "/work/api", 9), first.lines)
        self.assertNotEqual(first.token(), reseq.token())

    def test_no_answers_without_a_known_dialog(self):
        card = Card("w2:p1")
        codex = Agent("w2:p1", "codex", "blocked", None, 3)
        card.screen = Screen("w2:p1", codex, LINES)
        self.assertFalse(any(i.startswith("answer.") for i in ids(card, codex)))
        state = card.widgets("w", codex, (False, "no interrupt known for codex"))
        self.assertEqual(state[-1]["text"], "codex · blocked · answer it in its pane")
        self.assertEqual(state[3]["touch"], "none")
        # Herdr without state_change_seq: nothing to guard with, so no answers.
        card = Card("w1:p1")
        card.screen = Screen("w1:p1", Agent("w1:p1", "claude", "blocked"), LINES)
        self.assertFalse(card.answers(Agent("w1:p1", "claude", "blocked")))

    def test_the_card_page_passes_the_daemons_validation(self):
        f = Fleet(SESSION, True)
        f.set_agents([BLOCKED, Agent("w2:p1", "codex", "working")])
        f.set_bearings(Bearings(1, False, {"lab:w1:p1": task("fix-api")}))
        f.open_card("w1:p1")
        f.set_screen(drawn())
        apps, client = Apps(), FakeClient()
        apps.connect(client)
        apps.set_placed(f.tiles())
        apps.handle(client, {"op": "hello", "app": "agents", "tiles": f.tiles()})
        for msg in f.messages():
            apps.handle(client, msg)
        self.assertEqual([m for m in client.sent if m["op"] == "error"], [])
        page = apps.pages["agents"]
        self.assertEqual(page.widgets[0].look["lines"], LINES)
        self.assertEqual(f.interrupt_route(BLOCKED), ("firstmate", "fix-api"))
        self.assertEqual(f.interrupt_route(Agent("w2:p1", "codex", "working")), (None, "no interrupt known for codex"))
        f.set_agents([Agent("w2:p1", "codex", "working")])
        self.assertIsNone(f.card)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr([BLOCKED])
        self.herdr.screens["w1:p1"] = dialog("npm test")
        self.card = read_screen(self.herdr, "w1:p1")

    def test_sends_when_the_screen_is_the_one_drawn(self):
        self.assertEqual(guarded_send(self.herdr, self.card, ("1",)), (None, None))
        self.assertEqual(self.herdr.sent, [("w1:p1", ("1",))])

    def test_a_dialog_that_changed_between_draw_and_tap_gets_nothing(self):
        self.herdr.screens["w1:p1"] = dialog("rm -rf build")
        why, fresh = guarded_send(self.herdr, self.card, ("1",))
        self.assertEqual(why, "the dialog changed since the card was drawn")
        self.assertIn("rm -rf build", fresh.lines)
        self.assertEqual(self.herdr.sent, [])

    def test_a_dialog_that_changes_during_the_check_gets_nothing(self):
        # The fresh read is taken right before send-keys: a change after the
        # agent get but before the read is still caught.
        def swap(pane):
            self.herdr.screens[pane] = dialog("rm -rf build")

        self.herdr.after_read = swap
        card = read_screen(self.herdr, "w1:p1")
        self.herdr.after_read = None
        why, _ = guarded_send(self.herdr, card, ("1",))
        self.assertEqual(why, "the dialog changed since the card was drawn")
        self.assertEqual(self.herdr.sent, [])

    def test_a_state_change_or_another_agent_gets_nothing(self):
        cases = [
            (Agent("w1:p1", "claude", "working", "/work/api", 8), "the agent is working now"),
            (Agent("w1:p1", "claude", "blocked", "/work/api", 9), "the agent changed state since the card was drawn"),
            (Agent("w1:p1", "codex", "blocked", "/work/api", 7), "another agent is in the pane"),
        ]
        for agent, why in cases:
            self.herdr.set(0, agent)
            self.assertEqual(guarded_send(self.herdr, self.card, ("1",))[0], why)
        self.herdr.list = []
        self.assertIn("not found", guarded_send(self.herdr, self.card, ("1",))[0])
        self.assertEqual(self.herdr.sent, [])


if __name__ == "__main__":
    unittest.main()
