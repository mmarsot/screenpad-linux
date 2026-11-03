# Development

## Layout

| Path | What it is |
|---|---|
| `screenpad.py`, `screenpad_display/` | the display CLI, installed as `/usr/local/sbin/screenpad` |
| `touchbar.py`, `screenpad_touchbar/` | the touch bar daemon and CLI, installed as `/usr/local/bin/screenpad-touchbar` |
| `agents.py`, `screenpad_agents/` | the [agent app](agents.md), a client of the touch bar's socket, installed as `/usr/local/bin/screenpad-agents` |
| `gnome-shell/screenpad-touchbar@screenpad/` | the GNOME Shell extension that draws the bar and the pages below it: `extension.js`, `tiles.js`, `pages.js` and `notifications.js` use GNOME Shell; `contract.js`, `scene.js`, `widgets.js`, `cards.js` and `feedback.js` are plain modules with its logic, which the tests run under node; `click.wav` is the press click |
| `gnome-shell/make_click.py` | writes the extension's `click.wav` (`python3 gnome-shell/make_click.py`); not installed |
| `systemd/` | the boot unit, the resume hook, and the touch bar's and the agent app's user units |
| `udev/` | the `/dev/uinput` access rule |
| `initramfs/` | the initramfs hook, used only when nvidia-drm is in the initramfs |
| `install.sh` | installs, uninstalls and purges (run as root) |
| `tests/` | unit tests; `touchbar-v1-default.toml` to `touchbar-v5-default.toml` are configs earlier versions wrote, for the migration tests |
| `docs/` | this documentation; `docs/images/` holds the screenshots |
| `AGENTS.md`, `.agents/skills/` | the guide for contributors and coding agents: an index and one skill per area (`.claude/skills` links to it; `CLAUDE.md` imports `AGENTS.md`) |
| `Makefile`, `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json`, `eslint.config.js`, `.prettierrc.json` | the development checks and their pinned tools; nothing here is installed |
| `.github/workflows/ci.yml` | CI: the same checks on every push and pull request |
| `.no-mistakes.yaml` | the commands the no-mistakes review pipeline runs |

The programs are Python 3 (3.11 or newer, standard library only) and run from the checkout: `./screenpad.py status`, `./touchbar.py check`, `./agents.py show`. `screenpad.py`, `touchbar.py` and `agents.py` are small launchers: run in isolated mode (`python3 -I`), they put the directory they really live in on `sys.path` and call `main()` in their package, `screenpad_display`, `screenpad_touchbar` or `screenpad_agents`. `install.sh` copies them with their packages to `/usr/local/lib/screenpad/` and links `/usr/local/sbin/screenpad`, `/usr/local/bin/screenpad-touchbar` and `/usr/local/bin/screenpad-agents` to them, so the checkout and the installed copy have the same layout. `screenpad_agents` imports nothing of `screenpad_touchbar`: it is an app like any other, on the socket.

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
| `screenpad_touchbar/config.py` | `touchbar.toml`: the spots, buttons, shortcuts, `[notifications]`, `[screensaver]` and `[pager]`, parsing and loading |
| `screenpad_touchbar/defaults.py` | the default config and the defaults earlier versions wrote |
| `screenpad_touchbar/config_edit.py` | editing the config in place and migrating earlier defaults |
| `screenpad_touchbar/scene.py` | the tiles in `touchbar.json` |
| `screenpad_touchbar/splitter.py` | the zone split between the bar, the apps surface and the virtual touchpad |
| `screenpad_touchbar/pager.py` | the two pages: the swipe along the bar, the page button, the return to the touchpad |
| `screenpad_touchbar/widgets.py` | apps' pages for the apps page: validating a page and its widgets |
| `screenpad_touchbar/surface.py` | the apps page: the home page's list, an app's page laid out below the bar, and what touches on its widgets do |
| `screenpad_touchbar/bar.py` | what touches on the spots, the grip and a question do |
| `screenpad_touchbar/protocol.py` | validating app messages, questions as apps send them |
| `screenpad_touchbar/notify.py` | GNOME notifications: normalizing, the mirror rules, the question one becomes |
| `screenpad_touchbar/asks.py` | the questions waiting and the attention card |
| `screenpad_touchbar/apps.py` | the programs on the app socket and the socket itself |
| `screenpad_touchbar/devices.py` | the touchpad's evdev node and the uinput devices |
| `screenpad_touchbar/daemon.py` | the daemon's loop, the grab, routing contacts by zone and page, publishing the scene, reloading the config |
| `screenpad_touchbar/cli.py` | the commands and their arguments |
| `screenpad_agents/common.py` | the agent app's names, paths, timings and limits, the errors |
| `screenpad_agents/config.py` | `agents.toml`: parsing and loading, the opt-in |
| `screenpad_agents/herdr.py` | Herdr's CLI for one session: the agents, one agent, focusing a pane, reading its screen, sending keys |
| `screenpad_agents/keymap.py` | the per-harness keymap of the blocked card: which dialogs it answers, with which keys, and the interrupt keys |
| `screenpad_agents/screen.py` | a pane's screen as the card draws it, and the guard every answer goes through |
| `screenpad_agents/firstmate.py` | firstmate's reader (the Captain's Call count and the tasks by endpoint), its interrupt (`fm-control.sh`), and finding and opening its decisions board (`fm-bearings-board.sh path`, Lavish's session listing, the URL handler) |
| `screenpad_agents/card.py` | an agent's card: its screen, Back, Focus, Interrupt and the answers, and what a touch on them means |
| `screenpad_agents/fleet.py` | the Agents page (the grid or an open card) and the two tiles as socket messages, what a touch on them means |
| `screenpad_agents/client.py` | the connection to the touch bar's socket |
| `screenpad_agents/app.py` | the loop, and the threads that call Herdr and firstmate |
| `screenpad_agents/cli.py` | `run` and `show` |

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

They need no root and no hardware. There is one file per area: `tests/test_screenpad.py`, `test_screenpad_panel.py` and `test_screenpad_grub.py` cover the display CLI, `test_install.py` `install.sh`, `test_touchbar_config.py`, `test_touchbar_bar.py`, `test_touchbar_scene.py`, `test_touchbar_apps.py`, `test_touchbar_cards.py`, `test_touchbar_clicks.py`, `test_touchbar_notify.py`, `test_touchbar_pager.py`, `test_touchbar_surface.py`, `test_touchbar_commands.py`, `test_touchbar_extension.py`, `test_touchbar_extension_cards.py`, `test_touchbar_extension_widgets.py` and `test_touchbar_extension_repaint.py` the touch bar, `test_agents_config.py`, `test_agents_sources.py`, `test_agents_fleet.py`, `test_agents_card.py`, `test_agents_app.py`, `test_agents_answers.py` and `test_agents_board.py` the agent app, and `tests/test_skills.py` the skills (their front matter, the index in `AGENTS.md`, the `.claude/skills` link, and that their links and repository paths resolve). `screenpad_support.py`, `touchbar_support.py` and `agents_support.py` hold what each program's tests share. The display tests build a fake sysfs/debugfs/procfs/`/etc` tree in a temporary directory and replace the i2c reads and `update-grub`. They cover:

- checksum fixing: a synthetic EDID with a wrong CTA checksum, and the known single-byte `0x3e` → `0x6f` case;
- DMI refusal;
- card/connector/i2c/debugfs discovery, including swapped card numbers and debugfs directories named by minor;
- the exact writes `on`, `off` and `replug` make, on unlocked and locked-down kernels;
- the panel, through a fake asus-wmi (its `update_screenpad_bl_status()` choice, the backlight core's skip of unchanged `bl_power`, and this unit's polarity) plus a fake compositor that enables the forced connector: `on` and `replug` end lit, with `POWER=0` as the last call while the connector is enabled, and `off` ends dark, from all four starting states (`bl_power` 0/4 × firmware reports powered or not);
- lockdown detection, kernel command line parsing, and the kernel's `edid_firmware` connector matching;
- the DDC retry;
- `grub show/apply/remove`, including that apply then remove gives back the original file;
- `install.sh` against a fake root (`DESTDIR`), with stub `id`, `systemctl`, `lsinitramfs`, `update-initramfs` and `udevadm` first on `PATH`, including the touch bar's files, the programs' links and packages in `/usr/local/lib/screenpad/` (an update replacing a single-file install and dropping a module that is gone), the agent app installed but off (its unit, parsed, not enabled and not restarted after a config error, the command refusing to run without its config), and the installed commands running in isolated mode, including the extension's fallback command (`CLI_FALLBACK`). These tests are skipped when run as root.

The touch bar tests cover:

- the zone split: a touch that starts in the bar never reaches the virtual touchpad even after it slides down, a touchpad touch that slides into the bar stays a touchpad touch, the exact boundary, finger counts with a bar finger down, clicks only with a touchpad finger, palms, a slot reused by a new contact, resync after `SYN_DROPPED`, and releasing everything;
- config parsing: the default (and that its icons and the page button's are in the Adwaita theme), the spot layout (the page button included), every setting, and the errors (bad height, too many buttons or shortcuts, bad icon paths and app ids, unknown keys and settings, key and command together);
- config writing: the height line and the `[[shortcut]]` blocks edited with every other line kept, no blank lines piling up, refusing an edit that would change anything else, a broken file left alone, adding shortcuts at a spot, at the end, on a full bar and moving one already there, the `shortcut` command, and migrating every earlier default (replaced when untouched, its outdated blocks updated once when edited);
- the bar: hold and tap buttons (combo press and release order, modifiers shared between two held buttons, tap cancelled by sliding away, commands), shortcut tap to open, long press then lift to remove, slide away to cancel, the grip's live height (steps, limits, one finger only) saved on lift, and a held button staying held through a config reload;
- the scene: today's spots as tiles (geometry, classes, pressed and removing), app tiles online and offline, a question's tiles, and every style and class having a rule in the stylesheet;
- questions in the bar: lifting on an answer chooses it, the 0.3 s arming, sliding away, the text and a held button doing nothing, the grip still working, an answer whose question went away, and the queue;
- apps: `hello`, `set`, `ask` and `cancel` with their validation, events only to a tile's owner, answers (`chosen`, `timeout`, `unavailable`, `cancelled`), an app disconnecting, and too many apps; the socket itself (mode `0600`, bad JSON, a too long line, a stale or a live socket file), the `ask` command against a served socket, and device contacts through the splitter and the bar to an app, end to end;
- attention cards: the bar growing for a question and shrinking after an answer, a dismiss or a timeout; waiting for a touchpad finger to leave the band it would cover (at most 0.3 s); the 0.3 s arming counted from the growth; a queue staying grown and arming each card anew; a taller bar not growing; the grip and a held button through the growth; the splitter with the height changing under contacts down (they keep their kind, new ones take the new height, the clone gets full device coordinates); the daemon's own band check and its published `height` and `strut`;
- the pages: the swipe along the bar accepted at its limits (25 mm, half as steep, 0.4 s) and refused when short, slow, too steep, vertical, in the direction with no page, with a second finger on the bar, cancelled, or with the pages not drawn; a swipe's lift doing nothing on the spot it started on (the grip not saved); page button taps, sliding away, the page button beside a card, and no page button while the pages are not drawn; through the daemon's routing, frame by frame: a sideways stroke below the bar staying a pointer move, a bar swipe moving no pointer, contacts already down keeping their kind across a page turn both ways, the apps page sending nothing to the virtual touchpad (contacts, moves, palms, a click), the return after 8 s without a touch (held by a finger down, counted from the last lift) and when the page can no longer show, letting go; what the daemon publishes (`page`, `screensaver`, `surface`, the page button with the icon of the page it goes to, none in the version 1 spots), the pages needing renderer protocol 3 while questions need 2; `[screensaver]` and `[pager]`, their errors, `check`, a shortcut edit keeping them, and migrating the default before the pages and the one with the page dots;
- the apps page: a page and its widgets validated (names, the grid, overlaps, duplicates, a widget showing nothing, fixed-width lines, the touch kinds), pages through the socket (`page` replacing, an unchanged page changing nothing, `unpage`, another app's page, at most 4 per app, at most 8 in all with a replacement still taken, gone with the app), the home page's list and its layout, opening a page and Home, the page kept while away; a tap, a long press drawn held then sent as `long`, a tap widget held long, sliding away and back, a cancel, touches on inert widgets and between them; the 0.3 s arming after the page opens or turns to, after a widget changes (the others staying armed) and after a card grows or goes; a widget changing or its page going under a finger sending `cancel` (to an app that withdrew the page too), the page turning to the touchpad and letting go cancelling; nothing drawn or touched without renderer protocol 4; device frames through the daemon reaching a widget and none reaching the virtual touchpad; and the widgets the daemon publishes;
- the press click: what the daemon counts in the scene (`clicks`): a hold button as it goes down, a tap button, a shortcut, the page button, a card's answer and an apps page widget as the finger lifts on them; nothing for sliding away, a cancel, the grip, a swipe or a touch too soon after a card appeared; the count kept while the touchpad is let go and taken again. `feedback.js`, with a stand-in for GNOME's sound player, on the scenes the daemon publishes: one click per rise of the count, none for a first scene, a restarted daemon, no scene or a daemon that publishes no count; and `click.wav` being what `gnome-shell/make_click.py` writes (within one sample step), mono 16-bit, 10 to 30 ms, below 0.35 of full scale and silent at both ends;
- notifications: the mirror rules (buttons or urgent, low urgency, already seen, Do Not Disturb with critical ones through, banners off for the app, the config's `mirror`, `allow` and `deny`), normalizing what the extension sends, `notify` through the apps to the bar (a card with the notification's buttons, a tap answering `action-N`, dismiss and timeout, `ignored` with why, an update replacing the card, the extension cancelling it, `unavailable`, too many waiting, sharing the queue with an app's ask), the `[notifications]` config and its errors, `check`, a shortcut edit keeping the table, and migrating the default before notifications;
- the GNOME Shell extension, through its plain modules run under node (`contract.js`, `scene.js`, `widgets.js`, `cards.js`, `feedback.js`, `repaint.js`; the tests are skipped without node, except under CI): file names, the renderer handshake and the D-Bus interface matching the daemon's, only chrome options GNOME 50 accepts, the growth animation shorter than the arming delay, `renderer.json` as the extension writes it read by the daemon, and on scenes the daemon publishes: version 1 scenes not drawn, a press restyling a tile without rebuilding the bar, only apps on the bar launched, classes and what each tile shows, every tile, the grip and the page button drawn at every bar height at two monitor scales (the page button's icon fitted to it and not shrinking the others'), the pages (`pagesFrom` on the touchpad page, the apps page and without the clock, the clock redrawn at the minute, a page turn rebuilding the bar, the battery level from UPower's display device with its mark on external power and nothing without a battery), a card growing over the bar's own height, card text wrapping, the divider and badges, and drops handed to the real `shortcut add`; `widgets.js` on the apps page the daemon publishes (malformed widgets and classes dropped, a press restyling without a rebuild, every widget, its type and its fixed-width lines fitting on the monitor at two scales); the extension's `notify`, `hello` and `cancel` messages through the daemon's parser, apps and bar, and the daemon's answers pressing only the button the card showed (never a replaced one, nothing on dismiss or timeout); the repaint safeguard's plan (`repaint.js`) with a stand-in clock: two forced frames per repaint, the settle repaint once per burst of activity and at most `MAX_WAIT_MS` apart while it goes on, its own frames not counted as activity, display events repainting at once and again once settled, a storm of them staying bounded, nothing running while idle, giving up when no frame comes, and one timer for a whole burst; a stylesheet rule for every style and class; and, from the source of `extension.js`, only that it defines `Launch` and passes only the two chrome option sets. ESLint refuses tile kinds (`.kind`) and version 1 spots (`.spots`) in the extension;
- finding the touchpad node among other and virtual devices, `status` naming group `input` when the node is not readable, and the monitor identity read from an EDID the way mutter reports it.

The agent app's tests use stand-ins only: a fake `herdr` script, and a fake firstmate checkout whose `bin/fm-bearings-snapshot.sh` prints an `fm-bearings.v1` model and whose `bin/fm-control.sh` and `bin/fm-bearings-board.sh` record their calls, a fake `lavish-axi` printing a session listing, and a stand-in for the URL handler that only records the URL (so nothing opens on the screen), all in a temporary directory, and fake clients for the loop. They cover:

- the config: every setting (with `lavish`), the errors, no default pointing at a firstmate home, and the command refusing to run without a config (the opt-in);
- Herdr: every call naming the session (agent list, get, focus and send-keys, pane read), a wrapper command, the states, `state_change_seq` and odd entries, its errors (a command missing or not executable, a pane it does not know), a hang and an answer with no result;
- firstmate's reader: run with only `FM_HOME` of the `FM_*` variables and the arguments `--json --fields endpoints`, the Captain's Call count (decisions and the captain's contributions, more when some were left out), tasks joined by their Herdr endpoint, and its failures; its interrupt: `fm-control.sh <task> interrupt` with only `FM_HOME`, its failure, and task ids it refuses;
- the blocked card: the lines it draws (frame and blank lines dropped, the last 16, each clipped), the Claude Code keymap (Allow once, Always only when the dialog offers it, Deny; never the folder-trust, external-imports or bypass dialogs, nor an unknown harness), its widgets through the daemon's own validation, touches mapped only to the screen drawn, a changed screen being new buttons, and the guard: keys sent only when the pane is still blocked with the same `state_change_seq` and the same text, and nothing sent when the dialog changed between draw and tap (`npm test` turned `rm -rf build`), the state changed, or another agent is in the pane;
- the page and tiles, through the touch bar daemon's own validation: a widget per agent in Herdr's order, tones by state, the grid growing then "+N more", names clipped to the limits, odd pane ids, without firstmate, Herdr failing or reporting nothing, the Captain's Call states;
- the loop against the daemon's own socket server: hello, the page and tiles, a tap opening an agent's card and its Focus focusing the pane, nothing else doing so, a refused focus logged with the loop going on, the agents tile focusing the first blocked agent, Captain's Call taps reading firstmate again (and opening the board), changes sent and the rest not, Herdr failing and coming back, an unexpected error in either thread shown, logged and survived, the touch bar going and coming back, without firstmate, `welcome` and `error` logged; the package importing nothing of `screenpad_touchbar`; and `run` serving the bar and stopping on SIGTERM;
- the card in the loop: Allow once, Always and Deny sending their keys and closing the card, a dialog that changes between draw and tap answered with nothing and drawn again with new buttons that need a fresh tap (the old ones then doing nothing), a state change likewise, the card following its screen and closing when its agent goes, Interrupt through `fm-control.sh` for a firstmate task and through Herdr's keys for another agent, an unknown harness read-only, and a failed send shown;
- the decisions board: its path from `fm-bearings-board.sh path` with only `FM_HOME` (and never `build`), the session Lavish lists `open` for that file (both paths resolved, so a row listed through a symlink or with `//` or `.` matches) opened with the URL handler, none open (ended, another file, nothing listed) opening nothing, only a loopback `http` URL opened, `lavish-axi`, the opener and a missing script failing with why; and in the loop: a tap or a lift on the `calls` tile opening it (not a cancel, a long press or a lift off the tile), taps while it opens opening it once, no board open showing the one-answer card (a second tap replacing it, OK doing nothing), a failure shown on that card, a card the bar cannot show logged, and nothing sent to an agent or firstmate's control.

The screenshots in `docs/images/` were rendered by the extension in a headless GNOME Shell 50.1 (`gnome-shell --headless --virtual-monitor 1920x1080`) fed the scenes the daemon's own code publishes (a script importing `screenpad_touchbar`, with the virtual monitor's identity from `org.gnome.Mutter.DisplayConfig`), then cropped to the bar, or scaled to half for the pages (`touchbar-touchpad-page.png` with the shell's system bus a second `dbus-daemon` in the private runtime directory, given in `DBUS_SYSTEM_BUS_ADDRESS`, where a stand-in UPower display device gives the battery level and changes it with `PropertiesChanged`; the shell then warns in a banner that screen locking needs the GNOME display manager, so turn banners off on the private bus with `org.gnome.desktop.notifications show-banners false`; `touchbar-apps-page.png` and `touchbar-app-page.png` with two apps' pages given through the daemon's own `Apps`; `agents-page.png` and `agents-card.png` with the agent app's `App` in the script's process, connected to the daemon's own `AppServer` and fed the tests' `FakeHerdr` and `FakeFirstmate`, the card after a tap on the blocked agent). Run it under `dbus-run-session` with its own `HOME` and `XDG_*` directories, so it touches nothing of your session. Keep `XDG_CURRENT_DESKTOP` as your session has it (`ubuntu:GNOME` on Ubuntu): Ubuntu's schema overrides, which give the icons and font the screenshots show, apply only to that desktop. **Set them, and unset `DBUS_SESSION_BUS_ADDRESS`, `DISPLAY` and `WAYLAND_DISPLAY`, before starting `dbus-run-session`**, not in the script it runs: services the private bus starts on demand (dconf, gvfs, the portals) get the environment `dbus-run-session` started with, so otherwise `gsettings set` and the shell's own settings writes land in your real `~/.config/dconf/user`. Comparing that file's checksum before and after a run shows it stayed untouched; `org.gnome.Shell.Screenshot` answers only some bus names, such as `org.gnome.SettingsDaemon.MediaKeys`, which a script can own on that private bus. Keep the runtime directory's path short: Unix socket paths are limited to 108 bytes. To check notifications there, send them with `org.freedesktop.Notifications.Notify` on that bus; GNOME 50 sends `ActionInvoked` and `NotificationClosed` only to the notification's sender, so subscribe from the connection that sent it rather than with `gdbus monitor`. `docs/images/touchbar-notification.png` (a real notification, mirrored by the extension to the daemon's socket, which the script serves) and `touchbar-ask.png` come from such a session. To check that the screensaver stays behind windows and the apps page covers them, open a window there (`zenity --info` with `WAYLAND_DISPLAY=wayland-0` in the private runtime directory).
