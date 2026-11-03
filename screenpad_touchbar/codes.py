"""Linux input event codes, the evdev and uinput structs and their ioctls."""

import struct

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS, EV_MSC = 0x00, 0x01, 0x03, 0x04
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39
ABS_CNT = 0x40
MSC_TIMESTAMP = 0x05
BTN_LEFT = 0x110
BTN_TOOL_FINGER = 0x145
BTN_TOOL_QUINTTAP = 0x148
BTN_TOUCH = 0x14A
BTN_TOOL_DOUBLETAP = 0x14D
BTN_TOOL_TRIPLETAP = 0x14E
BTN_TOOL_QUADTAP = 0x14F
KEY_CNT = 0x300
# What asus-nb-wmi sends for Fn+F6.
KEY_TOUCHPAD_TOGGLE = 0x212
MT_TOOL_FINGER = 0
MT_TOOL_PEN = 1
MT_TOOL_PALM = 2
BUS_VIRTUAL = 0x06
# BTN_TOOL_* for 1..5 fingers, as input_mt_report_finger_count() sends them.
FINGER_TOOLS = (BTN_TOOL_FINGER, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP, BTN_TOOL_QUADTAP, BTN_TOOL_QUINTTAP)
# The key device can send every code below the BTN_* range.
MAX_KEY = 0xFF

INPUT_EVENT = struct.Struct("llHHi")
ABSINFO = struct.Struct("6i")
INPUT_ID = struct.Struct("4H")
UINPUT_SETUP = struct.Struct("4H80sI")
UINPUT_ABS_SETUP = struct.Struct("H2x6i")


def _ioc(direction, nr, size, kind="E"):
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def EVIOCGBIT(ev, size):
    return _ioc(2, 0x20 + ev, size)


def EVIOCGABS(code):
    return _ioc(2, 0x40 + code, ABSINFO.size)


EVIOCGID = _ioc(2, 0x02, INPUT_ID.size)
EVIOCGPROP = _ioc(2, 0x09, 4)
EVIOCGKEY = _ioc(2, 0x18, KEY_CNT // 8)
EVIOCGRAB = _ioc(1, 0x90, 4)
UI_DEV_CREATE = _ioc(0, 1, 0, "U")
UI_DEV_DESTROY = _ioc(0, 2, 0, "U")
UI_DEV_SETUP = _ioc(1, 3, UINPUT_SETUP.size, "U")
UI_ABS_SETUP = _ioc(1, 4, UINPUT_ABS_SETUP.size, "U")
UI_SET_EVBIT = _ioc(1, 100, 4, "U")
UI_SET_KEYBIT = _ioc(1, 101, 4, "U")
UI_SET_ABSBIT = _ioc(1, 103, 4, "U")
UI_SET_MSCBIT = _ioc(1, 104, 4, "U")
UI_SET_PROPBIT = _ioc(1, 110, 4, "U")


def EVIOCGMTSLOTS(nslots):
    return _ioc(2, 0x0A, 4 * (1 + nslots))
