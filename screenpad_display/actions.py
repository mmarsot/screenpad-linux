"""What the commands do to the panel.

Lighting and darkening it, the connector, capturing, saving and applying the
EDID, and the active flag.
"""

import os

from . import system
from .common import (
    ACTIVE_FLAG,
    BACKLIGHT,
    BL_POWER_OFF,
    BL_POWER_ON,
    DDC_RETRY_DELAY,
    DRM_CLASS,
    DRM_EDID_FIRMWARE,
    EDID_FIRMWARE_PARAM,
    EDID_HEADER,
    EDID_READ_TRIES,
    FIRMWARE_NAME,
    POWER_SETTLE,
    RELIGHT_GAP,
    SAVED_EDID,
    ScreenpadError,
    log,
)
from .edid import describe_edid, fix_checksums, manufacturer
from .lockdown import (
    boot_param,
    cmdline_edid_firmware,
    firmware_for_connector,
    firmware_path,
    install_firmware,
    set_runtime_firmware,
)
from .system import read_bytes, read_text, write_file

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
    system.write_attr(os.path.join(bl, "bl_power"), str(BL_POWER_ON))
    # bl_power may already be 0; a brightness write always reaches
    # update_status, which then sends LIGHT whatever the firmware reports.
    system.write_attr(os.path.join(bl, "brightness"), read_text(os.path.join(bl, "brightness")) or "128")


def light(env, gap=RELIGHT_GAP):
    # LIGHT first, even from bl_power 0 with the firmware reporting unpowered,
    # where a bl_power write alone would send nothing. It leaves the panel dark
    # and the firmware reporting powered.
    darken(env)
    env.sleep(gap)
    # SCREENPAD_POWER=0: picture.
    system.write_attr(env.path(BACKLIGHT, "bl_power"), str(BL_POWER_OFF))


def set_connector(env, target, state):
    system.write_attr(env.path(DRM_CLASS, target.connector, "status"), state)


def wait_for_enabled(env, target, deadline):
    """Wait until a compositor has given the connector a mode. True if it did."""
    path = env.path(DRM_CLASS, target.connector, "enabled")
    while True:
        if read_text(path) == "enabled":
            return True
        if env.clock() >= deadline:
            log("%s is still not enabled by a compositor; lighting the panel anyway" % target.connector)
            return False
        env.sleep(0.5)


def capture_edid(env, target):
    """Read the bridge EDID over the NVIDIA DDC buses and return it checksum-fixed."""
    if not target.buses:
        raise ScreenpadError("no NVIDIA DDC i2c adapters found")
    errors: list[str] = []
    for attempt in range(EDID_READ_TRIES):
        if attempt == 1:
            log(
                "no EDID over DDC yet (%s); retrying for up to %d s"
                % ("; ".join(errors), (EDID_READ_TRIES - 1) * DDC_RETRY_DELAY)
            )
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
        % (EDID_READ_TRIES, hint, "; ".join(errors[-len(target.buses) :]))
    )


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
    system.write_attr(os.path.join(target.debugfs, "edid_override"), edid)


def provide_firmware_edid(env, target, edid):
    """Lockdown path: put the EDID where drm.edid_firmware points the connector."""
    install_firmware(env, edid)
    short = target.short_connector
    if set_runtime_firmware(env, short):
        if firmware_for_connector(cmdline_edid_firmware(env), short) == FIRMWARE_NAME:
            log("re-set %s for this boot" % boot_param(short))
        else:
            log(
                "%s is not on the kernel command line yet; set it for this boot through %s. "
                "'screenpad grub apply' makes it permanent" % (boot_param(short), env.path(DRM_EDID_FIRMWARE))
            )


def check_connector_edid(env, target, edid):
    """Lockdown path: fail clearly when the forced connector got no EDID."""
    got = read_bytes(env.path(DRM_CLASS, target.connector, "edid")) or b""
    if got:
        if got[: len(edid)] != edid:
            log("warning: %s reports an EDID that differs from %s" % (target.connector, firmware_path(env)))
        return
    try:
        set_connector(env, target, "detect")
    except (OSError, ScreenpadError):
        pass
    if firmware_for_connector(cmdline_edid_firmware(env), target.short_connector) != FIRMWARE_NAME:
        why = (
            "The boot parameter is not applied yet, only the runtime value: run "
            "'screenpad grub apply', reboot and try again. If it still has no EDID, "
            "nvidia-drm does not use %s" % EDID_FIRMWARE_PARAM
        )
    else:
        why = "The boot parameter is applied, so nvidia-drm did not take the EDID from it"
    raise ScreenpadError(
        "%s has no EDID after forcing it on, so it is set back to detect. %s. "
        "Check 'journalctl -k -b | grep -i edid' (a 'Requesting EDID firmware' error means "
        "%s is missing) and 'If nvidia-drm does not take it' in docs/display.md"
        % (target.connector, why, firmware_path(env))
    )


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
