"""What the whole display CLI shares.

The supported machine, the sysfs, debugfs and /etc paths it uses (relative
to Env.root), the panel's constants, the errors and logging.
"""

import sys

SUPPORTED_DMI = (
    # (sys_vendor, product_name)
    ("ASUSTeK COMPUTER INC.", "ZenBook UX450FDX_UX480FD"),
)

NVIDIA_VENDOR = "0x10de"
NVIDIA_DRIVER = "nvidia"
NVIDIA_I2C_NAME = "NVIDIA i2c adapter"

BACKLIGHT = "sys/class/backlight/asus_screenpad"
DRM_CLASS = "sys/class/drm"
I2C_DEVICES = "sys/bus/i2c/devices"
DEBUGFS_DRI = "sys/kernel/debug/dri"
DMI_ID = "sys/class/dmi/id"
SAVED_EDID = "var/lib/screenpad/edid.bin"
ACTIVE_FLAG = "run/screenpad/active"
LOCKDOWN = "sys/kernel/security/lockdown"
CMDLINE = "proc/cmdline"
# drm is built in on Ubuntu, and this parameter is 0644 and not a hardware
# parameter, so lockdown leaves it writable at runtime.
DRM_EDID_FIRMWARE = "sys/module/drm/parameters/edid_firmware"
FIRMWARE_DIR = "lib/firmware"
# The name request_firmware() takes, relative to /lib/firmware.
FIRMWARE_NAME = "screenpad/edid.bin"
GRUB_DEFAULT = "etc/default/grub"
GRUB_DEFAULT_D = "etc/default/grub.d"
GRUB_BACKUP = "etc/default/grub.screenpad-backup"
GRUB_VAR = "GRUB_CMDLINE_LINUX_DEFAULT"
EDID_FIRMWARE_PARAM = "drm.edid_firmware"
# What the parameter holds when nothing is set: a sysfs write of "" is a
# no-op, and "\n" would make every connector request a firmware named "".
EDID_FIRMWARE_EMPTY = ","

# Backlight bl_power values (FB_BLANK_UNBLANK / FB_BLANK_POWERDOWN). asus-wmi
# does not map them to SCREENPAD_POWER=1 / =0; see light().
BL_POWER_ON = 0
BL_POWER_OFF = 4

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"
EDID_BLOCK = 128
EDID_I2C_ADDR = 0x50
I2C_SLAVE = 0x0703
HDMI_OUI = b"\x03\x0c\x00"

# Seconds between the SCREENPAD_LIGHT and SCREENPAD_POWER=0 writes of light().
# 1 s is what the report proposes; it was not measured on the hardware.
RELIGHT_GAP = 1.0
# Seconds to let the bridge come up after lighting before reading DDC, then
# how often and how long to keep retrying while it does not answer.
POWER_SETTLE = 2.0
DDC_RETRY_DELAY = 1.0
EDID_READ_TRIES = 8


class ScreenpadError(Exception):
    pass


class Unsupported(ScreenpadError):
    pass


def log(msg):
    print("screenpad: %s" % msg, file=sys.stderr, flush=True)
