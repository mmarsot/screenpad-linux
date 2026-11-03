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
- `make typecheck`: mypy with `check_untyped_defs` on the launchers `screenpad.py` and `touchbar.py`, their packages `screenpad_display/` and `screenpad_touchbar/`, and `tests/`.
- `make test`: `python3 -m unittest discover -s tests`, stdlib only, so it runs without uv or npm. The extension's tests run its plain modules with node; they are skipped without node, except under CI.
- The tool versions are pinned: ruff and mypy in `pyproject.toml` (`[dependency-groups] dev`) and `uv.lock`; Prettier and ESLint in `package.json` and `package-lock.json`. Their settings live in `pyproject.toml`, `.prettierrc.json` and `eslint.config.js`.
- Prettier formats only `gnome-shell/**/*.js` and `eslint.config.js`, and ruff only `*.py`; never run them on `docs/`, `README.md`, JSON or CSS, where they would rewrite hand-kept tables and snippets.
- CI (`.github/workflows/ci.yml`) runs the format check, lint and typecheck in one job and the tests on Python 3.11 and 3.14 (with node, for the extension) in another, on every push and pull request. `.no-mistakes.yaml` gives the no-mistakes pipeline the same commands.

## Rules

- **The checks pass before a change is done.** Formatting, lint, typecheck and tests are all green under `make check`. Fix the code rather than silencing a check; a `# type: ignore[code]` or `# noqa: CODE` names its code and says why on the same line.
- **The programs stay standard-library Python 3.11+.** `screenpad.py` and `touchbar.py` and their packages run from the checkout and as installed copies, with no third-party import. Tooling goes in the dev dependency group or `devDependencies`, never in what `install.sh` installs.
- **Keep files small: about 500 lines per module is the soft limit.** Split a module along its responsibilities instead of growing it past that, and put new code in the module of its area. Every file is under it today, the extension's and the tests included (tests go one file per area); keep it so.
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

- `tests/` holds stdlib `unittest` tests, one file per area: `test_screenpad*.py` (the display CLI), `test_install.py` (`install.sh`), `test_touchbar_*.py` (the touch bar; the area skills list them), `test_skills.py` (the skills and this index). `screenpad_support.py` and `touchbar_support.py` hold what each program's tests share and put the checkout on `sys.path`; isort keeps them imported before the packages (`[tool.ruff.lint.isort]` in `pyproject.toml`).
- They import the programs' packages from the checkout (the support modules set `sys.path` to the repository root). A new test goes in the file of its area; a new area gets its own file, added to the test modules listed under `[[tool.mypy.overrides]]` in `pyproject.toml`.
- The extension is tested through its plain modules (`contract.js`, `scene.js`, `cards.js`), run under node on what the daemon builds. Reading the extension's source with regular expressions shows only that text is in a file: two such checks remain, for names crossing into `extension.js` (the [gnome-extension](../gnome-extension/SKILL.md) skill), and no new one is added. After changing the extension run `make test`, not only the JavaScript checks.

## Skills are part of the code

- The skills live in `.agents/skills/<name>/SKILL.md`; `.claude/skills` is a committed symlink to `.agents/skills`. Edit only the `.agents/skills/` copy.
- **Update the matching skill in the same change** whenever you change an area's rules, invariants, owning files or tests. No approval is needed to add, edit, split or prune a skill.
- Skills point and the docs explain: behavior and rationale go in `docs/`; a skill keeps only what an implementer must check, and links the doc page instead of copying it.
- A new skill is a folder named like its `name`, a `SKILL.md` with `name` and a `description` saying which files and changes it covers, and a row in the AGENTS.md index. `tests/test_skills.py` checks all of that and that every relative link in a skill resolves.
- Keep a skill to what nearly every change in its area needs, and keep entries concise. Prefer rewriting or pruning an entry over appending one.
