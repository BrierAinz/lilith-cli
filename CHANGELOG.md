# Changelog

All notable changes to the public Lilith CLI repository are documented here.

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
