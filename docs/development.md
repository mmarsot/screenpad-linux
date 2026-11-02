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
| `tests/` | unit tests; `touchbar-v1-default.toml` to `touchbar-v4-default.toml` are configs earlier versions wrote, for the migration tests |
| `docs/` | this documentation; `docs/images/` holds the screenshots |

Both programs are single-file Python 3 (3.11 or newer, standard library only), so they can be run from the checkout: `./screenpad.py status`, `./touchbar.py check`.

## Tests

```sh
python3 -m unittest discover -s tests
```

They need no root and no hardware. `tests/test_screenpad.py` covers the display CLI and `install.sh`, `tests/test_touchbar.py` the touch bar and `tests/test_touchbar_cards.py` its attention cards (the elastic bar and notifications). The display tests build a fake sysfs/debugfs/procfs/`/etc` tree in a temporary directory and replace the i2c reads and `update-grub`. They cover:

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
- config writing: the height line and the `[[shortcut]]` blocks edited with every other line kept, no blank lines piling up, refusing an edit that would change anything else, a broken file left alone, adding shortcuts at a spot, at the end, on a full bar and moving one already there, the `shortcut` command, and migrating every earlier default (replaced when untouched, its outdated blocks updated once when edited);
- the bar: hold and tap buttons (combo press and release order, modifiers shared between two held buttons, tap cancelled by sliding away, commands), shortcut tap to open, long press then lift to remove, slide away to cancel, the grip's live height (steps, limits, one finger only) saved on lift, and a held button staying held through a config reload;
- the scene: today's spots as tiles (geometry, classes, pressed and removing), app tiles online and offline, a question's tiles, and every style and class having a rule in the stylesheet;
- questions in the bar: lifting on an answer chooses it, the 0.3 s arming, sliding away, the text and a held button doing nothing, the grip still working, an answer whose question went away, and the queue;
- apps: `hello`, `set`, `ask` and `cancel` with their validation, events only to a tile's owner, answers (`chosen`, `timeout`, `unavailable`, `cancelled`), an app disconnecting, and too many apps; the socket itself (mode `0600`, bad JSON, a too long line, a stale or a live socket file), the `ask` command against a served socket, and device contacts through the splitter and the bar to an app, end to end;
- attention cards: the bar growing for a question and shrinking after an answer, a dismiss or a timeout; waiting for a touchpad finger to leave the band it would cover (at most 0.3 s); the 0.3 s arming counted from the growth; a queue staying grown and arming each card anew; a taller bar not growing; the grip and a held button through the growth; the splitter with the height changing under contacts down (they keep their kind, new ones take the new height, the clone gets full device coordinates); the daemon's own band check and its published `height` and `strut`;
- notifications: the mirror rules (buttons or urgent, low urgency, already seen, Do Not Disturb with critical ones through, banners off for the app, the config's `mirror`, `allow` and `deny`), normalizing what the extension sends, `notify` through the apps to the bar (a card with the notification's buttons, a tap answering `action-N`, dismiss and timeout, `ignored` with why, an update replacing the card, the extension cancelling it, `unavailable`, too many waiting, sharing the queue with an app's ask), the `[notifications]` config and its errors, `check`, a shortcut edit keeping the table, and migrating the default before notifications;
- static checks on the GNOME Shell extension: only chrome options GNOME 50 accepts, the bar's own height registered as a strut (not the card's), the growth animated within the arming delay, `notifications.js` following the message tray, pressing a button only on a chosen answer and only if the notification still has it, and its messages matching the daemon's, the grip drawn by its own code rather than one icon, its D-Bus interface and renderer handshake matching the daemon's, a press restyling a tile without rebuilding the bar, and no tile kinds in the renderer;
- finding the touchpad node among other and virtual devices, `status` naming group `input` when the node is not readable, and the monitor identity read from an EDID the way mutter reports it.

The screenshots in `docs/images/` were rendered by the extension in a headless GNOME Shell 50.1 (`gnome-shell --headless --virtual-monitor 1920x1080`) fed a hand-written `touchbar.json` state, then cropped to the bar. Run it under `dbus-run-session` with its own `HOME` and `XDG_*` directories, so it touches nothing of your session; `org.gnome.Shell.Screenshot` answers only some bus names, such as `org.gnome.SettingsDaemon.MediaKeys`, which a script can own on that private bus. Keep the runtime directory's path short: Unix socket paths are limited to 108 bytes. To check notifications there, send them with `org.freedesktop.Notifications.Notify` on that bus; GNOME 50 sends `ActionInvoked` and `NotificationClosed` only to the notification's sender, so subscribe from the connection that sent it rather than with `gdbus monitor`. `docs/images/touchbar-notification.png` and `touchbar-ask.png` come from such a session.
