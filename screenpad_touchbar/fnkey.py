"""Fn+F6: the touchpad toggle key cycles the touchpad and the ScreenPad's display.

Each press goes one step: (1) touchpad and display off, (2) the touchpad on
with the display still off, (3) both on, then (1) again. The state is never
counted: it is read from GNOME's touchpad setting (send-events, followed with
'gsettings monitor') and the panel's backlight (bl_power), and a press goes
from what they say now. GNOME flips the setting on every press itself
(gsd-media-keys), so the daemon lets it and writes the setting back only for
the step that keeps the touchpad on, (2) to (3). Only presses change the
display. "Display off" is the ScreenPad's backlight off: 'screenpad light' and
'screenpad dark', which the udev rule lets the input group run, leave the
connector on, so the GPU still drives it and GNOME keeps it as a monitor. The
daemon grabs the touchpad only while the display is on, so in (1) and (2) the
bar draws nothing and the ScreenPad is a plain touchpad. GNOME's on-screen
indicator knows only the touchpad, so for (2) to (3) it says the touchpad is
off: the scene tells the extension when a press lights the display, and when
the next one will, and it shows "ScreenPad on" in its place.
"""

import os
import subprocess
import time

from .codes import EV_KEY, INPUT_EVENT, KEY_TOUCHPAD_TOGGLE
from .common import ACTIVE_FLAG, SYS_INPUT, log

_warned: set[str] = set()


def warn(message):
    """Log a problem once, not every time it is retried."""
    if message not in _warned:
        _warned.add(message)
        log(message)


HOTKEYS_NAME = "Asus WMI hotkeys"
TOUCHPAD_SCHEMA = "org.gnome.desktop.peripherals.touchpad"
SEND_EVENTS = "send-events"
GSETTINGS = ("gsettings",)
SCREENPAD = ("/usr/local/sbin/screenpad",)
BL_POWER = "/sys/class/backlight/asus_screenpad/bl_power"
# bl_power after 'screenpad on', 'replug' and 'light' (the panel lit); 'dark'
# and 'off' leave 0.
LIT = "4"
# How long after a press GNOME's own flip of the setting may take to show.
FLIP_WAIT = 1.0
# How often to look again for the hotkeys device or restart the monitor.
RETRY = 30.0
# How often to look at a running 'screenpad light' or 'dark'.
CHILD_POLL = 0.25

ON = "enabled"
OFF = "disabled"


def touchpad_on(value):
    """Whether send-events lets the touchpad work ('disabled-on-external-mouse' counts as on)."""
    return value != OFF


def next_state(touchpad, display):
    """(touchpad, display) after a press, from the real states before it."""
    if not touchpad:
        return True, False
    if not display:
        return True, True
    return False, False


def find_hotkeys(sys_input=SYS_INPUT):
    """/dev/input/eventN of the Asus WMI hotkeys (where Fn+F6 arrives), or None."""
    try:
        names = sorted(os.listdir(sys_input), key=lambda n: (len(n), n))
    except OSError:
        return None
    for name in names:
        dev = os.path.join(sys_input, name, "device")
        if not name.startswith("event") or "/devices/virtual/" in os.path.realpath(dev):
            continue
        try:
            with open(os.path.join(dev, "name")) as f:
                if f.read().strip() == HOTKEYS_NAME:
                    return os.path.join("/dev/input", name)
        except OSError:
            continue
    return None


class Hotkey:
    """The hotkeys device, read without grabbing it: GNOME sees every key too."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)

    def presses(self):
        """Touchpad toggle presses waiting; None once the device is gone."""
        count = 0
        while True:
            try:
                data = os.read(self.fd, INPUT_EVENT.size * 64)
            except BlockingIOError:
                return count
            except OSError:
                return None
            if not data:
                return None
            for i in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                _, _, kind, code, value = INPUT_EVENT.unpack_from(data, i)
                if kind == EV_KEY and code == KEY_TOUCHPAD_TOGGLE and value == 1:
                    count += 1

    def close(self):
        os.close(self.fd)


def parse_value(text):
    """'enabled' from "'enabled'" (gsettings get) or "send-events: 'enabled'" (monitor)."""
    text = text.strip()
    if text.startswith(SEND_EVENTS + ":"):
        text = text[len(SEND_EVENTS) + 1 :].strip()
    return text.strip("'\"") or None


class TouchpadSetting:
    """GNOME's send-events through the gsettings command: read once, then followed."""

    def __init__(self, command=GSETTINGS):
        self.command = list(command)
        self.value: str | None = None
        self.monitor: subprocess.Popen | None = None
        self.buffer = b""
        self.retry_at = 0.0
        self.setters: list[subprocess.Popen] = []

    def start(self, now):
        """Start the monitor (again), then read the value, so no change falls between."""
        self.setters = [c for c in self.setters if c.poll() is None]
        if self.monitor is not None or now < self.retry_at:
            return
        self.retry_at = now + RETRY
        key = [TOUCHPAD_SCHEMA, SEND_EVENTS]
        try:
            self.monitor = subprocess.Popen(
                self.command + ["monitor"] + key, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
            )
            assert self.monitor.stdout is not None
            os.set_blocking(self.monitor.stdout.fileno(), False)
            got = subprocess.run(self.command + ["get"] + key, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            warn("cannot follow the touchpad setting, so Fn+F6 only toggles the touchpad: %s" % e)
            self.stop()
            return
        if got.returncode == 0:
            self.value = parse_value(got.stdout)
        else:
            warn("cannot read the touchpad setting, so Fn+F6 only toggles the touchpad: %s" % got.stderr.strip())
            self.stop()

    def fd(self):
        return self.monitor.stdout.fileno() if self.monitor and self.monitor.stdout else None

    def readable(self):
        """Read what the monitor printed; the number of times the value changed."""
        fd = self.fd()
        if fd is None:
            return 0
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            return 0
        except OSError:
            data = b""
        if not data:
            log("the touchpad setting's monitor stopped")
            self.stop()
            return 0
        self.buffer += data
        changes = 0
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            value = parse_value(line.decode(errors="replace"))
            if value and value != self.value:
                self.value = value
                changes += 1
        return changes

    def set(self, value):
        """Write the setting; taken as the value at once (the monitor's echo then changes nothing)."""
        self.value = value
        try:
            self.setters.append(
                subprocess.Popen(
                    self.command + ["set", TOUCHPAD_SCHEMA, SEND_EVENTS, value],
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            )
        except OSError as e:
            log("cannot set the touchpad setting: %s" % e)

    def stop(self):
        if self.monitor is not None:
            self.monitor.kill()
            self.monitor.wait()
            if self.monitor.stdout:
                self.monitor.stdout.close()
        self.monitor = None
        self.buffer = b""
        for child in self.setters:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass


class Panel:
    """The ScreenPad's display: lit when bl_power reads 4, changed by 'screenpad light' or 'dark'."""

    def __init__(self, power=BL_POWER, command=SCREENPAD):
        self.power = power
        self.command = list(command)
        self.child: subprocess.Popen | None = None
        self.child_on = False
        self.queued: bool | None = None

    def lit(self):
        """What bl_power says; True when it cannot be read, so the bar keeps working without it."""
        try:
            with open(self.power) as f:
                return f.read().strip() == LIT
        except OSError:
            return True

    def busy(self):
        self.poll()
        return self.child is not None

    def wanted(self):
        """The display as it is, or as the command running or queued will leave it."""
        self.poll()
        if self.queued is not None:
            return self.queued
        if self.child is not None:
            return self.child_on
        return self.lit()

    def set(self, on):
        self.poll()
        if self.child is not None:
            self.queued = None if on == self.child_on else on
        elif on != self.lit():
            self.spawn(on)

    def poll(self):
        if self.child is None or self.child.poll() is None:
            return
        if self.child.returncode:
            log("screenpad %s failed (exit %d)" % ("light" if self.child_on else "dark", self.child.returncode))
        self.child = None
        queued, self.queued = self.queued, None
        if queued is not None and queued != self.lit():
            self.spawn(queued)

    def spawn(self, on):
        argv = self.command + ["light" if on else "dark"]
        try:
            self.child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
            self.child_on = on
        except OSError as e:
            log("%s: %s" % (" ".join(argv), e))


class Pending:
    """A press (or several) whose target waits for GNOME's flips of the setting."""

    def __init__(self, target, deadline):
        self.target = target
        self.deadline = deadline
        self.presses = 1
        self.changes = 0


class Toggle:
    """The cycle: presses of the key, GNOME's setting and the panel, kept in step."""

    def __init__(self, setting, panel, find=find_hotkeys, flag=ACTIVE_FLAG, clock=time.monotonic):
        self.setting = setting
        self.panel = panel
        self.find = find
        self.flag = flag
        self.clock = clock
        self.hotkey: Hotkey | None = None
        self.hotkey_at = 0.0
        self.pending: Pending | None = None
        # Presses that lit the display since the daemon started: the
        # extension shows "ScreenPad on" each time this goes up.
        self.lit = 0

    def screenpad_on(self):
        return os.path.exists(self.flag)

    def display_on(self):
        """Whether the bar should be up: the display lit, or about to be."""
        return self.panel.wanted()

    def lights_next(self):
        """Whether the next press lights the display: state (2), with the
        cycle working and no press still settling."""
        return (
            self.hotkey is not None
            and self.pending is None
            and self.setting.value is not None
            and touchpad_on(self.setting.value)
            and self.screenpad_on()
            and not self.panel.wanted()
        )

    def shown(self):
        """What the scene says of the cycle (touchbar.json's "fnkey")."""
        return {"lit": self.lit, "lights_next": self.lights_next()}

    def fds(self):
        fds = [self.hotkey.fd] if self.hotkey else []
        fd = self.setting.fd()
        return fds + ([fd] if fd is not None else [])

    def readable(self, ready):
        if self.hotkey and self.hotkey.fd in ready:
            presses = self.hotkey.presses()
            if presses is None:
                log("%s is gone" % self.hotkey.path)
                self.hotkey.close()
                self.hotkey = None
            for _ in range(presses or 0):
                self.press()
        fd = self.setting.fd()
        if fd is not None and fd in ready:
            changes = self.setting.readable()
            if self.pending:
                self.pending.changes += changes

    def press(self):
        if not self.screenpad_on() or self.setting.value is None:
            return
        now = self.clock()
        if self.pending:
            # Another press before the last one settled: one more step.
            self.pending.target = next_state(*self.pending.target)
            self.pending.presses += 1
            self.pending.deadline = now + FLIP_WAIT
        else:
            before = (touchpad_on(self.setting.value), self.panel.wanted())
            self.pending = Pending(next_state(*before), now + FLIP_WAIT)
        if self.pending.target[1] and not self.panel.wanted():
            self.lit += 1
        self.panel.set(self.pending.target[1])

    def tick(self):
        now = self.clock()
        self.setting.start(now)
        if self.hotkey is None and now >= self.hotkey_at:
            self.hotkey_at = now + RETRY
            path = self.find()
            try:
                self.hotkey = Hotkey(path) if path else None
            except OSError as e:
                warn("cannot read %s, so Fn+F6 only toggles the touchpad: %s" % (path, e))
        self.panel.poll()
        pending = self.pending
        if pending and (pending.changes >= pending.presses or now >= pending.deadline):
            # GNOME flipped the setting once per press, or does not handle
            # the key: put the touchpad where the cycle says.
            self.pending = None
            if touchpad_on(self.setting.value) != pending.target[0]:
                self.setting.set(ON if pending.target[0] else OFF)
            self.panel.set(pending.target[1])

    def deadline(self):
        times = [self.pending.deadline] if self.pending else []
        if self.panel.busy():
            times.append(self.clock() + CHILD_POLL)
        return min(times) if times else None

    def close(self):
        self.setting.stop()
        if self.hotkey:
            self.hotkey.close()
            self.hotkey = None
