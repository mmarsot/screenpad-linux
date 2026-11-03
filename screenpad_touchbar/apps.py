"""The programs on the app socket, their tiles and asks, and the socket itself."""

import json
import os
import socket
import struct
import time

from .asks import Asks
from .common import (
    HOME_COLUMNS,
    HOME_ROWS,
    MAX_APPS,
    MAX_LINE,
    MAX_NOTIFY_WAITING,
    MAX_OUT,
    MAX_PAGES,
    TILE_NAME,
    TouchbarError,
)
from .config import NotifyPolicy
from .notify import mirror_refusal, notification_ask, parse_notify
from .protocol import ProtocolError, known_fields, parse_ask
from .widgets import look_fields, parse_page


class Apps:
    """The programs on the socket: who provides which tile and which page
    of the apps surface, and their asks.

    A tile or a page belongs to the first app that provides it, until that
    app goes. on_tile_gone(name) lets the bar end touches on a tile whose
    app left; the surface sees a page go on its own (pages).
    """

    def __init__(self, clock=time.monotonic):
        self.clients = []
        self.owner = {}  # tile name -> client
        self.look = {}  # tile name -> what it shows
        self.placed = set()  # tile names the config gives a spot
        self.pages = {}  # page name -> widgets.Page, in the order given
        self.asks = Asks(clock)
        self.notify_policy = NotifyPolicy()
        self.on_tile_gone = lambda name: None
        self.dirty = False

    def take_changed(self):
        changed = self.dirty or self.asks.changed
        self.dirty = self.asks.changed = False
        return changed

    def connect(self, client):
        if len(self.clients) >= MAX_APPS:
            client.send({"op": "error", "message": "too many apps (%d)" % MAX_APPS})
            return False
        client.name = None
        self.clients.append(client)
        return True

    def disconnect(self, client):
        if client not in self.clients:
            return
        self.clients.remove(client)
        self.asks.drop(client)
        for name in [n for n, c in self.owner.items() if c is client]:
            del self.owner[name]
            self.look.pop(name, None)
            self.on_tile_gone(name)
            self.dirty = True
        for name in [n for n, p in self.pages.items() if p.client is client]:
            del self.pages[name]
            self.dirty = True

    def set_placed(self, names):
        self.placed = set(names)

    def online(self, name):
        return name in self.owner

    def provided(self):
        return {name: self.look.get(name, {}) for name in self.owner}

    def names(self):
        return sorted({c.name for c in self.clients if c.name})

    def event(self, name, kind, inside=None):
        client = self.owner.get(name)
        if client is None:
            return
        msg = {"op": "event", "tile": name, "type": kind}
        if inside is not None:
            msg["inside"] = inside
        client.send(msg)

    def press(self, client, page, widget, kind):
        """A touch on a widget of client's page: "down", then "tap", "long"
        or "cancel", the end even when the page has gone since; nothing
        once the app has disconnected."""
        if client in self.clients:
            client.send({"op": "event", "page": page, "widget": widget, "type": kind})

    def handle(self, client, msg):
        """One message from an app; errors go back to it."""
        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                client.send({"op": "pong"})
            elif op == "hello":
                self._hello(client, msg)
            elif client.name is None:
                raise ProtocolError("say hello first")
            elif op == "set":
                self._set(client, msg)
            elif op == "ask":
                self.asks.add(parse_ask(client, msg, self.asks.clock()))
            elif op == "page":
                self._page(client, msg)
            elif op == "unpage":
                self._unpage(client, msg)
            elif op == "notify":
                self._notify(client, msg)
            elif op == "cancel":
                known_fields(msg, {"id"})
                if not self.asks.cancel(client, msg.get("id")):
                    raise ProtocolError("no waiting question %r" % msg.get("id"))
            else:
                raise ProtocolError("unknown op %r" % op)
        except ProtocolError as e:
            reply = {"op": "error", "request": op if isinstance(op, str) else None, "message": str(e)}
            if isinstance(msg, dict) and isinstance(msg.get("id"), str):
                reply["id"] = msg["id"]
            client.send(reply)

    def _hello(self, client, msg):
        known_fields(msg, {"app", "tiles"})
        if client.name is not None:
            raise ProtocolError("hello was already said")
        name = msg.get("app")
        if not isinstance(name, str) or not TILE_NAME.fullmatch(name):
            raise ProtocolError('app must be a name like "agents" (lower case, at most 32)')
        tiles = msg.get("tiles", [])
        if (
            not isinstance(tiles, list)
            or len(tiles) > 8
            or not all(isinstance(t, str) and TILE_NAME.fullmatch(t) for t in tiles)
        ):
            raise ProtocolError("tiles must be a list of at most 8 tile names")
        client.name = name
        taken = []
        for tile in dict.fromkeys(tiles):
            if tile in self.owner:
                taken.append(tile)
                continue
            self.owner[tile] = client
            self.look[tile] = {}
            self.dirty = True
        mine = [t for t in dict.fromkeys(tiles) if t not in taken]
        client.send(
            {
                "op": "welcome",
                "app": name,
                "placed": [t for t in mine if t in self.placed],
                "unplaced": [t for t in mine if t not in self.placed],
                "taken": taken,
            }
        )

    def _notify(self, client, msg):
        """A GNOME notification, from the extension: a card if the rules
        say so, else answered "ignored" at once. One already waiting with
        the same id was updated, and the newer version replaces it."""
        card = parse_notify(msg)
        old = self.asks.find(client, card["id"])
        why = mirror_refusal(card, self.notify_policy)
        if (
            why is None
            and old is None
            and MAX_NOTIFY_WAITING <= sum(a.client is client and a.notification for a in self.asks.queue)
        ):
            why = "too many notifications waiting"
        if why is not None:
            if old is not None:
                self.asks.cancel(client, card["id"])
                return
            client.send({"op": "answer", "id": card["id"], "option": None, "reason": "ignored", "why": why})
            return
        ask = notification_ask(client, card, self.asks.clock(), self.notify_policy.timeout)
        if not self.asks.replace(ask):
            self.asks.add(ask)

    def _page(self, client, msg):
        """An app's page for the apps surface; it replaces the previous one."""
        page = parse_page(client, msg)
        old = self.pages.get(page.name)
        if old is not None and old.client is not client:
            raise ProtocolError("page %r is another app's" % page.name)
        if old is None and sum(p.client is client for p in self.pages.values()) >= MAX_PAGES:
            raise ProtocolError("at most %d pages per app" % MAX_PAGES)
        if old is None and len(self.pages) >= HOME_COLUMNS * HOME_ROWS:
            raise ProtocolError("the apps page is full (%d pages)" % (HOME_COLUMNS * HOME_ROWS))
        if (
            old is None
            or [w.key() for w in old.widgets] != [w.key() for w in page.widgets]
            or ((old.look, old.columns, old.rows) != (page.look, page.columns, page.rows))
        ):
            self.pages[page.name] = page
            self.dirty = True

    def _unpage(self, client, msg):
        known_fields(msg, {"page"})
        name = msg.get("page")
        page = self.pages.get(name) if isinstance(name, str) else None
        if page is None or page.client is not client:
            raise ProtocolError("page %r is not yours" % name)
        del self.pages[name]
        self.dirty = True

    def _set(self, client, msg):
        known_fields(msg, {"tile", "icon", "label", "text", "badge", "tone"})
        tile = msg.get("tile")
        if self.owner.get(tile) is not client:
            raise ProtocolError("tile %r is not yours; provide it in hello" % tile)
        look = look_fields(msg, {"label": 32, "text": 48, "badge": 4})
        if look != self.look.get(tile):
            self.look[tile] = look
            self.dirty = True


class AppClient:
    """One connection to the app socket."""

    def __init__(self, sock):
        self.sock = sock
        self.rbuf = b""
        self.wbuf = b""
        self.name = None
        # Set when it must be cut off: a too long line, or replies piling up.
        self.broken = False

    def fileno(self):
        return self.sock.fileno()

    def send(self, msg):
        if self.broken:
            return
        self.wbuf += (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        if len(self.wbuf) > MAX_OUT:
            self.broken = True


def peer_uid(sock):
    """The uid of the process at the other end of a Unix socket, or None."""
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError:
        return None
    return struct.unpack("3i", creds)[1]


class AppServer:
    """The listening socket and its connections; the daemon's loop drives it."""

    def __init__(self, path, apps):
        self.path, self.apps = path, apps
        self.clients = {}  # fd -> AppClient
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        if os.path.exists(path):
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
            try:
                probe.connect(path)
            except OSError:
                os.unlink(path)  # left by a daemon that died
            else:
                raise TouchbarError("another daemon answers on %s" % path)
            finally:
                probe.close()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC)
        old = os.umask(0o177)
        try:
            self.sock.bind(path)
        finally:
            os.umask(old)
        self.sock.listen(8)

    def rfds(self):
        return [self.sock.fileno()] + list(self.clients)

    def wfds(self):
        return [fd for fd, c in self.clients.items() if c.wbuf]

    def owns(self, fd):
        return fd == self.sock.fileno() or fd in self.clients

    def readable(self, fd):
        if fd == self.sock.fileno():
            self._accept()
            return
        client = self.clients.get(fd)
        if client is None:
            return
        try:
            data = client.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._drop(client)
            return
        client.rbuf += data
        while b"\n" in client.rbuf:
            line, client.rbuf = client.rbuf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                client.send({"op": "error", "message": "not a JSON object"})
                continue
            self.apps.handle(client, msg)
        if len(client.rbuf) > MAX_LINE:
            client.broken = True

    def _accept(self):
        try:
            conn, _ = self.sock.accept()
        except BlockingIOError:
            return
        if peer_uid(conn) != os.getuid():
            conn.close()
            return
        conn.setblocking(False)
        client = AppClient(conn)
        if self.apps.connect(client):
            self.clients[conn.fileno()] = client
        else:
            self._write(client)
            conn.close()

    def flush(self):
        """Send what is waiting, and cut off broken clients."""
        for client in list(self.clients.values()):
            if client.broken:
                self._drop(client)
            elif client.wbuf:
                self._write(client)

    def _write(self, client):
        try:
            n = client.sock.send(client.wbuf)
        except BlockingIOError:
            return
        except OSError:
            self._drop(client)
            return
        client.wbuf = client.wbuf[n:]

    def _drop(self, client):
        self.clients.pop(client.fileno(), None)
        self.apps.disconnect(client)
        client.sock.close()

    def close(self):
        for client in list(self.clients.values()):
            self._drop(client)
        self.sock.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
