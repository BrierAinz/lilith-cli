# Lilith CLI

**Norse-themed terminal IDE and AI agent interface.**

Lilith is a terminal-first coding environment: an interactive agent REPL, a full TUI IDE built on [Textual](https://textual.textualize.io/) (file tree, multi-tab editor, LSP, integrated terminal, git operations, agent diff preview), and orchestration commands for delegating work to sub-agents.

> Part of the [BrierStudios](https://github.com/BrierStudios) Yggdrasil ecosystem. This repository contains the open-source Lilith stack: the CLI/IDE and its two support libraries.

## Packages

| Package | Description |
|---|---|
| [`lilith-cli`](lilith-cli/) | The `lilith` command: chat REPL, TUI IDE, delegation and ecosystem ops |
| [`lilith-core`](lilith-core/) | Base types, configuration, message bus, hooks, logging and LLM providers |
| [`lilith-skills`](lilith-skills/) | Skill management, agent cards and cross-agent context |
| [`lilith-orchestrator`](lilith-orchestrator/) | Agent routing, sub-agent presets, workflows and MCP integration |
| [`lilith-memory`](lilith-memory/) | Vector memory store: SQLite backend, semantic chunker, hashed-embedding RAG |

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

## License

[MIT](LICENSE) © 2026 BrierAinz (BrierStudios)
