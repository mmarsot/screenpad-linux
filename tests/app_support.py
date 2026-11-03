"""What the client library's tests share: the checkout on sys.path, a step
clock, and the daemon's own Apps served on its AppServer in a thread."""

import os
import tempfile
import threading
import time

from touchbar_support import REPO, pump

from screenpad_touchbar import common
from screenpad_touchbar.apps import Apps, AppServer

__all__ = ["REPO", "Clock", "DaemonBar"]


class Clock:
    """A clock the test moves."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class DaemonBar:
    """The daemon's own Apps (or apps, a subclass) on its AppServer, served
    in a thread. Hold lock to look at or drive apps."""

    def __init__(self, test, placed=("counter",), apps=None):
        tmp = tempfile.TemporaryDirectory(dir="/tmp")
        test.addCleanup(tmp.cleanup)
        self.environ = {"XDG_RUNTIME_DIR": tmp.name}
        self.path = os.path.join(tmp.name, "screenpad", common.SOCKET_NAME)
        self.apps = apps or Apps()
        self.apps.set_placed(list(placed))
        self.server = AppServer(self.path, self.apps)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def serve(self):
        while not self.stop.is_set():
            with self.lock:
                pump(self.server)

    def wait(self, check, timeout=2.0):
        """Wait until check() holds, called under the lock."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                if check():
                    return
            time.sleep(0.01)
        raise AssertionError("never held")

    def drop(self):
        """Cut every app off, as a daemon restarting does."""
        with self.lock:
            for client in list(self.server.clients.values()):
                self.server._drop(client)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2.0)
        self.server.close()
