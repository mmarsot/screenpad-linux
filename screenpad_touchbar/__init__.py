"""ScreenPad touch bar: buttons along the top of the ScreenPad, touchpad below.

The ScreenPad digitizer (GDX1505) only has a touchpad HID collection. This
daemon grabs its evdev node (EVIOCGRAB), so the desktop no longer reads it,
and splits every contact by where it starts:

- in the bar (the top strip, by default 160 of the panel's 1080 rows): the
  contact is ours for its whole life, even if it slides out. It never reaches
  the desktop; it acts on the spot under it: one of three buttons (a key
  combo or a command), one of three app shortcuts (tap opens, a long press
  then lift removes), or the grip at the right end (drag to resize);
- anywhere else: the contact goes, unchanged, to a uinput clone of the
  touchpad (same multitouch axes, resolution, BUTTONPAD property, bus and
  ids), so libinput treats it as the real touchpad and the desktop's touchpad
  settings apply.

It grabs only while the ScreenPad is on ('screenpad on' creates
/run/screenpad/active, 'screenpad off' removes it). When it stops, or dies,
the kernel drops the grab and the uinput devices, and the plain touchpad works
again. It publishes the bar as a scene of tiles in
$XDG_RUNTIME_DIR/screenpad/touchbar.json; the GNOME Shell extension draws
whatever tiles it finds there, on the ScreenPad, and takes apps dropped on it.

Other programs use the bar through a socket next to that file
(touchbar.sock): an app draws and handles the button spots the config gives
it ('tile' in a [[button]] block), and any program can ask the user a
question with up to three answers ('screenpad-touchbar ask' does it from a
shell). The extension forwards GNOME's notifications there too, and those
with buttons (or urgent ones) become questions whose answers press the
notification's own buttons. A question is an attention card: the bar grows
to CARD_HEIGHT for it and shrinks back once it is answered, dismissed or
timed out. Contacts already down keep the kind they started with.

Runs as the desktop user (group input, plus access to /dev/uinput).
Commands: run, on, off, status, check, probe, shortcut, ask.
"""
