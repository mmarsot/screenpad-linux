"""Tests for Fn+F6's cycle (screenpad_touchbar/fnkey.py): the state mapping, the
cycle against a GNOME that flips the setting on each press, a touchpad turned
off elsewhere, what the scene says of it for the "ScreenPad on" indicator, the
hotkeys device, the gsettings and screenpad commands through stubs, the
daemon grabbing only while the display is on, and the touchpad's tool types
reset whenever libinput reads it again. No root, no hardware, no GNOME.

Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from touchbar_support import AXES, ConfigHome, FakePanel, FakeSetting, config, hotkey

from screenpad_touchbar import fnkey
from screenpad_touchbar.codes import EV_KEY, EV_SYN, INPUT_EVENT, KEY_TOUCHPAD_TOGGLE
from screenpad_touchbar.common import state_path
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
from screenpad_touchbar.keys import KeyEmitter
from screenpad_touchbar.splitter import Splitter

# The cycle, as (touchpad, display): (1) both off, (2) the touchpad only, (3) both.
BOTH_OFF, PAD_ONLY, BOTH_ON = (False, False), (True, False), (True, True)


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

    def test_a_touchpad_turned_off_elsewhere_leaves_the_display_alone(self):
        self.setting.change("disabled")  # the Settings panel, no key
        self.toggle.readable([self.setting.fd()])
        self.toggle.tick()
        self.assertEqual(self.panel.ran, [])
        # The next press goes on from (touchpad off, display on): state (2).
        self.press()
        self.assertEqual(self.state(), PAD_ONLY)

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

    def test_lit_counts_the_presses_that_light_the_display(self):
        hotkey(self, self.toggle)
        lit = []
        for _ in range(2):
            for _ in range(3):  # to (1), (2), (3)
                lit.append(self.toggle.shown())
                self.press()
        self.assertEqual(
            [(s["lit"], s["lights_next"]) for s in lit],
            [(0, False), (0, False), (0, True), (1, False), (1, False), (1, True)],
        )
        self.assertEqual(self.toggle.shown(), {"lit": 2, "lights_next": False})

    def test_lights_next_only_while_a_press_would(self):
        self.make(lit=False)
        self.assertFalse(self.toggle.lights_next())  # no hotkeys device: GNOME's indicator is right
        hotkey(self, self.toggle)
        self.assertTrue(self.toggle.lights_next())
        self.toggle.press()  # until GNOME's flip, the press is still settling
        self.assertFalse(self.toggle.lights_next())
        self.assertEqual(self.toggle.lit, 1)
        self.make(value="disabled", lit=False)  # a touchpad turned off elsewhere
        hotkey(self, self.toggle)
        self.assertFalse(self.toggle.lights_next())
        self.make(lit=False)
        hotkey(self, self.toggle)
        os.unlink(self.flag)  # screenpad off: presses only toggle the touchpad
        self.assertFalse(self.toggle.lights_next())
        self.press()
        self.assertEqual(self.toggle.lit, 0)

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

    def test_the_scene_says_a_press_lit_the_display_at_once(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            flag = os.path.join(d, "active")
            open(flag, "w").close()
            setting = FakeSetting()
            self.addCleanup(setting.stop)
            toggle = Toggle(setting, FakePanel(lit=False), find=lambda: None, flag=flag)
            w = hotkey(self, toggle)
            daemon = Daemon(config(), None, flag=flag, toggle=toggle)
            # Two rounds of the loop from state (2): Fn+F6 comes in during
            # the first, GNOME's flip not yet; the scene says the press lit
            # the display at the start of the next, before the daemon grabs.
            os.write(w, events((EV_KEY, KEY_TOUCHPAD_TOGGLE, 1), (EV_SYN, 0, 0)))
            rounds = []

            def scene():
                with open(state_path()) as f:
                    return json.load(f)["fnkey"]

            def select(r, _w, _x, _timeout):
                rounds.append(scene())
                daemon.stop = len(rounds) == 2
                return [toggle.hotkey.fd] if len(rounds) == 1 else [], [], []

            grabbing = []
            with (
                mock.patch("screenpad_touchbar.daemon.select.select", select),
                mock.patch.object(daemon, "activate", lambda: grabbing.append(scene())),
            ):
                daemon.loop()
            self.assertEqual(rounds, [{"lit": 0, "lights_next": True}, {"lit": 1, "lights_next": False}])
            self.assertEqual(grabbing, [{"lit": 1, "lights_next": False}])
            # A daemon without the cycle (Fn+F6 not followed) says nothing.
            Daemon(config(), None, flag=flag).publish()
            with open(state_path()) as f:
                self.assertIsNone(json.load(f)["fnkey"])

    def test_letting_go_resets_the_tool_types_first(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            daemon = Daemon(config(), None)
            calls = []

            class Grabbed:
                """The grabbed touchpad, as deactivate() uses it."""

                grabbed = True

                def touching(self):
                    return False

                def reset_tools(self):
                    calls.append("reset")

                def grab(self, on):
                    calls.append("grab %s" % on)

                def close(self):
                    calls.append("close")

            daemon.source = Grabbed()  # type: ignore[assignment]  # stands in for the parts deactivate() uses
            daemon.splitter = Splitter(AXES, 5, daemon._zone)
            daemon.bar = daemon.new_bar(KeyEmitter(lambda ev: None))
            with contextlib.redirect_stderr(io.StringIO()):
                daemon.deactivate("the display off")
            # While the grab still keeps it from libinput.
            self.assertEqual(calls, ["reset", "grab False", "close"])

    def test_the_touchpad_turned_on_with_no_grab_resets_the_tool_types(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            flag = os.path.join(d, "active")
            open(flag, "w").close()
            setting = FakeSetting("disabled")  # (1): both off
            self.addCleanup(setting.stop)
            toggle = Toggle(setting, FakePanel(lit=False), find=lambda: None, flag=flag)
            daemon = Daemon(config(), None, device="/dev/input/event5", flag=flag, toggle=toggle)
            rounds = []

            def select(r, _w, _x, _timeout):
                rounds.append(len(resets))
                if len(rounds) == 2:
                    setting.flip()  # Fn+F6: GNOME turns the touchpad on, (2)
                daemon.stop = len(rounds) == 4
                return [setting.fd()] if len(rounds) == 2 else [], [], []

            def activate():
                raise AssertionError("no grab while the display is off")

            resets = []
            with (
                mock.patch("screenpad_touchbar.daemon.select.select", select),
                mock.patch("screenpad_touchbar.daemon.reset_tools", resets.append),
                mock.patch.object(daemon, "activate", activate),
            ):
                daemon.loop()
            # Not at the start; once, as GNOME's flip is read; not again.
            self.assertEqual(rounds, [0, 0, 1, 1])
            self.assertEqual(resets, ["/dev/input/event5"])

    def test_turned_on_is_off_to_on_once(self):
        setting = FakeSetting(None)  # not read yet
        self.addCleanup(setting.stop)
        toggle = Toggle(setting, FakePanel(), find=lambda: None, flag="/nonexistent")
        seen = []
        for value in (None, "enabled", "disabled", "disabled", "enabled", "enabled", "disabled-on-external-mouse"):
            setting.value = value
            seen.append(toggle.turned_on())
        self.assertEqual(seen, [False, False, False, False, True, False, False])
        # From off to on with no call between still counts.
        setting.value = "disabled"
        toggle.turned_on()
        setting.value = "disabled-on-external-mouse"
        self.assertTrue(toggle.turned_on())

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
