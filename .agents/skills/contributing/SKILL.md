---
name: contributing
description: Load before any change - the checks (make check, format, lint, typecheck, tests) and CI, the file size limit, documenting what you write, working in steps, no-mistakes review, and keeping skills current. Then load the skill of each area the change touches.
---

# Contributing

The general rules for any change. Each area's own rules are in its skill: after this one, load the skill of every area the change touches, from the index in [AGENTS.md](../../../AGENTS.md).

Read first, as needed:

- [README.md](../../../README.md): what the project does, the supported hardware, and what was tested.
- [docs/development.md](../../../docs/development.md): the code layout, the checks, and what the tests cover.

## Commands

One command each; none needs root or the hardware. `make` installs the npm tools (`npm ci`) on first use; the Python tools run through [uv](https://docs.astral.sh/uv/).

- `make check`: everything CI runs, in order: format check, lint, typecheck, tests. **Run it before finishing every change.**
- `make format-check`: `ruff format --check` on the Python, Prettier `--check` on the extension's JavaScript. `make format` applies both.
- `make lint`: `ruff check` and ESLint (`eslint.config.js`).
- `make typecheck`: mypy with `check_untyped_defs` on `screenpad.py`, `touchbar.py` and `tests/`.
- `make test`: `python3 -m unittest discover -s tests`, stdlib only, so it runs without uv or npm.
- The tool versions are pinned: ruff and mypy in `pyproject.toml` (`[dependency-groups] dev`) and `uv.lock`; Prettier and ESLint in `package.json` and `package-lock.json`. Their settings live in `pyproject.toml`, `.prettierrc.json` and `eslint.config.js`.
- Prettier formats only `gnome-shell/**/*.js` and `eslint.config.js`, and ruff only `*.py`; never run them on `docs/`, `README.md`, JSON or CSS, where they would rewrite hand-kept tables and snippets.
- CI (`.github/workflows/ci.yml`) runs the format check, lint and typecheck in one job and the tests on Python 3.11 and 3.14 in another, on every push and pull request. `.no-mistakes.yaml` gives the no-mistakes pipeline the same commands.

## Rules

- **The checks pass before a change is done.** Formatting, lint, typecheck and tests are all green under `make check`. Fix the code rather than silencing a check; a `# type: ignore[code]` or `# noqa: CODE` names its code and says why on the same line.
- **The programs stay standard-library Python 3.11+.** `screenpad.py` and `touchbar.py` run from the checkout and as installed copies, with no third-party import. Tooling goes in the dev dependency group or `devDependencies`, never in what `install.sh` installs.
- **Keep files small: about 500 lines per module is the soft limit.** Split a module along its responsibilities instead of growing it past that, and put new code in the module of its area. Over it today: `touchbar.py` (about 3,200 lines), `screenpad.py` (about 1,150), `tests/test_touchbar.py` and `tests/test_screenpad.py`; `gnome-shell/screenpad-touchbar@screenpad/extension.js` and `tests/test_touchbar_cards.py` are at it. Do not grow them; move code out when a change reworks a part of them.
- **Document what you write, in the same change.** A behavior, command, setting, file or installed path that changes updates the page that describes it (README and `docs/`, see below), the test list in [docs/development.md](../../../docs/development.md#tests) when tests change, and the matching skill.
- **Work in steps.** A step is one coherent change with its tests, docs and skill update, `make check` green, and committed. Finish and document a step before moving on to the next; a large change goes in as several such steps (and pull requests), each complete on its own.
- **Every change goes through no-mistakes review.** Commit on a branch and run it through the no-mistakes gate (the `/no-mistakes` skill, or `git push no-mistakes <branch>`): it reviews, tests, lints, pushes, opens the pull request and watches CI. Never push to `main` directly.
- **Never touch the machine you work on.** Tests and manual checks use temporary directories, fake sysfs trees, `DESTDIR` and stub commands; never run `install.sh`, the daemon or `screenpad` against the live system, its config or the installed extension as part of a change.

## Where docs go

| Page | Owns |
|---|---|
| [README.md](../../../README.md) | overview, supported hardware and what was tested, quick start, limitations, troubleshooting |
| [docs/install.md](../../../docs/install.md) | installing, updating and removing, step by step |
| [docs/display.md](../../../docs/display.md) | how `screenpad` works, every command, Secure Boot, rollback |
| [docs/touchbar.md](../../../docs/touchbar.md) | how the touch bar works and behaves |
| [docs/configuration.md](../../../docs/configuration.md) | `touchbar.toml`, every setting |
| [docs/app-api.md](../../../docs/app-api.md) | the touch bar socket protocol for apps and questions |
| [docs/development.md](../../../docs/development.md) | code layout, the checks, what the tests cover, the screenshots |

## Tests

- `tests/` holds stdlib `unittest` tests: `test_screenpad.py` (the display CLI and `install.sh`), `test_touchbar.py` (the touch bar), `test_touchbar_cards.py` (attention cards and notifications, plus static checks of the extension), `test_skills.py` (the skills and this index).
- They import the programs from the checkout (`sys.path` set to the repository root). A new test goes in the file of its area; a new area gets its own file.
- Some tests are source contract checks: they read the extension's source with regular expressions to compare it with the daemon's constants. They are a known weakness, not a pattern to copy: they show only that text is in the file, and a refactor can break them. Replace them with behavioral tests of the extension's logic when you can, add no new ones, and after reformatting or restructuring the extension run `make test`, not only the JavaScript checks.

## Skills are part of the code

- The skills live in `.agents/skills/<name>/SKILL.md`; `.claude/skills` is a committed symlink to `.agents/skills`. Edit only the `.agents/skills/` copy.
- **Update the matching skill in the same change** whenever you change an area's rules, invariants, owning files or tests. No approval is needed to add, edit, split or prune a skill.
- Skills point and the docs explain: behavior and rationale go in `docs/`; a skill keeps only what an implementer must check, and links the doc page instead of copying it.
- A new skill is a folder named like its `name`, a `SKILL.md` with `name` and a `description` saying which files and changes it covers, and a row in the AGENTS.md index. `tests/test_skills.py` checks all of that and that every relative link in a skill resolves.
- Keep a skill to what nearly every change in its area needs, and keep entries concise. Prefer rewriting or pruning an entry over appending one.
