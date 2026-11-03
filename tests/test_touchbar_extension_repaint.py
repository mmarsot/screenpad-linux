"""Tests for the extension's repaint.js, the repaint safeguard's plan, run
under node with a stand-in clock: when the whole ScreenPad monitor is
repainted (once the content settles, at most MAX_WAIT_MS apart while it
keeps changing, on display events), that nothing runs while the pad is
idle, that a repaint is FULL_FRAMES frames in a row, that its own frames
are not activity, and that a burst of activity re-arms the one timer only when it must fire earlier.

Run with: python3 -m unittest discover -s tests
"""

import unittest

from touchbar_support import js

# Replays v.events ([time, input, argument]) on a RepaintPlan, firing the
# one timer as the steps arm it. With v.stage, a queued redraw paints the
# ScreenPad's view one frame (16 ms) later, as the stage does. Returns each
# redraw as [time, the input that asked for it], how often the timer was
# armed, and the plan's last step.
SIM = """(() => {
    const p = new m.RepaintPlan();
    let timerAt = null;
    let paintAt = null;
    let arms = 0;
    const redraws = [];
    let last = null;
    const apply = (t, input, step) => {
        last = step;
        if (step.redraw) {
            redraws.push([t, input]);
            if (v.stage && paintAt === null) paintAt = t + 16;
        }
        if (step.timer === m.CANCEL) timerAt = null;
        else if (step.timer !== null) {
            timerAt = t + step.timer;
            arms += 1;
        }
    };
    const advance = until => {
        for (;;) {
            const next = Math.min(timerAt ?? Infinity, paintAt ?? Infinity);
            if (next > until) return;
            if (next === paintAt) {
                paintAt = null;
                apply(next, 'frame', p.painted(next));
            } else {
                timerAt = null;
                apply(next, 'wake', p.wake(next));
            }
        }
    };
    for (const [t, input, arg] of v.events) {
        advance(t);
        const step =
            input === 'on' ? p.setOn(arg, t) : input === 'paint' ? p.painted(t) : p.request(t);
        apply(t, input, step);
    }
    advance(v.until);
    return {redraws, arms, last, timerAt};
})()"""


def constants():
    return js("repaint.js", "[m.SETTLE_MS, m.MAX_WAIT_MS, m.FULL_FRAMES, m.FRAME_WAIT_MS]")


def sim(events, until, stage=True):
    return js("repaint.js", SIM, events=events, until=until, stage=stage)


def starts(out):
    """The times a repaint started: the redraws the bar being drawn, a
    display event or the timer asked for (a paint only asks for a forced
    frame's next)."""
    return [t for t, input in out["redraws"] if input in ("on", "request", "wake")]


# Long enough that any periodic repaint would show.
IDLE_MS = 60 * 60 * 1000


class RepaintPlanTests(unittest.TestCase):
    """repaint.js: when the safeguard repaints, and what it costs."""

    def setUp(self):
        self.settle, self.max_wait, self.frames, self.frame_wait = constants()

    def test_constants(self):
        # Two frames, one per NVIDIA scanout buffer (mutter's buffer age 2);
        # the settle repaint 1 to 2 s after the last change; the cap well
        # above the settle time.
        self.assertEqual(self.frames, 2)
        self.assertTrue(1000 <= self.settle <= 2000)
        self.assertTrue(self.settle < self.max_wait)

    def test_off_does_nothing(self):
        out = sim([[0, "paint", None], [10, "request", None], [20, "paint", None]], IDLE_MS)
        self.assertEqual(out["redraws"], [])
        self.assertEqual(out["arms"], 0)

    def test_drawing_the_bar_repaints_two_frames_then_again_once_settled(self):
        # Enabled after unlock, or the ScreenPad back: repaint at once, and
        # again once things settle.
        out = sim([[0, "on", True]], 5000)
        self.assertEqual(
            out["redraws"], [[0, "on"], [16, "frame"], [32 + self.settle, "wake"], [48 + self.settle, "frame"]]
        )

    def test_settle_repaint_once_per_burst(self):
        # Things move on the pad for 2 s, a frame every 16 ms, then stop:
        # one repaint SETTLE_MS after the last frame, none while they move.
        events = [[0, "on", True]] + [[t, "paint", None] for t in range(10_000, 12_000, 16)]
        last = 10_000 + 16 * (2000 // 16 - 1)
        out = sim(events, 60_000)
        later = [t for t in starts(out) if t >= 10_000]
        self.assertEqual(later, [last + self.settle])
        # Each start is followed by FULL_FRAMES - 1 more forced frames.
        self.assertEqual(len(out["redraws"]), self.frames * len(starts(out)))

    def test_a_burst_does_not_rearm_the_timer_each_frame(self):
        events = [[0, "on", True]] + [[t, "paint", None] for t in range(10_000, 12_000, 16)]
        out = sim(events, 60_000)
        # The startup repaint, its settle repaint, one timer for the whole
        # burst and its repaint: a handful of arms, not one per frame.
        self.assertLess(out["arms"], 15)

    def test_its_own_frames_are_not_activity(self):
        # After a settle repaint, nothing more: no loop.
        out = sim([[0, "on", True], [10_000, "paint", None]], IDLE_MS)
        self.assertEqual(starts(out), [0, 32 + self.settle, 10_000 + self.settle])

    def test_still_content_repaints_at_most_max_wait_apart(self):
        # Something keeps changing (a frame every 500 ms, faster than the
        # settle time): a repaint every MAX_WAIT_MS, not never.
        events = [[0, "on", True]] + [[t, "paint", None] for t in range(10_000, 60_000, 500)]
        out = sim(events, 60_000)
        later = [t for t in starts(out) if t >= 10_000]
        self.assertTrue(later)
        gaps = [b - a for a, b in zip([10_000] + later[:-1], later, strict=True)]
        self.assertTrue(all(gap <= self.max_wait + 500 for gap in gaps), gaps)
        self.assertLessEqual(len(later), 50_000 // self.max_wait + 1)

    def test_nothing_runs_while_idle(self):
        # Drawn and untouched for an hour: the startup repaint and its
        # settle repaint, then no timer left armed.
        out = sim([[0, "on", True]], IDLE_MS)
        self.assertEqual(starts(out), [0, 32 + self.settle])
        self.assertIsNone(out["timerAt"])

    def test_display_event_repaints_now_and_again_once_settled(self):
        out = sim([[0, "on", True], [20_000, "request", None]], 30_000)
        self.assertEqual(starts(out), [0, 32 + self.settle, 20_000, 20_032 + self.settle])

    def test_a_storm_of_display_events_stays_bounded(self):
        # monitors-changed every 100 ms for 5 s (a dock hotplug storm).
        events = [[0, "on", True]] + [[t, "request", None] for t in range(10_000, 15_000, 100)]
        out = sim(events, 30_000)
        # One redraw per event at most, then a single settle repaint.
        self.assertLessEqual(len([t for t, input in out["redraws"] if input == "request"]), 50)
        self.assertEqual([t for t in starts(out) if t > 15_000], [14_932 + self.settle])

    def test_gives_up_when_no_frame_comes(self):
        # Blanked: the redraw is queued but no frame is painted. It gives up
        # after FRAME_WAIT_MS, with no settle repaint, and waits for the
        # next trigger with no timer armed.
        out = sim([[0, "on", True]], IDLE_MS, stage=False)
        self.assertEqual(starts(out), [0])
        self.assertEqual(out["arms"], 1)
        self.assertIsNone(out["timerAt"])

    def test_turning_off_cancels_the_timer(self):
        out = sim([[0, "on", True], [10_000, "paint", None], [10_100, "on", False]], IDLE_MS)
        self.assertEqual(starts(out), [0, 32 + self.settle])
        self.assertEqual(out["last"], {"redraw": False, "timer": -1})
        self.assertIsNone(out["timerAt"])

    def test_turning_on_again_repaints(self):
        out = sim([[0, "on", True], [5000, "on", False], [6000, "on", True]], 7000)
        self.assertEqual(starts(out), [0, 32 + self.settle, 6000])


if __name__ == "__main__":
    unittest.main()
