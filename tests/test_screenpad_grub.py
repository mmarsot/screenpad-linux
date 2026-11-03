"""Tests for 'screenpad grub' on a fake /etc/default/grub.

Run with: python3 -m unittest discover -s tests"""

import os
import unittest

from screenpad_support import PARAM, UBUNTU_GRUB, Base, tsb_edid

from screenpad_display.common import (
    ScreenpadError,
)
from screenpad_display.grub import grub_add, grub_remove


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


if __name__ == "__main__":
    unittest.main()
