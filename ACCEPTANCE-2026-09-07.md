# Local acceptance: useful project work and recovery

## Delivered

- `task`: uses the existing AgentSession, checkpoints tool intents/results, shows
  activity on stderr, and returns a final JSON record. Optional explicit local
  verification and bounded repair passes distinguish responses from verified work.
- Safe resume: unresolved tool effects block continuation. Confirmed file-write
  receipts are reused only while their recorded SHA-256 matches the current file.
  An external change requests inspection instead of replaying an old write.
- `remember set/list/forget`: explicit user/project preferences in the existing
  PreferenceStore schema. Project context overrides only its matching root.
- Versioned installations: local commit archive, locked environment, CLI smoke,
  atomic activation pointer and rollback. Working trees and user data are retained.
- Hoguera: displays verification, interruption, tool/error counts and last action.

## Real-provider exercise

The task ran in an isolated brierstudios-site worktree from its recorded HEAD,
with only `file_read` / `file_write` and two allowed files. The original site's
concurrent changes were preserved. The task corrected surrounding whitespace and
explicit zero limits in SiteSearch and added six Node tests using a VM browser stub.

One process exited at a safe checkpoint; a later invocation resumed its history.
The initial provider continuation returned no visible response and was marked
blocked. A later continuation produced edits whose verification found two defects;
the bounded feedback loop returned the failures to Lilith and obtained corrections.
Six generated tests passed. Review tightened positive/default limit assertions;
the independent existing-plus-new suite passed 16/16.

DeepSeek thinking mode requires reasoning_content to be preserved across tool
turns, per https://api-docs.deepseek.com/guides/thinking_mode/. The streaming
history now retains that protocol field. A per-profile thinking_enabled option
supports the documented toggle; the bounded acceptance fixture explicitly disabled
thinking to avoid spending its output allowance before a visible answer.

## Recovery and verification

Full CLI plus memory suite: **2,405 passed, 19 skipped**, with five pre-existing
IDE/LSP async-cleanup warnings. The website exercise passed **16/16** independently.

`scripts/smoke_recovery.py` writes a real file, saves state and exits. A second
process restores it, repeats the same requested tool call and confirms unchanged
content and mtime: WRITE_CHECKPOINT_OK / RESTART_NO_REPLAY_OK. Unit tests cover
unknown in-flight effects, changed-file rejection, atomic pause snapshots,
scoped preferences and failed-install preservation of the active pointer.

Test discovery exposed a legacy smoke invoking `/pr` against the real remote.
That test was stopped and the side-effecting command excluded from generic smoke;
mocked PR tests remain. Read-only GitHub checks found no remote branch and zero PRs
for feat/lilith-hearth. Tests must not publish branches as part of local validation.

## Limits

Verification means the supplied command passed with observable output, not release
approval or proof of all requirements. File-scoped tasks constrain only the enabled
file tools; a working directory alone is not a shell sandbox. Interrupted unknown
effects require review; actions are not automatically replayed. Receipt reuse is
for resumed tasks, not a global ban on intentional repeated edits.

Installation rollback switches code/environment, not database schemas. The installer
uses local commits, performs no fetch/push, and retains both releases. Live provider
calls in this exercise were scoped to the selected test files, not a qualification
of every supported provider or a latency guarantee.
