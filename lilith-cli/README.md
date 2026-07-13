# lilith-cli

Terminal interface for the Lilith ecosystem — part of the Yggdrasil monorepo.

## Commands

- `lilith chat` — Interactive REPL with streaming output, slash commands, and tool calls.
- `lilith ide` — **TUI coding IDE**: file tree, chat, multi-tab editor, LSP-backed editor, terminal, git operations, grep, patch application, agent diff preview, Yggdrasil orchestration panel, and integrated agent.
- `lilith delegate <provider> "<prompt>"` — One-shot prompt to a specific provider profile.
- `lilith subagent <preset> "<prompt>"` — Route through a Hlidskjalf sub-agent preset.
- `lilith prompt "<text>"` — Simple one-shot mode.
- `lilith status`, `lilith launch`, `lilith config` — Ecosystem operations.

## IDE mode

Launch the Norse-themed terminal IDE:

```bash
lilith ide
lilith ide --root ./src
lilith ide --provider kimi --model kimi-for-coding
```

On startup, a short Yggdrasil splash screen is shown; press any key or wait for it to auto-dismiss.

### Layout

- **Left** — Yggdrasil file tree (`RuneDirectoryTree`). Files are shown with Elder Futhark runes by extension; click or press Enter to open a tab.
- **Center top** — Chat log with Lilith (streaming responses, tool calls, reasoning).
- **Center bottom** — Input bar to talk to the agent.
- **Right** — Multi-tab editor with syntax highlighting, line numbers, modification tracking, LSP completion, hover, go-to-definition and diagnostics.
- **Bottom** — Integrated terminal for shell commands (multi-session; PTY on Windows).
- **Status bar** — Provider / model, modification indicator, LSP diagnostic summary and token usage.

### Package layout

The IDE source lives under `lilith_cli/ide/`, split by responsibility:

```
lilith_cli/ide/
├── app.py              # LilithIDEApp — Textual App + entry point (run_ide)
├── config.py           # IDEConfig — persisted IDE settings (~/.yggdrasil/ide.yaml)
├── context.py          # ContextItem / ContextManager — workspace context
├── keymaps.py          # IDE_BINDINGS — default keyboard shortcuts
├── plan.py             # AgentPlan / parsing helpers used by the agent view
├── plugins.py          # PluginManager / LoadedPlugin — .yggdrasil/plugins/
├── realms.py           # Realm / RealmManager — auto-indexed project tree
├── runestones.py       # Runestone / RunestoneForge — agent artifacts
├── theme.py            # Norse dark/light Textual themes + CSS
├── views/              # Mixins wired into LilithIDEApp (MRO composition)
│   ├── editor.py       # EditorMixin    — multi-tab editor + dirty markers
│   ├── agent_view.py   # AgentMixin     — chat log + slash commands + tool calls
│   ├── file_tree.py    # FileTreeMixin  — file tree actions
│   ├── terminal.py     # TerminalMixin  — integrated shell + /run handlers (multi-session PTY)
│   ├── git_view.py     # GitMixin       — status/blame + stage/commit/log
│   └── yggdrasil_panel.py  # YggdrasilPanelMixin — queue/spawn orchestration
├── screens/
│   ├── modals.py       # All ModalScreen subclasses (file search, grep, find, replace, git, outline, …)
│   └── splash.py       # SplashScreen — startup Yggdrasil banner
├── widgets/
│   ├── command_palette.py  # CommandPaletteScreen + PaletteItem (Ctrl+Shift+P)
│   ├── file_tree.py        # RuneDirectoryTree (textual-tree + rune glyphs)
│   └── runestone.py        # RunestoneWidget (placeholder)
├── lsp/
│   ├── client.py       # LSPClient — JSON-RPC client (placeholder)
│   ├── languages.py    # detect_language_server / language_server_command
│   └── manager.py      # LSPManager — per-root server lifecycle
└── utils/
    └── helpers.py      # GrepResult, _apply_patch, _parse_unified_diff, _shorten_path, …
```

All public symbols are re-exported from the top-level `lilith_cli.ide` package
(see `lilith_cli/ide/__init__.py`), so external code keeps using
`from lilith_cli.ide import CommandPaletteScreen, IDEConfig, …`.

### Keyboard shortcuts

Default bindings are declared in `lilith_cli/ide/keymaps.py` (`IDE_BINDINGS`):

| Shortcut | Action |
|----------|--------|
| `Ctrl+Q` | Quit the IDE. |
| `Ctrl+S` | Save the active file (auto-backup before saving). |
| `Ctrl+R` | Refresh the file tree. |
| `Ctrl+P` | Quick file search by name. |
| `Ctrl+Shift+F` | Grep / search text inside project files; selecting a result opens the file at that line. |
| `Ctrl+F` | Find text inside the active file. |
| `Ctrl+H` | Find and replace inside the active file. |
| `Ctrl+Shift+H` | Find and replace across project files. |
| `Ctrl+G` | Jump to a specific line number. |
| `Ctrl+Shift+G` | Show git status, diff, hunks and log for the current file. |
| `Ctrl+Shift+Alt+C` | Open commit dialog when there are staged changes. |
| `Ctrl+Y` | Open Yggdrasil orchestration panel (queue / spawns / presets). |
| `Ctrl+Shift+Y` | Load a previous conversation. |
| `Ctrl+T` | Cycle themes (`norse-dark`, `norse-light`, `textual-dark`, `textual-light`; persisted in `~/.yggdrasil/ide.yaml`). |
| `Ctrl+M` | Toggle markdown preview for `.md` files. |
| `Ctrl+W` | Close the active editor tab. |
| `Ctrl+Shift+T` | Reopen the last closed tab. |
| `Ctrl+E` | Recently opened files. |
| `Ctrl+Shift+P` | Command palette. |
| `Ctrl+Shift+I` | Auto-format the active file (ruff / black / prettier). |
| `Ctrl+Shift+O` | Show symbol outline for Python files. |
| `Ctrl+Shift+B` | Toggle bookmark on current line. |
| `Ctrl+Shift+C` | Copy the active file's project-relative path. |
| `Ctrl+F5` | Run the current file (Python, JS, TS, Go, shell). |
| `Ctrl+Equal` | Increase editor font size. |
| `Ctrl+Minus` | Decrease editor font size. |
| `Ctrl+Shift+Z` | Toggle soft-wrap. |
| `Ctrl+Shift+N` | Show recent toast notification history. |
| `Ctrl+,` | Open IDE settings (terminal height, auto-reload interval, run-on-save). |
| `Ctrl+Shift+D` | Show side-by-side diff of the current file against git HEAD. |
| `Ctrl+Shift+M` | Toggle zen mode (distraction-free editor). |
| `Ctrl+Shift+`` | Toggle terminal fullscreen. |
| `Ctrl+`` | Focus the terminal input. |
| `Esc` | Cancel the current agent generation. |
| `F1` | Show help in the chat log. |

A handful of additional bindings are declared inline in `LilithIDEApp.BINDINGS`
inside `lilith_cli/ide/app.py` (not yet centralised in `keymaps.py`):

| Shortcut | Action |
|----------|--------|
| `Ctrl+F9` | Debug the current Python file with pdb/ipdb. |
| `Ctrl+Space` | Request LSP completion (also auto-triggers on `.` and after typing). |
| `Ctrl+Shift+I` | Show LSP hover info. |
| `F12` | Go to the LSP definition of the symbol under the cursor. |

### Chat commands

| Command | Description |
|---------|-------------|
| `/run <cmd>` | Execute a shell command and show output in the chat. |
| `/test [args]` | Run `pytest` and stream results into the chat. |
| `/debug` | Debug the current Python file with pdb/ipdb. |
| `/patch` | Open a diff editor to review and apply a unified diff. |
| `/blame` | Show `git blame` for the active file. |
| `/export` | Export the current conversation to markdown. |
| `/git-stash` | Run `git stash` and show the result. |
| `/git-checkout <branch>` | Run `git checkout <branch>`. |
| `/git-branch <name>` | Run `git branch <name>`. |
| `/git-commit <message>` | Open commit dialog and run `git commit -m <message>`. |
| `/git-lines` | Show changed lines of the current file. |
| `/delegate <preset>` | Delegate the last chat message to a Hlidskjalf preset via the Yggdrasil queue. |
| `/undo-last` | Revert the last agent-proposed changes (uses backups). |
| `/new <template> <path>` | Create a new file from a snippet (`py`, `test`, `class`, `md`). |
| `/clear` | Clear the chat log. |
| `/theme` | Toggle theme. |
| `/save` | Save the active file (triggers run-on-save if configured). |
| `/history` | Open conversation history. |
| `/help` | Show help. |

### Patch application

Paste a unified diff (e.g. from Lilith) after typing `/patch`. The IDE shows a review dialog and, if confirmed, applies the patch with automatic `.bak.<timestamp>` backups.

### Run on save

Configure a shell command in `~/.yggdrasil/ide.yaml` (or via `Ctrl+,`) under `run_on_save`. The command runs asynchronously in the project root every time you save a file with `Ctrl+S` or `/save`.

```yaml
run_on_save: "python -m pytest -q"
```

### Session persistence

The IDE remembers the session when you quit with `Ctrl+Q` and restores it on the next launch:

- open files and active tab,
- cursor position per file,
- terminal height and fullscreen state,
- zen mode and sidebar width.

The state is stored in `~/.yggdrasil/ide.yaml` and saved every 30 seconds while the IDE is running.

### Agent diff preview

When Lilith proposes file changes in fenced code blocks, the IDE shows an `AgentDiffScreen` with the current and proposed content side-by-side. You can accept/reject per file or all at once. Accepted changes create both a local `.bak.<timestamp>` backup and a central backup under `~/.yggdrasil/backups/` with metadata, so `/undo-last` can revert the operation.

### Yggdrasil orchestration panel

Press `Ctrl+Y` to open the panel. It shows the current `lilith queue`, active `lilith spawn` subagents and available presets. Use `F1`–`F9` to delegate the last user message to a preset, `r`/`F5` to refresh, `d` to dequeue a message and `k` to kill a spawn.

### LSP integration

Opening a supported file starts the configured language server. The IDE forwards `didOpen/didChange/didSave`, shows diagnostics in the status bar, offers completion (`Ctrl+Space`), hover (`Ctrl+Shift+I`) and go-to-definition (`F12`).

## Installation

This package is part of the [lilith-cli workspace](../README.md). Install it with `uv` from the repository root:

```bash
uv sync
```

Then run:

```bash
lilith --help
```
