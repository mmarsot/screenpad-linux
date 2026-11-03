"""agents.toml: the app's config, and the opt-in it is.

The app runs only with a config: without one it refuses to start, so
installing it changes nothing until you write one (docs/agents.md). The config
is strict, as touchbar.toml is: an unknown table or key, or a bad value, is a
ConfigError naming it.

  [herdr]
  command = "herdr"            # or a list: a wrapper and its arguments
  session = "default"          # the Herdr session the agents run in
  poll = 2                     # seconds between two reads of Herdr

  [firstmate]                  # optional: tasks and Captain's Calls
  home = "/path/to/firstmate"  # FM_HOME
  code = "/path/to/firstmate"  # the checkout with bin/fm-bearings-snapshot.sh
                               # and bin/fm-control.sh
  poll = 60                    # seconds, at least 60
"""

import os
import re
import tomllib

from .common import FIRSTMATE_POLL, FIRSTMATE_POLL_RANGE, HERDR_POLL, HERDR_POLL_RANGE, ConfigError

SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
BEARINGS = os.path.join("bin", "fm-bearings-snapshot.sh")
CONTROL = os.path.join("bin", "fm-control.sh")


class Config:
    """The settings: Herdr's command and session, and firstmate's paths
    (home None when the config has no [firstmate])."""

    __slots__ = ("bearings", "control", "firstmate_poll", "herdr", "herdr_poll", "home", "path", "session")

    def __init__(
        self, path, herdr, session, herdr_poll, home=None, bearings=None, firstmate_poll=FIRSTMATE_POLL, control=None
    ):
        self.path, self.herdr, self.session, self.herdr_poll = path, herdr, session, herdr_poll
        self.home, self.bearings, self.firstmate_poll, self.control = home, bearings, firstmate_poll, control


def load(path):
    """The Config in path; ConfigError when it is missing or wrong."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError("%s does not exist; the agent app runs only once you write it (see docs/agents.md)" % path)
    except OSError as e:
        raise ConfigError("cannot read %s: %s" % (path, e.strerror))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("%s: %s" % (path, e))
    return parse(path, data)


def parse(path, data):
    _known(path, data, {"herdr", "firstmate"}, "table")
    herdr = _table(path, data, "herdr")
    if herdr is None:
        raise ConfigError("%s: [herdr] is required, with at least its session" % path)
    _known(path, herdr, {"command", "session", "poll"}, "key in [herdr]")
    command = _command(path, herdr.get("command", "herdr"))
    session = herdr.get("session")
    if not isinstance(session, str) or not SESSION.fullmatch(session):
        raise ConfigError('%s: [herdr] session must be a Herdr session name, like "default"' % path)
    herdr_poll = _seconds(path, herdr, "herdr", HERDR_POLL, HERDR_POLL_RANGE)
    config = Config(path, command, session, herdr_poll)
    fm = _table(path, data, "firstmate")
    if fm is not None:
        _known(path, fm, {"home", "code", "poll"}, "key in [firstmate]")
        config.home = _directory(path, fm, "home")
        code = _directory(path, fm, "code")
        config.bearings, config.control = os.path.join(code, BEARINGS), os.path.join(code, CONTROL)
        for script in (BEARINGS, CONTROL):
            if not os.access(os.path.join(code, script), os.X_OK):
                raise ConfigError("%s: [firstmate] code has no executable %s" % (path, script))
        config.firstmate_poll = _seconds(path, fm, "firstmate", FIRSTMATE_POLL, FIRSTMATE_POLL_RANGE)
    return config


def _known(path, table, allowed, what):
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError("%s: unknown %s %s" % (path, what, ", ".join(repr(k) for k in unknown)))


def _table(path, data, name):
    table = data.get(name)
    if table is not None and not isinstance(table, dict):
        raise ConfigError("%s: %s must be a table, [%s]" % (path, name, name))
    return table


def _command(path, value):
    words = [value] if isinstance(value, str) else value
    if not isinstance(words, list) or not words or not all(isinstance(w, str) and w for w in words):
        raise ConfigError('%s: [herdr] command must be a program, or a list of words like ["herdr"]' % path)
    return list(words)


def _seconds(path, table, name, default, bounds):
    value = table.get("poll", default)
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ConfigError("%s: [%s] poll must be %g to %g seconds" % (path, name, low, high))
    return float(value)


def _directory(path, table, key):
    value = table.get(key)
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ConfigError("%s: [firstmate] %s must be an absolute path" % (path, key))
    if not os.path.isdir(value):
        raise ConfigError("%s: [firstmate] %s: %s is not a directory" % (path, key, value))
    return value
