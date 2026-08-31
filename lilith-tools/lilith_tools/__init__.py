"""PC control, browser automation, RAG tools."""

__version__ = "1.3.0"

# ── Top-level exports (used by lilith-cli/agent.py) ─────────────────
# These imports trigger the @ToolRegistry.register decorators in each
# module, populating the global ToolRegistry._tools dict so the agent
# can discover tools via `ToolRegistry.list_tools()`.
from lilith_tools.registry import ToolRegistry

# Tool modules — importing them runs the @register decorators.
# We use contextlib-style graceful import so missing optional deps
# (browser, web_search) don't break the whole package.
import importlib as _il

for _mod in (
    "filesystem",
    "system",
    "env",
    "git_tools",
    "coding",
    "coding_tools",
    "browser",
    "web_search",
    "security",
    "vision",
    "process",
    "todos",
    "watcher",
    "snippets",
    "delegate",
    "conclave",
    "memory",
    "orchestration_state",
    "skill_run",
    "blender",
    "forja_tools",
    "crawl_tools",
):
    try:
        _il.import_module(f".{_mod}", package=__name__)
    except Exception:  # pragma: no cover — optional modules
        pass

# Base / isolation / package_guard — kept as before.
from lilith_tools.isolation import (  # noqa: E402
    ToolIsolationMode,
    ToolIsolationPolicy,
    ToolViolation,
)
from lilith_tools.package_guard import (  # noqa: E402
    DEFAULT_BLACKLIST,
    DEFAULT_TRUST,
    GuardConfig,
    GuardResult,
    GuardVerdict,
    LicensePolicy,
    PackageGuard,
    PackageGuardTool,
    PolicyHit,
    render_json,
    render_report,
)

__all__ = [
    # Core
    "ToolRegistry",
    # Isolation
    "ToolIsolationMode",
    "ToolIsolationPolicy",
    "ToolViolation",
    # Package Guard (AgentShield-style pre-install gate)
    "GuardConfig",
    "GuardResult",
    "GuardVerdict",
    "LicensePolicy",
    "PackageGuard",
    "PackageGuardTool",
    "PolicyHit",
    "DEFAULT_BLACKLIST",
    "DEFAULT_TRUST",
    "render_json",
    "render_report",
]
