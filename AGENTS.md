# AGENTS.md

Guidance for coding agents working in this repository.

## Project

`seto` is a Python 3.12+ CLI that orchestrates Docker Swarm and Docker Compose
deployments, with device/host targeting and automated NFS storage provisioning.
It is distributed as a `uv_build` package exposing the `seto` console script
(`seto = "seto:run"`). `README.md` is the user-facing documentation and the
authoritative reference for CLI behavior; this file only covers how to work on
the code.

## Development environment

Tooling is pinned through [devbox](https://www.jetify.com/devbox/) and exposed
via `direnv` (`.envrc`). Entry into the repo normally activates it. If commands
are missing, run `direnv allow` once.

- Python: 3.12 (`.python-version`), interpreter in `.venv/`.
- Core deps: `docker`, `paramiko`, `pyyaml`.
- Dev deps: `pytest`, `pytest-cov`, `pytest-mock`.

```sh
direnv allow     # load the devbox environment
make install     # recreate .venv and sync deps (uv venv + uv sync)
```

`uv` is provided by devbox. If `uv: command not found`, the devbox environment
is not loaded; activate it or use `.venv/bin/python -m ...` for local runs.

## Commands

```sh
make tests       # uv run pytest tests/ -v
make coverage    # uv run pytest --cov=server   (see note below)
make lint        # devbox run prek run --all-files
make fix         # ruff check . --fix
make clean       # drop caches, .coverage, dist/
```

Direct invocations when the devbox environment is not active:

```sh
.venv/bin/python -m pytest tests/ -q     # run the suite
.venv/bin/ruff check . --fix             # lint/format
.venv/bin/ruff format .                  # formatter
```

Local CLI testing: `uv tool install --editable . --force` links the checkout as
a global `seto` command (like `npm link`); `uv tool uninstall seto` removes it.

Known deviations in existing config — do not "fix" them opportunistically:

- `make coverage` / `coverage-html` pass `--cov=server`, but the package is
  `seto`; coverage is effectively empty. Use `pytest --cov=seto` instead.
- `ruff.toml` sets `[format] quote-style = "single"`, while the pre-commit
  `double-quote-string-fixer` and `[lint.flake8-quotes]` prefer double quotes.
  Existing code is mixed (single quotes dominate). Match the surrounding file
  and let pre-commit be the arbiter; do not mass-convert quotes.
- The suite is currently not fully green: `tests/test_parser.py::test_load_env_file`
  and `tests/test_parser.py::test_resolve_ignores_name_outside_compose_mode`
  fail on `main`. Treat other failures as new.

## Layout

`src/` layout, installed package name `seto`:

```
src/seto/__init__.py     argparse CLI: parser, subcommands, wiring, error exit codes
src/seto/__main__.py     `python -m seto` entry point
src/seto/commands/       one module per subcommand (setup, resolve, deploy, down,
                         check, volumes, mount, umount); each exposes execute_*()
src/seto/core/           parser, docker, swarm, network, volume, traefik, dns,
                         shell, driver, permissions, package metadata, command base
src/seto/drivers/        storage drivers; nfs.py is the only one (nfs:// scheme)
src/seto/shells/         LocalShell and RemoteShell (paramiko) execution backends
tests/                   pytest modules per area: cli, parser, docker, dns, shell
scripts/                 metadata.py (reads pyproject values), digest.py
services/enabled/        example stack definitions
```

Runtime state lives in `.seto/` (`config.json` from `setup`, plus cached
`images/`); it is gitignored and must not be committed.

Architecture rules:

- `commands/*` orchestrate only; parsing and Docker/NFS/network mechanics belong
  in `core/` or `drivers/`.
- A new subcommand needs an `execute_*` function in `src/seto/commands/` plus a
  subparser wired in `src/seto/__init__.py`; the handler signature is
  `(args, driver)`.
- Storage access goes through the `Driver` abstraction (`core/driver.py`), never
  direct remote calls from a command. Unknown driver types exit `40`.
- Exit codes are contractual: bad args `40`, SSH auth failure `41`,
  runtime failure `50`. Preserve them.

## Code style

- 2-space indentation, LF, final newline, no trailing whitespace
  (`.editorconfig`, `ruff.toml`).
- Line length 100 (`ruff.toml`), though `E501` is ignored.
- Every `.py` file starts with the Apache-2.0 header matching
  `LICENSE_HEADER.txt` (year range, `# ===...===` rule, then imports). The
  `insert-license` pre-commit hook adds/updates it automatically — do not
  hand-edit existing headers, and never remove them.
- Explicit type annotations are enforced repository-wide (pygrep hook), e.g.
  `def f(value: str) -> None:`.
- Imports are one-per-line and alphabetically sorted (enforced by
  `reorder-python-imports`), with relative imports inside the `seto` package.
- Avoid `eval` and `logging.warn`; the `debug-statements` hook rejects leftover
  `print`/`pdb` in committed code.
- Test functions are named `test_*` (`[tool.pytest.ini_options]
  python_functions = "test_"`); tests commonly use `pytest-mock`'s `mocker`.

Run `make lint` before considering work done — it runs all pre-commit hooks
(license headers, import order, autopep8, pylint, ruff, pyupgrade, doc8, yaml/
toml checks). Pre-commit is installed by the devbox init hook.

## Workflow conventions

- Commit messages follow Conventional Commits with a scope, as in the existing
  history: `feat(traefik): ...`, `fix(parser): ...`, `refactor(resolve): ...`,
  `chore(release): ...`.
- Releases are managed by `make <patch|minor|major|alpha|beta|rc>` (bumps
  `pyproject.toml` + `uv.lock`, requires a clean tree), then `make release`
  commits and tags, and `make publish` builds and pushes. Do not bump versions
  or tag unless asked.
- Record user-facing changes in `CHANGELOG.md` under the current version with
  the existing sections (Breaking Changes / Features / Improvements / Fixes /
  Testing); see the file's history for tone.
- Keep `README.md` in sync when CLI flags, subcommands, or environment variables
  change. Environment variables are `SETO_*` (`SETO_NAMESPACE`, `SETO_STACK`,
  `SETO_SSH_KEY`, `SETO_IMAGE_PREFIX`, `SETO_ENV_FILE`,
  `SETO_DEFAULT_VOLUME_MOUNT_MODE`, `SETO_DEFAULT_NFS_OPTIONS`).
- Never commit secrets or `.seto/` state. `make env` reveals git-secret files and
  is only for local use.
