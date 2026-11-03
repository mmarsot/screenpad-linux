---
name: gnome-extension
description: Load before changing the GNOME Shell extension - gnome-shell/screenpad-touchbar@screenpad/ (extension.js drawing the scene, the strut, drag and drop, the D-Bus Launch method, renderer.json; tiles.js building a tile; pages.js drawing the screensaver and the apps page; notifications.js mirroring notifications; the plain modules contract.js, scene.js and cards.js; stylesheet.css; metadata.json) or the tests that run it against the daemon.
---

# GNOME Shell extension

Owning files: `gnome-shell/screenpad-touchbar@screenpad/` — `extension.js` (draws the bar, the strut, drops, `Launch`, `renderer.json`), `tiles.js` (one tile's actors: the grip, the page dots, an icon, an app's icon, text), `pages.js` (below the bar: the touchpad page's black screensaver and clock, the apps page's surface), `notifications.js` (mirrors notifications to the daemon), and the plain modules they import: `contract.js` (what the extension and the daemon agree on: file names, the handshake, the D-Bus interface, the chrome options, `GROW_MS`), `scene.js` (which scenes are drawn, the layout key, where tiles, text, badges, the grip and the dots go, drops, launches, what the pages show and their sizes) and `cards.js` (the `hello`, `notify` and `cancel` messages, answers and which button one presses); `stylesheet.css`, `metadata.json`. `install.sh` copies every file in that folder to `/usr/local/share/gnome-shell/extensions/screenpad-touchbar@screenpad/`, so keep only extension files there.

Read first:

- [docs/touchbar.md](../../../docs/touchbar.md): what the bar looks like and does, cards and notifications.
- [docs/development.md](../../../docs/development.md#tests): the extension's tests and how the screenshots were made in a headless GNOME Shell.
- The daemon side of every contract: the [touchbar](../touchbar/SKILL.md) skill.

## Rules

- **GNOME Shell 50, ES modules** (`gi://` and `resource:///org/gnome/shell/...` imports). `metadata.json` declares `"shell-version": ["50"]`; supporting another version means checking the APIs used there.
- **Logic goes in the plain modules, GNOME in the others**: `contract.js`, `scene.js` and `cards.js` import nothing (no `gi://`, no `resource://`, no Node or GJS API beyond `TextEncoder`), so the tests run them under node. Put a decision or a computation there, as a function of plain values, and keep in `extension.js`, `tiles.js`, `pages.js` and `notifications.js` only what needs GNOME Shell: actors, files, D-Bus, drag and drop, the socket.
- **The extension draws tiles without knowing what they are**: it reads the scene from `$XDG_RUNTIME_DIR/screenpad/touchbar.json` and draws each tile's icon, app icon, text or grip with its classes (`tileContent`). No tile kinds and no version 1 spots: ESLint refuses any `.kind` or `.spots` (`no-restricted-properties` in `eslint.config.js`); new behavior goes in the daemon.
- **The pages come from the scene** (`pagesFrom`): the screensaver goes in `global.window_group` just above `Main.layoutManager._backgroundGroup`, so windows stay over it; the apps surface is chrome (`BAR_CHROME`) kept under the bar, over windows, and reactive so a mouse click does not fall through. Both cover only the ScreenPad's monitor and exist only while the bar is drawn. Black is drawn; the extension never touches brightness, and the clock redraws once a minute.
- **Tiles drawn by their own code (`glyph`: the grip, the dots) do not limit the icon size** (`barGeometry`), so a narrow tile never shrinks the others' icons.
- **A press restyles, it does not rebuild**: the layout key (`layoutKey`) leaves out `pressed`, so a finger going down or up only changes a tile's style.
- **What is mirrored from the daemon (`screenpad_touchbar/common.py`) must match**: in `contract.js` `STATE_NAME`, `RENDERER_NAME`, `RENDERER_PROTOCOL`, `SOCKET_NAME`, `DBUS_PATH` and the `DBUS_XML` interface, `CLI_FALLBACK` (`install.sh`'s `TOUCHBAR`); in `cards.js` `APP`, `URGENCY`, `ACTION` and the `notify` message fields. `GROW_MS` stays below the daemon's `ASK_ARM` so a card is fully drawn before it takes a tap. The tests check each one, and feed what the modules build to the daemon's own code.
- **`renderer.json` tells the daemon whether the bar is on screen**: written while enabled (`{protocol, drawn}`), removed in `disable()` before the bar is destroyed. The daemon shows questions only while it says drawn.
- **`addChrome` takes only `trackFullscreen` and `affectsStruts`** in GNOME 50, so `extension.js` and `pages.js` pass only `BAR_CHROME` or `STRUT_CHROME` from `contract.js`: any other key throws after the actor is already on stage, and the shell then drops the stylesheet. The strut is the bar's own height; a card's extra height covers windows instead of resizing them.
- **Only notifications' own buttons are pressed**, and only when that notification and that button still exist (`actionToPress`); dismiss, timeout or the bar going away only close the card.
- Every `style` and class a tile can carry has a rule in `stylesheet.css`; CSS class names outside `CLASS_NAME` are dropped.
- Formatting is Prettier (`.prettierrc.json`: 4 spaces, single quotes, no bracket spacing) and lint is ESLint (`eslint.config.js`, with the GJS globals); `make format-check lint` checks both. Then run `make test`, which runs the plain modules.

## Tests

- `tests/test_touchbar_extension.py` runs `contract.js` and `scene.js` under node (`js()` in `tests/touchbar_support.py`): the names against the daemon's, the `DBUS_XML` interface, `rendererJson` read by the daemon's `renderer_ready`, and `scene.js` on scenes the daemon publishes (the layout key, launches, classes and content, every tile, the grip and the page dots drawn at every height, the pages (`pagesFrom`, the clock's minute), a card's growth, text, the divider and badges, drops given to the real `shortcut add`). It also checks the stylesheet has a rule for every style and class.
- `tests/test_touchbar_extension_cards.py` runs `cards.js`: its messages through the daemon's `parse_notify`, apps and bar, and the daemon's answers back through `parseAnswer` and `actionToPress`.
- Only names that cross into GNOME Shell are read from the source: that `extension.js` defines `Launch` and passes only `BAR_CHROME` or `STRUT_CHROME` to `addChrome`. Add no other source check: move the logic into a plain module and test it there.
- The tests need node; without it they are skipped, except under CI (`CI` set), where they fail. Nothing runs `extension.js` itself in CI. Check a visible change in a headless GNOME Shell 50 under `dbus-run-session` with its own `HOME` and `XDG_*` directories, **set before `dbus-run-session` starts** (else the services it starts on demand, dconf first, write to your real settings; see [docs/development.md](../../../docs/development.md#tests)), never by reloading the extension in your own session, and refresh the screenshots in `docs/images/` when the bar's look changes.
