"""Tests for EDID checksums, the machine check, discovery, the commands and the
kernel command line, against a fake sysfs tree; no root and no hardware.

Run with: python3 -m unittest discover -s tests"""

import os
import tempfile
import unittest

from screenpad_support import CMDLINE, NVIDIA_PCI, PARAM, Base, tsb_edid

from screenpad_display.common import (
    ScreenpadError,
)
from screenpad_display.discovery import discover, short_pci, wait_for_target
from screenpad_display.edid import checksums_ok, describe_edid, fix_checksums
from screenpad_display.lockdown import (
    cmdline_edid_firmware,
    cmdline_params,
    firmware_for_connector,
)
from screenpad_display.system import Env


class ChecksumTests(unittest.TestCase):
    def test_known_ux580_case_single_byte(self):
        bad = tsb_edid(cta_checksum=0x3E)
        fixed, changes = fix_checksums(bad)
        self.assertEqual(changes, [(1, 0x3E, 0x6F)])
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(bad, fixed, strict=True)) if a != b]
        # cmp -l in the report prints this 1-based as offset 256.
        self.assertEqual(diff, [(255, 0x3E, 0x6F)])
        self.assertEqual(len(fixed), 256)

    def test_synthetic_wrong_cta_checksum(self):
        good = tsb_edid()
        for stored in (0x00, 0x6E, 0xFF):
            bad = good[:255] + bytes([stored])
            fixed, changes = fix_checksums(bad)
            self.assertEqual(fixed, good)
            self.assertEqual(changes, [(1, stored, 0x6F)])
            self.assertFalse(checksums_ok(bad))
            self.assertTrue(checksums_ok(fixed))

    def test_fixes_every_block(self):
        bad = bytearray(tsb_edid())
        bad[127] ^= 0x55
        bad[255] = 0x3E
        fixed, changes = fix_checksums(bytes(bad))
        self.assertEqual([c[0] for c in changes], [0, 1])
        self.assertEqual(fixed, tsb_edid())

    def test_valid_edid_untouched_and_trimmed(self):
        good = tsb_edid()
        fixed, changes = fix_checksums(good + b"\xff" * 128)
        self.assertEqual((fixed, changes), (good, []))

    def test_rejects_non_edid(self):
        with self.assertRaises(ScreenpadError):
            fix_checksums(b"\x00" * 256)
        with self.assertRaises(ScreenpadError):
            fix_checksums(tsb_edid()[: 128 + 64])

    def test_describe(self):
        desc = describe_edid(tsb_edid())
        self.assertIn("TSB 8888", desc)
        self.assertIn("1080x1920 @ 60.70 Hz, 140.00 MHz", desc)
        self.assertIn("HDMI VSDB", desc)
        self.assertNotIn("no HDMI VSDB", desc)
        self.assertIn("checksums ok", desc)


class DmiTests(Base):
    def test_refuses_other_machine_for_every_command(self):
        self.make(product="ZenBook UX425EA")
        for argv in (["on"], ["off"], ["replug"], ["light"], ["dark"], ["status"], ["capture-edid"]):
            code, _out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("unsupported machine", err)
        self.assertEqual(self.sys.read("sys/class/backlight/asus_screenpad/bl_power"), b"0")
        self.assertEqual(self.sys.read("sys/class/drm/card0-HDMI-A-2/status"), b"disconnected")
        self.assertEqual(self.reads, [])

    def test_refuses_missing_dmi(self):
        self.make()
        os.unlink(self.sys.p("sys/class/dmi/id/product_name"))
        self.assertEqual(self.run_cli("status")[0], 2)

    def test_non_root_refused_without_writes(self):
        self.make(euid=1000)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 1)
        self.assertIn("must run as root", err)
        self.assertEqual(self.sys.read("sys/class/drm/card0-HDMI-A-2/status"), b"disconnected")


class DiscoveryTests(Base):
    def test_finds_nvidia_card_connector_buses_debugfs(self):
        self.make()
        t = discover(self.env)
        self.assertEqual(
            (t.card, t.pci, t.connector, t.short_connector), ("card0", NVIDIA_PCI, "card0-HDMI-A-2", "HDMI-A-2")
        )
        self.assertEqual(t.buses, [9, 10])
        self.assertEqual(t.debugfs, self.sys.debugfs)

    def test_card_numbers_swapped(self):
        self.make(nvidia_card="card1", intel_card="card0")
        t = discover(self.env)
        self.assertEqual((t.card, t.connector), ("card1", "card1-HDMI-A-2"))
        self.assertEqual(t.debugfs, self.sys.debugfs)

    def test_debugfs_named_by_minor(self):
        self.make(nvidia_card="card1", intel_card="card0", debugfs_by_minor=True)
        t = discover(self.env)
        self.assertEqual(t.debugfs, self.sys.p("sys/kernel/debug/dri/1/HDMI-A-2"))

    def test_debugfs_unreadable(self):
        self.make()
        os.rename(self.sys.p("sys/kernel/debug"), self.sys.p("sys/kernel/debug-hidden"))
        t = discover(self.env)
        self.assertIsNone(t.debugfs)
        with self.assertRaisesRegex(ScreenpadError, "debugfs"):
            wait_for_target(self.env, 2)
        self.assertGreaterEqual(self.time.now, 2)

    def test_i2c_by_name_when_not_parented(self):
        self.make()
        node = self.sys.p("sys/devices/virtual/i2c-11")
        os.makedirs(node)
        with open(os.path.join(node, "name"), "w") as f:
            f.write("NVIDIA i2c adapter 3 at 2:00.0")
        os.symlink(node, self.sys.p("sys/bus/i2c/devices/i2c-11"))
        self.assertEqual(discover(self.env).buses, [9, 10, 11])

    def test_short_pci(self):
        self.assertEqual(short_pci("0000:02:00.0"), "2:00.0")
        self.assertEqual(short_pci("0000:3b:00.1"), "3b:00.1")

    def test_several_connectors_need_choice(self):
        self.make(connectors=("HDMI-A-2", "HDMI-A-4"))
        with self.assertRaisesRegex(ScreenpadError, "--connector"):
            discover(self.env)
        t = discover(self.env, "HDMI-A-4")
        self.assertEqual(t.connector, "card0-HDMI-A-4")

    def test_no_nvidia(self):
        self.make()
        os.unlink(self.sys.p("sys/class/drm/card0"))
        self.assertIsNone(discover(self.env))
        with self.assertRaisesRegex(ScreenpadError, "nvidia-drm"):
            wait_for_target(self.env, 1)


class CommandTests(Base):
    def test_on_captures_saves_overrides_and_forces(self):
        s = self.make(edid_by_bus={10: tsb_edid(cta_checksum=0x3E)})
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.reads[:2], [s.p("dev/i2c-9"), s.p("dev/i2c-10")])
        self.assertIn("block 1 checksum 0x3e fixed to 0x6f", err)
        self.assertEqual(s.read("var/lib/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), tsb_edid())
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"on")
        self.assertEqual(s.read("sys/class/backlight/asus_screenpad/bl_power"), b"4")
        self.assertTrue(self.wmi.lit)
        # Lit for the DDC read, then lit again once the connector is enabled.
        self.assertTrue(all(self.lit_at_read))
        self.assertEqual(self.wmi.calls[-1], ("POWER=0", True))
        self.assertTrue(os.path.exists(s.p("run/screenpad/active")))

    def test_on_uses_saved_edid_without_ddc(self):
        s = self.make(edid_by_bus={})
        os.makedirs(s.p("var/lib/screenpad"))
        with open(s.p("var/lib/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.reads, [])
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), tsb_edid())

    def test_on_fails_without_edid(self):
        s = self.make(edid_by_bus={})
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 1)
        self.assertIn("could not read an EDID", err)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"disconnected")
        self.assertFalse(os.path.exists(s.p("var/lib/screenpad/edid.bin")))

    def test_off_is_the_rollback(self):
        s = self.make()
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.run_cli("off")[0], 0)
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), b"reset")
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"detect")
        self.assertEqual(s.read("sys/class/backlight/asus_screenpad/bl_power"), b"0")
        self.assertFalse(self.wmi.lit)
        self.assertFalse(os.path.exists(s.p("run/screenpad/active")))
        self.assertTrue(os.path.exists(s.p("var/lib/screenpad/edid.bin")))

    def test_replug_if_active(self):
        s = self.make()
        self.assertEqual(self.run_cli("replug", "--if-active")[0], 0)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"disconnected")
        self.assertEqual(self.run_cli("on")[0], 0)
        s.write("sys/class/drm/card0-HDMI-A-2/status", "disconnected")
        self.assertEqual(self.run_cli("replug", "--if-active")[0], 0)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"on")

    def test_capture_from_file(self):
        s = self.make(edid_by_bus={})
        src = s.p("ux580-tsb-original.bin")
        with open(src, "wb") as f:
            f.write(tsb_edid(cta_checksum=0x3E))
        code, _, err = self.run_cli("capture-edid", "--from", src)
        self.assertEqual(code, 0, err)
        self.assertEqual(s.read("var/lib/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(self.reads, [])

    def test_status_unprivileged(self):
        self.make(euid=1000)
        os.rename(self.sys.p("sys/kernel/debug"), self.sys.p("sys/kernel/debug-hidden"))
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("card0-HDMI-A-2 status=disconnected", out)
        self.assertIn("ddc buses:  i2c-9 i2c-10", out)
        self.assertIn("not readable", out)
        self.assertIn("saved EDID: none", out)


class CmdlineTests(unittest.TestCase):
    def test_split_like_the_kernel(self):
        text = 'BOOT_IMAGE=/boot/vmlinuz ro quiet "%s" foo="a b" splash= -- drm.edid_firmware=x\n' % PARAM
        self.assertEqual(
            cmdline_params(text),
            [
                ("BOOT_IMAGE", "/boot/vmlinuz"),
                ("ro", None),
                ("quiet", None),
                ("drm.edid_firmware", "HDMI-A-2:screenpad/edid.bin"),
                ("foo", "a b"),
                ("splash", ""),
            ],
        )

    def edid_firmware(self, cmdline):
        env = Env(root=self.tmp)
        with open(os.path.join(self.tmp, "proc/cmdline"), "w") as f:
            f.write(cmdline + "\n")
        return cmdline_edid_firmware(env)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        os.makedirs(os.path.join(self.tmp, "proc"))

    def test_edid_firmware_from_cmdline(self):
        self.assertIsNone(self.edid_firmware(CMDLINE))
        self.assertEqual(self.edid_firmware(CMDLINE + " " + PARAM), "HDMI-A-2:screenpad/edid.bin")
        # - and _ are the same in parameter names, and the last one wins.
        self.assertEqual(self.edid_firmware("drm.edid-firmware=a.bin quiet drm.edid_firmware=b.bin"), "b.bin")
        self.assertIsNone(self.edid_firmware("quiet -- " + PARAM))
        self.assertIsNone(self.edid_firmware("drm_kms_helper.edid_firmware=old.bin"))

    def test_firmware_for_connector_mirrors_drm_edid_load(self):
        pick = firmware_for_connector
        self.assertEqual(pick("HDMI-A-2:screenpad/edid.bin", "HDMI-A-2"), "screenpad/edid.bin")
        self.assertIsNone(pick("HDMI-A-2:screenpad/edid.bin", "HDMI-A-1"))
        self.assertIsNone(pick("HDMI-A-2:screenpad/edid.bin", "eDP-1"))
        # The kernel compares against the bare connector name only.
        self.assertIsNone(pick("card0-HDMI-A-2:screenpad/edid.bin", "HDMI-A-2"))
        # strncmp over the given name: a prefix matches.
        self.assertEqual(pick("HDMI-A:x.bin", "HDMI-A-2"), "x.bin")
        self.assertEqual(pick("HDMI-A-1:a.bin,HDMI-A-2:b.bin,HDMI-A-2:c.bin", "HDMI-A-2"), "b.bin")
        # A name-less entry is the fallback for every connector; the last one wins.
        self.assertEqual(pick("a.bin,eDP-1:e.bin,b.bin", "HDMI-A-2"), "b.bin")
        self.assertEqual(pick("a.bin,eDP-1:e.bin,b.bin", "eDP-1"), "e.bin")
        self.assertEqual(pick("HDMI-A-2:screenpad/edid.bin\n", "HDMI-A-2"), "screenpad/edid.bin")
        for empty in (None, "", ","):
            self.assertIsNone(pick(empty, "HDMI-A-2"))


if __name__ == "__main__":
    unittest.main()
