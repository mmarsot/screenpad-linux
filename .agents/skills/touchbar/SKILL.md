---
name: touchbar
description: Load before changing the touch bar daemon and CLI - touchbar.py (the zone split, the bar's buttons, shortcuts and grip, touchbar.toml parsing, writing and migration, the scene in touchbar.json, the app socket and questions, notification cards, evdev and uinput, the daemon loop, the commands) or its tests.
---

# Touch bar daemon

Owning file: `touchbar.py`, installed as `/usr/local/bin/screenpad-touchbar` (see the [install](../install/SKILL.md) skill). It is organized in sections (`# --- keys`, `config`, `writing the config`, `the scene`, `the split`, `apps`, `notifications`, `evdev and uinput`, `daemon`, `commands`); new code goes in the section of its concern.

Read first:

- [docs/touchbar.md](../../../docs/touchbar.md): how the split, the bar, attention cards and notifications behave.
- [docs/configuration.md](../../../docs/configuration.md): every `touchbar.toml` setting.
- [docs/app-api.md](../../../docs/app-api.md): the socket protocol for apps and questions.
- The renderer side: the [gnome-extension](../gnome-extension/SKILL.md) skill.

## Rules

- **Standard library only, run isolated.** The shebang is `#!/usr/bin/python3 -I`: isolated mode puts neither the script's directory nor the user's site-packages on `sys.path`, so the installed copy cannot import a sibling file. Code split into more files must be installed and found explicitly.
- **A contact keeps the kind it started with** (`Splitter`): a touch that starts in the bar never reaches the virtual touchpad, a touchpad touch that slides into the bar stays a touchpad touch, and a height change (the grip, an attention card) applies only to new contacts. The virtual touchpad gets whole frames ending in `SYN_REPORT`; resync after `SYN_DROPPED`.
- **The daemon grabs only while `/run/screenpad/active` exists** (`screenpad on`), and only between touches; letting go releases every bar and pad contact first.
- **The config is strict**: unknown keys, settings and bad values are `ConfigError`, and the daemon then keeps the current bar. A new setting needs parsing, its errors, a line in `EXAMPLE_CONFIG`'s comments and in [docs/configuration.md](../../../docs/configuration.md), and tests.
- **The bar edits the config only in place**: `edit_config` rewrites the `height` line and the `[[shortcut]]` blocks and keeps every other line and comment, and refuses an edit that would change anything else.
- **Changing the default config (`EXAMPLE_CONFIG`) needs a migration**: add the sha256 of the default being replaced to `OLD_DEFAULTS`, save that default as `tests/touchbar-v<N>-default.toml`, and add a migration test. A default block that changed goes in `OLD_BLOCKS` (old text, new text), so an edited config gets the new block and keeps the rest. Users' configs are never otherwise rewritten.
- **The scene is drawn without knowing tile kinds**: `touchbar.json` (`SCENE_VERSION`) is a list of tiles with geometry, what to draw, CSS classes and `pressed`. A new kind of tile is daemon-only work, but every `style` and class it uses needs a rule in the extension's `stylesheet.css` (tested).
- **The renderer handshake must match the extension**: `RENDERER_NAME`, `RENDERER_PROTOCOL`, `SOCKET_NAME`, the D-Bus `Launch` interface, the notify fields, urgency names, action ids and the notifications app name are mirrored in `extension.js` and `notifications.js`. Bump `RENDERER_PROTOCOL` on both sides when an older extension would misdraw the scene.
- **Questions show only while they can be seen**: an ask is shown only while the touchpad is grabbed and `renderer.json` says the bar is drawn; otherwise the asker gets `unavailable`. A touch that starts within `ASK_ARM` (0.3 s) of a card growing does nothing, and the extension's growth animation is shorter than that.
- **A protocol change updates [docs/app-api.md](../../../docs/app-api.md)** and the app tests: ops, fields, limits (`MAX_APPS`, `MAX_LINE`, `MAX_OPTIONS`, timeouts) and answer reasons.
- Notifications only mirror; a card's button presses the notification's own button and nothing else activates a notification. The mirror rules are `mirror_refusal`.

## Tests

- `tests/test_touchbar.py`: the split, config parsing, writing and migration, the bar, the scene, questions, apps and the socket, device discovery, `status`. `tests/test_touchbar_cards.py`: attention cards and notifications, and static checks that the extension matches the daemon's constants.
- They run with no root, no hardware and no uinput: events are fed as tuples, `ConfigHome` points `XDG_CONFIG_HOME` and `XDG_RUNTIME_DIR` at a temporary directory for the config, the state files and the socket.
- [docs/development.md](../../../docs/development.md#tests) lists what the touch bar tests cover; keep it in step.
