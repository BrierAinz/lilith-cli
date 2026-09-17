# Lilith: robust skill kit and external delegation

The initial kit contains eight original, versioned SKILL.md workflows. It uses
the existing SkillLoader and injects only the selected skill, not the entire
catalog. `lilith kit list` exposes descriptions, versions and content hashes.

| Skill | Use |
|---|---|
| project-map | Identify entry points and responsibilities |
| code-review | Evidence-backed defect review |
| bug-fix | Minimal fix and behavioral regression |
| test-authoring | Isolated tests with observable assertions |
| docs-update | Source-grounded documentation |
| handoff-resume | Recover goals, evidence and pending actions |
| creative-brief | Personal Nordic / Souls / anime direction |
| tool-diagnosis | Distinguish configuration, transport and execution failures |

## Execution profiles

| Profile | Tools | Context character cap | Per-result cap | Iterations |
|---|---|---:|---:|---:|
| compact | file_read, file_write | 24,000 | 6,000 | 8 |
| standard | read, write, edit, append | 120,000 | 24,000 | 12 |
| reader | none; host supplies selected files | 24,000 | 12,000 | 1 |

These are character limits, not exact token counts or model context-size claims.
Overflow blocks explicitly rather than silently dropping project instructions.
File reads accept start_line and max_lines for bounded context. Strict argument
validation precedes execution, and file operations run serially to preserve order.
Existing requested tool/file limits intersect with the profile; a profile never
widens those permissions. Reader cannot perform edits.

Compact bounds output to 2,048 tokens and standard to 4,096 (or a lower caller
limit). For a configured DeepSeek profile, compact/reader disable thinking in
that run to reserve output for the requested result. Other providers retain their
own settings; no universal reasoning toggle is guessed.

```powershell
uv run --no-sync lilith kit list
uv run --no-sync lilith kit profiles
uv run --no-sync lilith kit doctor
uv run --no-sync lilith kit probe
uv run --no-sync lilith task "Revisa el parser" --root PATH --allowed-files parser.py --profile reader --skill code-review
```

`kit probe` sends one synthetic tool-call request and executes no tool. It reports
the observed format result and suggests compact or reader. A passing sample is
not a quality certification. The Hoguera exposes profile selection and maps its
four task templates to the corresponding skills.

## Codex integration

The local `lilith-delegate` skill under the user's `.agents/skills` directory
provides a JSON-request CLI adapter. It is an external worker, not a native Codex
agent role. Review is read-only by default; edit requests require a verification
command and explicit selected files. Replies, source hashes and checkpoints are
retained for parent review. Source drift invalidates a read-only delegation result.
The parent remains responsible for inspecting evidence and deciding what to adopt.

## Tools installed / reused

Ruff 0.16.6 was installed as a locked development dependency. JSON Schema
validation is explicitly declared for the CLI. Python, Git, ripgrep, Node and uv
were already available; the toolkit can resolve Node's existing Windows install
even when the calling process inherited an older PATH. No model weights, extra
browser servers or unrelated account connectors were installed.

## Next recommendations

1. Calibrate each chosen local/API model on a small fixed suite: read, diagnose,
   edit, test and resume. Keep models without reliable tool calls in reader mode.
2. Use deterministic tools for checks and small models for bounded proposals.
   Escalate ambiguous results to the parent instead of retrying indefinitely.
3. Select one suitable local serving runtime and reuse its compatible endpoint.
   Qualify the actual model/runtime combination before enabling edits.
4. Add skills after observed repeated work. Version their prompts and compare
   outcomes before promoting changes; a larger catalog alone is not robustness.

References consulted 2026-09-07:
- [Codex skills](https://learn.chatgpt.com/docs/build-skills)
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ruff](https://docs.astral.sh/ruff/)

Structured output constrains format; it does not make a weak model's conclusions
correct. This kit improves boundaries, observability and verification, not the
underlying model's reasoning capability.

## Verification in this environment

CLI and tool suites: **2,767 passed, 19 skipped**, with asynchronous warnings
treated as errors. Focused tests exercise invalid model arguments, unavailable
tools, context overflow, read-only fallback, selected-skill injection, file-read
ranges and ordered dependent writes. Two real DeepSeek delegations exercised
reader and compact (one file-read tool); the compact source hashes remained stable.
The synthetic DeepSeek tool-call probe also passed. Local model quality has not
been benchmarked by this work.
