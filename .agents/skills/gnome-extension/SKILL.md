---
name: gnome-extension
description: Load before changing the GNOME Shell extension - gnome-shell/screenpad-touchbar@screenpad/ (extension.js drawing the scene, the strut, drag and drop, the D-Bus Launch method, renderer.json; notifications.js mirroring notifications; stylesheet.css; metadata.json) or the tests that check it against the daemon.
---

# GNOME Shell extension

Owning files: `gnome-shell/screenpad-touchbar@screenpad/` — `extension.js` (draws the bar, the strut, drops, `Launch`, `renderer.json`), `notifications.js` (mirrors notifications to the daemon), `stylesheet.css`, `metadata.json`. `install.sh` copies every file in that folder to `/usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad/`, so keep only extension files there.

Read first:

- [docs/touchbar.md](../../../docs/touchbar.md): what the bar looks like and does, cards and notifications.
- [docs/development.md](../../../docs/development.md#tests): the static checks on the extension and how the screenshots were made in a headless GNOME Shell.
- The daemon side of every contract: the [touchbar](../touchbar/SKILL.md) skill.

## Rules

- **GNOME Shell 50, ES modules** (`gi://` and `resource:///org/gnome/shell/...` imports). `metadata.json` declares `"shell-version": ["50"]`; supporting another version means checking the APIs used there.
- **The extension draws tiles without knowing what they are**: it reads the scene from `$XDG_RUNTIME_DIR/screenpad/touchbar.json` and draws each tile's icon, app icon, text or grip with its classes. No tile kinds (`.kind ===`) and no fixed spots in the renderer; new behavior goes in the daemon.
- **A press restyles, it does not rebuild**: the layout key leaves out `pressed`, so a finger going down or up only changes a tile's style.
- **Constants mirrored from `touchbar.py` must match**: `RENDERER_NAME`, `RENDERER_PROTOCOL`, `SOCKET_NAME`, the `org.screenpad.Touchbar` `Launch` interface, and in `notifications.js` `APP`, `URGENCY`, `ACTION` and the `notify` message fields. `GROW_MS` stays below the daemon's `ASK_ARM` so a card is fully drawn before it takes a tap. The tests check each one.
- **`renderer.json` tells the daemon whether the bar is on screen**: written while enabled (`{protocol, drawn}`), removed in `disable()` before the bar is destroyed. The daemon shows questions only while it says drawn.
- **`addChrome` takes only `trackFullscreen` and `affectsStruts`** in GNOME 50: any other key throws after the actor is already on stage, and the shell then drops the stylesheet. The strut is the bar's own height; a card's extra height covers windows instead of resizing them.
- **Only notifications' own buttons are pressed**, and only when that notification and that button still exist; dismiss, timeout or the bar going away only close the card.
- Every `style` and class a tile can carry has a rule in `stylesheet.css`; CSS class names outside `CLASS_NAME` are dropped.
- Formatting is Prettier (`.prettierrc.json`: 4 spaces, single quotes, no bracket spacing) and lint is ESLint (`eslint.config.js`, with the GJS globals); `make format-check lint` checks both. Then run `make test`: the static checks read this source with regular expressions.

## Tests

- `tests/test_touchbar.py` and `tests/test_touchbar_cards.py` check the source statically: chrome options, `GROW_MS` against `ASK_ARM`, the handshake and D-Bus interface, the notify fields and names, the grip drawn by its own code, a press not rebuilding the bar, no tile kinds, and a stylesheet rule for every style and class.
- Nothing runs the extension in CI. Check a visible change in a headless GNOME Shell 50 under `dbus-run-session` with its own `HOME` and `XDG_*` directories ([docs/development.md](../../../docs/development.md#tests)), never by reloading the extension in your own session, and refresh the screenshots in `docs/images/` when the bar's look changes.
