"""Tests for Fn+F6's cycle (screenpad_touchbar/fnkey.py): the state mapping, the
cycle against a GNOME that flips the setting on each press, a touchpad turned
off elsewhere, the hotkeys device, the gsettings and screenpad commands through
stubs, and the daemon grabbing only while the display is on. No root, no
hardware, no GNOME.

Run with: python3 -m unittest discover -s tests"""

import os
import tempfile
import time
import unittest

from touchbar_support import ConfigHome, config

from screenpad_touchbar import fnkey
from screenpad_touchbar.codes import EV_KEY, EV_SYN, INPUT_EVENT, KEY_TOUCHPAD_TOGGLE
from screenpad_touchbar.daemon import Daemon
from screenpad_touchbar.fnkey import (
    FLIP_WAIT,
    Hotkey,
    Panel,
    Toggle,
    TouchpadSetting,
    find_hotkeys,
    next_state,
    parse_value,
    touchpad_on,
)

# The cycle, as (touchpad, display): (1) both off, (2) the touchpad only, (3) both.
BOTH_OFF, PAD_ONLY, BOTH_ON = (False, False), (True, False), (True, True)


class FakeSetting:
    """send-events as GNOME keeps it. flip() is what gsd-media-keys does on a
    press: the change shows only when the monitor's line is read."""

    def __init__(self, value="enabled"):
        self.value = value
        self.written = []
        self.r, self.w = os.pipe()
        self.lines = []

    def start(self, now):
        pass

    def fd(self):
        return self.r

    def flip(self):
        current = self.lines[-1] if self.lines else self.value
        self.change("disabled" if current == "enabled" else "enabled")

    def change(self, value):
        self.lines.append(value)
        os.write(self.w, b".")

    def readable(self):
        os.read(self.r, 64)
        changes = 0
        for value in self.lines:
            if value != self.value:
                self.value = value
                changes += 1
        self.lines = []
        return changes

    def set(self, value):
        self.written.append(value)
        self.value = value

    def stop(self):
        os.close(self.r)
        os.close(self.w)


class FakePanel(Panel):
    """The panel, with 'screenpad light' and 'dark' done at once (or failing)."""

    def __init__(self, lit=True):
        super().__init__(power="/nonexistent", command=["false"])
        self.is_lit = lit
        self.ran = []
        self.fail = False

    def lit(self):
        return self.is_lit

    def spawn(self, on):
        self.ran.append("light" if on else "dark")
        if not self.fail:
            self.is_lit = on


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.flag = os.path.join(self.tmp.name, "active")
        open(self.flag, "w").close()
        self.make()

    def make(self, value="enabled", lit=True):
        self.setting = FakeSetting(value)
        self.addCleanup(self.setting.stop)
        self.panel = FakePanel(lit)
        self.clock = Clock()
        self.toggle = Toggle(self.setting, self.panel, find=lambda: None, flag=self.flag, clock=self.clock)
        self.toggle.tick()

    def state(self):
        return touchpad_on(self.setting.value), self.panel.lit()

    def press(self, gnome=True):
        """Fn+F6: the daemon sees the key, then GNOME flips the setting."""
        self.toggle.press()
        if gnome:
            self.setting.flip()
            self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        if not gnome:
            self.clock.now += FLIP_WAIT
            self.toggle.tick()

    def test_next_state(self):
        self.assertEqual(next_state(*BOTH_ON), BOTH_OFF)
        self.assertEqual(next_state(*BOTH_OFF), PAD_ONLY)
        self.assertEqual(next_state(*PAD_ONLY), BOTH_ON)
        # A touchpad off with the display on is not in the cycle; a press
        # gives the touchpad back first.
        self.assertEqual(next_state(False, True), PAD_ONLY)

    def test_touchpad_on(self):
        self.assertTrue(touchpad_on("enabled"))
        self.assertTrue(touchpad_on("disabled-on-external-mouse"))
        self.assertFalse(touchpad_on("disabled"))

    def test_three_presses_go_round_the_cycle(self):
        for _ in range(3):
            self.press()
            self.assertEqual(self.state(), BOTH_OFF)
            self.press()
            self.assertEqual(self.state(), PAD_ONLY)
            self.press()
            self.assertEqual(self.state(), BOTH_ON)
        self.assertEqual(self.panel.ran, ["dark", "light"] * 3)
        # GNOME's flip is right for two steps; only the step that keeps the
        # touchpad on, (2) to (3), writes the setting back.
        self.assertEqual(self.setting.written, ["enabled"] * 3)

    def test_the_display_follows_the_press_at_once(self):
        self.toggle.press()
        self.assertFalse(self.toggle.display_on())
        self.assertEqual(self.panel.ran, ["dark"])

    def test_without_gnome_handling_the_key_the_setting_is_written(self):
        for want in (BOTH_OFF, PAD_ONLY, BOTH_ON):
            self.press(gnome=False)
            self.assertEqual(self.state(), want)
        self.assertEqual(self.setting.written, ["disabled", "enabled"])

    def test_a_slow_flip_is_waited_for(self):
        self.make(lit=False)
        self.toggle.press()
        self.clock.now += FLIP_WAIT / 2
        self.toggle.tick()
        self.assertEqual(self.setting.written, [])
        self.setting.flip()
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        self.assertEqual(self.state(), BOTH_ON)

    def test_two_quick_presses_take_two_steps(self):
        self.toggle.press()
        self.toggle.press()
        self.setting.flip()
        self.setting.flip()
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        self.assertEqual(self.state(), PAD_ONLY)
        self.assertEqual(self.setting.written, [])

    def test_a_touchpad_turned_off_elsewhere_darkens_the_display_once(self):
        self.panel.fail = True
        self.setting.change("disabled")  # the Settings panel, no key
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        self.toggle.tick()
        self.assertEqual(self.panel.ran, ["dark"])
        # Turned back on there, the display stays as it is: state (2).
        self.panel.fail = False
        self.panel.is_lit = False
        self.setting.change("enabled")
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        self.assertEqual(self.state(), PAD_ONLY)
        self.assertEqual(self.panel.ran, ["dark"])

    def test_a_display_lit_with_the_touchpad_off_is_darkened(self):
        # A reboot or 'screenpad on' with the touchpad left off in GNOME.
        os.unlink(self.flag)
        self.make(value="disabled", lit=False)
        self.panel.is_lit = True
        self.toggle.tick()
        self.assertEqual(self.panel.ran, [])
        open(self.flag, "w").close()
        self.toggle.tick()
        self.assertEqual(self.state(), BOTH_OFF)

    def test_nothing_while_the_screenpad_is_off(self):
        os.unlink(self.flag)
        self.press()
        self.assertEqual(self.panel.ran, [])
        self.assertEqual(self.setting.written, [])

    def test_nothing_without_the_setting(self):
        self.setting.value = None
        self.toggle.press()
        self.assertIsNone(self.toggle.pending)
        self.assertEqual(self.panel.ran, [])

    def test_disabled_on_external_mouse_counts_as_on(self):
        self.make(value="disabled-on-external-mouse")
        self.press()  # GNOME turns it to enabled; the cycle wants it off
        self.assertEqual(self.state(), BOTH_OFF)
        self.assertEqual(self.setting.written, ["disabled"])


def events(*evs):
    return b"".join(INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in evs)


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_find_hotkeys(self):
        root = self.tmp.name
        for n, name in ((3, "AT Translated Set 2 keyboard"), (13, "Asus WMI hotkeys")):
            os.makedirs(os.path.join(root, "event%d" % n, "device"))
            with open(os.path.join(root, "event%d" % n, "device", "name"), "w") as f:
                f.write(name + "\n")
        self.assertEqual(find_hotkeys(root), "/dev/input/event13")
        self.assertIsNone(find_hotkeys(os.path.join(root, "missing")))

    def test_counts_presses_only(self):
        path = os.path.join(self.tmp.name, "event13")
        os.mkfifo(path)
        key = Hotkey(path)
        self.addCleanup(key.close)
        w = os.open(path, os.O_WRONLY)
        self.assertEqual(key.presses(), 0)
        os.write(
            w,
            events(
                (EV_KEY, KEY_TOUCHPAD_TOGGLE, 1),
                (EV_SYN, 0, 0),
                (EV_KEY, KEY_TOUCHPAD_TOGGLE, 0),
                (EV_KEY, 0xE0, 1),  # brightness down
                (EV_KEY, KEY_TOUCHPAD_TOGGLE, 1),
            ),
        )
        self.assertEqual(key.presses(), 2)
        os.close(w)
        self.assertIsNone(key.presses())


def stub(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)
    return path


class CommandTests(unittest.TestCase):
    """The real TouchpadSetting and Panel, through stub commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "log")

    def logged(self):
        try:
            with open(self.log) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def wait(self, check):
        deadline = time.monotonic() + 5
        while not check():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)

    def test_touchpad_setting(self):
        gsettings = stub(
            os.path.join(self.tmp.name, "gsettings"),
            'echo "$*" >> "%s"\n' % self.log
            + 'case "$1" in\n'
            + "get) echo \"'enabled'\" ;;\n"
            + "monitor) echo \"send-events: 'disabled'\"; echo \"send-events: 'disabled'\"; exec sleep 30 ;;\n"
            + "esac\n",
        )
        setting = TouchpadSetting([gsettings])
        self.addCleanup(setting.stop)
        setting.start(0)
        self.assertEqual(setting.value, "enabled")
        changes = []
        self.wait(lambda: changes.append(setting.readable()) or sum(changes))
        self.assertEqual((sum(changes), setting.value), (1, "disabled"))
        setting.set("enabled")
        self.assertEqual(setting.value, "enabled")
        self.wait(lambda: "set" in self.logged())
        self.assertEqual(
            self.logged().splitlines(),
            [
                "monitor org.gnome.desktop.peripherals.touchpad send-events",
                "get org.gnome.desktop.peripherals.touchpad send-events",
                "set org.gnome.desktop.peripherals.touchpad send-events enabled",
            ],
        )

    def test_parse_value(self):
        self.assertEqual(parse_value("'enabled'\n"), "enabled")
        self.assertEqual(parse_value("send-events: 'disabled-on-external-mouse'"), "disabled-on-external-mouse")
        self.assertIsNone(parse_value(""))

    def test_panel_runs_screenpad_and_queues(self):
        power = os.path.join(self.tmp.name, "bl_power")
        with open(power, "w") as f:
            f.write("4\n")
        screenpad = stub(
            os.path.join(self.tmp.name, "screenpad"),
            'sleep 0.2; echo "$1" >> "%s"; [ "$1" = light ] && echo 4 > "%s" || echo 0 > "%s"\n'
            % (self.log, power, power),
        )
        panel = Panel(power, [screenpad])
        self.assertTrue(panel.lit())
        panel.set(True)
        self.assertFalse(panel.busy())
        panel.set(False)
        self.assertTrue(panel.busy())
        self.assertFalse(panel.wanted())
        panel.set(True)  # queued behind the dark
        self.assertTrue(panel.wanted())
        self.wait(lambda: not panel.busy())
        self.assertTrue(panel.lit())
        self.assertEqual(self.logged().split(), ["dark", "light"])
        os.unlink(power)
        self.assertTrue(panel.lit())  # no backlight: the bar works as before


class DaemonTests(unittest.TestCase):
    def test_the_daemon_wants_the_touchpad_only_while_the_display_is_on(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            flag = os.path.join(d, "active")
            setting = FakeSetting()
            self.addCleanup(setting.stop)
            panel = FakePanel(lit=True)
            toggle = Toggle(setting, panel, find=lambda: None, flag=flag)
            daemon = Daemon(config(), None, flag=flag, toggle=toggle)
            self.assertFalse(daemon.want_active())
            open(flag, "w").close()
            self.assertTrue(daemon.want_active())
            panel.is_lit = False
            self.assertFalse(daemon.want_active())
            self.assertFalse(Daemon(config(), None, flag=os.path.join(d, "none")).want_active())
            self.assertTrue(Daemon(config(), None, flag=flag).want_active())

    def test_the_hotkey_device_is_retried_quietly(self):
        clock = Clock()
        setting = FakeSetting()
        self.addCleanup(setting.stop)
        found = []
        toggle = Toggle(setting, FakePanel(), find=lambda: found.append(1), flag="/nonexistent", clock=clock)
        toggle.tick()
        toggle.tick()
        self.assertEqual(len(found), 1)
        clock.now += fnkey.RETRY
        toggle.tick()
        self.assertEqual(len(found), 2)


if __name__ == "__main__":
    unittest.main()
