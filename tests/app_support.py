"""What the client library's tests share: the checkout on sys.path, a step
clock, and the daemon's own Apps served on its AppServer in a thread."""

import os
import select
import tempfile
import threading
import time

from touchbar_support import REPO

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
    in a thread. Hold lock to look at or drive apps.

    The thread waits in select without the lock and takes it only to handle
    what is ready. Holding it across the wait and taking it again at once
    starves the test's thread on Python before 3.13, whose locks are not
    fair: tests stalled for minutes on CI there."""

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
                rfds, wfds = self.server.rfds(), self.server.wfds()
            try:
                ready, _, _ = select.select(rfds, wfds, [], 0.01)
            except (OSError, ValueError):
                continue  # drop() closed a client meanwhile
            with self.lock:
                for fd in ready:
                    if self.server.owns(fd):
                        self.server.readable(fd)
                self.server.flush()

    def wait(self, check, timeout=2.0):
        """Wait until check() holds, called under the lock."""
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            if not self.lock.acquire(timeout=left):
                break
            try:
                if check():
                    return
            finally:
                self.lock.release()
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
