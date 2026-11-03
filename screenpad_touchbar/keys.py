"""Key names and combos, and the uinput key device's presses."""

from .codes import EV_KEY, EV_SYN, MAX_KEY, SYN_REPORT
from .common import ConfigError


def _key_names():
    names = {}
    for row, first in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
        for i, c in enumerate(row):
            names[c] = first + i
    for i in range(1, 10):
        names[str(i)] = 1 + i
    names["0"] = 11
    for i in range(1, 11):
        names["f%d" % i] = 58 + i
    names["f11"], names["f12"] = 87, 88
    for i in range(13, 25):
        names["f%d" % i] = 170 + i
    names.update(
        {
            "esc": 1,
            "minus": 12,
            "equal": 13,
            "backspace": 14,
            "tab": 15,
            "leftbrace": 26,
            "rightbrace": 27,
            "enter": 28,
            "leftctrl": 29,
            "semicolon": 39,
            "apostrophe": 40,
            "grave": 41,
            "leftshift": 42,
            "backslash": 43,
            "comma": 51,
            "dot": 52,
            "slash": 53,
            "rightshift": 54,
            "leftalt": 56,
            "space": 57,
            "capslock": 58,
            "numlock": 69,
            "scrolllock": 70,
            "rightctrl": 97,
            "sysrq": 99,
            "rightalt": 100,
            "home": 102,
            "up": 103,
            "pageup": 104,
            "left": 105,
            "right": 106,
            "end": 107,
            "down": 108,
            "pagedown": 109,
            "insert": 110,
            "delete": 111,
            "mute": 113,
            "volumedown": 114,
            "volumeup": 115,
            "pause": 119,
            "leftmeta": 125,
            "rightmeta": 126,
            "compose": 127,
            "menu": 139,
            "calc": 140,
            "sleep": 142,
            "nextsong": 163,
            "playpause": 164,
            "previoussong": 165,
            "stopcd": 166,
            "record": 167,
            "print": 210,
            "camera": 212,
            "search": 217,
            "brightnessdown": 224,
            "brightnessup": 225,
            "micmute": 248,
        }
    )
    names.update(
        {
            "ctrl": 29,
            "control": 29,
            "shift": 42,
            "alt": 56,
            "altgr": 100,
            "super": 125,
            "meta": 125,
            "win": 125,
            "escape": 1,
            "return": 28,
            "del": 111,
            "pgup": 104,
            "pgdn": 109,
            "printscreen": 99,
            "playpause": 164,
            "next": 163,
            "previous": 165,
            "prev": 165,
        }
    )
    return names


KEY_NAMES = _key_names()


def parse_key(spec):
    """'ctrl+alt+t' -> (29, 56, 20). Names are evdev key names, KEY_ optional."""
    if not isinstance(spec, str) or not spec.strip():
        raise ConfigError('key must be a non-empty string like "alt+k" or "ctrl+alt+t"')
    codes = []
    for token in spec.split("+"):
        name = token.strip().lower()
        if name.startswith("key_"):
            name = name[4:]
        if name.isdigit():
            code = int(name)
            if not 0 < code <= MAX_KEY:
                raise ConfigError("key code %s is outside 1..%d" % (name, MAX_KEY))
        elif name in KEY_NAMES:
            code = KEY_NAMES[name]
        else:
            raise ConfigError("unknown key %r in %r" % (token.strip(), spec))
        if code in codes:
            raise ConfigError("key %r appears twice in %r" % (token.strip(), spec))
        codes.append(code)
    return tuple(codes)


class KeyEmitter:
    """Presses and releases key combos on the uinput key device.

    Counts presses per code, so two held buttons sharing a modifier do not
    release it under each other.
    """

    def __init__(self, write):
        self.write = write
        self.down = {}

    def _frame(self, changes):
        if changes:
            self.write([(EV_KEY, c, v) for c, v in changes] + [(EV_SYN, SYN_REPORT, 0)])

    def press(self, codes):
        changes = []
        for c in codes:
            self.down[c] = self.down.get(c, 0) + 1
            if self.down[c] == 1:
                changes.append((c, 1))
        self._frame(changes)

    def release(self, codes):
        changes = []
        for c in reversed(codes):
            n = self.down.get(c, 0)
            if n == 0:
                continue
            if n == 1:
                del self.down[c]
                changes.append((c, 0))
            else:
                self.down[c] = n - 1
        self._frame(changes)

    def tap(self, codes):
        self.press(codes)
        self.release(codes)

    def release_all(self):
        self._frame([(c, 0) for c in sorted(self.down)])
        self.down = {}
