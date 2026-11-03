"""The blocked card in the loop, against the touch bar daemon's own socket
server and Apps: opening a card, answering it through the guard, a dialog
that changes between draw and tap, and Interrupt through firstmate or Herdr.
Herdr and firstmate are fakes; nothing reaches a real one."""

import threading
import unittest

from agents_support import FakeFirstmate, FakeHerdr, Rig, config, dialog, task

from screenpad_agents.herdr import Agent


class CardLoopTests(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr(
            [Agent("w1:p1", "claude", "blocked", "/work/api", 7), Agent("w2:p1", "claude", "working", "/w/web", 3)]
        )
        self.herdr.screens = {"w1:p1": dialog("npm test"), "w2:p1": "$ make\nbuilding…\n"}
        self.firstmate = FakeFirstmate(calls=0, tasks={"lab:w1:p1": task("fix-api")})

    def rig(self, poll=0.05):
        rig = Rig(self, self.herdr, self.firstmate, cfg=config(herdr_poll=poll))
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1", "agent.w2.p1"])
        return rig

    def tap(self, rig, widget, kind="tap"):
        rig.apps.press(rig.client(), "agents", widget, kind)

    def answers(self, rig):
        return [w for w in rig.widgets() or [] if w.startswith("answer.")]

    def open(self, rig, widget="agent.w1.p1"):
        self.tap(rig, widget)
        rig.until(lambda: len(self.answers(rig)) == 2)
        return self.answers(rig)

    def look(self, rig, wid):
        """The look of widget wid on the page now, empty when it has none."""
        page = rig.apps.pages.get("agents")
        found = [w.look for w in page.widgets if w.id == wid] if page else []
        return found[0] if found else {"lines": [], "text": ""}

    def test_allow_once_always_and_deny(self):
        rig = self.rig()
        for touch, keys in (("tap", ("1",)), ("long", ("2",))):
            once, _ = self.open(rig)
            self.assertIn("npm test", self.look(rig, "card.screen")["lines"])
            self.tap(rig, once, touch)
            rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1", "agent.w2.p1"])
            self.assertEqual(self.herdr.sent[-1], ("w1:p1", keys))
        _, deny = self.open(rig)
        self.tap(rig, deny)
        rig.until(lambda: rig.logged[-1:] == ["answered deny in pane w1:p1"])
        self.assertEqual(self.herdr.sent, [("w1:p1", ("1",)), ("w1:p1", ("2",)), ("w1:p1", ("esc",))])

    def test_a_dialog_that_changes_between_draw_and_tap_is_not_answered(self):
        # Herdr is read only on demand here, so the card still shows the
        # first dialog when the tap comes.
        rig = self.rig(poll=60)
        once, deny = self.open(rig)
        self.herdr.screens["w1:p1"] = dialog("rm -rf build")
        self.tap(rig, once)
        rig.until(lambda: self.answers(rig) and self.answers(rig)[0] != once)
        self.assertEqual(self.herdr.sent, [])
        self.assertIn("rm -rf build", self.look(rig, "card.screen")["lines"])
        state = self.look(rig, "card.state")["text"]
        self.assertIn("nothing sent: the dialog changed since the card was drawn", state)
        self.assertIn("nothing sent to pane w1:p1: the dialog changed since the card was drawn", rig.logged)
        # The old buttons are gone: a tap on them is nothing, the new ones
        # (new ids, so the touch bar arms them again) need a fresh tap.
        _, new_deny = self.answers(rig)
        self.assertNotIn(deny, rig.widgets())
        self.tap(rig, once)
        self.tap(rig, deny)
        rig.turn(20)
        self.assertEqual(self.herdr.sent, [])
        self.tap(rig, new_deny)
        rig.until(lambda: self.herdr.sent == [("w1:p1", ("esc",))])

    def test_a_state_change_between_draw_and_tap_is_not_answered(self):
        rig = self.rig(poll=60)
        once, _ = self.open(rig)
        self.herdr.set(0, Agent("w1:p1", "claude", "working", "/work/api", 8))
        self.tap(rig, once)
        rig.until(lambda: "the agent is working now" in self.look(rig, "card.state")["text"])
        self.assertEqual(self.answers(rig), [])
        self.assertEqual(self.herdr.sent, [])

    def test_a_card_follows_its_screen_and_closes_when_the_agent_goes(self):
        rig = self.rig()
        self.tap(rig, "agent.w2.p1")
        rig.until(lambda: rig.widgets()[0] == "card.screen" and "building…" in self.look(rig, "card.screen")["lines"])
        self.assertEqual(self.answers(rig), [])
        self.herdr.screens["w2:p1"] = "$ make\ndone\n"
        rig.until(lambda: "done" in self.look(rig, "card.screen")["lines"])
        with self.herdr.lock:
            del self.herdr.list[1]
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1"])

    def test_interrupt_goes_through_firstmate_for_its_tasks(self):
        rig = self.rig()
        self.tap(rig, "agent.w1.p1")
        rig.until(lambda: rig.widgets()[0] == "card.screen")
        rig.until(lambda: self.look(rig, "card.interrupt").get("tone") == "warning")
        self.tap(rig, "card.interrupt")
        rig.until(lambda: "interrupt: delivered" in self.look(rig, "card.state")["text"])
        self.assertEqual(self.firstmate.interrupted, ["fix-api"])
        self.assertEqual(self.herdr.sent, [])
        self.firstmate.interrupt_error = "fm-control.sh interrupt failed: error: no such task"
        self.tap(rig, "card.interrupt")
        rig.until(lambda: "interrupt: failed: fm-control" in self.look(rig, "card.state")["text"])

    def test_a_second_interrupt_waits_for_the_first(self):
        # Two Escapes are not one interrupt to Claude Code: taps while one
        # is on its way do nothing.
        release = threading.Event()
        interrupt = self.firstmate.interrupt
        self.firstmate.interrupt = lambda task_id: release.wait(5) and interrupt(task_id)  # type: ignore[method-assign]  # held until the test lets it go
        rig = self.rig()
        self.tap(rig, "agent.w1.p1")
        rig.until(lambda: self.look(rig, "card.interrupt").get("tone") == "warning")
        for _ in range(3):
            self.tap(rig, "card.interrupt")
            rig.turn(3)
        release.set()
        rig.until(lambda: "interrupt: delivered" in self.look(rig, "card.state")["text"])
        rig.turn(10)
        self.assertEqual(self.firstmate.interrupted, ["fix-api"])
        self.tap(rig, "card.interrupt")
        rig.until(lambda: self.firstmate.interrupted == ["fix-api", "fix-api"])

    def test_interrupt_goes_through_herdr_keys_for_other_agents(self):
        rig = self.rig()
        self.tap(rig, "agent.w2.p1")
        rig.until(lambda: rig.widgets()[0] == "card.screen")
        self.tap(rig, "card.interrupt")
        rig.until(lambda: self.herdr.sent == [("w2:p1", ("esc",))])
        rig.until(lambda: "interrupt: sent esc" in self.look(rig, "card.state")["text"])
        self.assertEqual(self.firstmate.interrupted, [])

    def test_an_unknown_harness_is_read_only(self):
        self.herdr.set(1, Agent("w2:p1", "opencode", "blocked", "/w/web", 4))
        self.herdr.screens["w2:p1"] = dialog("npm test")
        rig = self.rig()
        self.tap(rig, "agent.w2.p1")
        rig.until(lambda: "npm test" in self.look(rig, "card.screen")["lines"])
        self.assertEqual(self.answers(rig), [])
        self.assertEqual(self.look(rig, "card.interrupt")["text"], "no interrupt known for opencode")
        self.tap(rig, "card.interrupt")
        rig.turn(10)
        self.assertEqual((self.herdr.sent, self.firstmate.interrupted), ([], []))

    def test_a_failed_send_is_shown_and_the_card_stays(self):
        rig = self.rig(poll=60)
        once, _ = self.open(rig)
        self.herdr.keys_error = "herdr agent send-keys: pane gone"
        self.tap(rig, once)
        rig.until(lambda: "sending failed" in self.look(rig, "card.state")["text"])
        self.assertEqual(len(self.answers(rig)), 2)


if __name__ == "__main__":
    unittest.main()
