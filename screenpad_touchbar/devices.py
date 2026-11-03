"""The touchpad's evdev node (finding, reading, grabbing it) and the uinput devices the daemon creates."""

import errno
import fcntl
import os
import struct

from .codes import (
    ABS_CNT,
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TOOL_TYPE,
    ABS_MT_TRACKING_ID,
    ABSINFO,
    BTN_LEFT,
    BTN_TOUCH,
    BUS_VIRTUAL,
    EV_ABS,
    EV_KEY,
    EV_MSC,
    EV_SYN,
    EVIOCGABS,
    EVIOCGBIT,
    EVIOCGID,
    EVIOCGKEY,
    EVIOCGMTSLOTS,
    EVIOCGPROP,
    EVIOCGRAB,
    INPUT_EVENT,
    INPUT_ID,
    KEY_CNT,
    MAX_KEY,
    MT_TOOL_PALM,
    MT_TOOL_PEN,
    SYN_REPORT,
    UI_ABS_SETUP,
    UI_DEV_CREATE,
    UI_DEV_DESTROY,
    UI_DEV_SETUP,
    UI_SET_ABSBIT,
    UI_SET_EVBIT,
    UI_SET_KEYBIT,
    UI_SET_MSCBIT,
    UI_SET_PROPBIT,
    UINPUT_ABS_SETUP,
    UINPUT_SETUP,
    _ioc,
)
from .common import KEYS_NAME, PAD_NAME, SOURCE_PRODUCT, SOURCE_SUFFIX, SOURCE_VENDOR, SYS_INPUT, TouchbarError, log
from .splitter import Axes


def bits(fd, ev, count):
    buf = bytearray((count + 7) // 8)
    fcntl.ioctl(fd, EVIOCGBIT(ev, len(buf)), buf)
    return [i for i in range(count) if buf[i // 8] >> (i % 8) & 1]


def absinfo(fd, code):
    buf = bytearray(ABSINFO.size)
    fcntl.ioctl(fd, EVIOCGABS(code), buf)
    return ABSINFO.unpack(buf)


def read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def find_source(sys_input=SYS_INPUT):
    """/dev/input/eventN of the ScreenPad touchpad, or None. Skips virtual devices."""
    try:
        names = sorted(os.listdir(sys_input), key=lambda n: (len(n), n))
    except OSError:
        return None
    for name in names:
        if not name.startswith("event"):
            continue
        dev = os.path.join(sys_input, name, "device")
        if "/devices/virtual/" in os.path.realpath(dev):
            continue
        vendor = read_sys(os.path.join(dev, "id", "vendor"))
        product = read_sys(os.path.join(dev, "id", "product"))
        dev_name = read_sys(os.path.join(dev, "name")) or ""
        if (
            vendor
            and product
            and int(vendor, 16) == SOURCE_VENDOR
            and int(product, 16) == SOURCE_PRODUCT
            and dev_name.endswith(SOURCE_SUFFIX)
        ):
            return os.path.join("/dev/input", name)
    return None


def write_events(fd, events):
    if events:
        os.write(fd, b"".join(INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events))


def tool_reset(tool, ids):
    """The events that leave the tool type the kernel keeps for each idle
    slot at MT_TOOL_PEN, which a touchpad never reports; tool is the
    ABS_MT_TOOL_TYPE absinfo (None without the axis), ids the slots'
    tracking ids.

    The kernel sends a slot's ABS_MT_TOOL_TYPE only when it changes, and
    libinput keeps the last one it read for each slot, across touches and
    across a resume. A palm libinput saw in a slot, then a finger there
    while the daemon held the grab (or libinput had the touchpad off), and
    every later finger in that slot is a palm to libinput: one finger no
    longer moves the pointer, two do. With the pen value kept, the next
    contact in every slot sends its own tool. The slots go in order, so
    the kernel's current slot is left at the last one and a new contact in
    slot 0 sends ABS_MT_SLOT too."""
    if tool is None or not tool[1] <= MT_TOOL_PEN < MT_TOOL_PALM <= tool[2]:
        return []
    events = []
    for slot, id in enumerate(ids):
        if id == -1:
            events += [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TOOL_TYPE, MT_TOOL_PEN)]
    return events + [(EV_SYN, SYN_REPORT, 0)] if events else []


class Source:
    """The grabbed ScreenPad touchpad."""

    def __init__(self, path):
        self.path = path
        # Written to only by reset_tools(); a node that is not writable is
        # still read.
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            buf = bytearray(INPUT_ID.size)
            fcntl.ioctl(self.fd, EVIOCGID, buf)
            self.id = INPUT_ID.unpack(buf)
            name = bytearray(256)
            n = fcntl.ioctl(self.fd, _ioc(2, 0x06, len(name)), name)
            self.name = bytes(name[: max(n - 1, 0)]).decode(errors="replace")
            prop = bytearray(4)
            fcntl.ioctl(self.fd, EVIOCGPROP, prop)
            self.props = [i for i in range(32) if prop[i // 8] >> (i % 8) & 1]
            self.keys = bits(self.fd, EV_KEY, KEY_CNT)
            self.abs = {code: absinfo(self.fd, code) for code in bits(self.fd, EV_ABS, ABS_CNT)}
            self.msc = bits(self.fd, EV_MSC, 8)
            for code in (ABS_MT_SLOT, ABS_MT_TRACKING_ID, ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                if code not in self.abs:
                    raise TouchbarError("%s has no multitouch axis %#x" % (path, code))
        except BaseException:
            os.close(self.fd)
            raise
        self.grabbed = False

    @property
    def nslots(self):
        return self.abs[ABS_MT_SLOT][2] + 1

    def axes(self):
        x, y = self.abs[ABS_MT_POSITION_X], self.abs[ABS_MT_POSITION_Y]
        return Axes(x[1], x[2], y[1], y[2])

    def touching(self):
        buf = bytearray(KEY_CNT // 8)
        fcntl.ioctl(self.fd, EVIOCGKEY, buf)
        return bool(buf[BTN_TOUCH // 8] >> (BTN_TOUCH % 8) & 1)

    def mt_slots(self, code):
        buf = bytearray(4 * (1 + self.nslots))
        struct.pack_into("I", buf, 0, code)
        fcntl.ioctl(self.fd, EVIOCGMTSLOTS(self.nslots), buf)
        return list(struct.unpack_from("%di" % self.nslots, buf, 4))

    def state(self):
        """Arguments for Splitter.resync()."""
        ids = self.mt_slots(ABS_MT_TRACKING_ID)
        xs = self.mt_slots(ABS_MT_POSITION_X)
        ys = self.mt_slots(ABS_MT_POSITION_Y)
        tools = self.mt_slots(ABS_MT_TOOL_TYPE) if ABS_MT_TOOL_TYPE in self.abs else [0] * self.nslots
        buf = bytearray(KEY_CNT // 8)
        fcntl.ioctl(self.fd, EVIOCGKEY, buf)
        left = buf[BTN_LEFT // 8] >> (BTN_LEFT % 8) & 1
        return ids, xs, ys, tools, left, absinfo(self.fd, ABS_MT_SLOT)[0]

    def reset_tools(self):
        """Make the next contact in each idle slot send its tool type
        (tool_reset()). Written while grabbed, only this reader sees it;
        otherwise libinput reads it too, as a slot that is no palm. A failure
        is logged: the touchpad works on without it."""
        tool = self.abs.get(ABS_MT_TOOL_TYPE)
        try:
            write_events(self.fd, tool_reset(tool, self.mt_slots(ABS_MT_TRACKING_ID) if tool else []))
        except OSError as e:
            log("cannot reset the touchpad's tool types: %s" % e)

    def grab(self, on):
        fcntl.ioctl(self.fd, EVIOCGRAB, 1 if on else 0)
        self.grabbed = on

    def read(self):
        """The pending events, as (type, code, value)."""
        try:
            data = os.read(self.fd, INPUT_EVENT.size * 64)
        except BlockingIOError:
            return []
        if not data:
            raise OSError(errno.ENODEV, "device gone")
        return [
            INPUT_EVENT.unpack_from(data, o)[2:] for o in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size)
        ]

    def close(self):
        os.close(self.fd)


def reset_tools(path):
    """Source.reset_tools() on the touchpad at path, which the daemon does not hold."""
    try:
        source = Source(path)
    except (OSError, TouchbarError) as e:
        log("cannot reset the touchpad's tool types: %s" % e)
        return
    try:
        source.reset_tools()
    finally:
        source.close()


def uinput_open():
    try:
        return os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except PermissionError:
        raise TouchbarError(
            "cannot open /dev/uinput; 'sudo ./install.sh' installs a udev rule that gives the desktop user access"
        )


def uinput_create(fd, name, bus, vendor, product, version):
    setup = UINPUT_SETUP.pack(bus, vendor, product, version, name.encode()[:79], 0)
    fcntl.ioctl(fd, UI_DEV_SETUP, setup)
    fcntl.ioctl(fd, UI_DEV_CREATE)


def create_pad(source):
    """A uinput touchpad with the source's capabilities, ids and resolution."""
    fd = uinput_open()
    try:
        for ev in (EV_SYN, EV_KEY, EV_ABS) + ((EV_MSC,) if source.msc else ()):
            fcntl.ioctl(fd, UI_SET_EVBIT, ev)
        for code in source.keys:
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        for code in source.msc:
            fcntl.ioctl(fd, UI_SET_MSCBIT, code)
        for prop in source.props:
            fcntl.ioctl(fd, UI_SET_PROPBIT, prop)
        for code, info in sorted(source.abs.items()):
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP, UINPUT_ABS_SETUP.pack(code, *info))
        # Same bus and ids as the real pad: libinput then tags it internal, so
        # disable-while-typing pairs it with the built-in keyboard.
        uinput_create(fd, PAD_NAME, *source.id)
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_keys():
    fd = uinput_open()
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in range(1, MAX_KEY + 1):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        uinput_create(fd, KEYS_NAME, BUS_VIRTUAL, SOURCE_VENDOR, SOURCE_PRODUCT, 1)
    except BaseException:
        os.close(fd)
        raise
    return fd


def uinput_destroy(fd):
    try:
        fcntl.ioctl(fd, UI_DEV_DESTROY)
    finally:
        os.close(fd)
