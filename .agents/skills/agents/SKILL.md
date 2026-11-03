---
name: agents
description: Load before changing the agent app - agents.py and the screenpad_agents package (reading Herdr's agents and focusing a pane, firstmate's bearings reader and the Captain's Call count, the task join by endpoint, the Agents page and the agents and calls tiles, agents.toml and its opt-in, the loop and its threads) or its tests (tests/test_agents_*.py).
---

# Agent app

Owning files: `screenpad_agents/` (`common`, `config`, `herdr`, `firstmate`, `fleet`, `client`, `app`, `cli`, imported in that order, one way) and `agents.py`, the launcher that runs its `cli.main()`. Installed as `/usr/local/lib/screenpad/` next to the other programs, with `/usr/local/bin/screenpad-agents` a link and `systemd/screenpad-agents.service` its user unit (see the [install](../install/SKILL.md) skill).

Read first:

- [docs/agents.md](../../../docs/agents.md): what it shows, the config, how it reads Herdr and firstmate.
- [docs/app-api.md](../../../docs/app-api.md): the socket protocol it speaks, its limits and the touch rules (arming, cancels) it relies on.

## Rules

- **An app like any other.** It talks to the touch bar only through the socket and **imports nothing of `screenpad_touchbar`** (`test_imports_nothing_of_the_daemon`). It finds the socket and writes within the limits as docs/app-api.md gives them (`common.py`); a protocol change is the [touchbar](../touchbar/SKILL.md) skill's, then this app follows. Standard library only, shebang `python3 -I`, like the other programs.
- **Off until configured.** Without `agents.toml` (or `--config`) every command exits 2 before touching anything; install never enables the unit, and the unit has `ConditionPathExists`. Nothing defaults to a firstmate home or checkout: without `[firstmate]` nothing of firstmate is read. The config is strict (unknown keys and bad values are `ConfigError`); a new setting needs parsing, its error, docs/agents.md and tests.
- **Herdr is the authority** on which agents run and their state; the app shows `agent list` as it is, in Herdr's order, and never infers state. **Every Herdr call passes `--session <configured session>`**; none relies on the ambient session.
- **firstmate through its reader only**: `FM_HOME=<home> <code>/bin/fm-bearings-snapshot.sh --json --fields endpoints`, with no other `FM_*` variable passed on, at most every `[firstmate] poll` (60 s or more) plus on demand when Captain's Call is tapped. Never parse firstmate's files. The count is `len(decisions_open) + len(contributions.captain)`; the join is the endpoint target `<session>:<pane id>`.
- **This version only reads and focuses.** It sends no keys, reads no pane and writes nothing to firstmate. Adding any of those is a new, reviewed step with its own safety rules (keys only to a pane still blocked on the dialog drawn).
- **Slow calls stay off the loop**: Herdr and firstmate run in their threads (`app.py`) and hand results through a queue and a wake pipe, so a touch is handled at once. A worker never dies on an error: any exception from a read or a focus is shown and logged like the source's own errors (`test_an_unexpected_error_keeps_both_threads_going`). A widget's id is derived from its pane (`fleet.widget_id`), and `Fleet.pane_of` answers only for the page last built, so a tap means the agent that was drawn.
- Keep widget text free of anything that changes on every read (times, counters): a widget that changes re-arms for 0.3 s and cancels a finger on it.

## Tests

- `tests/agents_support.py` (the checkout on `sys.path`, a fake `herdr` script, a fake firstmate checkout whose reader prints an `fm-bearings.v1` model, `FakeHerdr`, `FakeFirstmate`, `config()`), then one file per area: `test_agents_config.py` (the config, the opt-in, `show`), `test_agents_sources.py` (the Herdr client, firstmate's reader), `test_agents_fleet.py` (the page and tiles through the daemon's own `Apps` validation), `test_agents_app.py` (the loop against the daemon's own `AppServer`, the import rule, `run` and SIGTERM).
- **Never run against a live Herdr session or a real firstmate home**: tests use the fakes. A manual check uses an isolated Herdr session with fake agents and a throwaway firstmate home.
- [docs/development.md](../../../docs/development.md#tests) lists what these tests cover; keep it in step.
