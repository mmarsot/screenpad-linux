"""Captain's Call opening firstmate's decisions board: finding it through
fm-bearings-board.sh path and Lavish's session listing, opening its URL with
the opener (a stand-in here, so nothing opens on the screen), and the tap in
the loop: the board opened, or the card saying none is open."""

import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from agents_support import (
    FakeFirstmate,
    FakeHerdr,
    Rig,
    fake_bearings,
    fake_lavish,
    fake_opener,
    lavish_listing,
    read_calls,
    task,
)

from screenpad_agents.firstmate import Firstmate, FirstmateError, listed_url

URL = "http://127.0.0.1:4387/session/b2403039846e8e74"


class BoardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.code = os.path.join(self.dir, "fm")
        fake_bearings(self.code)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".lavish"))
        self.board = os.path.join(self.home, ".lavish", "bearings-board.html")
        self.lavish = fake_lavish(self.dir)
        self.opener = fake_opener(self.dir)
        bin_ = os.path.join(self.code, "bin")
        self.fm = Firstmate(
            self.home,
            os.path.join(bin_, "fm-bearings-snapshot.sh"),
            os.path.join(bin_, "fm-control.sh"),
            board=os.path.join(bin_, "fm-bearings-board.sh"),
            lavish=[self.lavish],
            opener=[self.opener, "open"],
        )

    def listing(self, *rows, raw=None):
        with open(os.path.join(self.dir, "listing.txt"), "w") as f:
            f.write(lavish_listing(*rows) if raw is None else raw)

    def opened(self):
        return read_calls(os.path.join(self.dir, "opened.jsonl"))

    def test_opens_the_board_lavish_lists_open(self):
        self.listing(("/elsewhere/other.html", "open", "http://127.0.0.1:4387/session/1"), (self.board, "open", URL))
        with mock.patch.dict(os.environ, {"FM_HOME": "/home/me/firstmate", "FM_BACKEND": "herdr"}):
            self.assertEqual(self.fm.open_board(), URL)
        self.assertEqual(self.opened(), [["open", URL]])
        # The board's path comes from firstmate, on the configured home only;
        # Lavish and the opener get no FM_* at all.
        board = read_calls(os.path.join(self.code, "bin", "board.jsonl"))
        self.assertEqual(board, [{"FM_HOME": self.home, "fm": ["FM_HOME"], "args": ["path"]}])
        self.assertEqual(read_calls(os.path.join(self.dir, "lavish.jsonl")), [{"fm": [], "args": []}])

    def test_a_browser_the_opener_starts_does_not_hold_the_open(self):
        # gio open returns at once, but a browser it starts lives on with its
        # output: the open must not wait for that browser (OPEN_TIMEOUT 15 s).
        self.listing((self.board, "open", URL))
        open(os.path.join(self.dir, "opener.linger"), "w").close()
        start = time.monotonic()
        self.assertEqual(self.fm.open_board(), URL)
        with open(os.path.join(self.dir, "linger.pid")) as f:
            pid = int(f.read())
        self.addCleanup(os.kill, pid, signal.SIGKILL)
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(self.opened(), [["open", URL]])

    def test_the_board_is_found_by_its_real_path(self):
        os.makedirs(os.path.join(self.dir, "real"))
        os.rmdir(os.path.join(self.home, ".lavish"))
        os.symlink(os.path.join(self.dir, "real"), os.path.join(self.home, ".lavish"))
        self.listing((os.path.join(self.dir, "real", "bearings-board.html"), "open", URL))
        self.assertEqual(self.fm.board_url(), URL)

    def test_the_board_is_found_when_lavish_lists_it_unresolved(self):
        os.makedirs(os.path.join(self.dir, "real"))
        os.rmdir(os.path.join(self.home, ".lavish"))
        os.symlink(os.path.join(self.dir, "real"), os.path.join(self.home, ".lavish"))
        for listed in (self.board, self.home + "/./.lavish//bearings-board.html"):
            with self.subTest(listed=listed):
                self.listing((listed, "open", URL))
                self.assertEqual(self.fm.board_url(), URL)

    def test_no_board_open_opens_nothing(self):
        for rows in (
            (),
            ((self.board, "user-ended", URL),),
            ((self.board + ".bak", "open", URL),),
            (("/elsewhere" + self.board, "open", URL),),
        ):
            with self.subTest(rows=rows):
                self.listing(*rows)
                self.assertIsNone(self.fm.open_board())
        self.assertEqual(self.opened(), [])

    def test_only_a_local_url_is_opened(self):
        for url in ("https://example.com/session/1", "file:///etc/passwd", "http://127.0.0.1:4387/a b", "-x"):
            with self.subTest(url=url):
                self.listing((self.board, "open", url))
                with self.assertRaisesRegex(FirstmateError, "not a local URL"):
                    self.fm.open_board()
        self.assertEqual(self.opened(), [])
        for url in ("http://localhost:4387/session/ab", "http://[::1]:80/"):
            self.assertEqual(listed_url('  %s,open,"%s",0,none\n' % (self.board, url), self.board), url)

    def test_failures_say_why(self):
        self.listing(raw="fail")
        with self.assertRaisesRegex(FirstmateError, "lavish-axi failed: lavish-axi: server unreachable"):
            self.fm.open_board()
        self.listing((self.board, "open", URL))
        open(os.path.join(self.dir, "opener.fail"), "w").close()
        with self.assertRaisesRegex(FirstmateError, "opening the board failed: exit 2"):
            self.fm.open_board()
        os.remove(os.path.join(self.code, "bin", "fm-bearings-board.sh"))
        with self.assertRaisesRegex(FirstmateError, "firstmate has no bin/fm-bearings-board.sh"):
            self.fm.open_board()
        self.assertIsNone(Firstmate(self.home, "r", "c").board)
        with self.assertRaisesRegex(FirstmateError, "firstmate has no"):
            Firstmate(self.home, "r", "c").board_url()

    def test_a_board_path_that_is_not_one_is_refused(self):
        for out in ("", "relative/board.html\n", "/a\n/b\n"):
            done = subprocess.CompletedProcess([], 0, out, "")
            fm = Firstmate(self.home, "r", "c", run=mock.Mock(return_value=done), board=self.fm.board)
            with self.subTest(out=out), self.assertRaisesRegex(FirstmateError, "printed no board path"):
                fm.board_url()


class BoardTapTests(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr([("w1:p1", "claude", "blocked")])
        self.firstmate = FakeFirstmate(calls=2, tasks={"lab:w1:p1": task("fix-api")})

    def rig(self):
        rig = Rig(self, self.herdr, self.firstmate)
        rig.apps.asks.set_available(True)
        rig.until(lambda: rig.widgets() == ["calls", "agent.w1.p1"])
        rig.until(lambda: rig.apps.provided().get("calls", {}).get("badge") == "2")
        return rig

    def asked(self, rig):
        return [(a.title, a.text, [o["label"] for o in a.options]) for a in rig.apps.asks.queue]

    def test_a_tap_opens_the_board_and_reads_firstmate_again(self):
        rig = self.rig()
        reads = self.firstmate.reads
        for kind in ("down", "cancel", "long"):
            rig.apps.press(rig.client(), "agents", "calls", kind)
        rig.apps.event("calls", "down")
        rig.apps.event("calls", "up", inside=False)
        rig.turn(10)
        self.assertEqual(self.firstmate.board_calls, 0)
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: self.firstmate.opened == [FakeFirstmate.BOARD])
        rig.until(lambda: self.firstmate.reads == reads + 1)
        rig.until(lambda: rig.logged == ["opened the decisions board: " + FakeFirstmate.BOARD])
        rig.apps.event("calls", "up", inside=True)
        rig.until(lambda: len(self.firstmate.opened) == 2)
        self.assertEqual(self.asked(rig), [])
        # Read-only: nothing went to an agent or to firstmate's control.
        self.assertEqual((self.herdr.sent, self.firstmate.interrupted), ([], []))

    def test_taps_while_it_opens_open_it_once(self):
        rig = self.rig()
        self.firstmate.release = threading.Event()
        for _ in range(3):
            rig.apps.press(rig.client(), "agents", "calls", "tap")
            rig.turn(3)
        rig.until(lambda: self.firstmate.board_calls == 1)
        self.firstmate.release.set()
        rig.until(lambda: self.firstmate.opened == [FakeFirstmate.BOARD])
        rig.turn(10)
        self.assertEqual(self.firstmate.board_calls, 1)

    def test_no_board_open_shows_a_card_and_builds_nothing(self):
        rig = self.rig()
        self.firstmate.board = None
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        card = ("No decisions board open", "Ask firstmate for /bearings lavish; it builds the board.", ["OK"])
        rig.until(lambda: self.asked(rig) == [card])
        self.assertEqual(self.firstmate.opened, [])
        self.assertEqual(rig.logged, ["no decisions board is open"])
        # A second tap replaces the card rather than queueing another.
        rig.apps.event("calls", "up", inside=True)
        rig.until(lambda: self.firstmate.board_calls == 2)
        rig.turn(10)
        self.assertEqual(self.asked(rig), [card])
        # OK closes it, and does nothing more.
        ask = rig.apps.asks.queue[0]
        rig.apps.asks.choose(ask, 0)
        rig.turn(10)
        self.assertEqual((self.asked(rig), rig.app._asked), ([], None))
        self.assertEqual((self.herdr.sent, self.firstmate.interrupted, self.firstmate.opened), ([], [], []))

    def test_a_failure_is_shown_on_the_card(self):
        rig = self.rig()
        self.firstmate.board_error = "lavish-axi failed: server unreachable"
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: self.asked(rig) == [("Decisions board not opened", self.firstmate.board_error, ["OK"])])
        self.assertEqual(rig.logged, ["the decisions board did not open: lavish-axi failed: server unreachable"])
        self.firstmate.board_error = RuntimeError("boom")
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: self.asked(rig) == [("Decisions board not opened", "RuntimeError: boom", ["OK"])])
        self.firstmate.board_error = None
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: self.firstmate.opened == [FakeFirstmate.BOARD])

    def test_a_card_the_bar_cannot_show_is_logged(self):
        rig = self.rig()
        rig.apps.asks.set_available(False)
        self.firstmate.board = None
        rig.apps.press(rig.client(), "agents", "calls", "tap")
        rig.until(lambda: "the touch bar could not show the board's card" in rig.logged)
        self.assertIsNone(rig.app._asked)

    def test_without_firstmate_a_tap_opens_nothing(self):
        rig = Rig(self, self.herdr, None)
        rig.until(lambda: rig.widgets() == ["agent.w1.p1"])
        rig.apps.event("calls", "up", inside=True)
        rig.app.captains_call()
        rig.turn(5)
        self.assertEqual(self.firstmate.board_calls, 0)


if __name__ == "__main__":
    unittest.main()
