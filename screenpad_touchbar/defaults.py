"""The default touchbar.toml, and what earlier versions wrote by default, for migrate_config()."""

from .common import DEFAULT_HEIGHT, MAX_HEIGHT, MIN_HEIGHT, NOTIFY_TIMEOUT, RETURN_AFTER

# The push-to-talk button's block in the default config.
PTT_BLOCK = """\
# Push to talk. Alt+K, held while you hold the button: Claude Code's voice
# push-to-talk when ~/.claude/keybindings.json binds "meta+k" (Claude
# Code's name for Alt+K) to "voice:pushToTalk" in the "Chat" context.
# Terminals send Alt+K, unlike F13 and up. For another voice app, set key
# to the push-to-talk key it uses.
[[button]]
icon = "audio-input-microphone-symbolic"
label = "Push to talk"
mode = "hold"
key = "alt+k"
"""

# What versions before it wrote. Terminals send nothing for F14 (GNOME maps
# it to XF86Launch5), so it could not be Claude Code's push-to-talk key.
OLD_PTT_BLOCK = """\
# Push to talk. F14 is on no keyboard and GNOME binds nothing to it
# (it reaches apps as XF86Launch5); set your voice app's push-to-talk key to
# it by holding this button, or change key to what the app already uses.
[[button]]
icon = "audio-input-microphone-symbolic"
label = "Push to talk"
mode = "hold"
key = "F14"
"""

# How the default config documents 'tile', below 'command'.
COMMAND_DOC = """\
#   command          instead of key: a command to run, as a string for sh
#                    or a list of arguments. With mode "hold" it runs on
#                    touch, and release_command (optional) runs on release.
"""
TILE_DOC = """\
#   tile             instead of key or command: the name of a tile that an
#                    app draws and handles in this spot (an app that talks
#                    to the bar's socket; see app-api.md in the docs). The
#                    icon or label shows, dimmed, while no app provides it.
"""

# How the default config described the bar before the page dots.
OLD_LAYOUT_DOC = """\
# The bar, left to right: three button spots, three shortcut spots, and a
# grip at the right end. Units are ScreenPad pixels as you see the panel
# with the laptop open: 1920 wide, 1080 tall, the top being the edge
# nearest the screen hinge. 16 pixels are about 1 mm.
"""
LAYOUT_DOC = """\
# The bar, left to right: three button spots, three shortcut spots, the
# page dots and a grip at the right end. Units are ScreenPad pixels as you
# see the panel with the laptop open: 1920 wide, 1080 tall, the top being
# the edge nearest the screen hinge. 16 pixels are about 1 mm.
"""

EXAMPLE_CONFIG = """\
# ScreenPad touch bar. Edit and save: the daemon picks changes up within a
# second. 'screenpad-touchbar check' validates this file. The bar writes
# here too, when you resize it or add or remove a shortcut; it changes only
# the height line and the [[shortcut]] blocks and keeps the rest as it is.
#
%(layout)s
# Height of the bar at the top of the panel, %(min)d to %(max)d. Touches that
# start in it never move the pointer or click; everything below stays a
# touchpad. Drag the grip at the bar's right end down or up to change it.
height = %(height)d

# Up to three [[button]] blocks, for the three button spots in order.
#   icon             a symbolic icon name from the icon theme, such as
#                    "audio-input-microphone-symbolic" (the names are the
#                    files in /usr/share/icons/Adwaita/symbolic), or the
#                    path of an image file (SVG or PNG)
#   label            the button's name; drawn instead when there is no icon
#   mode             "hold": keys down on touch, up on release
#                    "tap":  one key press when you lift the finger on it
#   key              a key or combo: "alt+k", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional;
#                    "meta" and "super" are the Super key, not Alt)
#   command          instead of key: a command to run, as a string for sh
#                    or a list of arguments. With mode "hold" it runs on
#                    touch, and release_command (optional) runs on release.
%(tile)s
%(ptt)s
# Mutes and unmutes the microphone, like a keyboard's mic mute key.
[[button]]
icon = "microphone-sensitivity-muted-symbolic"
label = "Mute microphone"
mode = "tap"
key = "micmute"

# GNOME's screenshot tool, like the Print Screen key.
[[button]]
icon = "applets-screenshooter-symbolic"
label = "Screenshot"
mode = "tap"
key = "printscreen"

# Up to three [[shortcut]] blocks, for the shortcut spots in order: apps by
# their desktop file id. Drag an app from the dock or the app grid onto the
# bar to add it; tap it to open the app; hold it for a second, until it
# turns red, and lift to remove it. 'screenpad-touchbar shortcut' does the
# same from a terminal. For example:
# [[shortcut]]
# app = "org.gnome.Nautilus.desktop"

# GNOME notifications that have buttons (Join, Reply, ...), and urgent ones,
# show in the bar: it grows into a card with the notification's text and
# buttons, and shrinks back when you tap one, dismiss it or it times out.
# Tapping a button presses the notification's own button. Dismissing only
# closes the card; the notification stays in GNOME's list. Nothing shows
# while Do Not Disturb is on, except what GNOME itself shows then (critical
# notifications). To change that, a [notifications] table:
# [notifications]
# mirror = false                             # no cards for notifications
# deny = ["org.gnome.Evolution.desktop"]     # never these apps
# allow = ["org.gnome.Calendar.desktop"]     # only these apps
# timeout = %(notify_timeout)d                               # seconds a card shows

# Two pages, shown by the page dots. The touchpad page is the usual one:
# below the bar a touchpad, drawn black behind your windows (black pixels;
# the brightness stays as it is), with a faint clock. A swipe along the bar
# (one finger, at least 25 mm, quickly, moving left) or a tap on the dots
# turns to the apps page: below the bar, touches go to apps and never move
# the pointer. A swipe to the right, a tap on the dots, or %(return_after)d seconds
# without a touch bring the touchpad back. To change that:
# [screensaver]
# enabled = false                            # no black, GNOME's background
# clock = false                              # black, without the clock
# [pager]
# enabled = false                            # no dots and no apps page
# return_after = %(return_after)d                           # seconds before the touchpad
""" % {
    "layout": LAYOUT_DOC,
    "return_after": RETURN_AFTER,
    "height": DEFAULT_HEIGHT,
    "min": MIN_HEIGHT,
    "max": MAX_HEIGHT,
    "ptt": PTT_BLOCK,
    "tile": TILE_DOC,
    "notify_timeout": NOTIFY_TIMEOUT,
}

# sha256 of the defaults earlier versions wrote. A file still exactly one of
# them is replaced by the current default (see migrate_config()).
OLD_DEFAULTS = {
    "dfabfa5d73979ec2302e2a4d343426085da0c6fcd9093f31e3cee4067bc30c36",
    # Before tiles (tests/touchbar-v3-default.toml).
    "a764addfad0135d0272d89ed390b4013ed575819ca224fd3fa704508dde2a755",
    # Before notifications (tests/touchbar-v4-default.toml).
    "d6137e89e4ace61da259ef3accf7645b090baee1863076db4145bc08d54d8456",
    # Before the pages (tests/touchbar-v5-default.toml).
    "f1c38e99d337195743b369674044d2f71053781a51822ebf10797d7dccf7d53c",
}

# Default blocks earlier versions wrote, each with its current text. A config
# that still holds one word for word gets the new one in its place and keeps
# the rest (see migrate_config()).
OLD_BLOCKS = (
    (OLD_PTT_BLOCK, PTT_BLOCK),
    (
        """\
#   key              a key or combo: "F14", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional)
""",
        """\
#   key              a key or combo: "alt+k", "ctrl+alt+t", "super+space",
#                    "micmute", "playpause" (evdev names, KEY_ optional;
#                    "meta" and "super" are the Super key, not Alt)
""",
    ),
    # The blank line after it marks the block without 'tile' yet, so this
    # applies once.
    (COMMAND_DOC + "\n", COMMAND_DOC + TILE_DOC + "\n"),
    (OLD_LAYOUT_DOC, LAYOUT_DOC),
)
