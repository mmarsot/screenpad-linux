"""The zone split: which contacts are the bar's, the apps surface's, and which go to the virtual touchpad."""

from .codes import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TOOL_TYPE,
    ABS_MT_TRACKING_ID,
    ABS_X,
    ABS_Y,
    BTN_LEFT,
    BTN_TOUCH,
    EV_ABS,
    EV_KEY,
    EV_MSC,
    EV_SYN,
    FINGER_TOOLS,
    MSC_TIMESTAMP,
    MT_TOOL_PALM,
    SYN_DROPPED,
    SYN_REPORT,
)
from .common import PANEL_H, PANEL_W


class Axes:
    """Maps digitizer units to panel pixels."""

    def __init__(self, xmin, xmax, ymin, ymax):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax

    def panel(self, x, y):
        return (
            (x - self.xmin) * PANEL_W / (self.xmax - self.xmin),
            (y - self.ymin) * PANEL_H / (self.ymax - self.ymin),
        )


PAD, BAR, SURFACE, IGNORED = "pad", "bar", "surface", "ignored"
# What feed() returns for the SYN_REPORT that ends a SYN_DROPPED gap.
RESYNC = "resync"


class Slot:
    __slots__ = ("id", "tool", "x", "y")

    def __init__(self):
        self.id, self.x, self.y, self.tool = -1, 0, 0, 0


class Contact:
    __slots__ = ("id", "kind", "order", "tool", "x", "x0", "y", "y0")

    def __init__(self, id, kind, x, y, tool, order):
        self.id, self.kind, self.x, self.y, self.tool, self.order = id, kind, x, y, tool, order
        self.x0, self.y0 = x, y  # where it went down


class Splitter:
    """Splits the source touchpad's frames between the bar, the apps surface
    and the virtual pad.

    zone(px, py) says what a new contact at panel pixels px, py is: BAR,
    SURFACE (below the bar while the apps page shows) or PAD.
    feed() takes the source's events one by one. At each SYN_REPORT it returns
    (pad, bar, surface), or RESYNC after a SYN_DROPPED gap (then call
    resync()): the events for the virtual touchpad (whole frames, ending in
    SYN_REPORT, or nothing if the pad did not change), then the bar's and the
    surface's contact events, ("begin"|"move"|"end"|"cancel", slot, px, py)
    in panel pixels. Only PAD contacts reach the virtual touchpad.
    Contacts keep the kind they started with: a bar contact that slides down
    stays out of the pad, and a pad contact that slides up stays a pad contact,
    so the pointer never sees a touch appear or vanish mid-stroke; a zone that
    changes (the bar growing, the page turning) applies to new contacts only.
    The one change is handoff(): a SURFACE contact the apps page decided is
    the pointer's (decide.py) becomes a PAD contact, before the virtual
    touchpad ever saw it.
    """

    def __init__(self, axes, nslots, zone, cur=0):
        self.axes = axes
        self.zone = zone  # zone(px, py) -> BAR, SURFACE or PAD
        self.slots = [Slot() for _ in range(nslots)]
        # The kernel sends ABS_MT_SLOT only when it changes, so a reader
        # starts from the value EVIOCGABS gives at open.
        self.cur = cur
        self.contacts = {}  # slot -> Contact
        self.sent = {}  # slot -> (id, x, y, tool) on the virtual pad
        self.sent_tool = None
        self.sent_left = False
        self.src_left = False
        self.left_changed = False
        self.timestamp = None
        self.dropping = False
        self.order = 0

    def feed(self, type, code, value):
        if self.dropping:
            if type == EV_SYN and code == SYN_REPORT:
                self.dropping = False
                return RESYNC
            return None
        if type == EV_SYN:
            if code == SYN_REPORT:
                return self.frame()
            if code == SYN_DROPPED:
                self.dropping = True
            return None
        slot = self.slots[self.cur] if 0 <= self.cur < len(self.slots) else None
        if type == EV_ABS:
            if code == ABS_MT_SLOT:
                self.cur = value
            elif slot is None:
                pass
            elif code == ABS_MT_TRACKING_ID:
                slot.id = value
            elif code == ABS_MT_POSITION_X:
                slot.x = value
            elif code == ABS_MT_POSITION_Y:
                slot.y = value
            elif code == ABS_MT_TOOL_TYPE:
                slot.tool = value
        elif type == EV_KEY and code == BTN_LEFT:
            self.src_left = bool(value)
            self.left_changed = True
        elif type == EV_MSC and code == MSC_TIMESTAMP:
            self.timestamp = value
        return None

    def resync(self, ids, xs, ys, tools, left, cur):
        """Take the device state after a SYN_DROPPED, as EVIOCGMTSLOTS reads it."""
        for s, slot in enumerate(self.slots):
            slot.id, slot.x, slot.y, slot.tool = ids[s], xs[s], ys[s], tools[s]
        self.cur = cur
        if bool(left) != self.src_left:
            self.src_left = bool(left)
            self.left_changed = True
        return self.frame()

    def frame(self):
        bar: list[tuple] = []
        surface: list[tuple] = []
        # The events of the contacts the daemon handles itself, by kind.
        out = {BAR: bar, SURFACE: surface}
        for s, slot in enumerate(self.slots):
            c = self.contacts.get(s)
            if c and c.id != slot.id:
                del self.contacts[s]
                if c.kind in out:
                    out[c.kind].append(("end", s) + self.axes.panel(c.x, c.y))
                c = None
            if slot.id == -1:
                continue
            if c is None:
                px, py = self.axes.panel(slot.x, slot.y)
                kind = self.zone(px, py)
                if kind != PAD and slot.tool == MT_TOOL_PALM:
                    kind = IGNORED
                elif kind != PAD:
                    out[kind].append(("begin", s, px, py))
                self.order += 1
                self.contacts[s] = Contact(slot.id, kind, slot.x, slot.y, slot.tool, self.order)
                continue
            moved = (slot.x, slot.y) != (c.x, c.y)
            c.x, c.y = slot.x, slot.y
            if c.kind in out and slot.tool == MT_TOOL_PALM:
                out[c.kind].append(("cancel", s) + self.axes.panel(c.x, c.y))
                c.kind = IGNORED
            elif c.kind in out and moved:
                out[c.kind].append(("move", s) + self.axes.panel(c.x, c.y))
            c.tool = slot.tool
        return self.pad_frames(), bar, surface

    def pad_down(self):
        """Whether a touchpad contact is down."""
        return any(c.kind == PAD for c in self.contacts.values())

    def pressing(self):
        """Whether the pad's button is pressed (a physical click)."""
        return self.src_left

    def handoff(self, slots):
        """Make these SURFACE contacts PAD contacts: the virtual touchpad's
        frames that start them where they went down, then move them to
        where they are, so libinput sees all their motion (and so no tap),
        with the button if it is pressed. Other slots are left alone."""
        moved = [s for s in slots if s in self.contacts and self.contacts[s].kind == SURFACE]
        if not moved:
            return []
        for s in moved:
            self.contacts[s].kind = PAD
        if self.src_left and not self.sent_left:
            self.left_changed = True
        start = self.wanted()
        for s in moved:
            c = self.contacts[s]
            start[s] = (c.id, c.x0, c.y0, c.tool)
        # The first frame is made up: no timestamp of its own.
        timestamp, self.timestamp = self.timestamp, None
        events = self.pad_frames(start)
        self.timestamp = timestamp
        return events + self.pad_frames()

    def wanted(self):
        return {s: (c.id, c.x, c.y, c.tool) for s, c in self.contacts.items() if c.kind == PAD}

    def pad_in_band(self, top, bottom):
        """Whether a touchpad contact is now between rows top and bottom:
        the band a growing bar would cover. It would stay a touchpad
        contact, but the card would be drawn under the finger."""
        return any(top <= self.axes.panel(c.x, c.y)[1] < bottom for c in self.contacts.values() if c.kind == PAD)

    def pad_frames(self, wanted=None):
        wanted = self.wanted() if wanted is None else wanted
        events = []
        # A slot that changes from one contact to another ends in a frame of
        # its own: libinput counts touches by TRACKING_ID -1.
        replaced = [s for s in self.sent if s in wanted and wanted[s][0] != self.sent[s][0]]
        if replaced:
            interim = {s: v for s, v in wanted.items() if s not in replaced and s in self.sent}
            events += self._diff(interim, False)
        events += self._diff(wanted, True)
        return events

    def _diff(self, wanted, final):
        ev = []
        for s in sorted(set(self.sent) | set(wanted)):
            old, new = self.sent.get(s), wanted.get(s)
            if old == new:
                continue
            ev.append((EV_ABS, ABS_MT_SLOT, s))
            if new is None:
                ev.append((EV_ABS, ABS_MT_TRACKING_ID, -1))
                del self.sent[s]
                continue
            if old is None or old[0] != new[0]:
                ev += [
                    (EV_ABS, ABS_MT_TRACKING_ID, new[0]),
                    (EV_ABS, ABS_MT_TOOL_TYPE, new[3]),
                    (EV_ABS, ABS_MT_POSITION_X, new[1]),
                    (EV_ABS, ABS_MT_POSITION_Y, new[2]),
                ]
            else:
                for i, code in ((1, ABS_MT_POSITION_X), (2, ABS_MT_POSITION_Y), (3, ABS_MT_TOOL_TYPE)):
                    if old[i] != new[i]:
                        ev.append((EV_ABS, code, new[i]))
            self.sent[s] = new
        n = len(self.sent)
        tool = FINGER_TOOLS[min(n, len(FINGER_TOOLS)) - 1] if n else None
        if tool != self.sent_tool:
            if (self.sent_tool is None) != (tool is None):
                ev.append((EV_KEY, BTN_TOUCH, 1 if tool else 0))
            if self.sent_tool is not None:
                ev.append((EV_KEY, self.sent_tool, 0))
            if tool is not None:
                ev.append((EV_KEY, tool, 1))
            self.sent_tool = tool
        if ev and self.sent:
            # Pointer emulation follows the oldest contact, as input_mt does.
            s = min(self.sent, key=lambda s: self.contacts[s].order if s in self.contacts else 0)
            ev += [(EV_ABS, ABS_X, self.sent[s][1]), (EV_ABS, ABS_Y, self.sent[s][2])]
        if final:
            ev += self._left()
        if not ev:
            return ev
        if final and self.timestamp is not None:
            ev.append((EV_MSC, MSC_TIMESTAMP, self.timestamp))
        ev.append((EV_SYN, SYN_REPORT, 0))
        return ev

    def _left(self):
        # A click counts on the desktop only while a pad contact is down, and
        # its release goes out only if its press did.
        if not self.left_changed:
            return []
        self.left_changed = False
        if self.src_left and not self.sent_left and self.sent:
            self.sent_left = True
            return [(EV_KEY, BTN_LEFT, 1)]
        if not self.src_left and self.sent_left:
            self.sent_left = False
            return [(EV_KEY, BTN_LEFT, 0)]
        return []

    def release_all(self):
        """End everything: the pad's frames, and cancels for the bar's and the surface's contacts."""
        bar, surface = (
            [("cancel", s) + self.axes.panel(c.x, c.y) for s, c in sorted(self.contacts.items()) if c.kind == kind]
            for kind in (BAR, SURFACE)
        )
        for c in self.contacts.values():
            c.kind = IGNORED
        self.src_left = False
        self.left_changed = True
        return self.pad_frames({}), bar, surface
