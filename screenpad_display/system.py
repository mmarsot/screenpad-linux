"""The filesystem root and side effects (Env).

File and sysfs attribute access, the DDC read of the EDID, and the machine
and root checks.
"""

import fcntl
import os
import subprocess
import time

from .common import (
    DMI_ID,
    EDID_BLOCK,
    EDID_HEADER,
    EDID_I2C_ADDR,
    I2C_SLAVE,
    SUPPORTED_DMI,
    ScreenpadError,
    Unsupported,
)


def read_edid_i2c(dev):
    """Read EDID blocks 0 and 1 from the DDC slave at 0x50 behind an i2c-dev node."""
    fd = os.open(dev, os.O_RDWR)
    try:
        fcntl.ioctl(fd, I2C_SLAVE, EDID_I2C_ADDR)

        def block(offset):
            os.write(fd, bytes([offset]))
            data = os.read(fd, EDID_BLOCK)
            if len(data) != EDID_BLOCK:
                raise OSError("short read (%d bytes) at offset %d" % (len(data), offset))
            return data

        edid = block(0)
        if edid[:8] != EDID_HEADER:
            return edid
        extensions = edid[126]
        if extensions > 1:
            raise ScreenpadError("%s: EDID announces %d extensions; only 1 is supported" % (dev, extensions))
        if extensions:
            edid += block(EDID_BLOCK)
        return edid
    finally:
        os.close(fd)


class Env:
    """Filesystem root and side effects, replaceable in tests."""

    def __init__(
        self, root="/", sleep=time.sleep, euid=os.geteuid, i2c_read=read_edid_i2c, clock=time.monotonic, run=None
    ):
        self.root = root
        self.sleep = sleep
        self.euid = euid
        self.i2c_read = i2c_read
        self.clock = clock
        self.run = run or (lambda argv: subprocess.run(argv, check=True))

    def path(self, *parts):
        return os.path.join(self.root, *(p.lstrip("/") for p in parts))


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def write_file(path, data, mode=0o644):
    """Replace a regular file atomically."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_attr(path, data):
    """Write one buffer to an existing sysfs/debugfs attribute in a single write().

    Callers use it as system.write_attr, so the tests replace it in one place
    with a fake asus-wmi.
    """
    if isinstance(data, str):
        data = data.encode()
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        written = os.write(fd, data)
    finally:
        os.close(fd)
    if written != len(data):
        raise ScreenpadError("%s: short write (%d of %d bytes)" % (path, written, len(data)))


def check_dmi(env):
    vendor = read_text(env.path(DMI_ID, "sys_vendor")) or "?"
    product = read_text(env.path(DMI_ID, "product_name")) or "?"
    if (vendor, product) not in SUPPORTED_DMI:
        raise Unsupported("unsupported machine %r %r; this tool only knows the ZenBook UX480FD" % (vendor, product))
    return vendor, product


def require_root(env):
    if env.euid() != 0:
        raise ScreenpadError("must run as root")
