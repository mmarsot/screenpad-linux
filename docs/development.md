# Development

## Layout

| Path | What it is |
|---|---|
| `screenpad.py`, `screenpad_display/` | the display CLI, installed as `/usr/local/sbin/screenpad` |
| `touchbar.py`, `screenpad_touchbar/` | the touch bar daemon and CLI, installed as `/usr/local/bin/screenpad-touchbar` |
| `gnome-shell/screenpad-touchbar@screenpad/` | the GNOME Shell extension that draws the bar: `extension.js`, `tiles.js` and `notifications.js` use GNOME Shell; `contract.js`, `scene.js` and `cards.js` are plain modules with its logic, which the tests run under node |
| `systemd/` | the boot unit, the resume hook and the touch bar's user unit |
| `udev/` | the `/dev/uinput` access rule |
| `initramfs/` | the initramfs hook, used only when nvidia-drm is in the initramfs |
| `install.sh` | installs, uninstalls and purges (run as root) |
| `tests/` | unit tests; `touchbar-v1-default.toml` to `touchbar-v4-default.toml` are configs earlier versions wrote, for the migration tests |
| `docs/` | this documentation; `docs/images/` holds the screenshots |
| `AGENTS.md`, `.agents/skills/` | the guide for contributors and coding agents: an index and one skill per area (`.claude/skills` links to it; `CLAUDE.md` imports `AGENTS.md`) |
| `Makefile`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `eslint.config.js`, `.prettierrc.json` | the development checks and their pinned tools; nothing here is installed |
| `.github/workflows/ci.yml` | CI: the same checks on every push and pull request |
| `.no-mistakes.yaml` | the commands the no-mistakes review pipeline runs |

Both programs are Python 3 (3.11 or newer, standard library only) and run from the checkout: `./screenpad.py status`, `./touchbar.py check`. `screenpad.py` and `touchbar.py` are small launchers: run in isolated mode (`python3 -I`), they put the directory they really live in on `sys.path` and call `main()` in their package, `screenpad_display` or `screenpad_touchbar`. `install.sh` copies both with their packages to `/usr/local/lib/screenpad/` and links `/usr/local/sbin/screenpad` and `/usr/local/bin/screenpad-touchbar` to them, so the checkout and the installed copy have the same layout.

Each package has one module per concern, each under about 500 lines:

| Module | What it holds |
|---|---|
| `screenpad_display/common.py` | the supported machine, the paths, the panel's constants, the errors |
| `screenpad_display/edid.py` | EDID checksums and description |
| `screenpad_display/system.py` | `Env` (the filesystem root and side effects), file and sysfs access, the DDC read, the machine check |
| `screenpad_display/lockdown.py` | lockdown and the `drm.edid_firmware` path |
| `screenpad_display/discovery.py` | the NVIDIA card, the connector, its DDC bus and debugfs directory |
| `screenpad_display/actions.py` | lighting and darkening the panel, the connector, the EDID, the active flag |
| `screenpad_display/commands.py` | `on`, `replug`, `off`, `capture-edid`, `status` |
| `screenpad_display/grub.py` | `grub show|apply|remove` |
| `screenpad_display/cli.py` | the arguments and `main()` |
| `screenpad_touchbar/common.py` | the panel and the bar's geometry, names, paths, timings and limits, the errors, the runtime files |
| `screenpad_touchbar/codes.py` | Linux input event codes, the evdev and uinput structs and ioctls |
| `screenpad_touchbar/keys.py` | key names and combos, the key device's presses |
| `screenpad_touchbar/config.py` | `touchbar.toml`: the spots, buttons, shortcuts and `[notifications]`, parsing and loading |
| `screenpad_touchbar/defaults.py` | the default config and the defaults earlier versions wrote |
| `screenpad_touchbar/config_edit.py` | editing the config in place and migrating earlier defaults |
| `screenpad_touchbar/scene.py` | the tiles in `touchbar.json` |
| `screenpad_touchbar/splitter.py` | the zone split between the bar and the virtual touchpad |
| `screenpad_touchbar/bar.py` | what touches on the spots, the grip and a question do |
| `screenpad_touchbar/protocol.py` | validating app messages, questions as apps send them |
| `screenpad_touchbar/notify.py` | GNOME notifications: normalizing, the mirror rules, the question one becomes |
| `screenpad_touchbar/asks.py` | the questions waiting and the attention card |
| `screenpad_touchbar/apps.py` | the programs on the app socket and the socket itself |
| `screenpad_touchbar/devices.py` | the touchpad's evdev node and the uinput devices |
| `screenpad_touchbar/daemon.py` | the daemon's loop, the grab, publishing the scene, reloading the config |
| `screenpad_touchbar/cli.py` | the commands and their arguments |

The imports between them go one way (no cycles): `common` and `codes` import nothing of the package, and `cli` sits on top.

## Checks

```sh
make check
```

runs, in order, everything CI runs; each step is also a target of its own:

| Command | What it checks | Tools |
|---|---|---|
| `make format-check` | formatting; `make format` applies it | ruff format (Python), Prettier (the extension's JavaScript) |
| `make lint` | likely mistakes | ruff check, ESLint |
| `make typecheck` | types | mypy, with `check_untyped_defs`, on the programs and the tests |
| `make test` | the unit tests below | `python3 -m unittest`, standard library only |

The Python tools run through [uv](https://docs.astral.sh/uv/) (versions pinned in `pyproject.toml` and `uv.lock`), the JavaScript ones through npm (`package.json`, `package-lock.json`; `make` runs `npm ci` the first time). Only `make test` works without them; it uses node, when installed, for the extension's tests. CI (`.github/workflows/ci.yml`) runs the format check, lint and typecheck once and the tests on Python 3.11 and 3.14, with node.

Changes go through the [no-mistakes](https://github.com/kunchenguid/no-mistakes) review pipeline before they reach GitHub; the rules for contributors and agents are in [AGENTS.md](../AGENTS.md) and its skills.

## Tests

```sh
make test   # or: python3 -m unittest discover -s tests
```

They need no root and no hardware. There is one file per area: `tests/test_screenpad.py`, `test_screenpad_panel.py` and `test_screenpad_grub.py` cover the display CLI, `test_install.py` `install.sh`, `test_touchbar_config.py`, `test_touchbar_bar.py`, `test_touchbar_scene.py`, `test_touchbar_apps.py`, `test_touchbar_cards.py`, `test_touchbar_notify.py`, `test_touchbar_commands.py`, `test_touchbar_extension.py` and `test_touchbar_extension_cards.py` the touch bar, and `tests/test_skills.py` the skills (their front matter, the index in `AGENTS.md`, the `.claude/skills` link, and that their links and repository paths resolve). `screenpad_support.py` and `touchbar_support.py` hold what each program's tests share. The display tests build a fake sysfs/debugfs/procfs/`/etc` tree in a temporary directory and replace the i2c reads and `update-grub`. They cover:

- checksum fixing: a synthetic EDID with a wrong CTA checksum, and the known single-byte `0x3e` → `0x6f` case;
- DMI refusal;
- card/connector/i2c/debugfs discovery, including swapped card numbers and debugfs directories named by minor;
- the exact writes `on`, `off` and `replug` make, on unlocked and locked-down kernels;
- the panel, through a fake asus-wmi (its `update_screenpad_bl_status()` choice, the backlight core's skip of unchanged `bl_power`, and this unit's polarity) plus a fake compositor that enables the forced connector: `on` and `replug` end lit, with `POWER=0` as the last call while the connector is enabled, and `off` ends dark, from all four starting states (`bl_power` 0/4 × firmware reports powered or not);
- lockdown detection, kernel command line parsing, and the kernel's `edid_firmware` connector matching;
- the DDC retry;
- `grub show/apply/remove`, including that apply then remove gives back the original file;
- `install.sh` against a fake root (`DESTDIR`), with stub `id`, `systemctl`, `lsinitramfs`, `update-initramfs` and `udevadm` first on `PATH`, including the touch bar's files, the programs' links and packages in `/usr/local/lib/screenpad/` (an update replacing a single-file install and dropping a module that is gone), and both installed commands running in isolated mode. These tests are skipped when run as root.

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
- the GNOME Shell extension, through its plain modules run under node (`contract.js`, `scene.js`, `cards.js`; the tests are skipped without node, except under CI): file names, the renderer handshake, the D-Bus interface and the installed command matching the daemon's and `install.sh`'s, only chrome options GNOME 50 accepts, the growth animation shorter than the arming delay, `renderer.json` as the extension writes it read by the daemon, and on scenes the daemon publishes: version 1 scenes not drawn, a press restyling a tile without rebuilding the bar, only apps on the bar launched, classes and what each tile shows, every tile and the grip drawn at every bar height at two monitor scales, a card growing over the bar's own height, card text wrapping, the divider and badges, and drops handed to the real `shortcut add`; the extension's `notify`, `hello` and `cancel` messages through the daemon's parser, apps and bar, and the daemon's answers pressing only the button the card showed (never a replaced one, nothing on dismiss or timeout); a stylesheet rule for every style and class; and, from the extension's source, only that it defines `Launch` and passes only the two chrome option sets. ESLint refuses tile kinds (`.kind`) and version 1 spots (`.spots`) in the extension;
- finding the touchpad node among other and virtual devices, `status` naming group `input` when the node is not readable, and the monitor identity read from an EDID the way mutter reports it.

The screenshots in `docs/images/` were rendered by the extension in a headless GNOME Shell 50.1 (`gnome-shell --headless --virtual-monitor 1920x1080`) fed a hand-written `touchbar.json` state, then cropped to the bar. Run it under `dbus-run-session` with its own `HOME` and `XDG_*` directories, so it touches nothing of your session; `org.gnome.Shell.Screenshot` answers only some bus names, such as `org.gnome.SettingsDaemon.MediaKeys`, which a script can own on that private bus. Keep the runtime directory's path short: Unix socket paths are limited to 108 bytes. To check notifications there, send them with `org.freedesktop.Notifications.Notify` on that bus; GNOME 50 sends `ActionInvoked` and `NotificationClosed` only to the notification's sender, so subscribe from the connection that sent it rather than with `gdbus monitor`. `docs/images/touchbar-notification.png` and `touchbar-ask.png` come from such a session.
