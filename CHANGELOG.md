# Changelog

All notable changes to the public Lilith CLI repository are documented here.

## Unreleased

### Fixed

- `/format --check` no longer runs the writing variant of prettier, gofmt or rustfmt, and never goes through a shell.
- `/test` shows pytest's results when pytest reports errors, and runs in the working directory with the project's `.venv` instead of a hard-coded private layout.
- `/macro` recording and playback work from the REPL: `/macro stop` stops, and playback reaches every slash command.
- `/watch` records file events (the observer called a method that did not exist).
- `lilith web --dev` starts; the web frontend type-checks, builds and speaks the server's protocol.
- Governance no longer crashes when an agent has a sandbox violation; audit filters accept `datetime` bounds; `OntologyGraph()` works with its default in-memory database.
- Memory stores close their SQLite connections, and unreadable JSON stores are copied aside before a save can overwrite them.
- `lilith.ps1` no longer forces the `fabric` provider; set the user variable `LILITH_PROVIDER_OVERRIDE` to opt in.
- `/calc` refuses integer powers large enough to hang the REPL.

### Changed

- Slash commands are declared once in `lilith_cli/slash_router.py`; handlers moved from `extra_commands.py` to `lilith_cli/slash_commands/` (the old module re-exports them).
- Small utilities (`/calc`, `/uuid`, `/qr`, `/timer`, ...) are the bundled `utilities` command plugin; packages can add commands through the `lilith_cli.slash_commands` entry point. `qrcode` is now the optional `qr` extra.
- CI adds ruff, mypy (library packages), coverage, Python 3.13 and the frontend build.
- Long-form documentation moved to `docs/`.

### Security

- Without `LILITH_AUTH_TOKEN`, the web console also requires a local `Host` and `Origin`, which blocks DNS rebinding.
- IDE plugins shipped in a project run only after `/plugins trust`.
- Hub imports no longer insert guessed directories at the front of `sys.path`.

## v4.6.0 — 2026-09-18

### Added

- Hearth workspace with recoverable work sessions, task verification and editable memory.
- Mission/Court primitives for bounded, role-based multi-agent collaboration.
- Optional web console backed by the canonical `SessionRuntime`.
- Durable delegation references, job inspection and loop-detection journals.
- Model calibration, qualification and capability-aware execution profiles.
- Expanded IDE/TUI quality, recovery, collaboration and visual-regression coverage.

### Changed

- External Vor/Huginn/Muninn-compatible adapters are explicitly configured with environment variables instead of assuming workstation-specific paths.
- The public repository no longer ships private privileged-launcher, saved-credential or personal Discord/Telegram runtime glue.
- The web console is a true optional extra; importing the base CLI does not require FastAPI.
- Public CI installs only supported repository extras and runs on Windows/Linux with Python 3.11 and 3.12.
- Legacy mutable Yggdrasil panel contracts are retired in favor of the canonical Mission/Court dashboard.

### Security

- Optional collaborators fail closed when their wrapper or observation root is not configured.
- Delegation intent is persisted before launch and unknown effects are not treated as safe to retry.
- Review-only authority remains fail-closed for tools without an explicitly read-only capability.
- Machine-specific privileged execution remains outside the public core.
