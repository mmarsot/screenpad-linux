# Development

## Layout

| Path | What it is |
|---|---|
| `screenpad.py` | the display CLI, installed as `/usr/local/sbin/screenpad` |
| `touchbar.py` | the touch bar daemon and CLI, installed as `/usr/local/bin/screenpad-touchbar` |
| `gnome-shell/screenpad-touchbar@screenpad/` | the GNOME Shell extension that draws the bar |
| `systemd/` | the boot unit, the resume hook and the touch bar's user unit |
| `udev/` | the `/dev/uinput` access rule |
| `initramfs/` | the initramfs hook, used only when nvidia-drm is in the initramfs |
| `install.sh` | installs, uninstalls and purges (run as root) |
| `tests/` | unit tests; `touchbar-v1-default.toml` and `touchbar-v2-default.toml` are configs earlier versions wrote, for the migration tests |
| `docs/` | this documentation; `docs/images/` holds the screenshots |

Both programs are single-file Python 3 (3.11 or newer, standard library only), so they can be run from the checkout: `./screenpad.py status`, `./touchbar.py check`.

## Tests

```sh
python3 -m unittest discover -s tests
```

They need no root and no hardware. `tests/test_screenpad.py` covers the display CLI and `install.sh`, `tests/test_touchbar.py` the touch bar. The display tests build a fake sysfs/debugfs/procfs/`/etc` tree in a temporary directory and replace the i2c reads and `update-grub`. They cover:

- checksum fixing: a synthetic EDID with a wrong CTA checksum, and the known single-byte `0x3e` → `0x6f` case;
- DMI refusal;
- card/connector/i2c/debugfs discovery, including swapped card numbers and debugfs directories named by minor;
- the exact writes `on`, `off` and `replug` make, on unlocked and locked-down kernels;
- the panel, through a fake asus-wmi (its `update_screenpad_bl_status()` choice, the backlight core's skip of unchanged `bl_power`, and this unit's polarity) plus a fake compositor that enables the forced connector: `on` and `replug` end lit, with `POWER=0` as the last call while the connector is enabled, and `off` ends dark, from all four starting states (`bl_power` 0/4 × firmware reports powered or not);
- lockdown detection, kernel command line parsing, and the kernel's `edid_firmware` connector matching;
- the DDC retry;
- `grub show/apply/remove`, including that apply then remove gives back the original file;
- `install.sh` against a fake root (`DESTDIR`), with stub `id`, `systemctl`, `lsinitramfs`, `update-initramfs` and `udevadm` first on `PATH`, including the touch bar's files. These tests are skipped when run as root.

The touch bar tests cover:

- the zone split: a touch that starts in the bar never reaches the virtual touchpad even after it slides down, a touchpad touch that slides into the bar stays a touchpad touch, the exact boundary, finger counts with a bar finger down, clicks only with a touchpad finger, palms, a slot reused by a new contact, resync after `SYN_DROPPED`, and releasing everything;
- config parsing: the default (and that its icons are in the Adwaita theme), the spot layout, every setting, and the errors (bad height, too many buttons or shortcuts, bad icon paths and app ids, unknown keys and settings, key and command together);
- config writing: the height line and the `[[shortcut]]` blocks edited with every other line kept, no blank lines piling up, refusing an edit that would change anything else, a broken file left alone, adding shortcuts at a spot, at the end, on a full bar and moving one already there, the `shortcut` command, and replacing the first version's untouched default;
- the bar: hold and tap buttons (combo press and release order, modifiers shared between two held buttons, tap cancelled by sliding away, commands), shortcut tap to open, long press then lift to remove, slide away to cancel, the grip's live height (steps, limits, one finger only) saved on lift, and a held button staying held through a config reload;
- static checks on the GNOME Shell extension: only chrome options GNOME 50 accepts, the bar registered as a strut, the grip drawn by its own code rather than one icon, its D-Bus interface matching the daemon's, and a style for every spot kind and state;
- finding the touchpad node among other and virtual devices, `status` naming group `input` when the node is not readable, and the monitor identity read from an EDID the way mutter reports it.

The screenshots in `docs/images/` were rendered by the extension in a headless GNOME Shell 50.1 (`gnome-shell --headless --virtual-monitor 1920x1080`) fed a hand-written `touchbar.json` state, then cropped to the bar.
