"""The screenpad command line: its arguments and main()."""

import argparse
import subprocess

from . import __doc__ as PACKAGE_DOC
from .commands import cmd_capture_edid, cmd_off, cmd_on, cmd_replug, cmd_status
from .common import RELIGHT_GAP, ScreenpadError, Unsupported, log
from .grub import cmd_grub
from .system import Env


def seconds(text):
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def parse_args(argv):
    p = argparse.ArgumentParser(prog="screenpad", description=PACKAGE_DOC.split("\n\n")[0])
    p.add_argument("--connector", help="NVIDIA connector, e.g. HDMI-A-2 (default: the only one)")
    sub = p.add_subparsers(dest="command", required=True)
    on = sub.add_parser("on", help="load the fixed EDID, force the connector on, light the panel")
    sub.add_parser("off", help="reset the EDID override, re-detect, darken the panel")
    replug = sub.add_parser("replug", help="re-plug the connector with the EDID and relight the panel")
    replug.add_argument(
        "--if-active", action="store_true", help="do nothing unless 'on' ran since boot (for the resume hook)"
    )
    sub.add_parser("status", help="show what was found; needs no root")
    cap = sub.add_parser("capture-edid", help="read the EDID over DDC, fix checksums, save it")
    cap.add_argument("--from", dest="source", metavar="FILE", help="take the EDID from FILE instead of DDC")
    for s in (on, replug, cap):
        s.add_argument(
            "--wait",
            type=float,
            default=10,
            metavar="SECONDS",
            help="how long to wait for the card and backlight and, for on and "
            "replug, a compositor to enable the connector (default 10)",
        )
        s.add_argument(
            "--relight-gap",
            type=seconds,
            default=RELIGHT_GAP,
            metavar="SECONDS",
            help="pause between the two bl_power writes that light the panel (default %g)" % RELIGHT_GAP,
        )
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
