"""What the client library's tests share: the checkout on sys.path, a step
clock, and a log to collect into."""

import os
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)


class Clock:
    """A clock the test moves."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now
