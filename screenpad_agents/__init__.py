"""screenpad-agents: the agent app for the ScreenPad touch bar.

A separate program, and only a client of the touch bar's app socket
(docs/app-api.md), through the client library screenpad_app: it imports
nothing of screenpad_touchbar. It shows every
agent Herdr reports (busy, idle, blocked; Herdr's own state is authoritative)
as a widget on the apps page and as a tile in the bar, joins them to
firstmate's tasks through the endpoints firstmate records, focuses an agent's
pane through Herdr when its widget is tapped, and shows how many Captain's
Calls are waiting. docs/agents.md is the reference.

The modules, imported one way: common, config, herdr, keymap, screen,
firstmate, card, fleet, app, cli.
"""
