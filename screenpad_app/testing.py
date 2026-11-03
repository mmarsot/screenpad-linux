"""FakeBar: a small stand-in for the touch bar's app socket, for an app's tests.

It listens in a temporary directory laid out like $XDG_RUNTIME_DIR
(environ gives that variable, socket_path(bar.environ) the socket), answers
hello the way the daemon does, and keeps what each app sent. It runs in its
own thread, so an app under test can block on it or run its own loop.

It checks the handshake and little else: unknown ops, messages before hello,
someone else's tile or page. The limits and the touch rules (arming,
cancels) are the daemon's, and docs/app-api.md gives them; a test that needs
them runs against the daemon itself.

    with FakeBar(placed=["counter"]) as bar:
        app = Touchbar(socket_path(bar.environ), "counter", ["counter"])
        app.handshake()
        bar.press("counter")          # a tap on the tile
        ...
        bar.wait(lambda: bar.tiles["counter"].get("label") == "1")
"""

import json
import os
import select
import shutil
import socket
import tempfile
import threading
import time

from .common import MAX_LINE, MIN_PROTOCOL, NAME, PROTOCOL, SOCKET


class _Conn:
    def __init__(self, sock):
        self.sock, self.buf = sock, b""
        self.name = self.protocol = None


class FakeBar:
    """The daemon's side of the socket, for tests.

    placed: the tiles that have a spot (the rest are "unplaced").
    min_protocol, max_protocol: the app protocol versions it speaks;
    legacy=True answers like a daemon from before the version (it refuses
    a hello that says one).

    What the apps sent: tiles (tile -> its last "set" fields), pages (page
    -> its last "page" message), asks (id -> the waiting "ask" message),
    apps (the names said hello), received (every message, as (app, msg)).
    """

    def __init__(self, placed=(), min_protocol=MIN_PROTOCOL, max_protocol=PROTOCOL, legacy=False):
        self.placed = set(placed)
        self.min_protocol, self.max_protocol, self.legacy = min_protocol, max_protocol, legacy
        self.dir = tempfile.mkdtemp(prefix="fakebar-")
        self.environ = {"XDG_RUNTIME_DIR": self.dir}
        self.path = os.path.join(self.dir, *SOCKET)
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        self.tiles, self.pages, self.asks = {}, {}, {}
        self.received = []
        self._owner = {}  # ("tile" | "page" | "ask", name) -> _Conn
        self._conns = []
        self._lock = threading.RLock()
        self._listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._thread = None
        self._wake_r, self._wake_w = os.pipe()
        self._stop = False

    # --- running -------------------------------------------------------------

    def start(self):
        self._listen.bind(self.path)
        self._listen.listen(8)
        self._thread = threading.Thread(target=self._serve, name="fakebar", daemon=True)
        self._thread.start()
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._stop = True
        os.write(self._wake_w, b"x")
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            for conn in list(self._conns):
                self._gone(conn)
        self._listen.close()
        for fd in (self._wake_r, self._wake_w):
            os.close(fd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _serve(self):
        while not self._stop:
            with self._lock:
                fds: list = [self._listen, self._wake_r] + [c.sock for c in self._conns]
            try:
                ready, _, _ = select.select(fds, [], [], 0.5)
            except (OSError, ValueError):
                continue  # a connection closed by drop() while waiting
            for r in ready:
                if r is self._listen:
                    sock, _ = self._listen.accept()
                    with self._lock:
                        self._conns.append(_Conn(sock))
                elif r == self._wake_r:
                    os.read(self._wake_r, 64)
                else:
                    self._readable(r)

    def _readable(self, sock):
        with self._lock:
            conn = next((c for c in self._conns if c.sock is sock), None)
            if conn is None:
                return
            try:
                data = sock.recv(65536)
            except OSError:
                data = b""
            if not data:
                self._gone(conn)
                return
            conn.buf += data
            *lines, conn.buf = conn.buf.split(b"\n")
            for line in lines:
                if line.strip():
                    self._message(conn, line)
            if len(conn.buf) > MAX_LINE:
                self._gone(conn)

    def _gone(self, conn):
        if conn not in self._conns:
            return
        self._conns.remove(conn)
        for key in [k for k, c in self._owner.items() if c is conn]:
            del self._owner[key]
            {"tile": self.tiles, "page": self.pages, "ask": self.asks}[key[0]].pop(key[1], None)
        conn.sock.close()

    # --- what the apps send --------------------------------------------------

    def _message(self, conn, line):
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if not isinstance(msg, dict):
            self._send(conn, {"op": "error", "message": "not a JSON object"})
            return
        self.received.append((conn.name, msg))
        op = msg.get("op")
        if op == "ping":
            self._send(conn, {"op": "pong"})
        elif op == "hello":
            self._hello(conn, msg)
        elif conn.name is None:
            self._error(conn, msg, "say hello first")
        elif op == "set":
            if self._owner.get(("tile", msg.get("tile"))) is not conn:
                self._error(conn, msg, "tile %r is not yours; provide it in hello" % msg.get("tile"))
            else:
                self.tiles[msg["tile"]] = {k: v for k, v in msg.items() if k not in ("op", "tile")}
        elif op == "page":
            self._claim(conn, msg, "page", self.pages, msg)
        elif op == "unpage":
            if self._owner.get(("page", msg.get("page"))) is not conn:
                self._error(conn, msg, "page %r is not yours" % msg.get("page"))
            else:
                del self._owner[("page", msg["page"])], self.pages[msg["page"]]
        elif op == "ask":
            self._claim(conn, msg, "ask", self.asks, msg, key=msg.get("id"))
        elif op == "cancel":
            if self._owner.get(("ask", msg.get("id"))) is not conn:
                self._error(conn, msg, "no waiting question %r" % msg.get("id"))
            else:
                self.answer(msg["id"], None, "cancelled")
        else:
            self._error(conn, msg, "unknown op %r" % op)

    def _claim(self, conn, msg, kind, store, value, key=None):
        key = msg.get(kind) if key is None else key
        if not isinstance(key, str):
            self._error(conn, msg, "%s needs a name" % kind)
        elif self._owner.setdefault((kind, key), conn) is not conn:
            self._error(conn, msg, "%s %r is another app's" % (kind, key))
        else:
            store[key] = value

    def _hello(self, conn, msg):
        if conn.name is not None:
            return self._error(conn, msg, "hello was already said")
        if "protocol" in msg and self.legacy:
            return self._error(conn, msg, "unknown field(s): protocol")
        protocol = msg.get("protocol", 1)
        if isinstance(protocol, bool) or not isinstance(protocol, int) or protocol < 1:
            return self._error(conn, msg, "protocol must be a version number (1 or more)")
        if not self.min_protocol <= protocol <= self.max_protocol:
            return self._error(
                conn,
                msg,
                "app protocol %d is not supported: this touch bar speaks %d to %d"
                % (protocol, self.min_protocol, self.max_protocol),
                code="unsupported-protocol",
                protocol=protocol,
                supported={"min": self.min_protocol, "max": self.max_protocol},
            )
        name = msg.get("app")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            return self._error(conn, msg, 'app must be a name like "agents" (lower case, at most 32)')
        conn.name, conn.protocol = name, protocol
        mine: list[str] = []
        taken: list[str] = []
        for tile in dict.fromkeys(msg.get("tiles") or []):
            (mine if self._owner.setdefault(("tile", tile), conn) is conn else taken).append(tile)
        welcome: dict = {
            "op": "welcome",
            "app": name,
            "placed": [t for t in mine if t in self.placed],
            "unplaced": [t for t in mine if t not in self.placed],
            "taken": taken,
        }
        if not self.legacy:
            welcome["protocol"] = protocol
        self._send(conn, welcome)

    def _error(self, conn, msg, message, **fields):
        reply = {"op": "error", "request": msg.get("op"), "message": message, **fields}
        if isinstance(msg.get("id"), str):
            reply["id"] = msg["id"]
        self._send(conn, reply)

    def _send(self, conn, msg):
        try:
            conn.sock.sendall(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
        except OSError:
            self._gone(conn)

    # --- what the test does --------------------------------------------------

    @property
    def apps(self):
        with self._lock:
            return sorted(c.name for c in self._conns if c.name)

    def protocol_of(self, app):
        """The version an app's hello said it speaks (None: not connected)."""
        with self._lock:
            return next((c.protocol for c in self._conns if c.name == app), None)

    def send(self, app, msg):
        """Send msg to the app named app."""
        with self._lock:
            conn = next((c for c in self._conns if c.name == app), None)
            if conn is None:
                raise AssertionError("no app %r is connected" % app)
            self._send(conn, msg)

    def press(self, tile, inside=True):
        """A finger down on a tile and up: a tap when inside."""
        self._to_owner("tile", tile, {"op": "event", "tile": tile, "type": "down"})
        self._to_owner("tile", tile, {"op": "event", "tile": tile, "type": "up", "inside": inside})

    def tap(self, page, widget, kind="tap"):
        """A touch on a widget of a page: "down", then kind ("tap", "long" or "cancel")."""
        for t in ("down", kind):
            self._to_owner("page", page, {"op": "event", "page": page, "widget": widget, "type": t})

    def answer(self, id, option, reason="chosen"):
        """Answer a waiting question (option None with a reason: "dismissed", "timeout", ...)."""
        with self._lock:
            conn = self._owner.pop(("ask", id), None)
            self.asks.pop(id, None)
            if conn is None:
                raise AssertionError("no waiting question %r" % id)
            self._send(conn, {"op": "answer", "id": id, "option": option, "reason": reason})

    def _to_owner(self, kind, name, msg):
        with self._lock:
            conn = self._owner.get((kind, name))
            if conn is None:
                raise AssertionError("no app provides the %s %r" % (kind, name))
            self._send(conn, msg)

    def drop(self):
        """Close every connection, as a daemon restarting does."""
        with self._lock:
            for conn in list(self._conns):
                self._gone(conn)
        os.write(self._wake_w, b"x")

    def wait(self, check, timeout=2.0):
        """Wait until check() is true; AssertionError past timeout seconds."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if check():
                    return
            time.sleep(0.005)
        raise AssertionError("not reached in %g s" % timeout)
