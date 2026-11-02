# Touch bar app API

Other programs can use the touch bar: an **app** draws and handles button spots the config gives it, and any program can **ask** the user a question with up to three answers. Both go through a Unix socket that the daemon (`screenpad-touchbar run`) opens next to its state file:

```
$XDG_RUNTIME_DIR/screenpad/touchbar.sock
```

The directory is your user's own and the socket is mode `0600`; the daemon also checks that the connecting process runs as the same user. There is no other authentication: any program you run can use the bar.

![A question in the touch bar: its text over the button spots, Cancel and Deploy in the shortcut spots, the grip at the right end](images/touchbar-ask.png)

*A question, as `screenpad-touchbar ask` below shows it. Rendered by the extension in a headless GNOME Shell 50.1.*

## Asking from a shell

```sh
screenpad-touchbar ask "Deploy to staging?" --text "branch agent-bar, 3 new commits" \
    --option no=Cancel --option yes=Deploy --tone no=reject --tone yes=accept --timeout 30
```

It shows the question in the bar until you tap an answer or the timeout passes, then prints the chosen answer's id (`yes` here) and exits 0. Without an answer it prints why on stderr and exits 1: `timeout`, `unavailable` (the bar cannot show questions right now, see [When questions show](#when-questions-show)) or the daemon not running. A malformed question exits 2.

| Option | Meaning |
|---|---|
| `TITLE` | the question, at most 80 characters |
| `--text TEXT` | a second, smaller line, at most 200 characters |
| `--option ID=LABEL` | an answer, one to three: the id printed when it is chosen (letters, digits, `.`, `_`, `-`, at most 32) and its label (at most 24 characters) |
| `--tone ID=TONE` | an answer's color: `neutral` (grey, the default), `accept` (green), `reject` (red), `warning` (amber) |
| `--timeout SECONDS` | 1 to 3600, default 60 |

## How a question shows and is answered

- The question covers the bar's button and shortcut spots: its title and text over the three button spots, its answers in the three shortcut spots, left to right. The grip stays and still resizes the bar. The bar does not grow for it.
- Tap an answer and lift on it (or within 60 px of it, as for tap buttons) to choose it. Sliding away before lifting cancels the tap. A touch on the text does nothing.
- A touch that starts within 0.3 s of a question appearing does nothing, so a tap already on its way to a button cannot land on an answer.
- A touch keeps what it started on: a push-to-talk button held when a question appears stays held until you lift, and a finger on an answer when its question goes away (answered elsewhere, timed out) chooses nothing.
- Questions queue: the first shows, the others wait (the text tile shows `+N`). Each one's timeout counts from when it was asked, not from when it shows.

### When questions show

Only while the daemon holds the touchpad (the ScreenPad is on) **and** the bar is on screen, drawn by an extension that knows questions. The extension says so in `renderer.json` next to the socket: `{"protocol": 2, "drawn": true}` while the bar is on screen, `"drawn": false` while it is not (no ScreenPad monitor found, for example). It writes the file while it is enabled and removes it when it is disabled. An extension from before this version, still loaded until you log out and in, does not write it, and could not draw a question. The rule behind all of this: a touch must never answer a question nobody can see. GNOME also disables extensions on the lock screen, so no question shows, or takes an answer, while the screen is locked.

When questions cannot show, a new question is answered `unavailable` at once, and questions already waiting are answered `unavailable` too. Whoever asked can then ask another way, for example in a terminal.

## The protocol

Each side sends one JSON object per line (UTF-8, `\n`-terminated, at most 16 KiB per line). A client that sends a longer line, or does not read its replies (256 KiB pending), is disconnected. At most 16 apps can be connected at once.

### From an app

| Message | Meaning |
|---|---|
| `{"op": "hello", "app": "fleet", "tiles": ["agents"]}` | Say who you are (`app`: lower case letters, digits, `.`, `_`, `-`, at most 32), and which tiles you draw (at most 8 names, same rules). Required first. A tile belongs to the first app that provides it, until that app disconnects |
| `{"op": "set", "tile": "agents", "label": "1 blocked", "tone": "warning", "badge": "2"}` | What one of your tiles shows. Fields: `icon` (an icon name from the theme, or an absolute image path), `label` (at most 32 characters), `text` (a second line, at most 48), `badge` (a short count in the corner, at most 4), `tone` (as for answers). Each `set` replaces the previous one. The extension draws the icon if there is one, otherwise the label and text. A tile you have not `set` yet shows the config's `icon` or `label` |
| `{"op": "ask", "id": "q1", "title": "Run npm test?", "text": "in ~/api", "options": [{"id": "no", "label": "Deny", "tone": "reject"}, {"id": "yes", "label": "Allow once", "tone": "accept"}], "timeout": 45}` | Ask a question. `id` names it in the answer and in `cancel`; it must be unique among your waiting questions. The limits are the `ask` command's |
| `{"op": "cancel", "id": "q1"}` | Withdraw a waiting question; it is answered `cancelled` |
| `{"op": "ping"}` | Answered `{"op": "pong"}` |

### To an app

| Message | Meaning |
|---|---|
| `{"op": "welcome", "app": "fleet", "placed": ["agents"], "unplaced": [], "taken": []}` | The answer to `hello`: your tiles that have a spot in the config (`placed`), those that do not (`unplaced`: nothing shows them until the config gives them one), and those another app already provides (`taken`) |
| `{"op": "event", "tile": "agents", "type": "down"}` | A finger went down on your tile |
| `{"op": "event", "tile": "agents", "type": "up", "inside": true}` | It lifted: `inside` is true when it lifted on the tile (or within 60 px of it), which is a tap; false when it slid away, or the touch was cancelled. Hold-style behavior is yours to build from `down` and `up` |
| `{"op": "answer", "id": "q1", "option": "yes", "reason": "chosen"}` | The answer to a question. `option` is `null` when there is none, with `reason` `timeout`, `unavailable` or `cancelled` |
| `{"op": "error", "request": "set", "message": "..."}` | A message was refused; `id` is added when the message had one. The connection stays open |

When an app disconnects, its tiles go back to the config's dimmed look and its waiting questions are dropped unanswered.

## Giving an app a spot

A button spot becomes an app's tile with `tile` instead of `key` or `command` (see [configuration](configuration.md#settings)):

```toml
[[button]]
icon = "dialog-information-symbolic"
label = "Agents"
tile = "agents"
```

While no app provides `agents`, the spot shows that icon (or label) dimmed and ignores touches.

## A minimal app

```python
import json, os, socket

path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "screenpad", "touchbar.sock")
s = socket.socket(socket.AF_UNIX)
s.connect(path)
f = s.makefile("rw")

def send(msg):
    f.write(json.dumps(msg) + "\n")
    f.flush()

send({"op": "hello", "app": "counter", "tiles": ["agents"]})
count = 0
send({"op": "set", "tile": "agents", "label": "0"})
for line in f:
    msg = json.loads(line)
    if msg["op"] == "event" and msg["type"] == "up" and msg.get("inside"):
        count += 1
        send({"op": "set", "tile": "agents", "label": str(count)})
```

## What the extension draws

The daemon publishes the bar as a scene of tiles in `touchbar.json` (`"version": 2`). The extension draws each tile without knowing what it is, so new kinds of tiles and new apps need no extension update (GNOME loads extension code only at login). The tile fields are documented at the top of the scene section in `touchbar.py`. The version 1 fields (`spots`, `pressed`, `removing`) are still published, so an extension loaded before this version keeps drawing the ordinary bar until the next login. It shows no app tiles and no questions.
