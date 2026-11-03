# Touch bar configuration

The touch bar reads `~/.config/screenpad/touchbar.toml`. `screenpad-touchbar on` writes the default there if the file is missing; the file itself has comments explaining every setting.

- **Applying changes:** save the file. The daemon reloads it within a second (or on `systemctl --user reload screenpad-touchbar`). No restart or re-login is needed.
- **Errors:** if the new file has an error, the daemon keeps the current bar and logs why (`journalctl --user -u screenpad-touchbar`). `screenpad-touchbar check` shows the error too, and lists every spot when the file is valid.
- **The bar edits it too:** resizing with the grip and adding or removing shortcuts write the `height` line and the `[[shortcut]]` blocks. Every other line and comment is kept.

## The default

```toml
height = 160

[[button]]
icon = "audio-input-microphone-symbolic"
label = "Push to talk"
mode = "hold"
key = "alt+k"

[[button]]
icon = "microphone-sensitivity-muted-symbolic"
label = "Mute microphone"
mode = "tap"
key = "micmute"

[[button]]
icon = "applets-screenshooter-symbolic"
label = "Screenshot"
mode = "tap"
key = "printscreen"

# [[shortcut]]
# app = "org.gnome.Nautilus.desktop"
```

The file then documents the `[notifications]`, `[screensaver]` and `[pager]` tables, commented out (see [Notifications](#notifications) and [Pages](#pages)).

## Settings

| Setting | Meaning |
|---|---|
| `height` | the bar's height in panel pixels (about 16 per mm), 64..540; the grip changes it too |
| `[[button]]` | up to three, filling the button spots left to right; fewer leave spots empty |
| `icon` | a symbolic icon name from the icon theme (the files under `/usr/share/icons/Adwaita/symbolic`, without `.svg`), or the path of an SVG or PNG (`/…` or `~/…`). Symbolic icons are drawn white |
| `label` | the button's name; drawn as text only when there is no `icon` |
| `mode` | `"hold"`: the key goes down when the touch starts and up when it ends, wherever the finger is by then. `"tap"` (the default): one press when the finger lifts on the button (or within 60 px of it) |
| `key` | a key or combo, by evdev name with or without `KEY_`, joined by `+`: `"alt+k"`, `"ctrl+alt+t"`, `"super+space"`, `"micmute"`, `"playpause"`, or a raw code such as `"184"`. `meta` and `super` both mean the Super key, not Alt |
| `command` | instead of `key`: a command, as a string for `sh -c` or a list of arguments. In `"hold"` mode it runs when the touch starts, and `release_command` (optional) runs when it ends |
| `tile` | instead of `key` or `command`: the name of a tile an app draws and handles in this spot (lower case letters, digits, `.`, `_`, `-`). While no app provides it, the spot shows its `icon` or `label` dimmed and ignores touches. `mode` does not apply. See the [app API](app-api.md) |
| `[[shortcut]]` `app` | up to three, filling the shortcut spots: an app's desktop file id, such as `"org.gnome.Nautilus.desktop"` (`.desktop` optional). Normally added by drag and drop |
| `[notifications]` | which GNOME notifications the bar shows as cards; see [Notifications](#notifications) |
| `[screensaver]`, `[pager]` | the touchpad page's black screensaver and clock, and the apps page; see [Pages](#pages) |

The bar sends keys through its own virtual keyboard, so a key goes to whatever window has the keyboard focus, exactly as if it were typed. Key names are evdev key positions, so on a non-QWERTY layout a letter key gives whatever your layout puts at that position.

## Examples

A play/pause button in the third spot, instead of Screenshot:

```toml
[[button]]
icon = "media-playback-start-symbolic"
label = "Play/pause"
mode = "tap"
key = "playpause"
```

A button that opens a terminal by running a command:

```toml
[[button]]
icon = "utilities-terminal-symbolic"
label = "Terminal"
mode = "tap"
command = ["ptyxis", "--new-window"]
```

A spot for an app (here one that calls its tile `agents`; see the [app API](app-api.md)):

```toml
[[button]]
icon = "dialog-information-symbolic"
label = "Agents"
tile = "agents"
```

A push-to-talk button that unmutes the microphone only while held, for apps without a push-to-talk key:

```toml
[[button]]
icon = "audio-input-microphone-symbolic"
label = "Talk"
mode = "hold"
command = "pactl set-source-mute @DEFAULT_SOURCE@ 0"
release_command = "pactl set-source-mute @DEFAULT_SOURCE@ 1"
```

## Notifications

GNOME notifications that have buttons (a calendar's Join and Snooze, a chat's Reply), and urgent ones, show in the bar: it grows into a card with the notification's title, text and first three buttons, and shrinks back when you tap one, dismiss the card, or after 20 s. Tapping a button presses the notification's own button. Dismissing, or letting the card time out, only closes the card: the notification stays in GNOME's list. A touch in the card's first 0.3 s does nothing, and the bar grows only when no touchpad finger is where the card will be (see the [app API](app-api.md#how-a-question-shows-and-is-answered)).

Nothing shows while **Do Not Disturb** is on, except critical notifications (a battery about to run out), which GNOME itself shows then too. Notifications GNOME would not show a banner for (low urgency, or banners turned off for that app in Settings) never show in the bar either.

To change it, add a `[notifications]` table (after the `[[button]]` and `[[shortcut]]` blocks):

```toml
[notifications]
mirror = false
```

| Setting | Meaning |
|---|---|
| `mirror` | `false`: no notification shows in the bar. Default `true` |
| `deny` | a list of desktop file ids whose notifications never show, such as `["org.gnome.Evolution.desktop"]` |
| `allow` | a list of desktop file ids: only their notifications show. Notifications that no app claims (from `notify-send`, for example) then never show |
| `timeout` | how long a card shows, in seconds, 1 to 3600. Default 20 |

`screenpad-touchbar check` prints what applies. Questions from apps (`screenpad-touchbar ask`) are not notifications; these settings do not change them.

## Pages

The bar's page dots show two pages: the touchpad page, with a black screensaver below the bar, and the apps page, where touches below the bar go to apps and never move the pointer. A swipe along the bar or a tap on the dots turns the page, and the apps page goes back to the touchpad by itself; see [The two pages](touchbar.md#the-two-pages). Two tables change that (after the `[[button]]` and `[[shortcut]]` blocks):

```toml
[screensaver]
clock = false

[pager]
return_after = 15
```

| Setting | Meaning |
|---|---|
| `[screensaver]` `enabled` | `false`: below the bar, the touchpad page shows GNOME's desktop background as before, not black. Default `true` |
| `[screensaver]` `clock` | `false`: no clock on the black. Default `true` |
| `[pager]` `enabled` | `false`: no page dots, no swipe, no apps page; the panel is always the touchpad page. Default `true` |
| `[pager]` `return_after` | how many seconds without a touch on the panel the apps page waits before going back to the touchpad, 1 to 3600. Default 8 |

`screenpad-touchbar check` prints what applies (`pages:`).

## Push to talk

![Push to talk held: the microphone button turns red](images/touchbar-push-to-talk.png)

The default Push to talk button holds Alt+K down for as long as your finger is on it. Set your voice app's push-to-talk key to Alt+K, or set `key` to the key the app already uses, for example `key = "ctrl+alt+p"`. Apps that read the keyboard only while focused, or only through X11 on a Wayland session, may not see a global push-to-talk key; that is the app's limit.

### Example: Claude Code voice dictation

The default was chosen to work with [Claude Code's voice dictation](https://code.claude.com/docs/en/voice-dictation) in hold mode, in a terminal. Bind the key in `~/.claude/keybindings.json`, where Claude Code calls Alt `meta`:

```json
{
  "bindings": [
    { "context": "Chat", "bindings": { "meta+k": "voice:pushToTalk" } }
  ]
}
```

Then restart Claude Code and run `/voice hold`; it says to hold `meta+k`. Click into the terminal so it has the keyboard focus, then hold the button while you speak.

- Claude Code has one push-to-talk key, so this replaces holding Space. Space still types a space.
- A modifier combo starts recording on the first press, with no warmup. Recording stops shortly after you lift, when the terminal's key repeat stops.
- A terminal multiplexer in between (tmux, for example) passes Alt+K on, as long as it binds nothing to it itself; tmux does not by default.

**Why not F14, or another key no keyboard has?** GNOME maps F13 to F24 to `XF86Tools`, `XF86Launch5` and similar, or to nothing. VTE terminals such as Ptyxis and GNOME Terminal send nothing for those keys, so a terminal app never sees them, and Claude Code names only F1 to F12 anyway. Alt+K reaches the terminal as `ESC k` and repeats while held, and neither GNOME nor Ptyxis binds it.

## Configs from earlier versions

The first version placed buttons with `x` and `width`. Those keys are now ignored, since the spots place the buttons.

When the daemon or `screenpad-touchbar on` starts:

- A config that is still exactly an earlier version's default is replaced by the current default.
- In any other config, the earlier default Push to talk block (its comment and `key = "F14"`, word for word) is updated to `alt+k`, the settings comment gains the lines about `tile`, and the comment describing the bar gains the page dots. The rest of the file is yours and is left as it is.
