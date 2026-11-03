"""A connection to the touch bar's app socket: one JSON object per line.

Touchbar is one connection. It says hello with the app protocol version it
speaks (PROTOCOL) and the app's tiles, sends a tile or page only when it
differs from the last one sent for it (the daemon would ignore it anyway, and
this keeps the socket quiet), and splits what the daemon sends into
messages. The daemon's answer to hello arrives as the first message, so the
connection never blocks an app's loop: receive() returns the welcome like any
message and raises HelloRefused (ProtocolMismatch for a version the bar does
not speak) when hello was refused. handshake() waits for that answer instead,
for an app that has no loop yet.

Link keeps a Touchbar up: it connects when due, retries every `retry`
seconds while the daemon is not running or refuses the app, and logs a loss
or a refusal once rather than on every retry.
"""

import json
import socket
import sys
import time

from .common import MAX_LINE, PROTOCOL, HelloRefused, ProtocolMismatch, TouchbarError, TouchbarGone

# Seconds between tries while the daemon is not running.
RECONNECT = 5.0


class Touchbar:
    """One connection, said hello on. welcome is the daemon's answer to hello
    once it arrived (None before), protocol the version it said it speaks."""

    def __init__(self, path, app, tiles=(), protocol=PROTOCOL, timeout=2.0):
        self.speaks = protocol
        self.welcome = None
        self.protocol = None
        self._buf = b""
        self._sent = {}
        self._early = []
        hello = {"op": "hello", "app": app, "tiles": list(tiles)}
        if protocol != 1:
            hello["protocol"] = protocol  # version 1's hello says no version
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(timeout)
            self.sock.connect(path)
            self.send(hello)
        except (OSError, TouchbarError):
            self.sock.close()
            raise

    def fileno(self):
        return self.sock.fileno()

    def send(self, msg):
        if msg.get("op") == "unpage":
            self._sent.pop(("page", msg.get("page")), None)
        try:
            self.sock.sendall(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
        except OSError as e:
            raise TouchbarGone(str(e))

    def show(self, messages):
        """Send each "set" (tile) or "page" message that changed since it was
        last sent; other messages go as they are."""
        for msg in messages:
            if msg.get("op") not in ("set", "page"):
                self.send(msg)
                continue
            key = (msg["op"], msg.get("tile") or msg.get("page"))
            if self._sent.get(key) != msg:
                self.send(msg)
                self._sent[key] = msg

    def tile(self, tile, **look):
        """What one of the app's tiles shows: icon, label, text, badge, tone."""
        self.show([{"op": "set", "tile": tile, **look}])

    def unpage(self, page):
        self.send({"op": "unpage", "page": page})

    def ask(self, id, title, options, text=None, timeout=None):
        """Ask a question; options are (id, label) or (id, label, tone)
        tuples, or the protocol's dicts. Its answer comes back as an
        "answer" message with this id."""
        msg = {"op": "ask", "id": id, "title": title, "options": [_option(o) for o in options]}
        if text is not None:
            msg["text"] = text
        if timeout is not None:
            msg["timeout"] = timeout
        self.send(msg)

    def cancel(self, id):
        self.send({"op": "cancel", "id": id})

    def receive(self):
        """The messages that arrived; call it when fileno() is readable.
        TouchbarGone when the daemon closed, HelloRefused (or
        ProtocolMismatch) when it refused hello."""
        if self._early:
            out, self._early = self._early, []
            return out
        return self._read()

    def _read(self):
        try:
            data = self.sock.recv(65536)
        except (BlockingIOError, TimeoutError):
            return []
        except OSError as e:
            raise TouchbarGone(str(e))
        if not data:
            raise TouchbarGone("the touch bar closed the connection")
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        if len(self._buf) > MAX_LINE:
            raise TouchbarGone("the touch bar sent a line too long")
        out = []
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                if self.welcome is None:
                    self._answer_to_hello(msg)
                out.append(msg)
        return out

    def _answer_to_hello(self, msg):
        op = msg.get("op")
        if op == "welcome":
            self.welcome = msg
            self.protocol = msg.get("protocol", 1)
        elif op == "error" and msg.get("request") in ("hello", None):
            raise hello_refusal(msg, self.speaks)

    def handshake(self, timeout=2.0):
        """Wait for the answer to hello and return the welcome; HelloRefused
        (or ProtocolMismatch) if it was refused, TouchbarGone if none came in
        time. Messages that came after the welcome are kept: the next
        receive() returns them at once, so call it before waiting on
        fileno()."""
        end = time.monotonic() + timeout
        old = self.sock.gettimeout()
        try:
            while self.welcome is None:
                left = end - time.monotonic()
                if left <= 0:
                    raise TouchbarGone("the touch bar did not answer hello in %g s" % timeout)
                self.sock.settimeout(left)
                got = self._read()
                if self.welcome is not None:
                    self._early = got[got.index(self.welcome) + 1 :]
        finally:
            self.sock.settimeout(old)
        return self.welcome

    def close(self):
        self.sock.close()


def hello_refusal(msg, speaks):
    """The error to raise for the daemon's refusal of a hello."""
    supported = msg.get("supported")
    if msg.get("code") == "unsupported-protocol" and isinstance(supported, dict):
        return ProtocolMismatch(speaks, (supported.get("min"), supported.get("max")))
    if speaks != 1 and "protocol" in str(msg.get("message")) and msg.get("code") is None:
        # A daemon from before the version: "unknown field(s): protocol".
        return ProtocolMismatch(speaks, None)
    return HelloRefused("the touch bar refused hello: %s" % msg.get("message"))


def _option(o):
    if isinstance(o, dict):
        return o
    out = {"id": o[0], "label": o[1]}
    if len(o) > 2:
        out["tone"] = o[2]
    return out


class Link:
    """A Touchbar kept connected.

    Call connect() on every turn of the app's loop: it connects when due
    and returns True when a new connection was made, after which the app
    shows everything again (a new connection has sent nothing yet). show(),
    send() and receive() do nothing while there is no connection, and a
    connection that fails is dropped and retried after `retry` seconds.
    """

    def __init__(
        self, path, app, tiles=(), retry=RECONNECT, log=None, clock=time.monotonic, protocol=PROTOCOL, timeout=2.0
    ):
        self.path, self.app, self.tiles, self.retry = path, app, list(tiles), retry
        self.protocol, self.timeout, self.clock = protocol, timeout, clock
        self.log = log or (lambda line: print("%s: %s" % (app, line), file=sys.stderr, flush=True))
        self.touchbar = None
        self.refused = None  # the last HelloRefused, until a welcome
        self._retry_at = 0.0
        self._told = None

    @property
    def connected(self):
        return self.touchbar is not None

    def fileno(self):
        return None if self.touchbar is None else self.touchbar.fileno()

    def connect(self):
        if self.touchbar is not None or self.clock() < self._retry_at:
            return False
        try:
            self.touchbar = Touchbar(self.path, self.app, self.tiles, self.protocol, self.timeout)
        except (OSError, TouchbarError):
            self._retry_at = self.clock() + self.retry
            return False
        return True

    def send(self, msg):
        self._guard(lambda tb: tb.send(msg))

    def show(self, messages):
        self._guard(lambda tb: tb.show(messages))

    def receive(self):
        out = self._guard(lambda tb: tb.receive())
        if out and self.touchbar is not None and self.touchbar.welcome is not None and self._told:
            self.refused = self._told = None
        return out or []

    def _guard(self, act):
        if self.touchbar is None:
            return None
        try:
            return act(self.touchbar)
        except HelloRefused as e:
            self.refused = e
            self._drop("%s; retrying every %g s" % (e, self.retry))
        except TouchbarError as e:
            self._drop("lost the touch bar (%s); retrying every %g s" % (e, self.retry))
        return None

    def _drop(self, why):
        if why != self._told:
            self.log(why)
            self._told = why
        self.close()
        self._retry_at = self.clock() + self.retry

    def close(self):
        if self.touchbar is not None:
            self.touchbar.close()
        self.touchbar = None
