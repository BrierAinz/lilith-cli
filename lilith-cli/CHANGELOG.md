# Changelog

All notable changes to `lilith-cli` will be documented in this file.

## [Unreleased]

## [4.4.0] - 2026-07-12

### Added
- New `/qr` slash command: QR code generation with `--save`, `--last` and
  persistent preferences.
- User aliases created with `/alias` now actually expand in the REPL
  (single-level, built-ins always win, extra args are appended).
- Wiring-guard tests: `/help` catalog and `_SLASH_COMMANDS` autocomplete are
  checked against the real dispatcher; REPL handlers must be imported.
- Added `/profile` and `/test` dispatch support in the REPL.
- Added `/compare`, `/log`, `/capture` and `/qr` to the `/help` catalog.
- Added smoke tests covering every `run_*_command` entry point.

### Fixed
- Wired 6 orphaned commands that produced "Comando desconocido":
  `/alias`, `/env`, `/replay`, `/tour`, `/model-info`, `/stream`; plus the
  `/cap`, `/l`, `/rev` autocomplete shorthands.
- `/macro`, `/pipeline` and `/uuid` raised `NameError` at runtime (dispatched
  without imports); multiline continuation prompt crashed on undefined theme.
- IDE: project find/replace and LSP completion pickers raised `NameError`
  (modal screens were never imported).
- Corrupt `templates.json` crashed `_load_templates` instead of falling back
  to defaults.
- Terminal tab tests de-flaked for real: waits now cover the deferred
  `_post_new_terminal` activation (full suite green 3x in a row).
- Fixed eight smoke-audit crash paths across command entry points.
- Fixed streaming replies for `/redo`, `/continue`, `/summary`, and `/recap`.
- Fixed cp1252 console output by reconfiguring stdout to UTF-8.
- Fixed pytest runs in `RunTestTool` to use the current Python interpreter.
- Repaired lint, workflow, redact, and diff-config test coverage.
- Completed the v4.3.1 version bump in `pyproject.toml`.

### Changed
- Removed a byte-identical duplicate `render_context` definition (85 lines).
- Improved `/search` and `/macro` visual output.
- Improved `/metrics`, `/tokens`, and `/usage` visual output.
- Switched `/bench` timing to `time.perf_counter()` for more reliable rates.

## [4.3.1] - 2026-07-12

- Bumped version to 4.3.1

## [4.3.0] - 2026-07-10

### Added
- New `/changelog` slash command to view the project changelog from the REPL.
- New `/tip` slash command to show random Lilith tips for feature discovery.
- New `/release` slash command to bump version (patch|minor|major) and prepend a CHANGELOG entry.

## [4.2.0] - 2026-07-07

### Added
- `/stream` slash command for configurable streaming behavior.
- `/recap` command to summarize recent conversation turns.
- `/bench` command for benchmarking tool/provider latencies.

### Changed
- Improved 50K token truncation handling for long contexts.

## [4.1.0] - 2026-07-01

### Added
- `/env` slash command for inspecting environment variables and system info.
- `/git` slash command wrapper around `GitOperationTool`.
- `/todos` slash command for task tracking.
- `/search` slash command for project-wide search.

### Fixed
- Resolved cancellation token race when interrupting streaming responses.

## [4.0.0] - 2026-06-20

### Added
- Initial stable release of Lilith CLI with interactive REPL.
- 38 built-in tools and 42 slash commands.
- Safety workflow, plan mode, undo, and did-you-mean suggestions.
- Per-tool timeout, parallel tool execution, and cost estimation.

### Changed
- Project extracted from the Yggdrasil monorepo into `lilith-cli`.
