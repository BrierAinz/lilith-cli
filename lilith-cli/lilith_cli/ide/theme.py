"""Norse-themed colour schemes and CSS for the Lilith IDE."""

from textual.theme import Theme

_NORSE_THEME = Theme(
    name="norse-dark",
    primary="#5f9e6e",
    secondary="#8b5a2b",
    warning="#c78a2e",
    error="#c94f4f",
    success="#6aa66a",
    accent="#d4a24c",
    dark=True,
    background="#12141a",
    surface="#1a1d26",
    panel="#20242f",
    boost="#2a2f3d",
    foreground="#e0e2e8",
)

_NORSE_LIGHT_THEME = Theme(
    name="norse-light",
    primary="#3a7a4f",
    secondary="#8b5a2b",
    warning="#b87a1e",
    error="#b93d3d",
    success="#4a8a4a",
    accent="#b07f2c",
    dark=False,
    background="#f4f1ea",
    surface="#ede8dd",
    panel="#e6e0d2",
    boost="#dcd5c5",
    foreground="#2b2b2b",
)

_NORSE_CSS = """
Screen {
    align: center middle;
}

#main {
    height: 100%;
}

#workspace {
    layout: grid;
    grid-size: 3 1;
    grid-columns: 1fr 2fr 2fr;
    height: 1fr;
}

#sidebar {
    border: outer $primary-lighten-2;
    padding: 0 1;
}

#chat-panel {
    border: outer $primary;
    padding: 0 1;
}

#editor-panel {
    border: outer $primary-lighten-2;
    padding: 0 1;
}

.panel-title {
    text-style: bold;
    color: $text-accent;
    height: 1;
    margin: 1 0 0 0;
}

#file-tree {
    height: 1fr;
    border: none;
    padding: 0;
}

#chat-log {
    height: 1fr;
    border: none;
    padding: 0;
}

#editor-tabs {
    height: 1fr;
}

#editor-tabs TabPane {
    padding: 0;
}

#editor-tabs TextArea {
    height: 1fr;
    border: none;
}

.editor-info {
    height: 1;
    color: $text-muted;
}

#input-bar {
    layout: horizontal;
    height: 3;
    border: outer $primary;
    padding: 0 1;
}

#chat-input {
    width: 1fr;
}

#send-button {
    width: 8;
    margin-left: 1;
}

#terminal-panel {
    height: 8;
    border: outer $primary-darken-1;
    padding: 0 1;
}

#terminal-title {
    height: 1;
    text-style: bold;
    color: $text-accent;
}

#terminal-log {
    height: 1fr;
    border: none;
    padding: 0;
}

#terminal-input {
    height: 1;
    border: none;
}

#status-bar {
    layout: horizontal;
    height: 1;
    color: $text-muted;
    background: $surface-darken-1;
    padding: 0 1;
}

.status-left {
    width: 1fr;
}

.status-center {
    width: 1fr;
    text-align: center;
}

.status-right {
    width: 1fr;
    text-align: right;
}

#file-search-screen,
#grep-screen,
#history-screen,
#patch-screen,
#git-screen,
#git-hunk-screen,
#commit-screen,
#git-log-screen,
#git-commit-screen,
#find-screen,
#findreplace-screen,
#goto-screen,
#config-screen,
#runestone-screen,
#completion-screen,
#hover-screen,
#diagnostics-screen,
#agent-diff-screen {
    align: center middle;
}

.modal-dialog {
    width: 90;
    height: 28;
    border: thick $background 80%;
    background: $surface;
    padding: 0 1;
}

#runestone-dialog {
    width: 100;
    height: 32;
    border: thick $accent 90%;
    background: $surface;
    padding: 0 1;
}

#completion-dialog {
    width: 80;
    height: 24;
    border: thick $success 80%;
    background: $surface;
    padding: 0 1;
}

#hover-dialog {
    width: 80;
    height: 20;
    border: thick $warning 80%;
    background: $surface;
    padding: 0 1;
}

#diagnostics-dialog {
    width: 90;
    height: 28;
    border: thick $error 80%;
    background: $surface;
    padding: 0 1;
}

#agent-diff-dialog {
    width: 110;
    height: 36;
    border: thick $primary 80%;
    background: $surface;
    padding: 0 1;
}

#agent-diff-dialog #agent-diff-files {
    height: 6;
    border: solid $primary-darken-2;
}

#agent-diff-dialog #agent-diff-left,
#agent-diff-dialog #agent-diff-right {
    width: 1fr;
    height: 1fr;
    border: solid $primary-darken-2;
}

#palette-dialog {
    width: 80;
    height: 26;
    border: thick $primary 80%;
    background: $surface;
    padding: 0 1;
}

.palette-category {
    color: $text-muted;
    text-style: bold;
    height: 1;
}

.modal-input {
    margin: 1 0;
}

.modal-results {
    height: 1fr;
    border: solid $primary-darken-2;
}

.modal-textarea {
    height: 1fr;
}

.modal-buttons {
    layout: horizontal;
    height: 3;
}

.file-search-item,
.grep-result-item,
.history-item {
    padding: 0 1;
}

Header {
    background: $surface-darken-1;
    color: $text-accent;
}

Footer {
    background: $surface-darken-1;
}

TabbedContent:focus {
    border: outer $accent;
}
"""
