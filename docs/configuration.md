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
- In any other config, the earlier default Push to talk block (its comment and `key = "F14"`, word for word) is updated to `alt+k`, and the settings comment gains the lines about `tile`. The rest of the file is yours and is left as it is.
