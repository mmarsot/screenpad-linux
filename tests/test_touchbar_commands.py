"""Tests for the commands (shortcut, status), finding the touchpad, resetting its tool types, and the monitor identity.

No root, no hardware, no uinput. Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from touchbar_support import ConfigHome

from screenpad_touchbar import cli
from screenpad_touchbar.cli import main
from screenpad_touchbar.codes import (
    ABS_MT_SLOT,
    ABS_MT_TOOL_TYPE,
    ABS_MT_TRACKING_ID,
    EV_ABS,
    EV_SYN,
    MT_TOOL_FINGER,
    MT_TOOL_PALM,
    MT_TOOL_PEN,
    SYN_REPORT,
)
from screenpad_touchbar.common import (
    MONITOR_ID,
    edid_identity,
    monitor_identity,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.devices import find_source, tool_reset


class ShortcutCommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["shortcut"] + list(argv))
        return code, out.getvalue()

    def test_add_list_remove(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            # No file yet: the default's three keys, then the app.
            self.assertEqual(self.run_cli("add", "org.gnome.Nautilus")[0], 0)
            self.assertEqual(self.run_cli("add", "b.desktop", "--spot", "1")[0], 0)
            _code, out = self.run_cli("list")
            lines = out.splitlines()
            self.assertEqual(len(lines), 6)
            self.assertEqual(lines[0], "1  b.desktop")
            self.assertTrue(lines[1].startswith("2  Push to talk, "), lines[1])
            self.assertEqual(lines[4:], ["5  org.gnome.Nautilus.desktop", "6  (empty)"])
            self.assertEqual(self.run_cli("remove", "1")[0], 0)
            bar = load_config(path)[0].bar
            self.assertEqual([i.name for i in bar], [None, None, None, "org.gnome.Nautilus.desktop"])
            # Any kind: the second spot is the default's mute key, and only it goes.
            code, out = self.run_cli("remove", "3")
            self.assertEqual((code, out), (0, "removed Screenshot from the bar\n"))
            bar = load_config(path)[0].bar
            self.assertEqual([i.label for i in bar[:2]], ["Push to talk", "Mute microphone"])
            self.assertEqual(len(bar), 3)
            # An empty spot, and the page button and grip after the sixth: nothing.
            self.assertEqual(self.run_cli("remove", "6")[0], 2)
            self.assertEqual(self.run_cli("remove", "7")[0], 2)
            self.assertEqual(self.run_cli("remove", "0")[0], 2)
            self.assertEqual(len(load_config(path)[0].bar), 3)
            self.assertEqual(self.run_cli("add", "bad name")[0], 2)
            self.assertEqual(self.run_cli("add", "c", "--spot", "7")[0], 2)
            self.assertEqual(len(load_config(path)[0].bar), 3)

    def test_the_apps_page(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d) as path:
            self.assertEqual(self.run_cli("add", "org.gnome.Settings", "--apps")[0], 0)
            code, out = self.run_cli("add", "bitwarden", "--apps", "--spot", "1")
            self.assertEqual((code, out), (0, "the apps page's apps: bitwarden.desktop, org.gnome.Settings.desktop\n"))
            _code, out = self.run_cli("list", "--apps")
            self.assertEqual(
                out.splitlines()[:3], ["1  bitwarden.desktop", "2  org.gnome.Settings.desktop", "3  (empty)"]
            )
            self.assertEqual(len(out.splitlines()), 8)
            c = load_config(path)[0]
            self.assertEqual(
                ([i.name for i in c.apps], len(c.bar)), (["bitwarden.desktop", "org.gnome.Settings.desktop"], 3)
            )
            self.assertEqual(self.run_cli("remove", "2", "--apps")[0], 0)
            self.assertEqual([i.name for i in load_config(path)[0].apps], ["bitwarden.desktop"])
            self.assertEqual(self.run_cli("add", "c", "--apps", "--spot", "9")[0], 2)


class StatusTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "root reads any file")
    def test_unreadable_touchpad_names_the_group(self):
        with tempfile.TemporaryDirectory() as d, ConfigHome(d):
            node = os.path.join(d, "event5")
            open(node, "w").close()
            os.chmod(node, 0)
            out = io.StringIO()
            done = mock.Mock(stdout="active\n")
            with (
                mock.patch.object(cli, "find_source", return_value=node),
                mock.patch.object(subprocess, "run", return_value=done),
                contextlib.redirect_stdout(out),
            ):
                self.assertEqual(main(["status"]), 0)
            self.assertIn("touchpad: %s (not readable: add your user to group input" % node, out.getvalue())


class SourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys", "class", "input")
        os.makedirs(self.sys)

    def add(self, event, name, vendor, product, virtual=False):
        kind = "virtual/input" if virtual else "platform/i2c/0018:27C6:01F1.0001/input"
        dev = os.path.join(self.tmp.name, "sys", "devices", kind, "input%s" % event)
        os.makedirs(os.path.join(dev, "id"))
        for rel, value in (("name", name), ("id/vendor", vendor), ("id/product", product)):
            with open(os.path.join(dev, rel), "w") as f:
                f.write(value + "\n")
        os.makedirs(os.path.join(self.sys, "event%s" % event))
        os.symlink(dev, os.path.join(self.sys, "event%s" % event, "device"))

    def test_finds_only_the_real_touchpad(self):
        self.add(4, "GDX1505:00 27C6:01F1 Mouse", "27c6", "01f1")
        self.add(3, "ScreenPad Touchpad", "27c6", "01f1", virtual=True)
        self.add(7, "SYNA Touchpad", "06cb", "cd8b")
        self.assertIsNone(find_source(self.sys))
        self.add(12, "GDX1505:00 27C6:01F1 Touchpad", "27c6", "01f1")
        self.assertEqual(find_source(self.sys), "/dev/input/event12")


# ABS_MT_TOOL_TYPE as the GDX1505 has it: finger to palm.
TOOL = (0, MT_TOOL_FINGER, MT_TOOL_PALM, 0, 0, 0)


class Kernel:
    """The input core for one multitouch device: it keeps each slot's
    values and passes an event on only when it changes the value (sending
    ABS_MT_SLOT just before the first such event of another slot), and
    only to the reader holding the grab."""

    def __init__(self, nslots=5):
        self.slots = [{ABS_MT_TRACKING_ID: -1, ABS_MT_TOOL_TYPE: MT_TOOL_FINGER} for _ in range(nslots)]
        self.staged = self.sent_slot = 0
        self.grab = None
        self.read = {"libinput": [], "daemon": []}

    def emit(self, events):
        passed = []
        for type, code, value in events:
            if (type, code) == (EV_ABS, ABS_MT_SLOT):
                self.staged = value
                continue
            if type == EV_ABS:
                if self.slots[self.staged][code] == value:
                    continue
                self.slots[self.staged][code] = value
                if self.staged != self.sent_slot:
                    self.sent_slot = self.staged
                    passed.append((EV_ABS, ABS_MT_SLOT, self.staged))
            passed.append((type, code, value))
        for reader in [self.grab] if self.grab else list(self.read):
            self.read[reader] += passed

    def touch(self, slot, id, tool):
        """A contact down and up again, as hid-multitouch reports it."""
        self.emit([(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, id), (EV_ABS, ABS_MT_TOOL_TYPE, tool)])
        self.emit([(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)])

    def palm(self, reader):
        """Whether the reader takes slot 0 for a palm: libinput keeps the
        last tool type it read for a slot."""
        slot, palm = 0, False
        for type, code, value in self.read[reader]:
            if (type, code) == (EV_ABS, ABS_MT_SLOT):
                slot = value
            elif (type, code) == (EV_ABS, ABS_MT_TOOL_TYPE) and slot == 0:
                palm = value == MT_TOOL_PALM
        return palm


class ToolResetTests(unittest.TestCase):
    def test_idle_slots_are_left_at_the_pen_in_order(self):
        self.assertEqual(
            tool_reset(TOOL, [7, -1, -1, 9, -1]),
            [
                (EV_ABS, ABS_MT_SLOT, 1),
                (EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN),
                (EV_ABS, ABS_MT_SLOT, 2),
                (EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN),
                (EV_ABS, ABS_MT_SLOT, 4),
                (EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN),
                (EV_SYN, SYN_REPORT, 0),
            ],
        )
        self.assertEqual(tool_reset(TOOL, [7, 8]), [])  # every slot has a contact
        self.assertEqual(tool_reset(None, [-1, -1]), [])  # no tool type at all
        self.assertEqual(tool_reset((0, 0, 0, 0, 0, 0), [-1]), [])  # fingers only: never a palm

    def scenario(self, reset):
        """Fn+F6's cycle from (2): a palm in slot 0 while libinput reads the
        touchpad, a finger there while the daemon holds the grab, then a
        finger after it lets go."""
        k = Kernel()
        k.touch(0, 1, MT_TOOL_PALM)  # (2): a palm on the touchpad while typing
        self.assertTrue(k.palm("libinput"))
        k.grab = "daemon"  # (3)
        k.touch(0, 2, MT_TOOL_FINGER)
        if reset:
            k.emit(tool_reset(TOOL, [s[ABS_MT_TRACKING_ID] for s in k.slots]))
        k.grab = None  # (1), then (2)
        k.touch(0, 3, MT_TOOL_FINGER)
        return k

    def test_without_the_reset_one_finger_is_a_palm_to_libinput(self):
        self.assertTrue(self.scenario(reset=False).palm("libinput"))

    def test_with_the_reset_the_next_finger_sends_its_tool(self):
        k = self.scenario(reset=True)
        self.assertFalse(k.palm("libinput"))
        # The reset itself went to the grabbing reader only.
        self.assertNotIn((EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN), k.read["libinput"])
        self.assertIn((EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN), k.read["daemon"])
        # A palm after it still reads as a palm.
        k.touch(0, 4, MT_TOOL_PALM)
        self.assertTrue(k.palm("libinput"))


class MonitorIdentityTests(unittest.TestCase):
    @staticmethod
    def edid(name=b"no-audio-HD", serial_text=None):
        e = bytearray(128)
        e[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
        e[8:10] = b"\x52\x62"  # TSB
        e[10:12] = b"\x88\x88"
        e[12:16] = b"\x00\x88\x88\x88"
        descriptors = []
        if name:
            descriptors.append(b"\0\0\0\xfc\0" + (name + b"\n").ljust(13, b" "))
        if serial_text:
            descriptors.append(b"\0\0\0\xff\0" + (serial_text + b"\n").ljust(13, b" "))
        for i, d in enumerate(descriptors):
            e[72 + 18 * i : 90 + 18 * i] = d
        return bytes(e)

    def test_like_mutter(self):
        # What mutter reports for the ScreenPad on this machine.
        self.assertEqual(edid_identity(self.edid()), ("TSB", "no-audio-HD", "0x88888800"))
        self.assertEqual(edid_identity(self.edid(name=None)), ("TSB", "0x8888", "0x88888800"))
        self.assertEqual(edid_identity(self.edid(b"ACME 27IN", b"SN0001234")), ("TSB", "ACME 27IN", "SN0001234"))
        self.assertIsNone(edid_identity(b"\0" * 128))
        self.assertIsNone(edid_identity(self.edid()[:100]))

    def test_saved_edid_or_builtin(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "edid.bin")
            self.assertEqual(monitor_identity(p), list(MONITOR_ID))
            with open(p, "wb") as f:
                f.write(self.edid(b"other"))
            self.assertEqual(monitor_identity(p), ["TSB", "other", "0x88888800"])


if __name__ == "__main__":
    unittest.main()
