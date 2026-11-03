"""The on, replug, off, light, dark, capture-edid and status commands."""

import os

from . import system
from .actions import (
    apply_edid,
    capture_edid,
    check_connector_edid,
    darken,
    edid_for,
    light,
    load_saved_edid,
    provide_firmware_edid,
    save_edid,
    set_active,
    set_connector,
    wait_for_enabled,
)
from .common import (
    ACTIVE_FLAG,
    BACKLIGHT,
    BL_POWER_ON,
    DRM_CLASS,
    DRM_EDID_FIRMWARE,
    EDID_FIRMWARE_PARAM,
    FIRMWARE_NAME,
    LOCKDOWN,
    POWER_SETTLE,
    ScreenpadError,
    log,
)
from .discovery import discover, wait_for_target
from .edid import describe_edid, fix_checksums
from .lockdown import (
    clear_runtime_firmware,
    cmdline_edid_firmware,
    firmware_entry,
    firmware_for_connector,
    firmware_path,
    lockdown_state,
    locked_down,
    runtime_edid_firmware,
)
from .system import check_dmi, read_bytes, read_text, require_root


def cmd_on(env, args):
    check_dmi(env)
    require_root(env)
    locked = locked_down(env)
    deadline = env.clock() + args.wait
    target = wait_for_target(env, args.wait, args.connector, need_debugfs=not locked)
    edid = edid_for(env, target, args.relight_gap)
    if locked:
        log("kernel lockdown (%s): debugfs is closed, using %s" % (lockdown_state(env), EDID_FIRMWARE_PARAM))
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
    log(
        "%s forced on and lit; status %s"
        % (target.connector, read_text(env.path(DRM_CLASS, target.connector, "status")))
    )
    return 0


def cmd_replug(env, args):
    check_dmi(env)
    if args.if_active and not os.path.exists(env.path(ACTIVE_FLAG)):
        return 0
    require_root(env)
    # bl_power 0 while active: 'screenpad dark' turned it off (on and replug
    # end at 4), so --keep-dark leaves it dark.
    keep_dark = args.keep_dark and is_dark(env)
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
    if keep_dark:
        darken(env)
    else:
        light(env, args.relight_gap)
    set_active(env, True)
    log("%s replugged and %s" % (target.connector, "kept dark" if keep_dark else "lit"))
    return 0


def is_dark(env):
    return read_text(env.path(BACKLIGHT, "bl_power")) == str(BL_POWER_ON)


def panel_access(env, action):
    """Run action(); a refused backlight write says how to get the right."""
    try:
        action()
    except PermissionError as e:
        if env.euid() == 0:
            raise
        raise ScreenpadError(
            "%s: run as root, or let the input group write the backlight with the udev rule "
            "install.sh installs (70-screenpad-backlight.rules)" % e
        )


def cmd_light(env, args):
    """Light the panel again after 'dark', leaving the connector alone."""
    check_dmi(env)
    if not os.path.exists(env.path(ACTIVE_FLAG)):
        raise ScreenpadError("the ScreenPad is off; 'screenpad on' first")
    panel_access(env, lambda: light(env, args.relight_gap))
    log("lit")
    return 0


def cmd_dark(env, args):
    """Darken the panel, leaving the connector and the active flag alone."""
    check_dmi(env)
    panel_access(env, lambda: darken(env))
    log("darkened")
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
            system.write_attr(os.path.join(target.debugfs, "edid_override"), "reset")
        except (OSError, ScreenpadError) as e:
            failed.append("edid_override reset: %s" % e)
    else:
        failed.append("no NVIDIA connector debugfs to reset")
    if target:
        try:
            # For this boot only; a boot parameter comes back at the next boot.
            if clear_runtime_firmware(env, target.short_connector):
                log("removed %s from %s" % (firmware_entry(target.short_connector), env.path(DRM_EDID_FIRMWARE)))
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
        target = wait_for_target(env, args.wait, args.connector, need_debugfs=not locked_down(env))
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
        out.append(
            "backlight:  bl_power=%s brightness=%s/%s"
            % tuple(read_text(os.path.join(bl, f)) for f in ("bl_power", "brightness", "max_brightness"))
        )
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
        out.append(
            "connector:  %s status=%s enabled=%s modes=%s edid=%d bytes"
            % (
                target.connector,
                read_text(os.path.join(conn, "status")),
                read_text(os.path.join(conn, "enabled")),
                ",".join(modes) or "none",
                len(read_bytes(os.path.join(conn, "edid")) or b""),
            )
        )
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
