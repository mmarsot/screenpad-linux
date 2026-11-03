"""The screenpad-agents command: run, and show."""

import argparse
import json
import signal
import sys

from .app import App
from .common import AgentsError, config_path, socket_path
from .config import load
from .firstmate import Firstmate, FirstmateError
from .fleet import Fleet
from .herdr import Herdr, HerdrError


def clients(config):
    herdr = Herdr(config.herdr, config.session)
    firstmate = None
    if config.home:
        firstmate = Firstmate(
            config.home,
            config.bearings,
            config.control,
            board=config.board,
            lavish=config.lavish,
            opener=config.opener,
        )
    return herdr, firstmate


def cmd_run(args):
    config = load(args.config)
    herdr, firstmate = clients(config)
    app = App(config, herdr, firstmate, socket_path())
    signal.signal(signal.SIGTERM, lambda *_: app.stop())
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_show(args):
    """Read Herdr and firstmate once and print what the touch bar would show."""
    config = load(args.config)
    herdr, firstmate = clients(config)
    fleet = Fleet(config.session, firstmate is not None)
    try:
        fleet.set_agents(herdr.agents())
    except HerdrError as e:
        fleet.set_agents(None, str(e))
    if firstmate is not None:
        try:
            fleet.set_bearings(firstmate.read())
        except FirstmateError as e:
            fleet.set_bearings(None, str(e))
    if args.json:
        print(json.dumps(fleet.messages(), indent=2))
        return 0
    print("Herdr session %s: %s" % (config.session, fleet.summary()))
    if fleet.herdr_error:
        print("  " + fleet.herdr_error)
    for agent in fleet.agents or []:
        task = fleet.task(agent)
        print("  %-8s %-8s %-10s %s" % (agent.pane, agent.kind, agent.state, task.id if task else "-"))
    if firstmate is not None:
        tile = fleet.calls_tile()
        print("Captain's Call: %s" % tile["text"])
        if fleet.firstmate_error:
            print("  " + fleet.firstmate_error)
        try:
            url = firstmate.board_url()
        except FirstmateError as e:
            print("Decisions board: unknown (%s)" % e)
        else:
            print("Decisions board: %s" % (url or "none open; ask firstmate for /bearings lavish"))
    return 1 if fleet.herdr_error else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="screenpad-agents",
        description="The agent app for the ScreenPad touch bar: Herdr's agents and firstmate's Captain's Calls.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (
        ("run", "connect to the touch bar and keep its agents page and tiles up to date"),
        ("show", "read Herdr and firstmate once and print what the touch bar would show"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--config", default=None, help="the config file (default: ~/.config/screenpad/agents.toml)")
        if name == "show":
            p.add_argument("--json", action="store_true", help="print the socket messages instead")
    args = parser.parse_args(argv)
    try:
        args.config = args.config or config_path()
        return {"run": cmd_run, "show": cmd_show}[args.command](args)
    except AgentsError as e:
        print("screenpad-agents: %s" % e, file=sys.stderr)
        return 2
