# Contributing to Lilith CLI

Thank you for helping improve Lilith. This repository is a Python 3.11+ `uv` workspace containing the CLI and its supporting libraries.

## Before you begin

- Search existing issues and pull requests before opening a new one.
- Use the issue forms for reproducible bugs, focused proposals, or questions.
- For large architectural changes, open an issue first so scope and boundaries are clear.
- Never include API keys, tokens, private prompts, personal data, or proprietary model artifacts.

## Local setup

Requirements:

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Git

```bash
git clone https://github.com/BrierAinz/lilith-cli.git
cd lilith-cli
uv sync --all-extras
uv run lilith --help
```

## Running tests

Run the complete workspace suite:

```bash
uv run pytest
```

Run a focused package suite while developing:

```bash
uv run pytest lilith-cli/tests
uv run pytest lilith-core/tests
uv run pytest lilith-memory/tests
uv run pytest lilith-orchestrator/tests
uv run pytest lilith-skills/tests
```

Add or update tests for every behavior change. A fix should include a regression test whenever practical.

## Branches and commits

Use a short, descriptive branch name:

```text
feat/short-description
fix/short-description
docs/short-description
```

Prefer focused commits using conventional prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `chore:`.

## Pull requests

A pull request should:

1. Explain the problem and the chosen approach.
2. Stay within one coherent scope.
3. List the verification commands that were actually run.
4. Call out compatibility, migration, or security effects.
5. Include screenshots for visible TUI changes.
6. Avoid generated files or unrelated formatting changes.

Maintainers may request a smaller scope when a change crosses package boundaries without a clear need.
