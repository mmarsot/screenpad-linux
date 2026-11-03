// What the extension and the daemon (screenpad-touchbar) agree on, and the
// GNOME Shell API options the extension relies on.
//
// Plain JavaScript with no GNOME imports, so the tests run it under node
// and compare it with the daemon's constants (screenpad_touchbar/common.py):
// file names, the renderer handshake, the D-Bus interface and timings.

// In $XDG_RUNTIME_DIR/screenpad: the scene the daemon publishes, the
// handshake this extension writes, and the daemon's app socket.
export const STATE_NAME = 'touchbar.json';
export const RENDERER_NAME = 'renderer.json';
export const RENDERER_PROTOCOL = 4;
export const SOCKET_NAME = 'touchbar.sock';

// How long the bar takes to grow into a card, in ms. Less than the daemon's
// ASK_ARM (0.3 s), so a card is fully drawn before it takes a tap.
export const GROW_MS = 150;

// The command that adds a dropped app as a shortcut.
export const CLI = 'screenpad-touchbar';
export const CLI_FALLBACK = '/usr/local/bin/screenpad-touchbar';

// The daemon opens a shortcut by calling Launch(app) here.
export const DBUS_PATH = '/org/screenpad/Touchbar';
export const DBUS_IFACE = 'org.screenpad.Touchbar';
export const DBUS_XML = `<node>
  <interface name="${DBUS_IFACE}">
    <method name="Launch">
      <arg type="s" direction="in" name="app"/>
    </method>
  </interface>
</node>`;

// GNOME 50's addChrome takes only trackFullscreen and affectsStruts and
// throws on any other key, after it has already put the actor on stage.
export const BAR_CHROME = {};
export const STRUT_CHROME = {affectsStruts: true};

// renderer.json: the protocol this extension draws, and whether the bar is
// on screen now. The daemon shows questions (protocol 2), the pages
// (protocol 3) and apps' widgets on the apps page (protocol 4) only while
// both say so.
export function rendererJson(drawn) {
    return JSON.stringify({protocol: RENDERER_PROTOCOL, drawn});
}
