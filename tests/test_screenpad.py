"""Tests against a fake sysfs tree; no root and no hardware needed.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)

from screenpad_display import system
from screenpad_display.cli import main
from screenpad_display.common import (
    BACKLIGHT,
    DDC_RETRY_DELAY,
    DRM_CLASS,
    EDID_HEADER,
    EDID_READ_TRIES,
    POWER_SETTLE,
    RELIGHT_GAP,
    ScreenpadError,
)
from screenpad_display.discovery import discover, short_pci, wait_for_target
from screenpad_display.edid import block_checksum, checksums_ok, describe_edid, fix_checksums
from screenpad_display.grub import grub_add, grub_remove
from screenpad_display.lockdown import (
    cmdline_edid_firmware,
    cmdline_params,
    firmware_for_connector,
    lockdown_state,
    locked_down,
)
from screenpad_display.system import Env, write_attr

NVIDIA_PCI = "0000:02:00.0"
INTEL_PCI = "0000:00:02.0"
PARAM = "drm.edid_firmware=HDMI-A-2:screenpad/edid.bin"
CMDLINE = (
    "BOOT_IMAGE=/boot/vmlinuz-7.0.0-38-generic root=UUID=b4dea716 ro quiet splash crashkernel=2G-4G:320M,4G-32G:512M"
)
UBUNTU_GRUB = """# If you change this file, run 'update-grub' afterwards to update
# /boot/grub/grub.cfg.

GRUB_DEFAULT=0
GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`( . /etc/os-release; echo ${NAME:-Ubuntu} ) 2>/dev/null || echo Ubuntu`
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""

# Uncomment to disable graphical terminal
#GRUB_TERMINAL=console
"""


def repo_file(rel):
    with open(os.path.join(REPO, rel), "rb") as f:
        return f.read()


def tsb_edid(cta_checksum=None):
    """A two-block EDID shaped like the UX580 Toshiba bridge dump.

    Block 0 is valid. Block 1 is a CTA block with the HDMI VSDB, padded so its
    correct checksum is 0x6f; cta_checksum overrides the stored byte.
    """
    b0 = bytearray(128)
    b0[0:8] = EDID_HEADER
    b0[8:10] = b"\x52\x62"  # "TSB"
    b0[10:12] = b"\x88\x88"  # product 0x8888
    b0[18:20] = b"\x01\x03"  # EDID 1.3
    b0[20] = 0x80  # digital input
    # 140.00 MHz, 1080x1920, blanking 115/10, flags 0x18 (-H -V).
    b0[54:72] = bytes([0xB0, 0x36, 0x38, 0x73, 0x40, 0x80, 0x0A, 0x70, 0x10, 0x10, 0x11, 0x00, 0, 0, 0, 0, 0, 0x18])
    b0[126] = 1
    b0[127] = block_checksum(b0)

    b1 = bytearray(128)
    b1[0:4] = b"\x02\x03\x0b\x00"
    b1[4:11] = b"\x66\x03\x0c\x00\x10\x00\x00"  # vendor block, HDMI OUI 00-0C-03
    b1[100] = (0x91 - sum(b1[:127])) & 0xFF  # makes the correct checksum 0x6f
    assert block_checksum(b1) == 0x6F
    b1[127] = 0x6F if cta_checksum is None else cta_checksum
    return bytes(b0 + b1)


class FakeSys:
    """A minimal sysfs/debugfs/dev tree under a temporary root."""

    def __init__(
        self,
        root,
        nvidia_card="card0",
        intel_card="card1",
        product="ZenBook UX450FDX_UX480FD",
        debugfs_by_minor=False,
        connectors=("HDMI-A-2",),
        lockdown=None,
        cmdline=CMDLINE,
    ):
        self.root = root
        if lockdown:
            modes = ("none", "integrity", "confidentiality")
            self.write(
                "sys/kernel/security/lockdown", " ".join("[%s]" % m if m == lockdown else m for m in modes) + "\n"
            )
        self.write("proc/cmdline", cmdline + "\n")
        self.write("sys/module/drm/parameters/edid_firmware", "\n")
        self.write("etc/default/grub", UBUNTU_GRUB)
        self.write("sys/class/dmi/id/sys_vendor", "ASUSTeK COMPUTER INC.")
        self.write("sys/class/dmi/id/product_name", product)
        self.write("sys/class/dmi/id/board_name", "UX450FDX")

        bl = "sys/devices/platform/asus-nb-wmi/backlight/asus_screenpad"
        for name, value in (("bl_power", "0"), ("brightness", "255"), ("max_brightness", "255")):
            self.write("%s/%s" % (bl, name), value)
        self.link("sys/class/backlight/asus_screenpad", bl)

        os.makedirs(self.p("sys/bus/pci/drivers/nvidia"))
        os.makedirs(self.p("sys/bus/pci/drivers/i915"))

        nv = "sys/devices/pci0000:00/0000:00:1c.4/" + NVIDIA_PCI
        intel = "sys/devices/pci0000:00/" + INTEL_PCI
        self.gpu(nv, "0x10de", "nvidia", nvidia_card, connectors)
        self.gpu(intel, "0x8086", "i915", intel_card, ("eDP-1", "HDMI-A-1"))
        self.nvidia_card = nvidia_card

        self.adapter(nv, 9, "NVIDIA i2c adapter 1 at 2:00.0")
        self.adapter(nv, 10, "NVIDIA i2c adapter 6 at 2:00.0")
        self.adapter(intel, 5, "i915 gmbus dpc")
        self.adapter(intel, 8, "AUX A/DDI A/PHY A")
        self.adapter("sys/devices/pci0000:00/0000:00:15.1", 1, "Synopsys DesignWare I2C adapter")

        nv_dir = nvidia_card[len("card") :] if debugfs_by_minor else NVIDIA_PCI
        intel_dir = intel_card[len("card") :] if debugfs_by_minor else INTEL_PCI
        dri = "sys/kernel/debug/dri"
        self.write("%s/%s/name" % (dri, nv_dir), "nvidia-drm dev=%s unique=%s" % (NVIDIA_PCI, NVIDIA_PCI))
        self.write("%s/%s/name" % (dri, intel_dir), "i915 dev=%s unique=%s" % (INTEL_PCI, INTEL_PCI))
        for conn in connectors:
            self.write("%s/%s/%s/edid_override" % (dri, nv_dir, conn), "")
        self.write("%s/%s/eDP-1/edid_override" % (dri, intel_dir), "")
        self.debugfs = self.p(dri, nv_dir, connectors[0])

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
        with open(self.p(rel), "w") as f:
            f.write(text)

    def link(self, rel, target):
        os.makedirs(os.path.dirname(self.p(rel)), exist_ok=True)
        os.symlink(self.p(target), self.p(rel))

    def gpu(self, dev, vendor, driver, card, connectors):
        self.write(dev + "/vendor", vendor)
        self.link(dev + "/driver", "sys/bus/pci/drivers/" + driver)
        self.link("%s/drm/%s/device" % (dev, card), dev)
        self.link("sys/class/drm/" + card, "%s/drm/%s" % (dev, card))
        for conn in connectors:
            node = "%s/drm/%s/%s-%s" % (dev, card, card, conn)
            self.write(node + "/status", "disconnected")
            self.write(node + "/enabled", "disabled")
            self.write(node + "/modes", "")
            self.write(node + "/edid", "")
            self.link("sys/class/drm/%s-%s" % (card, conn), node)

    def adapter(self, dev, n, name):
        node = "%s/i2c-%d" % (dev, n)
        self.write(node + "/name", name)
        self.link("sys/bus/i2c/devices/i2c-%d" % n, node)

    def read(self, rel):
        with open(self.p(rel), "rb") as f:
            return f.read()


class FakeAsusWmi:
    """asus-wmi's screenpad backlight and the UX480FD firmware behind it.

    The backlight core calls update_status on every brightness write and on a
    bl_power write that changes the value. update_status compares the new
    bl_power with the power the firmware reports (drivers/platform/x86/
    asus-wmi.c, update_screenpad_bl_status). On this unit SCREENPAD_POWER=0
    lights the panel and makes the firmware report unpowered; any
    SCREENPAD_LIGHT darkens it and makes the firmware report powered.

    It also stands in for a compositor: forcing the connector on enables it
    after compositor_delay seconds (never if None), and off/detect disable it.
    """

    def __init__(self, sys, clock, powered=True, compositor_delay=0):
        self.sys = sys
        self.clock = clock
        self.powered = powered  # what DSTS SCREENPAD_POWER reads back
        self.lit = not powered
        self.calls = []  # ("LIGHT" | "POWER=0", connector enabled?)
        self.compositor_delay = compositor_delay
        self.enable_at = None
        self.bl = sys.p(BACKLIGHT)
        self.conn = sys.p(DRM_CLASS, "card0-HDMI-A-2")
        self.real_write_attr = write_attr

    def enabled(self):
        return self.sys.read(os.path.join(self.conn, "enabled")) == b"enabled"

    def tick(self):
        if self.enable_at is not None and self.clock.now >= self.enable_at:
            self.sys.write(os.path.join(self.conn, "enabled"), "enabled")
            self.enable_at = None

    def update_status(self):
        bl_power = int(self.sys.read(os.path.join(self.bl, "bl_power")))
        reported = 0 if self.powered else 4
        if bl_power != reported:
            call = "LIGHT" if reported != 0 else "POWER=0"
        elif reported == 0:
            call = "LIGHT"
        else:
            return
        self.calls.append((call, self.enabled()))
        self.lit = call == "POWER=0"
        self.powered = not self.lit

    def write_attr(self, path, data):
        text = data.decode("latin-1") if isinstance(data, bytes) else data
        if path == os.path.join(self.bl, "bl_power"):
            changed = self.sys.read(os.path.join(self.bl, "bl_power")).decode() != text
            self.real_write_attr(path, data)
            if changed:
                self.update_status()
            return
        self.real_write_attr(path, data)
        if path == os.path.join(self.bl, "brightness"):
            self.update_status()
        elif path == os.path.join(self.conn, "status"):
            if text == "on":
                if self.compositor_delay is not None:
                    self.enable_at = self.clock.now + self.compositor_delay
                    self.tick()
            else:
                self.enable_at = None
                self.sys.write(os.path.join(self.conn, "enabled"), "disabled")


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reads = []

    def make(self, euid=0, edid_by_bus=None, powered=True, compositor_delay=0, **kw):
        self.sys = FakeSys(self.tmp.name, **kw)
        self.time = FakeClock()
        self.wmi = FakeAsusWmi(self.sys, self.time, powered, compositor_delay)
        patch = mock.patch.object(system, "write_attr", self.wmi.write_attr)
        patch.start()
        self.addCleanup(patch.stop)
        edid_by_bus = edid_by_bus if edid_by_bus is not None else {9: tsb_edid(cta_checksum=0x3E)}
        self.lit_at_read = []

        def sleep(seconds):
            self.time.sleep(seconds)
            self.wmi.tick()

        def i2c_read(dev):
            self.reads.append(dev)
            self.lit_at_read.append(self.wmi.lit)
            bus = int(dev.rsplit("-", 1)[1])
            if bus not in edid_by_bus:
                raise OSError(6, "No such device or address")
            return edid_by_bus[bus]

        self.ran = []
        self.env = Env(
            root=self.tmp.name,
            sleep=sleep,
            euid=lambda: euid,
            i2c_read=i2c_read,
            clock=self.time.clock,
            run=self.ran.append,
        )
        return self.sys

    def run_cli(self, *argv):
        err = io.StringIO()
        out = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            code = main(list(argv), self.env)
        return code, out.getvalue(), err.getvalue()


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
        for argv in (["on"], ["off"], ["replug"], ["status"], ["capture-edid"]):
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


class LockdownTests(Base):
    def lit(self, s):
        """Let the fake kernel report the EDID that drm.edid_firmware provides."""
        s.write("sys/class/drm/card0-HDMI-A-2/edid", "")
        with open(s.p("sys/class/drm/card0-HDMI-A-2/edid"), "wb") as f:
            f.write(tsb_edid())

    def saved(self, s):
        os.makedirs(s.p("var/lib/screenpad"))
        with open(s.p("var/lib/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())

    def runtime(self, s):
        return s.read("sys/module/drm/parameters/edid_firmware").decode()

    def test_lockdown_state(self):
        for mode, locked in (("none", False), ("integrity", True), ("confidentiality", True)):
            self.tmp.cleanup()
            os.makedirs(self.tmp.name)
            self.make(lockdown=mode)
            self.assertEqual(lockdown_state(self.env), mode)
            self.assertEqual(locked_down(self.env), locked)
        os.unlink(self.sys.p("sys/kernel/security/lockdown"))
        self.assertIsNone(lockdown_state(self.env))
        self.assertFalse(locked_down(self.env))

    def test_on_skips_debugfs_and_sets_edid_firmware(self):
        s = self.make(lockdown="integrity")
        self.saved(s)
        self.lit(s)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 0, err)
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), b"")
        self.assertEqual(s.read("lib/firmware/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(self.runtime(s), "HDMI-A-2:screenpad/edid.bin")
        self.assertIn("not on the kernel command line yet", err)
        self.assertIn("grub apply", err)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"on")
        self.assertEqual(s.read("sys/class/backlight/asus_screenpad/bl_power"), b"4")
        self.assertTrue(self.wmi.lit)
        self.assertTrue(os.path.exists(s.p("run/screenpad/active")))
        self.assertEqual(self.reads, [])

    def test_on_does_not_wait_for_debugfs(self):
        s = self.make(lockdown="integrity")
        self.saved(s)
        self.lit(s)
        shutil.rmtree(s.p("sys/kernel/debug"))
        self.assertEqual(self.run_cli("on", "--wait", "5")[0], 0)
        self.assertEqual(self.time.sleeps, [RELIGHT_GAP])

    def test_on_with_boot_param_applied(self):
        s = self.make(lockdown="integrity", cmdline=CMDLINE + " " + PARAM)
        s.write("sys/module/drm/parameters/edid_firmware", "HDMI-A-2:screenpad/edid.bin\n")
        self.saved(s)
        self.lit(s)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 0, err)
        self.assertNotIn("for this boot", err)
        self.assertEqual(self.runtime(s), "HDMI-A-2:screenpad/edid.bin\n")

    def test_on_keeps_other_edid_firmware_entries(self):
        s = self.make(lockdown="integrity")
        s.write("sys/module/drm/parameters/edid_firmware", "DP-1:other.bin,HDMI-A:foo.bin\n")
        self.saved(s)
        self.lit(s)
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.runtime(s), "HDMI-A-2:screenpad/edid.bin,DP-1:other.bin,HDMI-A:foo.bin")

    def test_on_drops_our_file_for_another_connector(self):
        # Boot parameter written while nvidia-drm was HDMI-A-1; this boot it is HDMI-A-2.
        s = self.make(lockdown="integrity", cmdline=CMDLINE + " drm.edid_firmware=HDMI-A-1:screenpad/edid.bin")
        s.write("sys/module/drm/parameters/edid_firmware", "HDMI-A-1:screenpad/edid.bin,DP-1:x.bin\n")
        self.saved(s)
        self.lit(s)
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.runtime(s), "HDMI-A-2:screenpad/edid.bin,DP-1:x.bin")

    def test_on_without_connector_edid_says_why(self):
        s = self.make(lockdown="integrity")
        self.saved(s)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 1)
        self.assertIn("card0-HDMI-A-2 has no EDID", err)
        self.assertIn("boot parameter is not applied yet, only the runtime value", err)
        self.assertIn("screenpad grub apply", err)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"detect")
        self.assertFalse(os.path.exists(s.p("run/screenpad/active")))

    def test_on_without_edid_with_boot_param_blames_nvidia(self):
        s = self.make(lockdown="integrity", cmdline=CMDLINE + " " + PARAM)
        s.write("sys/module/drm/parameters/edid_firmware", "HDMI-A-2:screenpad/edid.bin\n")
        self.saved(s)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 1)
        self.assertIn("The boot parameter is applied, so nvidia-drm did not take the EDID", err)

    def test_on_captures_over_ddc_when_nothing_saved(self):
        s = self.make(lockdown="integrity")
        self.lit(s)
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(s.read("var/lib/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(s.read("lib/firmware/screenpad/edid.bin"), tsb_edid())

    def test_off_removes_only_our_entry(self):
        s = self.make(lockdown="integrity")
        self.saved(s)
        self.lit(s)
        self.assertEqual(self.run_cli("on")[0], 0)
        code, _, err = self.run_cli("off")
        self.assertEqual(code, 0, err)
        # A sysfs write of "" is ignored, so "," stands for empty.
        self.assertEqual(self.runtime(s), ",")
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), b"")
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"detect")
        self.assertFalse(self.wmi.lit)
        self.assertFalse(os.path.exists(s.p("run/screenpad/active")))

        s.write("sys/module/drm/parameters/edid_firmware", "DP-1:x.bin,HDMI-A-2:screenpad/edid.bin\n")
        self.assertEqual(self.run_cli("off")[0], 0)
        self.assertEqual(self.runtime(s), "DP-1:x.bin")

    def test_replug(self):
        s = self.make(lockdown="integrity")
        self.saved(s)
        self.lit(s)
        self.assertEqual(self.run_cli("on")[0], 0)
        s.write("sys/class/drm/card0-HDMI-A-2/status", "disconnected")
        self.assertEqual(self.run_cli("replug", "--if-active")[0], 0)
        self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"on")
        self.assertEqual(s.read("sys/kernel/debug/dri/%s/HDMI-A-2/edid_override" % NVIDIA_PCI), b"")

    def test_capture_edid_writes_firmware(self):
        s = self.make(lockdown="integrity")
        shutil.rmtree(s.p("sys/kernel/debug"))
        code, _, err = self.run_cli("capture-edid")
        self.assertEqual(code, 0, err)
        self.assertEqual(s.read("lib/firmware/screenpad/edid.bin"), tsb_edid())
        self.assertTrue(self.lit_at_read and all(self.lit_at_read))

    def test_status(self):
        s = self.make(euid=1000, lockdown="integrity", cmdline=CMDLINE + " " + PARAM)
        s.write("sys/module/drm/parameters/edid_firmware", "HDMI-A-2:screenpad/edid.bin\n")
        self.saved(s)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("lockdown:   integrity; using drm.edid_firmware", out)
        self.assertIn("boot param: %s on the kernel command line" % PARAM, out)
        self.assertIn("HDMI-A-2 gets screenpad/edid.bin", out)
        self.assertIn("firmware:   %s missing" % s.p("lib/firmware/screenpad/edid.bin"), out)

        os.makedirs(s.p("lib/firmware/screenpad"))
        with open(s.p("lib/firmware/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())
        s.write("proc/cmdline", CMDLINE + " drm.edid_firmware=HDMI-A-1:screenpad/edid.bin\n")
        out = self.run_cli("status")[1]
        self.assertIn("TSB 8888", out.split("firmware:")[1])
        self.assertIn("same as saved EDID", out)
        self.assertIn("(but it does not give HDMI-A-2 screenpad/edid.bin)", out)

    def test_status_unlocked(self):
        self.make(lockdown="none")
        out = self.run_cli("status")[1]
        self.assertIn("lockdown:   none; using debugfs edid_override", out)
        self.assertIn("boot param: none on the kernel command line", out)
        self.assertIn("runtime:    edid_firmware=(empty)", out)


class PanelTests(Base):
    """on/replug end lit and off ends dark, whatever state the panel was in."""

    # (bl_power, what the firmware reports for SCREENPAD_POWER)
    STATES = tuple((bl_power, powered) for bl_power in ("0", "4") for powered in (False, True))

    def start(self, bl_power, powered, lockdown="integrity", **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        s = self.make(lockdown=lockdown, powered=powered, **kw)
        os.makedirs(s.p("var/lib/screenpad"))
        with open(s.p("var/lib/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())
        with open(s.p("sys/class/drm/card0-HDMI-A-2/edid"), "wb") as f:
            f.write(tsb_edid())
        s.write("sys/class/backlight/asus_screenpad/bl_power", bl_power)
        return s

    def assert_lit(self):
        self.assertTrue(self.wmi.lit)
        self.assertFalse(self.wmi.powered)
        self.assertEqual(self.sys.read("sys/class/backlight/asus_screenpad/bl_power"), b"4")
        # LIGHT, then POWER=0 while a compositor drives the output.
        self.assertEqual([c for c, _ in self.wmi.calls[-2:]], ["LIGHT", "POWER=0"])
        self.assertEqual(self.wmi.calls[-1], ("POWER=0", True))

    def test_on_ends_lit_from_every_state(self):
        for lockdown in ("integrity", None):
            for state in self.STATES:
                with self.subTest(state=state, lockdown=lockdown):
                    self.start(*state, lockdown=lockdown)
                    code, _, err = self.run_cli("on")
                    self.assertEqual(code, 0, err)
                    self.assert_lit()

    def test_replug_ends_lit_from_every_state(self):
        for lockdown in ("integrity", None):
            for state in self.STATES:
                with self.subTest(state=state, lockdown=lockdown):
                    s = self.start(*state, lockdown=lockdown)
                    s.write("run/screenpad/active", "")
                    code, _, err = self.run_cli("replug", "--if-active")
                    self.assertEqual(code, 0, err)
                    self.assert_lit()
                    self.assertEqual(s.read("sys/class/drm/card0-HDMI-A-2/status"), b"on")

    def test_off_ends_dark_from_every_state(self):
        for state in self.STATES:
            with self.subTest(state=state):
                self.start(*state)
                code, _, err = self.run_cli("off")
                self.assertEqual(code, 0, err)
                self.assertFalse(self.wmi.lit)
                self.assertTrue(self.wmi.powered)
                self.assertEqual(self.wmi.calls[-1][0], "LIGHT")

    def test_lit_on_replug_after_on(self):
        self.start("0", True)
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assertEqual(self.run_cli("replug", "--if-active")[0], 0)
        self.assert_lit()
        self.assertEqual(self.run_cli("off")[0], 0)
        self.assertFalse(self.wmi.lit)
        self.assertEqual(self.run_cli("on")[0], 0)
        self.assert_lit()

    def test_on_waits_for_the_compositor(self):
        self.start("0", True, compositor_delay=3)
        code, _, err = self.run_cli("on")
        self.assertEqual(code, 0, err)
        self.assert_lit()
        self.assertGreaterEqual(self.time.now, 3 + RELIGHT_GAP)
        self.assertNotIn("not enabled", err)

    def test_on_lights_anyway_without_a_compositor(self):
        self.start("0", True, compositor_delay=None)
        code, _, err = self.run_cli("on", "--wait", "4")
        self.assertEqual(code, 0, err)
        self.assertIn("card0-HDMI-A-2 is still not enabled by a compositor", err)
        self.assertTrue(self.wmi.lit)
        self.assertEqual(self.wmi.calls[-1], ("POWER=0", False))
        # One deadline for the card and the compositor, then the relight gap.
        self.assertEqual(self.time.now, 4 + RELIGHT_GAP)

    def test_relight_gap_option(self):
        self.start("4", False)
        self.assertEqual(self.run_cli("on", "--relight-gap", "2.5")[0], 0)
        self.assertEqual(self.time.sleeps, [2.5])
        self.assert_lit()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cli("on", "--relight-gap", "-1")


class DdcRetryTests(Base):
    def test_retries_until_the_bridge_answers(self):
        s = self.make(edid_by_bus={})
        answers = [OSError(5, "Input/output error")] * 6 + [tsb_edid(cta_checksum=0x3E)]

        def flaky(dev):
            self.reads.append(dev)
            if dev.endswith("i2c-10"):
                raise OSError(5, "Input/output error")
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.env.i2c_read = flaky
        code, _, err = self.run_cli("capture-edid")
        self.assertEqual(code, 0, err)
        self.assertIn("retrying", err)
        self.assertEqual(s.read("var/lib/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(self.time.sleeps, [RELIGHT_GAP, POWER_SETTLE] + [DDC_RETRY_DELAY] * 6)

    def test_gives_up_with_the_file_hint(self):
        self.make(edid_by_bus={})
        self.env.i2c_read = lambda dev: (_ for _ in ()).throw(OSError(5, "Input/output error"))
        code, _, err = self.run_cli("capture-edid")
        self.assertEqual(code, 1)
        self.assertIn("after %d attempts" % EDID_READ_TRIES, err)
        self.assertIn("--from ux580-tsb-fixed.bin", err)


class GrubTests(Base):
    def firmware(self, s):
        os.makedirs(s.p("lib/firmware/screenpad"))
        with open(s.p("lib/firmware/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())

    def grub(self, s):
        return s.read("etc/default/grub").decode()

    def test_show_needs_no_root_and_writes_nothing(self):
        s = self.make(euid=1000)
        code, out, err = self.run_cli("grub")
        self.assertEqual(code, 0, err)
        self.assertIn('now:           GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"', out)
        self.assertIn('after apply:   GRUB_CMDLINE_LINUX_DEFAULT="quiet splash %s"' % PARAM, out)
        self.assertEqual(self.grub(s), UBUNTU_GRUB)
        self.assertEqual(self.ran, [])

    def test_apply_then_remove_is_exact(self):
        s = self.make()
        self.firmware(s)
        code, _, err = self.run_cli("grub", "apply")
        self.assertEqual(code, 0, err)
        want = UBUNTU_GRUB.replace('"quiet splash"', '"quiet splash %s"' % PARAM)
        self.assertEqual(self.grub(s), want)
        self.assertEqual(s.read("etc/default/grub.screenpad-backup").decode(), UBUNTU_GRUB)
        self.assertEqual(self.ran, [["update-grub"]])

        self.assertEqual(self.run_cli("grub", "apply")[0], 0)
        self.assertEqual(self.grub(s), want)
        self.assertEqual(self.ran, [["update-grub"]])

        out = self.run_cli("grub", "show")[1]
        self.assertIn('after remove:  GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"', out)

        self.assertEqual(self.run_cli("grub", "remove")[0], 0)
        self.assertEqual(self.grub(s), UBUNTU_GRUB)
        self.assertEqual(self.ran, [["update-grub"], ["update-grub"]])
        self.assertEqual(self.run_cli("grub", "remove")[0], 0)
        self.assertEqual(len(self.ran), 2)

    def test_add_and_remove_edge_cases(self):
        add, remove = grub_add, grub_remove
        self.assertEqual(add('GRUB_CMDLINE_LINUX_DEFAULT=""\n', PARAM), 'GRUB_CMDLINE_LINUX_DEFAULT="%s"\n' % PARAM)
        self.assertEqual(
            add("GRUB_CMDLINE_LINUX_DEFAULT='quiet'", PARAM), "GRUB_CMDLINE_LINUX_DEFAULT='quiet %s'" % PARAM
        )
        self.assertEqual(add("GRUB_TIMEOUT=0", PARAM), 'GRUB_TIMEOUT=0\nGRUB_CMDLINE_LINUX_DEFAULT="%s"\n' % PARAM)
        # The last assignment is the one the shell keeps.
        two = 'GRUB_CMDLINE_LINUX_DEFAULT="a"\n#GRUB_CMDLINE_LINUX_DEFAULT="x"\nGRUB_CMDLINE_LINUX_DEFAULT="b"\n'
        self.assertEqual(add(two, PARAM), two.replace('"b"', '"b %s"' % PARAM))
        self.assertEqual(
            remove('GRUB_CMDLINE_LINUX_DEFAULT="%s quiet"\n' % PARAM), 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
        )
        # Ours under an older connector name goes too; anything else stays.
        self.assertEqual(
            remove('GRUB_CMDLINE_LINUX_DEFAULT="quiet drm.edid_firmware=HDMI-A-1:screenpad/edid.bin"'),
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet"',
        )
        for other in (
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet %sx"\n' % PARAM,
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet drm.edid_firmware=HDMI-A-2:edid/x.bin"\n',
        ):
            self.assertEqual(remove(other), other)
        with self.assertRaisesRegex(ScreenpadError, "already sets"):
            add('GRUB_CMDLINE_LINUX_DEFAULT="quiet drm.edid_firmware=edid/x.bin"\n', PARAM)

    def test_apply_needs_root_and_the_firmware(self):
        s = self.make(euid=1000)
        self.firmware(s)
        self.assertEqual(self.run_cli("grub", "apply")[0], 1)
        self.assertEqual(self.grub(s), UBUNTU_GRUB)
        self.tmp.cleanup()
        os.makedirs(self.tmp.name)
        s = self.make()
        code, _, err = self.run_cli("grub", "apply")
        self.assertEqual(code, 1)
        self.assertIn("lib/firmware/screenpad/edid.bin missing", err)
        self.assertEqual(self.grub(s), UBUNTU_GRUB)
        self.assertEqual(self.ran, [])

    def test_connector_option_and_override_warning(self):
        s = self.make(euid=1000)
        s.write(
            "etc/default/grub.d/kdump-tools.cfg",
            'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT crashkernel=512M"\n',
        )
        out = self.run_cli("--connector", "card0-HDMI-A-4", "grub")[1]
        self.assertIn("parameter:     drm.edid_firmware=HDMI-A-4:screenpad/edid.bin", out)
        self.assertNotIn("warning", out)
        s.write("etc/default/grub.d/50-cloudimg.cfg", 'GRUB_CMDLINE_LINUX_DEFAULT="console=tty1"\n')
        out = self.run_cli("grub")[1]
        self.assertIn("50-cloudimg.cfg sets GRUB_CMDLINE_LINUX_DEFAULT", out)

    def test_refused_on_other_machine(self):
        self.make(product="ZenBook UX425EA")
        self.assertEqual(self.run_cli("grub", "apply")[0], 2)


@unittest.skipIf(os.geteuid() == 0, "runs install.sh, so never as root")
class InstallTests(Base):
    STUBS = ("id", "systemctl", "update-initramfs", "lsinitramfs", "udevadm")
    TOUCHBAR_FILES = (
        "usr/local/bin/screenpad-touchbar",
        "usr/lib/systemd/user/screenpad-touchbar.service",
        "usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad",
        "etc/udev/rules.d/70-screenpad-uinput.rules",
    )

    def setUp(self):
        super().setUp()
        self.s = self.make()
        self.bin = os.path.join(self.tmp.name, "stub-bin")
        self.calls = os.path.join(self.tmp.name, "calls")
        os.makedirs(self.bin)
        for name in self.STUBS:
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> "$STUB_CALLS"\n' % name)
                if name == "id":
                    f.write("echo 0\n")
                if name == "lsinitramfs":
                    f.write('cat "$STUB_INITRAMFS" 2>/dev/null\n')
            os.chmod(path, 0o755)
        self.path = self.bin + os.pathsep + os.environ.get("PATH", "")
        for name in self.STUBS:
            # Fail closed: the real commands must never run.
            self.assertEqual(shutil.which(name, path=self.path), os.path.join(self.bin, name))

    def install(self, *argv, initramfs=""):
        listing = os.path.join(self.tmp.name, "initramfs.txt")
        with open(listing, "w") as f:
            f.write(initramfs)
        env = dict(os.environ, DESTDIR=self.s.root, PATH=self.path, STUB_CALLS=self.calls, STUB_INITRAMFS=listing)
        return subprocess.run(
            ["sh", os.path.join(REPO, "install.sh")] + list(argv), env=env, capture_output=True, text=True
        )

    def called(self):
        try:
            with open(self.calls) as f:
                return [line.split()[0:2] for line in f if not line.startswith("id ")]
        except FileNotFoundError:
            return []

    def saved(self):
        os.makedirs(self.s.p("var/lib/screenpad"))
        with open(self.s.p("var/lib/screenpad/edid.bin"), "wb") as f:
            f.write(tsb_edid())

    def test_install_copies_the_edid_to_firmware(self):
        self.saved()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("lib/firmware/screenpad/edid.bin"), tsb_edid())
        self.assertEqual(self.s.read("usr/local/sbin/screenpad"), repo_file("screenpad.py"))
        for rel in (
            "etc/systemd/system/screenpad.service",
            "usr/lib/systemd/system-sleep/screenpad",
            "usr/local/share/doc/screenpad/README.md",
            "usr/local/share/doc/screenpad/docs/install.md",
            "usr/local/share/doc/screenpad/docs/touchbar.md",
        ):
            self.assertTrue(os.path.exists(self.s.p(rel)), rel)
        self.assertFalse(os.path.exists(self.s.p("etc/initramfs-tools/hooks/screenpad")))
        self.assertEqual(
            self.called(),
            [
                ["udevadm", "control"],
                ["udevadm", "trigger"],
                ["lsinitramfs", self.s.p("boot/initrd.img-%s" % os.uname().release)],
                ["systemctl", "daemon-reload"],
                ["systemctl", "enable"],
            ],
        )
        self.assertNotIn("lockdown", r.stdout)
        self.assertEqual(self.s.read("etc/default/grub").decode(), UBUNTU_GRUB)

    def test_install_touchbar(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("usr/local/bin/screenpad-touchbar"), repo_file("touchbar.py"))
        self.assertTrue(os.access(self.s.p("usr/local/bin/screenpad-touchbar"), os.X_OK))
        self.assertEqual(
            self.s.read("usr/lib/systemd/user/screenpad-touchbar.service"),
            repo_file("systemd/screenpad-touchbar.service"),
        )
        self.assertEqual(
            self.s.read("etc/udev/rules.d/70-screenpad-uinput.rules"), repo_file("udev/70-screenpad-uinput.rules")
        )
        ext = "gnome-shell/screenpad-touchbar@screenpad"
        for name in os.listdir(os.path.join(REPO, ext)):
            self.assertEqual(
                self.s.read("usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad/%s" % name),
                repo_file("%s/%s" % (ext, name)),
            )
        self.assertIn("screenpad-touchbar on", r.stdout)
        # The touch bar is the user's to turn on; install never enables it.
        self.assertNotIn(["systemctl", "--user"], self.called())

    def test_install_programs(self):
        # An earlier version installed the programs as single files, and a
        # module it had must not linger in the package.
        self.s.write("usr/local/bin/screenpad-touchbar", "old single file\n")
        self.s.write("usr/local/lib/screenpad/screenpad_touchbar/gone.py", "")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        lib = "usr/local/lib/screenpad"
        for link, script in (
            ("usr/local/sbin/screenpad", "screenpad.py"),
            ("usr/local/bin/screenpad-touchbar", "touchbar.py"),
        ):
            self.assertEqual(os.readlink(self.s.p(link)), "../lib/screenpad/" + script)
            self.assertEqual(self.s.read("%s/%s" % (lib, script)), repo_file(script))
            self.assertTrue(os.access(self.s.p(lib, script), os.X_OK))
        for pkg in ("screenpad_display", "screenpad_touchbar"):
            modules = sorted(n for n in os.listdir(os.path.join(REPO, pkg)) if n.endswith(".py"))
            self.assertEqual(sorted(os.listdir(self.s.p(lib, pkg))), modules)
            for name in modules:
                self.assertEqual(self.s.read("%s/%s/%s" % (lib, pkg, name)), repo_file("%s/%s" % (pkg, name)))
        # The installed commands find their package from where they are
        # installed, in isolated mode, run from anywhere.
        for link, prog in (
            ("usr/local/sbin/screenpad", "screenpad"),
            ("usr/local/bin/screenpad-touchbar", "screenpad-touchbar"),
        ):
            run = subprocess.run(
                [sys.executable, "-I", self.s.p(link), "--help"], cwd=self.tmp.name, capture_output=True, text=True
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue(run.stdout.startswith("usage: %s " % prog), run.stdout)

    def test_install_without_saved_edid(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No saved EDID yet", r.stdout)
        self.assertFalse(os.path.exists(self.s.p("lib/firmware/screenpad/edid.bin")))

    def test_initramfs_only_when_nvidia_drm_is_in_it(self):
        self.saved()
        r = self.install(initramfs="usr/lib/modules/7.0.0-38-generic/kernel/nvidia-580/nvidia-drm.ko.zst\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.s.read("etc/initramfs-tools/hooks/screenpad"), repo_file("initramfs/screenpad"))
        self.assertIn(["update-initramfs", "-u"], self.called())

        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.s.p("etc/initramfs-tools/hooks/screenpad")))
        self.assertEqual(self.called().count(["update-initramfs", "-u"]), 2)

    def test_lockdown_hint(self):
        self.s.write("sys/kernel/security/lockdown", "none [integrity] confidentiality\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("screenpad grub show", r.stdout)

    def test_uninstall_refuses_while_grub_loads_the_edid(self):
        self.saved()
        self.assertEqual(self.install().returncode, 0)
        self.s.write("etc/default/grub", UBUNTU_GRUB.replace('"quiet splash"', '"quiet splash %s"' % PARAM))
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 1)
        self.assertIn("screenpad grub remove", r.stderr)
        self.assertTrue(os.path.exists(self.s.p("usr/local/sbin/screenpad")))

    def test_uninstall_and_purge(self):
        self.saved()
        self.assertEqual(self.install().returncode, 0)
        r = self.install("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (
            "usr/local/sbin/screenpad",
            "etc/systemd/system/screenpad.service",
            "usr/lib/systemd/system-sleep/screenpad",
            "usr/local/share/doc/screenpad",
            "lib/firmware/screenpad",
            "usr/local/lib/screenpad",
        ) + self.TOUCHBAR_FILES:
            self.assertFalse(os.path.exists(self.s.p(rel)), rel)
        self.assertTrue(os.path.exists(self.s.p("var/lib/screenpad/edid.bin")))
        self.assertEqual(self.install("purge").returncode, 0)
        self.assertFalse(os.path.exists(self.s.p("var/lib/screenpad")))

    def test_refuses_other_machine(self):
        self.s.write("sys/class/dmi/id/product_name", "ZenBook UX425EA")
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsupported machine", r.stderr)
        self.assertFalse(os.path.exists(self.s.p("usr/local/sbin/screenpad")))


if __name__ == "__main__":
    unittest.main()
