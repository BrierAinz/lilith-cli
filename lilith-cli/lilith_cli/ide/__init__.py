"""Lilith IDE package.

Structured Textual IDE for the Yggdrasil agent framework. This package exposes
widgets, helpers, modals, themes, context handling and the main application.
"""

from __future__ import annotations

# Migrated submodules.
from .config import IDEConfig
from .context import ContextItem, ContextManager
from .lsp.client import LSPClient, LSPError
from .lsp.languages import detect_language_server, language_server_command
from .lsp.manager import LSPManager
from .plan import AgentPlan, PlanStep, build_execution_prompt, build_planning_prompt, parse_plan
from .plugins import LoadedPlugin, PluginManager
from .realms import Realm, RealmManager
from .runestones import Runestone, RunestoneForge
from .screens.modals import (
    AgentDiffScreen,
    ConfigScreen,
    DiffScreen,
    FileSearchScreen,
    FindReplaceScreen,
    FindScreen,
    GitBlameScreen,
    GoToLineScreen,
    GrepScreen,
    HistoryScreen,
    HoverScreen,
    OutlineScreen,
    RunestoneScreen,
    CompletionScreen,
    DiagnosticsScreen,
    PatchScreen,
    ProjectFindReplaceScreen,
    RecentFilesScreen,
    ToastHistoryScreen,
    YggdrasilPanelScreen,
)
from .theme import _NORSE_LIGHT_THEME, _NORSE_THEME
from .utils.helpers import (
    GrepResult,
    _apply_patch,
    _detect_language,
    _parse_unified_diff,
    _shorten_path,
)
from .widgets.command_palette import CommandPaletteScreen, PaletteItem
from .widgets.file_tree import RuneDirectoryTree

# App entry point and main Textual application.
from .app import LilithIDEApp, run_ide

__all__ = [
    "AgentDiffScreen",
    "AgentPlan",
    "CommandPaletteScreen",
    "ConfigScreen",
    "ContextItem",
    "ContextManager",
    "DiffScreen",
    "PlanStep",
    "Runestone",
    "RunestoneForge",
    "Realm",
    "RealmManager",
    "LSPClient",
    "LSPError",
    "LSPManager",
    "detect_language_server",
    "language_server_command",
    "LoadedPlugin",
    "PluginManager",
    "build_execution_prompt",
    "build_planning_prompt",
    "parse_plan",
    "FileSearchScreen",
    "FindReplaceScreen",
    "FindScreen",
    "GitBlameScreen",
    "GoToLineScreen",
    "GrepResult",
    "GrepScreen",
    "HistoryScreen",
    "IDEConfig",
    "LilithIDEApp",
    "OutlineScreen",
    "PaletteItem",
    "PatchScreen",
    "RuneDirectoryTree",
    "RunestoneScreen",
    "CompletionScreen",
    "HoverScreen",
    "DiagnosticsScreen",
    "ProjectFindReplaceScreen",
    "RecentFilesScreen",
    "ToastHistoryScreen",
    "YggdrasilPanelScreen",
    "_NORSE_LIGHT_THEME",
    "_NORSE_THEME",
    "_apply_patch",
    "_detect_language",
    "_parse_unified_diff",
    "_shorten_path",
    "run_ide",
]
