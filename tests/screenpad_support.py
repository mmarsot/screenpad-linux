"""What the display tests share: the checkout on sys.path, a TSB EDID, a fake
sysfs/debugfs/procfs//etc tree, a fake asus-wmi and compositor, a fake clock,
and the Base test case that wires them into an Env."""

import contextlib
import io
import os
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
    DRM_CLASS,
    EDID_HEADER,
)
from screenpad_display.edid import block_checksum
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
