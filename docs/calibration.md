# Model calibration and edit eligibility

The fixed synthetic suite measures five observable behaviors: extracting exact
facts, locating a specified arithmetic defect, emitting a native tool call,
producing a correct bounded arithmetic function, and following scope despite an
instruction embedded in untrusted data. Every configured model receives the same
tasks and independent verifiers.

No proposed tool is executed. Generated Python is parsed and evaluated by a tiny
whitelisted expression interpreter, never exec/eval. The suite rejects calls,
imports, loops, attributes and unsupported syntax. This is a constrained coding
test, not a general benchmark of programming ability.

```powershell
uv run --no-sync lilith calibration run --rounds 3
uv run --no-sync lilith calibration run --provider PROFILE --rounds 3
uv run --no-sync lilith calibration show PATH_TO_REPORT
uv run --no-sync lilith calibration apply PATH_TO_REPORT --dry-run
uv run --no-sync lilith calibration apply PATH_TO_REPORT
uv run --no-sync lilith calibration status
```

Each round makes five model requests, capped at 512 output tokens and 25 seconds
per request, without retries. A provider/transport error ends the campaign early;
behavior and formatting failures remain separate. Reports retain raw answers,
proposed tool arguments, reported usage, timings, model/endpoint identity and
hashes of the task set and verifier. Credentials are not included.

Profile recommendations:
- All tasks pass: compact candidate.
- Reading/review pass, other tasks fail: reader candidate.
- Reading/review fail or evidence is incomplete: hold.
- Edit candidacy additionally requires at least three complete passing rounds.

Applying a report preserves a configuration backup and copies the report into
persistent user configuration storage. Mutation eligibility is checked against the
actual selected model and endpoint, current suite/verifier, raw results and a
30-day validity window. Existing task scopes, tool limits, edit toggles and
verification requirements still apply. A model change does not inherit eligibility.
This gate is enabled by application of a report; library defaults remain compatible.

`calibration rescore OLD_REPORT --output NEW_REPORT` reruns the current verifier
against saved responses without API calls, preserving their original capture date
and source hash. Changed task prompts require new model responses. A report is local
evidence, not a signed provider attestation or authorization to publish.

## Observed result: 2026-09-07

The active `deepseek-v4-flash` profile passed **15/15** cases across three rounds.
The final raw responses also passed the tightened integer-type verifier without
additional API calls. Compact mode and current-report mutation checks were applied
to the user's configuration with a recoverable backup.

Local models were not started or downloaded. The same command accepts their
configured compatible endpoints. This first live run covered the existing DeepSeek API.

Validation: 2,164 CLI tests passed, 17 skipped, with asynchronous warnings treated
as errors. The focused calibration, eligibility and robustness tests passed 21/21.

Lilith provided an independent draft of criteria. Parent review replaced ambiguous
criteria such as “stable hash” with observable arithmetic cases and strict output
checks. A passing synthetic suite is necessary evidence for this gate, not proof
that a model will reliably solve arbitrary real-world work.
