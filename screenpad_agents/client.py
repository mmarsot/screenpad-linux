"""The connection to the touch bar's app socket: one JSON object per line.

It says hello for the app's tiles, sends a message only when it differs from
the last one sent for that tile or page (the daemon would ignore it anyway,
and this keeps the socket quiet), and splits what the daemon sends into
messages.
"""

import json
import socket

from .common import APP

MAX_LINE = 16384


class TouchbarGone(Exception):
    pass


class Touchbar:
    def __init__(self, path, tiles):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(2.0)
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise
        self._buf = b""
        self._sent = {}
        self.send({"op": "hello", "app": APP, "tiles": list(tiles)})

    def fileno(self):
        return self.sock.fileno()

    def send(self, msg):
        try:
            self.sock.sendall(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
        except OSError as e:
            raise TouchbarGone(str(e))

    def show(self, messages):
        """Send each page or tile message that changed since it was last sent."""
        for msg in messages:
            key = (msg["op"], msg.get("tile") or msg.get("page"))
            if self._sent.get(key) != msg:
                self.send(msg)
                self._sent[key] = msg

    def receive(self):
        """The messages that arrived; TouchbarGone when the daemon closed."""
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
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
                out.append(msg)
        return out

    def close(self):
        self.sock.close()
