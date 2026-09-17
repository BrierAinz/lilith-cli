# Hoguera workspace — acceptance, 2026-09-07

## Completed scope

- Task form with objective, explicit file selection, verification command and edit toggle.
- Four templates: fix a bug, review code, add tests and update documentation.
- Separate task subprocess, live activity, elapsed time, observed changes and
  provider-reported token usage. Request contents stay in a private local file.
- Pause at a safe checkpoint, resume from saved state and cancellation with
  unknown effects retained for review. Active work prevents accidental UI exit.
- Before/after diff plus actual verification output. Opening the diff enables
  explicit acceptance; changed hashes invalidate that acceptance.
- Visual memory list/editor with separate personal and project scopes.
- Saved UI tasks reopen with their original scope, baseline and results.
- File picker excludes runtime/vendor directories and does not follow junctions.
- Compact task layout and keyboard navigation at 80 and 130 columns.

Accepting a review stores a local record, not a Git commit or publication. Edits
are applied to the selected files during execution, as the edit control states.
The baseline is retained under the task's local run directory. Explicit test
commands run in the project context; file-tool restrictions are not a shell sandbox.

## Verification

Full CLI and memory suite: **2,417 passed, 19 skipped**, with RuntimeWarning and
PytestUnraisableExceptionWarning promoted to errors. No warnings were emitted.
After final layout/picker adjustments, **19 focused tests passed** with the same
warning policy. Counts overlap.

The UI end-to-end test drives Textual, a separate CLI process, the real HTTP
provider wrapper against a scripted loopback server, a real file-write tool,
an explicit Python verification command, diff review and acceptance. Variants
exercise pause/resume with unchanged file mtime and cancellation before a write.
The test provider is simulated; this does not claim new live-provider qualification.

Further tests cover scope-specific memory editing/deletion, template selection,
file-picker behavior, reopening saved tasks and rejecting stale diff acceptance.

The former IDE/LSP warnings were addressed by awaiting LSP shutdown and reader
tasks, closing transports, tracking clients during startup, and replacing the
read-only status/blame shell subprocesses with bounded structured calls. Rune
lookup tests no longer create unmounted DirectoryTree async watchers.

```powershell
uv run --no-sync python -m pytest -q lilith-cli/tests lilith-memory/tests -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning
uv run --no-sync python -m pytest -q lilith-cli/tests/test_task_workspace_ui.py
uv run --no-sync python scripts/preview_hearth.py --output PATH
```

Preview generation uses only synthetic sessions and temporary preference storage.
The existing Bifröst hidden-start configuration is unchanged.
