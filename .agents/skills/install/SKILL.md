---
name: install
description: Load before changing install.sh (install, uninstall, purge), the systemd units and resume hook in systemd/, the udev rule, the initramfs hook, or any installed path - and whenever a program, unit or extension file is added, renamed or moved.
---

# Install, units and hooks

Owning files: `install.sh`, `systemd/screenpad.service` (boot unit), `systemd/screenpad-sleep` (resume hook), `systemd/screenpad-touchbar.service` and `systemd/screenpad-agents.service` (user units), `udev/70-screenpad-uinput.rules`, `initramfs/screenpad`.

Read first:

- [docs/install.md](../../../docs/install.md): installing, updating and removing, step by step, and what `install.sh` installs where. It is the user-facing contract; keep it in step.
- [docs/display.md](../../../docs/display.md#rolling-back-by-hand): undoing `screenpad on` by hand and keeping the boot unit from running.

## Rules

- **The installed paths are a contract**: the variables at the top of `install.sh` (`BIN`, `LIB`, `TOUCHBAR`, `AGENTS`, `UNIT`, `HOOK`, `TOUCHBAR_UNIT`, `AGENTS_UNIT`, `EXTENSION`, `UDEV_RULE`, `FIRMWARE`, `INITRAMFS_HOOK`, `DOC`, `STATE`) must match the `ExecStart`/`ExecStop` lines of the units, the resume hook, the extension's `CLI_FALLBACK`, the docs and the tests. Moving one updates all of them in the same change, and `uninstall` must remove the new and the old location so an update leaves nothing behind.
- **`install.sh` is POSIX `sh`, run as root, and refuses another machine** (`PRODUCT`, the same model as `SUPPORTED_DMI` in `screenpad_display/common.py`). Every path is prefixed with `DESTDIR` (`$R`) so the tests can install into a fake root.
- **It never edits GRUB and never starts the panel**: it enables `screenpad.service` without starting it, and `uninstall` refuses while GRUB still loads the EDID file (`screenpad grub remove` first). The touch bar is turned on per user with `screenpad-touchbar on`, never by `install.sh`. The agent app's unit is installed but never enabled, and starts only once its config exists (`ConditionPathExists`); the app itself refuses to run without it.
- **The saved EDID survives**: `uninstall` keeps `/var/lib/screenpad`; only `purge` deletes it. An update (`install.sh` again) keeps the EDID, the boot parameter and the user's touch bar config.
- **The initramfs hook is installed only when nvidia-drm is in the initramfs**, so the EDID file is there when the driver probes early; `uninstall` removes it and rebuilds the initramfs.
- **The touch bar's user unit belongs to the graphical session** (`PartOf=graphical-session.target`), restarts on failure, and removes the state file on stop so the extension stops drawing at once. The udev rule tags `/dev/uinput` `uaccess`, so the user at the seat can create the virtual devices; the daemon needs group `input` for the touchpad node.
- **The programs live in `LIB`** (`/usr/local/lib/screenpad/`): `install_programs` copies `screenpad.py`, `touchbar.py`, `agents.py` and every module of `screenpad_display/`, `screenpad_touchbar/` and `screenpad_agents/` there, replacing each package directory whole so no old module lingers, and makes `BIN`, `TOUCHBAR` and `AGENTS` relative links to the three scripts. The links keep the command paths the units, the resume hook and the extension use; `uninstall` removes the links and `LIB`.
- New files to install get a line in `install_all`, their removal in `uninstall_all`, and a check in the `install.sh` tests. A new module in a package needs none of that.

## Tests

- The `install.sh` tests in `tests/test_install.py` run it against a fake root (`DESTDIR`) with stub `id`, `systemctl`, `lsinitramfs`, `update-initramfs` and `udevadm` first on `PATH`: install, the programs (links, packages, and the installed commands running in isolated mode, the extension's `CLI_FALLBACK` among them), the touch bar's files, the agent app installed off, the initramfs case, uninstall refusing while GRUB loads the EDID, uninstall and purge. They are skipped when run as root.
- Never run `install.sh` on the machine you work on to test a change; the fake root covers it.
