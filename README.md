<p align="center">
  <img src="https://raw.githubusercontent.com/BrierAinz/BrierAinz/main/assets/brierstudios-strip.svg" width="100%" alt="BrierStudios — Yggdrasil ecosystem">
</p>

# ᛚ Lilith CLI

<p>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11-111820?style=flat-square&logo=python&logoColor=8FD8E8" alt="Python >= 3.11">
  <img src="https://img.shields.io/badge/interface-Textual_TUI-111820?style=flat-square&logoColor=8FD8E8" alt="Textual TUI">
  <img src="https://img.shields.io/badge/architecture-local--first-111820?style=flat-square&logoColor=D5B96D" alt="Local-first">
  <img src="https://img.shields.io/badge/license-MIT-111820?style=flat-square&logoColor=8FD8E8" alt="MIT">
</p>

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
lilith chat                  # interactive agent REPL
lilith ide                   # launch the TUI IDE
lilith prompt "hello"        # one-shot prompt
lilith status                # ecosystem status
```

Full IDE documentation — layout, keyboard shortcuts, chat commands, LSP, session persistence — lives in [`lilith-cli/README.md`](lilith-cli/README.md).

## Design boundary

Lilith CLI is the public terminal workspace and interface layer. It is intentionally separated from private orchestration and infrastructure repositories so the open-source surface can remain understandable and usable on its own.

## License

[MIT](LICENSE) © 2026 BrierAinz / BrierStudios
