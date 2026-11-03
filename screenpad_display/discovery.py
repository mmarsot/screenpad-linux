"""Finding the NVIDIA card, the ScreenPad's connector, its DDC bus and its debugfs directory."""

import os
import re

from .common import (
    BACKLIGHT,
    DEBUGFS_DRI,
    DRM_CLASS,
    I2C_DEVICES,
    NVIDIA_DRIVER,
    NVIDIA_I2C_NAME,
    NVIDIA_VENDOR,
    ScreenpadError,
)
from .system import read_text


class Target:
    """The NVIDIA card, its HDMI connector, DDC buses and debugfs directory."""

    def __init__(self, card, pci, device, connector, buses, debugfs):
        self.card = card  # "card0"
        self.pci = pci  # "0000:02:00.0"
        self.device = device  # real path of the PCI device
        self.connector = connector  # "card0-HDMI-A-2"
        self.buses = buses  # [9, 10]
        self.debugfs = debugfs  # ".../dri/0000:02:00.0/HDMI-A-2" or None

    @property
    def short_connector(self):
        return self.connector.split("-", 1)[1]


def find_nvidia_card(env):
    """Return (card, pci address, real device path) of the nvidia-driven DRM card."""
    drm = env.path(DRM_CLASS)
    try:
        names = sorted(os.listdir(drm))
    except OSError:
        names = []
    for name in names:
        if not re.fullmatch(r"card\d+", name):
            continue
        device = os.path.join(drm, name, "device")
        if read_text(os.path.join(device, "vendor")) != NVIDIA_VENDOR:
            continue
        if os.path.basename(os.path.realpath(os.path.join(device, "driver"))) != NVIDIA_DRIVER:
            continue
        real = os.path.realpath(device)
        return name, os.path.basename(real), real
    return None


def find_connector(env, card, wanted=None):
    drm = env.path(DRM_CLASS)
    try:
        names = os.listdir(drm)
    except OSError:
        return None
    hdmi = sorted(n for n in names if n.startswith(card + "-HDMI-"))
    if wanted:
        full = wanted if wanted.startswith(card + "-") else "%s-%s" % (card, wanted)
        return full if full in names else None
    if len(hdmi) > 1:
        raise ScreenpadError("several NVIDIA HDMI connectors (%s); pick one with --connector" % ", ".join(hdmi))
    return hdmi[0] if hdmi else None


def short_pci(pci):
    """'0000:02:00.0' -> '2:00.0', as the NVIDIA i2c adapter names print it."""
    _domain, bus, devfn = pci.split(":")
    return "%x:%s" % (int(bus, 16), devfn)


def find_ddc_buses(env, pci, device):
    buses: list[int] = []
    root = env.path(I2C_DEVICES)
    try:
        names = os.listdir(root)
    except OSError:
        return buses
    suffix = " at " + short_pci(pci)
    for name in names:
        m = re.fullmatch(r"i2c-(\d+)", name)
        if not m:
            continue
        adapter = os.path.join(root, name)
        adapter_name = read_text(os.path.join(adapter, "name")) or ""
        parent = os.path.dirname(os.path.realpath(adapter))
        if parent == device or (adapter_name.startswith(NVIDIA_I2C_NAME) and adapter_name.endswith(suffix)):
            buses.append(int(m.group(1)))
    return sorted(buses)


def find_debugfs_dir(env, card, pci):
    """Find the debugfs DRM directory of the card; None if absent or unreadable."""
    dri = env.path(DEBUGFS_DRI)
    try:
        entries = sorted(os.listdir(dri))
    except OSError:
        return None
    # Newer kernels name it after the device; older ones after the minor.
    if pci in entries:
        return os.path.join(dri, pci)
    for entry in entries:
        name = read_text(os.path.join(dri, entry, "name")) or ""
        if re.search(r"\bdev=%s\b" % re.escape(pci), name):
            return os.path.join(dri, entry)
    minor = card[len("card") :]
    if minor in entries:
        return os.path.join(dri, minor)
    return None


def discover(env, connector=None):
    """Return a Target, or None while the NVIDIA card or connector is missing."""
    card = find_nvidia_card(env)
    if not card:
        return None
    name, pci, device = card
    conn = find_connector(env, name, connector)
    if not conn:
        return None
    buses = find_ddc_buses(env, pci, device)
    debugfs = None
    base = find_debugfs_dir(env, name, pci)
    if base:
        candidate = os.path.join(base, conn.split("-", 1)[1])
        if os.path.exists(os.path.join(candidate, "edid_override")):
            debugfs = candidate
    return Target(name, pci, device, conn, buses, debugfs)


def wait_for_target(env, timeout, connector=None, need_debugfs=True):
    deadline = env.clock() + timeout
    while True:
        target = discover(env, connector)
        ready = target and (target.debugfs or not need_debugfs) and os.path.isdir(env.path(BACKLIGHT))
        if ready:
            return target
        if env.clock() >= deadline:
            break
        env.sleep(0.5)
    if not os.path.isdir(env.path(BACKLIGHT)):
        raise ScreenpadError("no %s (is asus_nb_wmi loaded?)" % env.path(BACKLIGHT))
    if not target:
        raise ScreenpadError("no nvidia-drm card with an HDMI connector (is nvidia-drm loaded with modeset=1?)")
    raise ScreenpadError(
        "no debugfs edid_override for %s (is debugfs mounted at %s?)" % (target.connector, env.path("sys/kernel/debug"))
    )
