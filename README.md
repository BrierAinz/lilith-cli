# Lilith CLI

**Norse-themed terminal IDE and AI agent interface.**

Lilith is a terminal-first coding environment: an interactive agent REPL, a full TUI IDE built on [Textual](https://textual.textualize.io/) (file tree, multi-tab editor, LSP, integrated terminal, git operations, agent diff preview), and orchestration commands for delegating work to sub-agents.

> Born in the Yggdrasil ecosystem. This repository contains the complete open-source Lilith stack required by the CLI and terminal IDE.

## Packages

| Package | Description |
|---|---|
| [`lilith-cli`](lilith-cli/) | The `lilith` command: chat REPL, TUI IDE, delegation and ecosystem ops |
| [`lilith-core`](lilith-core/) | Base types, configuration, message bus, hooks, logging and LLM providers |
| [`lilith-skills`](lilith-skills/) | Skill management, agent cards and cross-agent context |
| [`lilith-orchestrator`](lilith-orchestrator/) | Agent routing, sub-agent presets, workflows and MCP integration |
| [`lilith-memory`](lilith-memory/) | Vector memory store: SQLite backend, semantic chunker, hashed-embedding RAG |
| [`lilith-tools`](lilith-tools/) | Coding, filesystem, MCP, search, delegation and operator tools used by the CLI |

## Installation

Requires Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/BrierAinz/lilith-cli.git
cd lilith-cli
uv sync
uv run lilith --help
```

## Quick start

```bash
lilith chat                 # interactive agent REPL
lilith ide                  # launch the TUI IDE
lilith prompt "hello"       # one-shot prompt
lilith status               # ecosystem status
```

Full IDE documentation — layout, keyboard shortcuts, chat commands, LSP, session persistence — lives in [`lilith-cli/README.md`](lilith-cli/README.md).

## Persistence

Goal stores use an atomic JSON replacement with a flushed staging file. A stable
sidecar lock serializes cooperating processes across the revision check and the
replacement. Stale writers raise a revision conflict instead of replacing newer
state. Keep the `.json.lock` sidecars in place; their locks are released by the
operating system when a process exits. This is a local-filesystem contract, not a
distributed lock for network shares.

Goal documents declare their schema, version and revision. Goal reads and writes
reject foreign schemas, unsupported versions and mismatched file identities.
Core goal handoffs validate their embedded state; legacy unversioned handoffs
remain supported. Session handoffs are atomically written and their IDs are
confined to the storage directory.

Policy audit queries include rotated archives after restart. Successful appends
are flushed before being counted or invoking callbacks, and the in-memory cache
is bounded. Audit serialization is per trail instance; it does not provide the
cross-process writer contract of the goal stores. `clear()` removes the active
file and cache only; archived history remains queryable.

The orchestration SQLite backend requests FULL synchronization, declares schema
version 3 and rejects newer versions or existing tables missing required columns.

Validation on Windows / Python 3.11, 2026-09-07: core, skills and tools suites
passed 1,666 tests with 8 skips, excluding the persistence regression file and
provider E2E directory. Persistence regressions plus the complete goal-state
suite passed another 112 tests (goal-state tests overlap the broader run).
Commands run from the enclosing Asgard workspace with its existing environment:

```powershell
uv run --no-sync python -m pytest -q lilith-stack/lilith-core/tests lilith-stack/lilith-skills/tests lilith-stack/lilith-tools/tests --ignore=lilith-stack/lilith-tools/tests/e2e --ignore=lilith-stack/lilith-core/tests/test_persistence_hardening.py
uv run --no-sync python -m pytest -q lilith-stack/lilith-core/tests/test_persistence_hardening.py lilith-stack/lilith-core/tests/test_goal_state.py
```

Test infrastructure note (resolved 2026-09-10): `lilith-tools/tests/e2e/conftest.py`
now marks only tests inside `tests/e2e`. Sibling unit suites remain selectable with
`-m 'not e2e'`; regression coverage lives in `tests/test_e2e_collection_scope.py`.
These checks do not cover live provider calls, other packages or deployment.

## Yggdrasil Router bridge

Lilith can consume the provider/account router in `Vanaheim/Core/yggdrasil_router`
without taking ownership of its account registry. The boundary is intentional:

```text
Lilith TaskRouter (intent/tier)
        |
        v
Yggdrasil Router (provider/account/model/quota)
        |
        v
provider/runtime execution
```

The bridge is opt-in through `YGGDRASIL_ROUTER_CONFIG`. If the router package is
not installed, Lilith can load the sibling checkout or an explicit
`YGGDRASIL_ROUTER_HOME`. `YGGDRASIL_ROUTER_MODE=shadow` is the safe default: it asks the global router
across all configured providers/accounts, records the sanitized recommendation
and whether Lilith's current executor could honor it, but keeps the existing
preset credential. `YGGDRASIL_ROUTER_MODE=enforce` is intentionally narrower
until a cross-provider execution adapter exists: it pins the current Lilith
provider/model, lets Yggdrasil choose among eligible accounts/quotas for that
backend, and applies the selected account only when its credential is an
`env://` reference that resolves in-process. Otherwise the request fails closed
before contacting a provider.

Neither route output nor orchestration post-mortems persist `secret_ref` or the
resolved credential value. In `enforce`, the router instance is cached by config
path + mtime so request/token quotas and circuit state evolve during the Lilith
process; durable cross-process state remains a Yggdrasil Router/Niflheim concern.
`lilith doctor --fast --json` reports the gateway as `yggdrasil.gateway` and
distinguishes unconfigured, shadow and enforce states.

## License

[MIT](LICENSE) © 2026 BrierAinz (BrierStudios)
