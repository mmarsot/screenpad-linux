"""Tests for lockdown and drm.edid_firmware, the panel through a fake asus-wmi,
and the DDC retry; no root and no hardware.

Run with: python3 -m unittest discover -s tests"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from screenpad_support import (
    CMDLINE,
    NVIDIA_PCI,
    PARAM,
    Base,
    tsb_edid,
)

from screenpad_display.common import (
    DDC_RETRY_DELAY,
    EDID_READ_TRIES,
    POWER_SETTLE,
    RELIGHT_GAP,
)
from screenpad_display.lockdown import (
    lockdown_state,
    locked_down,
)


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


if __name__ == "__main__":
    unittest.main()
