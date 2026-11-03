"""Tests for the commands (shortcut, status), finding the touchpad, and the monitor identity.

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
from screenpad_touchbar.common import (
    MONITOR_ID,
    edid_identity,
    monitor_identity,
)
from screenpad_touchbar.config import load_config
from screenpad_touchbar.devices import find_source


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
            # Only an app: the keys are the file's.
            self.assertEqual(self.run_cli("remove", "2")[0], 2)
            self.assertEqual(self.run_cli("remove", "6")[0], 2)
            self.assertEqual(self.run_cli("remove", "7")[0], 2)
            self.assertEqual(self.run_cli("add", "bad name")[0], 2)
            self.assertEqual(self.run_cli("add", "c", "--spot", "7")[0], 2)
            self.assertEqual(len(load_config(path)[0].bar), 4)

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
