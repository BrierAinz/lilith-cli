# Lilith CLI — Public Roadmap

Lilith is a local-first terminal workspace for auditable AI-assisted software engineering.
This roadmap describes the public repository only; private infrastructure and operator-specific
adapters are intentionally outside its scope.

## Current public surface

- Conversational coding REPL and one-shot prompts.
- Textual TUI/IDE with project browsing, diffs, Git integration and session recovery.
- Persistent goals, memory, task checkpoints and deterministic verification hooks.
- MCP/tool orchestration with capability-scoped authority modes.
- Mission/Court abstractions for bounded multi-agent collaboration.
- Optional local web console sharing the canonical `SessionRuntime`.
- Optional external collaborator wrappers configured explicitly by environment variables.

## Near term — release hardening

- [ ] Keep clean installs reproducible on Windows and Linux.
- [ ] Maintain CI across supported Python versions.
- [ ] Remove remaining legacy UI/mojibake and improve narrow-terminal behavior.
- [ ] Expand security regression tests for tool injection, path confinement and recovery.
- [ ] Document optional integrations without assuming a specific workstation layout.

## Agent runtime

- [x] Persist recoverable sessions and tool checkpoints.
- [x] Separate read-only/review authority from mutating tools.
- [x] Record delegation references before execution when a configured adapter is used.
- [x] Preserve unknown effects instead of blindly retrying timed-out work.
- [ ] Improve portable cancellation/ownership semantics for external workers.
- [ ] Add stronger provenance for externally produced results.

## Mission and collaboration

- [x] Mission specifications, success criteria and bounded budgets.
- [x] Role-based Court abstraction separated from compute/provider identity.
- [x] Deterministic verification before successful mission closure when available.
- [x] Reusable skill/version history and rollback primitives.
- [ ] Improve provider-independent health and quota reporting.
- [ ] Expand public examples for custom Court roles and compute adapters.

## Interfaces

- [x] Terminal REPL and one-shot CLI.
- [x] Textual IDE and Hearth workspace.
- [x] Optional web console.
- [ ] Consolidate legacy command surfaces behind the canonical runtime.
- [ ] Improve accessibility, reduced-motion behavior and visual regression coverage.

## Security principles

- Fail closed when tool capability or authority is unknown.
- Do not treat process exit as proof that a requested task was correct.
- Do not retry operations with unknown effects without explicit recovery evidence.
- Keep credentials outside repository state and command arguments where practical.
- Keep machine-specific privileged launchers outside the public core.
