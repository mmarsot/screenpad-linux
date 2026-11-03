# Development checks; none of them needs root or the hardware. The programs
# themselves need nothing from here: install.sh copies the files.
#   make check         everything CI runs: format-check, lint, typecheck, test
#   make format        apply ruff and Prettier formatting
# Python tools (ruff, mypy) come from uv (pyproject.toml, uv.lock), the
# extension's (Prettier, ESLint) from npm (package.json, package-lock.json).

UV = uv run --frozen --only-group dev
NPM = node_modules/.bin

.PHONY: check format format-check lint typecheck test

check: format-check lint typecheck test

format: node_modules
	$(UV) ruff format .
	$(NPM)/prettier --write 'gnome-shell/**/*.js' eslint.config.js

format-check: node_modules
	$(UV) ruff format --check .
	$(NPM)/prettier --check 'gnome-shell/**/*.js' eslint.config.js

lint: node_modules
	$(UV) ruff check .
	$(NPM)/eslint

typecheck:
	$(UV) mypy

test:
	python3 -m unittest discover -s tests

node_modules: package-lock.json
	npm ci
	@touch node_modules
