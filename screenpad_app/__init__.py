"""screenpad_app: the client library for touch bar apps.

An app is a separate program that uses the ScreenPad touch bar only through
its app socket (docs/app-api.md). This package is what such an app needs and
nothing more: standard library only, no import of the daemon, installable on
its own (pip install from this repository's git URL; the root pyproject.toml
builds only this package).

- common: the protocol version it speaks (PROTOCOL), the socket's path, the
  bar's limits an app writes within, clip(), and the errors;
- client: Touchbar, one connection (hello and its answer, tiles, pages,
  questions, what comes back), and Link, a connection kept up.
"""

from .client import Link, Touchbar
from .common import (
    MIN_PROTOCOL,
    PROTOCOL,
    HelloRefused,
    ProtocolMismatch,
    TouchbarError,
    TouchbarGone,
    clip,
    socket_path,
)

__all__ = [
    "MIN_PROTOCOL",
    "PROTOCOL",
    "HelloRefused",
    "Link",
    "ProtocolMismatch",
    "Touchbar",
    "TouchbarError",
    "TouchbarGone",
    "clip",
    "socket_path",
]
