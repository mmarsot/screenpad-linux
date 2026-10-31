#!/usr/bin/python3 -I
"""Light the first-generation ASUS ScreenPad on the ZenBook UX480FD.

The panel is an HDMI sink on the NVIDIA dGPU behind a Toshiba HDMI-to-MIPI
bridge. It needs three things the stock stack does not do:

1. An EDID the NVIDIA driver accepts. The bridge serves one with a bad CTA
   extension checksum, so we fix every block checksum and hand the result to
   the DRM core: through the debugfs edid_override, or, when the kernel is in
   lockdown (Secure Boot) and debugfs is closed, through the drm.edid_firmware
   parameter and /lib/firmware/screenpad/edid.bin.
2. The connector forced on.
3. Lighting through ASUS WMI once a compositor drives the output, by stepping
   the in-kernel asus_screenpad backlight's bl_power 0 -> 4 (see light()).

Run as root. Commands: on, off, replug, status, capture-edid, grub.
"""

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time

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


# --- EDID ------------------------------------------------------------------


def block_checksum(block):
    return (-sum(block[:EDID_BLOCK - 1])) & 0xFF


def edid_blocks(edid):
    """Validate the EDID framing and return its block count."""
    if len(edid) < EDID_BLOCK or edid[:8] != EDID_HEADER:
        raise ScreenpadError("not an EDID (bad header or shorter than 128 bytes)")
    count = 1 + edid[126]
    if len(edid) < count * EDID_BLOCK:
        raise ScreenpadError(
            "EDID announces %d extension(s) but has only %d bytes" % (count - 1, len(edid))
        )
    return count


def fix_checksums(edid):
    """Return (fixed EDID trimmed to its blocks, [(block, stored, correct)] for each fix)."""
    count = edid_blocks(edid)
    fixed = bytearray(edid[:count * EDID_BLOCK])
    changes = []
    for n in range(count):
        start = n * EDID_BLOCK
        correct = block_checksum(fixed[start:start + EDID_BLOCK])
        stored = fixed[start + EDID_BLOCK - 1]
        if stored != correct:
            fixed[start + EDID_BLOCK - 1] = correct
            changes.append((n, stored, correct))
    return bytes(fixed), changes


def checksums_ok(edid):
    try:
        return not fix_checksums(edid)[1]
    except ScreenpadError:
        return False


def manufacturer(edid):
    word = (edid[8] << 8) | edid[9]
    return "".join(chr(ord("A") - 1 + ((word >> shift) & 0x1F)) for shift in (10, 5, 0))


def detailed_timing(edid):
    """(pixel clock MHz, hactive, vactive, refresh Hz) of the first detailed timing, or None."""
    d = edid[54:72]
    clock = d[0] | (d[1] << 8)
    if clock == 0:
        return None
    hactive = d[2] | ((d[4] >> 4) << 8)
    hblank = d[3] | ((d[4] & 0xF) << 8)
    vactive = d[5] | ((d[7] >> 4) << 8)
    vblank = d[6] | ((d[7] & 0xF) << 8)
    hz = clock * 10000 / ((hactive + hblank) * (vactive + vblank))
    return clock / 100, hactive, vactive, hz


def has_hdmi_vsdb(edid):
    for n in range(1, edid_blocks(edid)):
        block = edid[n * EDID_BLOCK:(n + 1) * EDID_BLOCK]
        if block[0] != 0x02:
            continue
        pos, end = 4, block[2]
        while pos < end and pos < EDID_BLOCK - 1:
            tag, length = block[pos] >> 5, block[pos] & 0x1F
            if tag == 3 and block[pos + 1:pos + 4] == HDMI_OUI:
                return True
            pos += 1 + length
    return False


def describe_edid(edid):
    count = edid_blocks(edid)
    product = edid[10] | (edid[11] << 8)
    parts = ["%s %04x" % (manufacturer(edid), product), "%d block(s)" % count]
    timing = detailed_timing(edid)
    if timing:
        parts.append("%dx%d @ %.2f Hz, %.2f MHz" % (timing[1], timing[2], timing[3], timing[0]))
    parts.append("HDMI VSDB" if has_hdmi_vsdb(edid) else "no HDMI VSDB")
    parts.append("checksums ok" if checksums_ok(edid) else "BAD checksums")
    return ", ".join(parts)


# --- environment -------------------------------------------------------------


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
            raise ScreenpadError(
                "%s: EDID announces %d extensions; only 1 is supported" % (dev, extensions)
            )
        if extensions:
            edid += block(EDID_BLOCK)
        return edid
    finally:
        os.close(fd)


class Env:
    """Filesystem root and side effects, replaceable in tests."""

    def __init__(self, root="/", sleep=time.sleep, euid=os.geteuid,
                 i2c_read=read_edid_i2c, clock=time.monotonic, run=None):
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
    """Write one buffer to an existing sysfs/debugfs attribute in a single write()."""
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
        raise Unsupported(
            "unsupported machine %r %r; this tool only knows the ZenBook UX480FD" % (vendor, product)
        )
    return vendor, product


def require_root(env):
    if env.euid() != 0:
        raise ScreenpadError("must run as root")


# --- lockdown and drm.edid_firmware ----------------------------------------


def lockdown_state(env):
    """The active lockdown mode ("none", "integrity", "confidentiality"), or None if unknown."""
    text = read_text(env.path(LOCKDOWN))
    m = re.search(r"\[(\w+)\]", text or "")
    return m.group(1) if m else None


def locked_down(env):
    return lockdown_state(env) not in (None, "none")


def cmdline_params(text):
    """Split a kernel command line into (name, value) pairs as the kernel does.

    Spaces separate parameters, double quotes group, and "--" ends the kernel's
    part. value is None for a bare flag.
    """
    params = []
    word, quoted = [], False
    for ch in text + " ":
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if word:
                token = "".join(word)
                if token == "--":
                    break
                name, eq, value = token.partition("=")
                params.append((name, value if eq else None))
                word = []
        else:
            word.append(ch)
    return params


def cmdline_edid_firmware(env):
    """The drm.edid_firmware value on the kernel command line, or None."""
    value = None
    for name, val in cmdline_params(read_text(env.path(CMDLINE)) or ""):
        # The kernel treats - and _ in parameter names alike; the last one wins.
        if name.replace("-", "_") == EDID_FIRMWARE_PARAM.replace("-", "_"):
            value = val or ""
    return value


def firmware_for_connector(value, connector):
    """The file drm_edid_load_firmware() picks for a connector name, or None.

    Mirrors drivers/gpu/drm/drm_edid_load.c: entries are comma separated; the
    first "name:file" whose name is a prefix of the connector name wins (the
    name is the bare connector name, e.g. "HDMI-A-2", never "card0-HDMI-A-2");
    otherwise the last entry without a name applies to every connector.
    """
    if not value:
        return None
    fallback = None
    for entry in value.split(","):
        name, colon, path = entry.partition(":")
        if colon:
            if connector.startswith(name):
                return path.rstrip("\n") or None
        elif entry:
            fallback = entry
    return fallback.rstrip("\n") if fallback else None


def firmware_entry(connector):
    return "%s:%s" % (connector, FIRMWARE_NAME)


def boot_param(connector):
    return "%s=%s" % (EDID_FIRMWARE_PARAM, firmware_entry(connector))


def runtime_edid_firmware(env):
    """The live drm edid_firmware value ("" when unset), or None if the kernel lacks it."""
    value = read_text(env.path(DRM_EDID_FIRMWARE))
    if value is None:
        return None
    return "" if value == EDID_FIRMWARE_EMPTY else value


def set_runtime_firmware(env, connector):
    """Make the live drm edid_firmware give the connector our file. True if it changed."""
    current = runtime_edid_firmware(env)
    if current is None:
        raise ScreenpadError("%s missing: this kernel cannot load an EDID from firmware "
                             "(CONFIG_DRM_LOAD_EDID_FIRMWARE)" % env.path(DRM_EDID_FIRMWARE))
    if firmware_for_connector(current, connector) == FIRMWARE_NAME:
        return False
    # Ours goes first, so it wins over any entry whose name is a prefix of
    # ours. Entries giving our file to another connector go: connector numbers
    # follow driver load order, so a boot parameter can name the wrong one.
    others = [e for e in current.split(",")
              if e and not e.startswith(connector + ":") and not e.endswith(":" + FIRMWARE_NAME)]
    try:
        write_attr(env.path(DRM_EDID_FIRMWARE), ",".join([firmware_entry(connector)] + others))
    except OSError as e:
        raise ScreenpadError("cannot set drm edid_firmware at runtime (%s); add the boot "
                             "parameter with 'screenpad grub apply' and reboot" % e)
    return True


def clear_runtime_firmware(env, connector):
    """Drop our entry from the live drm edid_firmware. True if it changed."""
    current = runtime_edid_firmware(env)
    if not current:
        return False
    entries = current.split(",")
    kept = [e for e in entries if e != firmware_entry(connector)]
    if len(kept) == len(entries):
        return False
    write_attr(env.path(DRM_EDID_FIRMWARE), ",".join(kept) or EDID_FIRMWARE_EMPTY)
    return True


def firmware_path(env):
    return env.path(FIRMWARE_DIR, FIRMWARE_NAME)


def install_firmware(env, edid):
    """Keep /lib/firmware/screenpad/edid.bin equal to the EDID in use."""
    path = firmware_path(env)
    if read_bytes(path) != edid:
        write_file(path, edid)
        log("saved %s" % path)


# --- discovery ---------------------------------------------------------------


class Target:
    """The NVIDIA card, its HDMI connector, DDC buses and debugfs directory."""

    def __init__(self, card, pci, device, connector, buses, debugfs):
        self.card = card            # "card0"
        self.pci = pci              # "0000:02:00.0"
        self.device = device        # real path of the PCI device
        self.connector = connector  # "card0-HDMI-A-2"
        self.buses = buses          # [9, 10]
        self.debugfs = debugfs      # ".../dri/0000:02:00.0/HDMI-A-2" or None

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
        raise ScreenpadError(
            "several NVIDIA HDMI connectors (%s); pick one with --connector" % ", ".join(hdmi)
        )
    return hdmi[0] if hdmi else None


def short_pci(pci):
    """'0000:02:00.0' -> '2:00.0', as the NVIDIA i2c adapter names print it."""
    _domain, bus, devfn = pci.split(":")
    return "%x:%s" % (int(bus, 16), devfn)


def find_ddc_buses(env, pci, device):
    buses = []
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
        if parent == device or (adapter_name.startswith(NVIDIA_I2C_NAME)
                                and adapter_name.endswith(suffix)):
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
    minor = card[len("card"):]
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
        ready = (target and (target.debugfs or not need_debugfs)
                 and os.path.isdir(env.path(BACKLIGHT)))
        if ready:
            return target
        if env.clock() >= deadline:
            break
        env.sleep(0.5)
    if not os.path.isdir(env.path(BACKLIGHT)):
        raise ScreenpadError("no %s (is asus_nb_wmi loaded?)" % env.path(BACKLIGHT))
    if not target:
        raise ScreenpadError("no nvidia-drm card with an HDMI connector "
                             "(is nvidia-drm loaded with modeset=1?)")
    raise ScreenpadError("no debugfs edid_override for %s (is debugfs mounted at %s?)"
                         % (target.connector, env.path("sys/kernel/debug")))


# --- actions -----------------------------------------------------------------


# On the UX480FD the ScreenPad shows a picture after asus-wmi sends
# SCREENPAD_POWER=0 and goes dark after any SCREENPAD_LIGHT write, which also
# makes the firmware report it powered. asus-wmi compares the new bl_power with
# the power it reads back: it sends POWER=0 only when bl_power becomes 4 while
# the firmware reports powered, LIGHT when bl_power is 0, and nothing when
# bl_power is 4 and the firmware reports unpowered
# (drivers/platform/x86/asus-wmi.c, update_screenpad_bl_status). The backlight
# core calls that on every brightness write but on a bl_power write only when
# the value changes. Nothing times out: the panel stays lit until the next
# LIGHT write.


def darken(env):
    bl = env.path(BACKLIGHT)
    write_attr(os.path.join(bl, "bl_power"), str(BL_POWER_ON))
    # bl_power may already be 0; a brightness write always reaches
    # update_status, which then sends LIGHT whatever the firmware reports.
    write_attr(os.path.join(bl, "brightness"), read_text(os.path.join(bl, "brightness")) or "128")


def light(env, gap=RELIGHT_GAP):
    # LIGHT first, even from bl_power 0 with the firmware reporting unpowered,
    # where a bl_power write alone would send nothing. It leaves the panel dark
    # and the firmware reporting powered.
    darken(env)
    env.sleep(gap)
    # SCREENPAD_POWER=0: picture.
    write_attr(env.path(BACKLIGHT, "bl_power"), str(BL_POWER_OFF))


def set_connector(env, target, state):
    write_attr(env.path(DRM_CLASS, target.connector, "status"), state)


def wait_for_enabled(env, target, deadline):
    """Wait until a compositor has given the connector a mode. True if it did."""
    path = env.path(DRM_CLASS, target.connector, "enabled")
    while True:
        if read_text(path) == "enabled":
            return True
        if env.clock() >= deadline:
            log("%s is still not enabled by a compositor; lighting the panel anyway"
                % target.connector)
            return False
        env.sleep(0.5)


def capture_edid(env, target):
    """Read the bridge EDID over the NVIDIA DDC buses and return it checksum-fixed."""
    if not target.buses:
        raise ScreenpadError("no NVIDIA DDC i2c adapters found")
    errors = []
    for attempt in range(EDID_READ_TRIES):
        if attempt == 1:
            log("no EDID over DDC yet (%s); retrying for up to %d s"
                % ("; ".join(errors), (EDID_READ_TRIES - 1) * DDC_RETRY_DELAY))
        if attempt:
            env.sleep(DDC_RETRY_DELAY)
        for bus in target.buses:
            dev = env.path("dev", "i2c-%d" % bus)
            try:
                raw = env.i2c_read(dev)
            except OSError as e:
                errors.append("i2c-%d: %s" % (bus, e))
                continue
            if raw[:8] != EDID_HEADER:
                errors.append("i2c-%d: no EDID" % bus)
                continue
            fixed, changes = fix_checksums(raw)
            for block, stored, correct in changes:
                log("i2c-%d: block %d checksum %#04x fixed to %#04x" % (bus, block, stored, correct))
            log("EDID from i2c-%d: %s" % (bus, describe_edid(fixed)))
            return fixed
    hint = ""
    if not any(os.path.exists(env.path("dev", "i2c-%d" % b)) for b in target.buses):
        hint = " (no /dev/i2c-* nodes: modprobe i2c-dev)"
    raise ScreenpadError(
        "could not read an EDID after %d attempts%s: %s; take it from a file instead "
        "with 'capture-edid --from ux580-tsb-fixed.bin' (see docs/install.md)"
        % (EDID_READ_TRIES, hint, "; ".join(errors[-len(target.buses):])))


def save_edid(env, edid):
    path = env.path(SAVED_EDID)
    write_file(path, edid)
    log("saved %s" % path)
    install_firmware(env, edid)


def load_saved_edid(env):
    path = env.path(SAVED_EDID)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return None
    try:
        fixed, changes = fix_checksums(data)
    except ScreenpadError as e:
        raise ScreenpadError("%s: %s; recapture with capture-edid" % (path, e))
    if changes:
        log("%s had bad checksums; fixed in memory" % path)
    return fixed


def edid_for(env, target, gap=RELIGHT_GAP):
    edid = load_saved_edid(env)
    if edid is None:
        # The bridge probably answers DDC only while the panel is lit.
        light(env, gap)
        env.sleep(POWER_SETTLE)
        edid = capture_edid(env, target)
        save_edid(env, edid)
    elif manufacturer(edid) != "TSB":
        log("warning: saved EDID is %s, not the Toshiba bridge" % manufacturer(edid))
    return edid


def apply_edid(env, target, edid):
    write_attr(os.path.join(target.debugfs, "edid_override"), edid)


def provide_firmware_edid(env, target, edid):
    """Lockdown path: put the EDID where drm.edid_firmware points the connector."""
    install_firmware(env, edid)
    short = target.short_connector
    if set_runtime_firmware(env, short):
        if firmware_for_connector(cmdline_edid_firmware(env), short) == FIRMWARE_NAME:
            log("re-set %s for this boot" % boot_param(short))
        else:
            log("%s is not on the kernel command line yet; set it for this boot through %s. "
                "'screenpad grub apply' makes it permanent"
                % (boot_param(short), env.path(DRM_EDID_FIRMWARE)))


def check_connector_edid(env, target, edid):
    """Lockdown path: fail clearly when the forced connector got no EDID."""
    got = read_bytes(env.path(DRM_CLASS, target.connector, "edid")) or b""
    if got:
        if got[:len(edid)] != edid:
            log("warning: %s reports an EDID that differs from %s"
                % (target.connector, firmware_path(env)))
        return
    try:
        set_connector(env, target, "detect")
    except (OSError, ScreenpadError):
        pass
    if firmware_for_connector(cmdline_edid_firmware(env), target.short_connector) != FIRMWARE_NAME:
        why = ("The boot parameter is not applied yet, only the runtime value: run "
               "'screenpad grub apply', reboot and try again. If it still has no EDID, "
               "nvidia-drm does not use %s" % EDID_FIRMWARE_PARAM)
    else:
        why = "The boot parameter is applied, so nvidia-drm did not take the EDID from it"
    raise ScreenpadError(
        "%s has no EDID after forcing it on, so it is set back to detect. %s. "
        "Check 'journalctl -k -b | grep -i edid' (a 'Requesting EDID firmware' error means "
        "%s is missing) and 'If nvidia-drm does not take it' in docs/display.md"
        % (target.connector, why, firmware_path(env)))


def set_active(env, active):
    flag = env.path(ACTIVE_FLAG)
    if active:
        os.makedirs(os.path.dirname(flag), exist_ok=True)
        open(flag, "w").close()
    else:
        try:
            os.unlink(flag)
        except FileNotFoundError:
            pass


# --- commands ----------------------------------------------------------------


def cmd_on(env, args):
    check_dmi(env)
    require_root(env)
    locked = locked_down(env)
    deadline = env.clock() + args.wait
    target = wait_for_target(env, args.wait, args.connector, need_debugfs=not locked)
    edid = edid_for(env, target, args.relight_gap)
    if locked:
        log("kernel lockdown (%s): debugfs is closed, using %s"
            % (lockdown_state(env), EDID_FIRMWARE_PARAM))
        provide_firmware_edid(env, target, edid)
        # Writing "on" only re-probes when the force state changes.
        set_connector(env, target, "detect")
        set_connector(env, target, "on")
        check_connector_edid(env, target, edid)
    else:
        apply_edid(env, target, edid)
        set_connector(env, target, "on")
    # Light only once a compositor drives the output.
    wait_for_enabled(env, target, deadline)
    light(env, args.relight_gap)
    set_active(env, True)
    log("%s forced on and lit; status %s" % (
        target.connector, read_text(env.path(DRM_CLASS, target.connector, "status"))))
    return 0


def cmd_replug(env, args):
    check_dmi(env)
    if args.if_active and not os.path.exists(env.path(ACTIVE_FLAG)):
        return 0
    require_root(env)
    locked = locked_down(env)
    deadline = env.clock() + args.wait
    target = wait_for_target(env, args.wait, args.connector, need_debugfs=not locked)
    edid = edid_for(env, target, args.relight_gap)
    set_connector(env, target, "off")
    env.sleep(1)
    if locked:
        provide_firmware_edid(env, target, edid)
        set_connector(env, target, "on")
        check_connector_edid(env, target, edid)
    else:
        apply_edid(env, target, edid)
        set_connector(env, target, "on")
    wait_for_enabled(env, target, deadline)
    light(env, args.relight_gap)
    set_active(env, True)
    log("%s replugged and lit" % target.connector)
    return 0


def cmd_off(env, args):
    check_dmi(env)
    require_root(env)
    failed = []
    target = discover(env, args.connector)
    if locked_down(env):
        pass  # debugfs is closed; only drm.edid_firmware can have been used
    elif target and target.debugfs:
        try:
            # Exactly 5 bytes: "reset\n" would be taken as an EDID blob.
            write_attr(os.path.join(target.debugfs, "edid_override"), "reset")
        except (OSError, ScreenpadError) as e:
            failed.append("edid_override reset: %s" % e)
    else:
        failed.append("no NVIDIA connector debugfs to reset")
    if target:
        try:
            # For this boot only; a boot parameter comes back at the next boot.
            if clear_runtime_firmware(env, target.short_connector):
                log("removed %s from %s" % (firmware_entry(target.short_connector),
                                            env.path(DRM_EDID_FIRMWARE)))
        except (OSError, ScreenpadError) as e:
            failed.append("edid_firmware: %s" % e)
        try:
            set_connector(env, target, "detect")
        except (OSError, ScreenpadError) as e:
            failed.append("status detect: %s" % e)
    try:
        darken(env)
    except (OSError, ScreenpadError) as e:
        failed.append("darken: %s" % e)
    set_active(env, False)
    for f in failed:
        log("off: %s" % f)
    return 1 if failed else 0


def cmd_capture_edid(env, args):
    check_dmi(env)
    require_root(env)
    if args.source:
        with open(args.source, "rb") as f:
            edid, changes = fix_checksums(f.read())
        for block, stored, correct in changes:
            log("%s: block %d checksum %#04x fixed to %#04x" % (args.source, block, stored, correct))
        log("EDID from %s: %s" % (args.source, describe_edid(edid)))
    else:
        target = wait_for_target(env, args.wait, args.connector,
                                 need_debugfs=not locked_down(env))
        # The bridge probably answers DDC only while the panel is lit.
        light(env, args.relight_gap)
        env.sleep(POWER_SETTLE)
        edid = capture_edid(env, target)
    save_edid(env, edid)
    return 0


def cmd_status(env, args):
    vendor, product = check_dmi(env)
    lock = lockdown_state(env)
    out = []
    out.append("machine:    %s %s" % (vendor, product))
    bl = env.path(BACKLIGHT)
    if os.path.isdir(bl):
        out.append("backlight:  bl_power=%s brightness=%s/%s" % tuple(
            read_text(os.path.join(bl, f)) for f in ("bl_power", "brightness", "max_brightness")))
    else:
        out.append("backlight:  missing (%s)" % bl)
    try:
        target = discover(env, args.connector)
    except ScreenpadError as e:
        target = None
        out.append("connector:  %s" % e)
    if target:
        conn = env.path(DRM_CLASS, target.connector)
        modes = (read_text(os.path.join(conn, "modes")) or "").split()
        out.append("card:       %s (%s)" % (target.card, target.pci))
        out.append("connector:  %s status=%s enabled=%s modes=%s edid=%d bytes" % (
            target.connector, read_text(os.path.join(conn, "status")),
            read_text(os.path.join(conn, "enabled")), ",".join(modes) or "none",
            len(read_bytes(os.path.join(conn, "edid")) or b"")))
        out.append("ddc buses:  %s" % (" ".join("i2c-%d" % b for b in target.buses) or "none"))
        if lock not in (None, "none"):
            debugfs = "%s closed by kernel lockdown" % (target.debugfs or "")
        elif target.debugfs:
            debugfs = target.debugfs
        else:
            debugfs = "not found or not readable (run as root)"
        out.append("debugfs:    %s" % debugfs)
    elif not any(line.startswith("connector:") for line in out):
        out.append("connector:  no nvidia-drm card with an HDMI connector")
    if lock is None:
        out.append("lockdown:   unknown (no %s); using debugfs" % env.path(LOCKDOWN))
    elif lock == "none":
        out.append("lockdown:   none; using debugfs edid_override")
    else:
        out.append("lockdown:   %s; using %s" % (lock, EDID_FIRMWARE_PARAM))

    short = target.short_connector if target else None
    cmdline = cmdline_edid_firmware(env)
    if cmdline is None:
        line = "boot param: none on the kernel command line"
    else:
        line = "boot param: %s=%s on the kernel command line" % (EDID_FIRMWARE_PARAM, cmdline)
        if short and firmware_for_connector(cmdline, short) != FIRMWARE_NAME:
            line += " (but it does not give %s %s)" % (short, FIRMWARE_NAME)
    out.append(line)
    live = runtime_edid_firmware(env)
    if live is None:
        out.append("runtime:    no %s (kernel cannot load EDID firmware)" % env.path(DRM_EDID_FIRMWARE))
    else:
        line = "runtime:    edid_firmware=%s" % (live or "(empty)")
        if short:
            line += "; %s gets %s" % (short, firmware_for_connector(live, short) or "nothing")
        out.append(line)

    try:
        saved = load_saved_edid(env)
        out.append("saved EDID: %s" % (describe_edid(saved) if saved else "none"))
    except (OSError, ScreenpadError) as e:
        saved = None
        out.append("saved EDID: %s" % e)
    firmware = read_bytes(firmware_path(env))
    if firmware is None:
        out.append("firmware:   %s missing" % firmware_path(env))
    else:
        try:
            desc = describe_edid(firmware)
        except ScreenpadError as e:
            desc = str(e)
        if saved:
            desc += "; same as saved EDID" if firmware == saved else "; DIFFERS from saved EDID"
        out.append("firmware:   %s: %s" % (firmware_path(env), desc))
    out.append("active:     %s" % ("yes" if os.path.exists(env.path(ACTIVE_FLAG)) else "no"))
    print("\n".join(out))
    return 0


# --- GRUB ----------------------------------------------------------------------


GRUB_LINE = re.compile(r"""^(\s*%s=)(["']?)(.*?)\2(\s*(?:#.*)?)$""" % GRUB_VAR)


def grub_assignments(lines):
    """[(index, match)] for each GRUB_CMDLINE_LINUX_DEFAULT assignment line."""
    found = []
    for i, line in enumerate(lines):
        m = GRUB_LINE.match(line.rstrip("\n"))
        if m:
            found.append((i, m))
    return found


def grub_set_value(lines, i, m, value):
    quote = m.group(2) or '"'
    end = "\n" if lines[i].endswith("\n") else ""
    lines[i] = "%s%s%s%s%s%s" % (m.group(1), quote, value, quote, m.group(4), end)


def grub_add(text, token):
    """/etc/default/grub text with token added to GRUB_CMDLINE_LINUX_DEFAULT."""
    lines = text.splitlines(keepends=True)
    found = grub_assignments(lines)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + '%s="%s"\n' % (GRUB_VAR, token)
    i, m = found[-1]  # the shell keeps the last assignment
    words = m.group(3).split()
    if token in words:
        return text
    name = token.split("=", 1)[0] + "="
    other = [w for w in words if w.startswith(name)]
    if other:
        raise ScreenpadError("%s already sets %s; edit it by hand" % (GRUB_VAR, " ".join(other)))
    value = m.group(3).rstrip()
    grub_set_value(lines, i, m, value + " " + token if value else token)
    return "".join(lines)


# Ours for any connector name: the name can change between boots.
GRUB_TOKEN = re.compile(r"%s=[^\s:]*:%s" % (re.escape(EDID_FIRMWARE_PARAM), re.escape(FIRMWARE_NAME)))


def grub_has_ours(text):
    return any(GRUB_TOKEN.fullmatch(w)
               for _, m in grub_assignments(text.splitlines()) for w in m.group(3).split())


def grub_remove(text):
    """/etc/default/grub text with our drm.edid_firmware word removed from GRUB_CMDLINE_LINUX_DEFAULT."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(r"\s*(?<!\S)%s(?!\S)" % GRUB_TOKEN.pattern)
    for i, m in grub_assignments(lines):
        value = m.group(3)
        new = pattern.sub("", value)
        if new == value:
            continue
        if not value[:1].isspace():
            new = new.lstrip()
        grub_set_value(lines, i, m, new)
    return "".join(lines)


def grub_overrides(env):
    """grub.d files that replace GRUB_CMDLINE_LINUX_DEFAULT instead of extending it."""
    d = env.path(GRUB_DEFAULT_D)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".cfg"))
    except OSError:
        return []
    found = []
    for name in names:
        for line in (read_text(os.path.join(d, name)) or "").splitlines():
            if (re.match(r"\s*%s=" % GRUB_VAR, line)
                    and not re.search(r"\$\{?%s\b" % GRUB_VAR, line)):
                found.append(os.path.join(d, name))
                break
    return found


def grub_connector(env, args):
    if args.connector:
        return re.sub(r"^card\d+-", "", args.connector)
    target = discover(env)
    if not target:
        raise ScreenpadError("no nvidia-drm HDMI connector found; name it with --connector HDMI-A-2")
    return target.short_connector


def changed_line(old, new):
    """The first line of new that differs from old (grub_add/grub_remove change one)."""
    old_lines = old.splitlines()
    for i, line in enumerate(new.splitlines()):
        if i >= len(old_lines) or line != old_lines[i]:
            return line
    return None


def cmd_grub(env, args):
    check_dmi(env)
    token = boot_param(grub_connector(env, args))
    path = env.path(GRUB_DEFAULT)
    with open(path) as f:
        text = f.read()
    present = grub_has_ours(text)

    if args.action == "show":
        found = grub_assignments(text.splitlines())
        print("parameter:     %s" % token)
        print("file:          %s" % path)
        print("now:           %s" % (found[-1][1].group(0) if found else "(no %s)" % GRUB_VAR))
        if present:
            print("after remove:  %s" % changed_line(text, grub_remove(text)))
        else:
            try:
                print("after apply:   %s" % changed_line(text, grub_add(text, token)))
            except ScreenpadError as e:
                print("cannot apply:  %s" % e)
        print("apply:         sudo screenpad grub apply   (edits that line, runs update-grub)")
        print("remove:        sudo screenpad grub remove  (deletes exactly that parameter, runs update-grub)")
        print("Either one takes effect at the next boot.")
        for f in grub_overrides(env):
            print("warning: %s sets %s itself and may override this" % (f, GRUB_VAR))
        return 0

    require_root(env)
    if args.action == "apply":
        if not os.path.exists(firmware_path(env)):
            raise ScreenpadError("%s missing, so the kernel would have nothing to load; "
                                 "run capture-edid or install.sh first" % firmware_path(env))
        new = grub_add(text, token)
        if new == text:
            log("%s already has %s" % (path, token))
            return 0
        backup = env.path(GRUB_BACKUP)
        if not os.path.exists(backup):
            write_file(backup, text.encode())
            log("saved a copy of %s as %s" % (path, backup))
    else:
        new = grub_remove(text)
        if new == text:
            log("%s has no %s=…:%s; nothing to remove" % (path, EDID_FIRMWARE_PARAM, FIRMWARE_NAME))
            return 0
    write_file(path, new.encode(), mode=os.stat(path).st_mode & 0o7777)
    log("%s: %s" % (path, changed_line(text, new)))
    for f in grub_overrides(env):
        log("warning: %s sets %s itself and may override this" % (f, GRUB_VAR))
    env.run(["update-grub"])
    log("reboot for it to take effect; 'screenpad status' then shows the boot parameter")
    return 0


def seconds(text):
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def parse_args(argv):
    p = argparse.ArgumentParser(prog="screenpad", description=__doc__.split("\n\n")[0])
    p.add_argument("--connector", help="NVIDIA connector, e.g. HDMI-A-2 (default: the only one)")
    sub = p.add_subparsers(dest="command", required=True)
    on = sub.add_parser("on", help="load the fixed EDID, force the connector on, light the panel")
    off = sub.add_parser("off", help="reset the EDID override, re-detect, darken the panel")
    replug = sub.add_parser("replug", help="re-plug the connector with the EDID and relight the panel")
    replug.add_argument("--if-active", action="store_true",
                        help="do nothing unless 'on' ran since boot (for the resume hook)")
    sub.add_parser("status", help="show what was found; needs no root")
    cap = sub.add_parser("capture-edid", help="read the EDID over DDC, fix checksums, save it")
    cap.add_argument("--from", dest="source", metavar="FILE",
                     help="take the EDID from FILE instead of DDC")
    for s in (on, replug, cap):
        s.add_argument("--wait", type=float, default=10, metavar="SECONDS",
                       help="how long to wait for the card and backlight and, for on and "
                            "replug, a compositor to enable the connector (default 10)")
        s.add_argument("--relight-gap", type=seconds, default=RELIGHT_GAP, metavar="SECONDS",
                       help="pause between the two bl_power writes that light the panel "
                            "(default %g)" % RELIGHT_GAP)
    grub = sub.add_parser("grub", help="show, apply or remove the drm.edid_firmware boot parameter")
    grub.add_argument("action", nargs="?", choices=("show", "apply", "remove"), default="show")
    return p.parse_args(argv)


COMMANDS = {
    "on": cmd_on,
    "off": cmd_off,
    "replug": cmd_replug,
    "status": cmd_status,
    "capture-edid": cmd_capture_edid,
    "grub": cmd_grub,
}


def main(argv=None, env=None):
    args = parse_args(argv)
    env = env or Env()
    try:
        return COMMANDS[args.command](env, args)
    except Unsupported as e:
        log(str(e))
        return 2
    except (OSError, ScreenpadError, subprocess.CalledProcessError) as e:
        log(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
