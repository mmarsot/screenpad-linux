"""Kernel lockdown and the drm.edid_firmware path.

The kernel command line, the runtime parameter and
/lib/firmware/screenpad/edid.bin, used when debugfs is closed.
"""

import re

from . import system
from .common import (
    CMDLINE,
    DRM_EDID_FIRMWARE,
    EDID_FIRMWARE_EMPTY,
    EDID_FIRMWARE_PARAM,
    FIRMWARE_DIR,
    FIRMWARE_NAME,
    LOCKDOWN,
    ScreenpadError,
    log,
)
from .system import read_bytes, read_text, write_file


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
    word: list[str] = []
    quoted = False
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
        raise ScreenpadError(
            "%s missing: this kernel cannot load an EDID from firmware "
            "(CONFIG_DRM_LOAD_EDID_FIRMWARE)" % env.path(DRM_EDID_FIRMWARE)
        )
    if firmware_for_connector(current, connector) == FIRMWARE_NAME:
        return False
    # Ours goes first, so it wins over any entry whose name is a prefix of
    # ours. Entries giving our file to another connector go: connector numbers
    # follow driver load order, so a boot parameter can name the wrong one.
    others = [
        e for e in current.split(",") if e and not e.startswith(connector + ":") and not e.endswith(":" + FIRMWARE_NAME)
    ]
    try:
        system.write_attr(env.path(DRM_EDID_FIRMWARE), ",".join([firmware_entry(connector)] + others))
    except OSError as e:
        raise ScreenpadError(
            "cannot set drm edid_firmware at runtime (%s); add the boot "
            "parameter with 'screenpad grub apply' and reboot" % e
        )
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
    system.write_attr(env.path(DRM_EDID_FIRMWARE), ",".join(kept) or EDID_FIRMWARE_EMPTY)
    return True


def firmware_path(env):
    return env.path(FIRMWARE_DIR, FIRMWARE_NAME)


def install_firmware(env, edid):
    """Keep /lib/firmware/screenpad/edid.bin equal to the EDID in use."""
    path = firmware_path(env)
    if read_bytes(path) != edid:
        write_file(path, edid)
        log("saved %s" % path)
