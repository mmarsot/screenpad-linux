---
name: display
description: Load before changing the display CLI - screenpad.py and the screenpad_display package (the EDID checksum fix and capture, NVIDIA card, connector, DDC and debugfs discovery, the asus_screenpad backlight light/darken sequence, kernel lockdown and drm.edid_firmware, the active flag, status, the grub subcommand) or its tests (tests/test_screenpad*.py).
---

# Display CLI

Owning files: `screenpad_display/`, one module per concern, and `screenpad.py`, the launcher that runs its `cli.main()`. Installed as `/usr/local/lib/screenpad/`, with `/usr/local/sbin/screenpad` a link to `screenpad.py` there, and run as root by `screenpad.service` (`on --wait 30` at boot, `off` at stop) and the resume hook `systemd/screenpad-sleep` (`replug --if-active`); see the [install](../install/SKILL.md) skill. The modules (map in [docs/development.md](../../../docs/development.md#layout)): `common` (paths, constants, errors), `edid`, `system` (`Env`, file and sysfs access), `lockdown`, `discovery`, `actions`, `commands`, `grub`, `cli`. New code goes in the module of its concern; the imports go one way, `common` at the bottom and `cli` on top.

Read first:

- [docs/display.md](../../../docs/display.md): how the panel is lit, every command, Secure Boot, the manual rollback. It owns the explanation; keep it in step with the code.
- [README.md](../../../README.md#supported-hardware-and-what-was-tested): what was tested on the hardware and what only by unit tests.

## Rules

- **One machine only**: every command starts with `check_dmi` against `SUPPORTED_DMI` (the UX480FD). Adding a model means its DMI id plus checking the connector, the EDID and the WMI calls on that hardware; `install.sh` has its own `PRODUCT` check to change with it.
- **All filesystem access and side effects go through `Env`** (`env.path()` under `root`, `sleep`, `euid`, `i2c_read`, `clock`, `run`), so the tests can run on a fake sysfs, debugfs, procfs and `/etc` tree. Never read or write an absolute path directly. Attribute writes are `system.write_attr(...)`, called through the module (never imported by name), so the panel tests replace it with a fake asus-wmi in one place.
- **The panel's WMI calls are inverted on this unit**: `light()` darkens first (a `LIGHT` write), waits, then writes `bl_power` 4 (`SCREENPAD_POWER=0`, the picture); any later `brightness` write darkens it. The comments above `darken()` and [docs/display.md](../../../docs/display.md#how-it-works) explain why; a change to the sequence needs the panel tests for all four starting states.
- **Lockdown changes the EDID path**: without lockdown the EDID goes through debugfs `edid_override`; in lockdown (Secure Boot) through `drm.edid_firmware` and `/lib/firmware/screenpad/edid.bin`. Both paths are tested; only the lockdown one was checked on the hardware.
- **`on` and `replug` end lit, `off` ends dark**, and `on`/`off` create and remove `/run/screenpad/active`, which the touch bar daemon watches.
- **GRUB is edited only by `screenpad grub apply|remove`**, never by `install.sh`; `remove` after `apply` gives back the original file, and `show` prints the change without making it.
- Errors are one-line messages: raise `ScreenpadError` (`Unsupported` for a refused machine); `main()` prints them and exits 1, or 2 for `Unsupported`.

## Tests

- `tests/test_screenpad.py` covers checksums, DMI refusal, discovery, the exact writes `on`, `off` and `replug` make on unlocked and locked-down kernels, and the command line; `test_screenpad_panel.py` lockdown, the panel through a fake asus-wmi and compositor, and the DDC retry; `test_screenpad_grub.py` `grub`. They share `tests/screenpad_support.py` (`FakeSys`, `FakeAsusWmi`, `FakeClock`, `Base`). No root or hardware. `install.sh` has its own tests (the [install](../install/SKILL.md) skill).
- [docs/development.md](../../../docs/development.md#tests) lists what they cover; keep it in step.
