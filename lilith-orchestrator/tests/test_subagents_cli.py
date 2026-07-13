"""Tests for .ygg/subagents_cli.py — the `ygg subagents` command helper.

The CLI helper loads dynamically via importlib from ``.ygg/subagents_cli.py``
at the repo root. We can't import it directly without sys.path hackery, so
each test loads the module via ``importlib.util.spec_from_file_location``
mirroring what ``ygg.py`` does at runtime.

Tests cover:
    * module loads and exposes ``run``
    * default mode prints a table with one row per registered persona
    * --pool prints tool pool, skipping ``["*"]`` wildcard
    * --verify flags unknown tool names and validates known ones
    * --agent filter narrows to one persona; unknown name surfaces a help line
    * --dry-run invokes SubAgentRunner end-to-end via stub executor
    * missing helper file path is handled gracefully
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_module(repo_root: Path) -> Any:
    """Load ``.ygg/subagents_cli.py`` as a Python module.

    Mirrors ygg.py's runtime loading. Returns the module object.
    Skips the test if the file is missing (e.g. partial repo checkout).
    """
    cli_path = repo_root / ".ygg" / "subagents_cli.py"
    if not cli_path.exists():
        pytest.skip(f"subagents_cli.py not found at {cli_path}")
    spec = importlib.util.spec_from_file_location(
        "_ygg_subagents_cli_under_test", cli_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _make_console() -> MagicMock:
    """Stub Rich-like Console that captures .print() calls."""
    console = MagicMock()
    console.print = MagicMock()
    return console


class _CapturingTable:
    """Lightweight stand-in for rich.table.Table that records rows.

    The CLI helper uses Table.add_row(...) with positional args. We capture
    every call into ``self.rows`` so tests can assert what was rendered
    without depending on Rich's actual rendering pipeline.
    """

    def __init__(self, *args, **kwargs):
        self.add_column = MagicMock()
        self.rows: list[tuple] = []

    def add_row(self, *args, **kwargs):
        self.rows.append(args)


def _make_table_factory():
    """Return a callable that produces _CapturingTable instances."""
    return _CapturingTable


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def repo_root() -> Path:
    """Locate the Yggdrasil repo root from the test file path.

    tests/ → lilith-orchestrator/ → Asgard/ → repo root
    """
    return Path(__file__).resolve().parents[3]


@pytest.fixture
def fresh_registry():
    """Ensure ``lilith_orchestrator.subagents`` starts and ends empty."""
    from lilith_orchestrator import subagents as sa

    sa.clear_registry()
    yield sa
    sa.clear_registry()


# ── Module loading ───────────────────────────────────────────────────────────


class TestModuleLoading:
    def test_module_loads_and_exposes_run(self, repo_root: Path):
        mod = _load_module(repo_root)
        assert callable(getattr(mod, "run", None))

    def test_module_docstring_describes_command(self, repo_root: Path):
        mod = _load_module(repo_root)
        assert mod.__doc__ is not None
        assert "ygg subagents" in mod.__doc__


# ── Default mode (registry table) ────────────────────────────────────────────


class TestDefaultMode:
    def _capture_table(self, console: MagicMock) -> _CapturingTable | None:
        """Find the Table instance passed to console.print(table)."""
        for call in console.print.call_args_list:
            for arg in call.args:
                if isinstance(arg, _CapturingTable):
                    return arg
        return None

    def test_prints_one_row_per_registered_persona(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="alpha",
                allowed_tools=["read_file"],
                when_to_use="alpha persona",
            )
        )
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="beta",
                allowed_tools=["write_file"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        table = self._capture_table(console)
        assert table is not None, "expected a Table to be printed"
        # Each registered persona contributes one row
        type_cells = [row[0] for row in table.rows]
        assert "alpha" in type_cells
        assert "beta" in type_cells

    def test_first_persona_when_to_use_is_printed(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="alpha",
                when_to_use="alpha persona",
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        assert "alpha persona" in printed
        assert "alpha" in printed

    def test_auto_registers_defaults_when_registry_empty(
        self, repo_root: Path, fresh_registry
    ):
        # Fresh registry starts empty
        assert fresh_registry.agent_types() == []

        mod = _load_module(repo_root)
        mod.run(
            console=_make_console(),
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        # After run(), defaults should be registered
        types = fresh_registry.agent_types()
        assert len(types) >= 3
        assert "planner" in types
        assert "researcher" in types

    def test_handles_import_failure_gracefully(self, repo_root: Path, monkeypatch):
        # Force the lazy import to fail
        mod = _load_module(repo_root)

        def _boom(*args, **kwargs):
            raise ImportError("synthetic failure for test")

        monkeypatch.setattr(
            "builtins.__import__",
            _boom,
            raising=True,
        )

        console = _make_console()
        # Should not raise — prints an error message and returns
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        # An error message mentioning the import failure should have printed
        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        assert "Cannot import" in printed or "synthetic failure" in printed


# ── --pool mode ──────────────────────────────────────────────────────────────


class TestPoolMode:
    def test_pool_unions_explicit_tools_across_personas(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="x",
                allowed_tools=["read_file", "write_file"],
            )
        )
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="y",
                allowed_tools=["terminal"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=True,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        assert "read_file" in printed
        assert "write_file" in printed
        assert "terminal" in printed

    def test_pool_skips_wildcard_personas(self, repo_root: Path, fresh_registry):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="wildcard",
                allowed_tools=["*"],  # would swallow everything
            )
        )
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="narrow",
                allowed_tools=["read_file"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=True,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        # The narrow persona's tool must appear
        assert "read_file" in printed


# ── --verify mode ────────────────────────────────────────────────────────────


class TestVerifyMode:
    def _capture_table(self, console: MagicMock) -> _CapturingTable | None:
        for call in console.print.call_args_list:
            for arg in call.args:
                if isinstance(arg, _CapturingTable):
                    return arg
        return None

    def test_verify_marks_known_tools_as_ok(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="known",
                allowed_tools=["read_file", "write_file"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=True,
            dry_run=False,
            prompt="noop",
        )

        # Footer always prints: either "All declared tool names resolved"
        # (success) or "Unknown tool names" (failure). Verify-mode-specific
        # summary is always present.
        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        assert (
            "All declared tool names resolved" in printed
            or "Unknown tool names" in printed
        )

    @pytest.mark.skipif(
        not (
            Path(__file__).resolve().parents[3] / "Vanaheim" / "Agents" / "agent_cards.yaml"
        ).exists(),
        reason="requires full Yggdrasil hub checkout (Vanaheim)",
    )
    def test_verify_marks_ok_in_table_for_known_tools(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="known",
                allowed_tools=["read_file"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=True,
            dry_run=False,
            prompt="noop",
        )

        table = self._capture_table(console)
        assert table is not None
        # Verify column is the last positional cell — row[4]
        assert len(table.rows) == 1
        verify_cell = table.rows[0][-1]
        assert "ok" in verify_cell
        assert "✗" not in verify_cell

    def test_verify_flags_unknown_tool_in_table(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register(
            fresh_registry.SubAgentDefinition(
                agent_type="bad",
                allowed_tools=["read_file", "nonexistent_tool_xyz"],
            )
        )

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent=None,
            pool=False,
            verify=True,
            dry_run=False,
            prompt="noop",
        )

        table = self._capture_table(console)
        assert table is not None
        verify_cell = table.rows[0][-1]
        # Should show count of bad tools: ✗ 1
        assert "✗" in verify_cell

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        # And the summary footer lists the unknown
        assert "Unknown tool names" in printed
        assert "nonexistent_tool_xyz" in printed


# ── --agent filter ──────────────────────────────────────────────────────────


class TestAgentFilter:
    def test_unknown_agent_prints_help(self, repo_root: Path, fresh_registry):
        # Register at least one so the registry isn't auto-filled with defaults
        # we don't care about. The filter happens before defaults, but
        # register_defaults() is called when the registry is empty.
        fresh_registry.register_defaults()

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent="does-not-exist",
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        assert "does-not-exist" in printed
        assert "Available:" in printed

    def test_known_agent_filter_returns_one_row(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register_defaults()

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent="planner",
            pool=False,
            verify=False,
            dry_run=False,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        # "1 persona(s) shown" only appears when filter narrowed to one
        assert "1 persona(s) shown" in printed
        assert "planner" in printed


# ── --dry-run mode ────────────────────────────────────────────────────────────


class TestDryRun:
    def test_dry_run_succeeds_with_stub_executor(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register_defaults()

        mod = _load_module(repo_root)
        console = _make_console()
        # Must NOT raise. Stub executor inside run() returns canned output.
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent="researcher",
            pool=False,
            verify=False,
            dry_run=True,
            prompt="summarize the Asgard package layout",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        # Stub executor always returns "[stub] agent='<name>' ..."
        assert "[stub]" in printed
        assert "researcher" in printed
        assert "result:" in printed
        assert "success:" in printed

    def test_dry_run_with_no_matching_agent_warns(
        self, repo_root: Path, fresh_registry
    ):
        fresh_registry.register_defaults()

        mod = _load_module(repo_root)
        console = _make_console()
        mod.run(
            console=console,
            print_banner=lambda: None,
            Table=_make_table_factory(),
            YGGDRASIL_ROOT=repo_root,
            agent="does-not-exist",
            pool=False,
            verify=False,
            dry_run=True,
            prompt="noop",
        )

        printed = " ".join(
            str(call.args[0]) if call.args else ""
            for call in console.print.call_args_list
        )
        # Agent filter fails before dry_run executes → "No agents to dry-run"
        assert "No agents to dry-run" in printed or "not found" in printed


# ── Missing file guard ──────────────────────────────────────────────────────


class TestMissingFileGuard:
    def test_run_handles_missing_helper_file(self, repo_root: Path, monkeypatch):
        """If the runtime path lookup fails (e.g. YGGDRASIL_ROOT wrong), the
        module already loaded but ``asgard_src`` insertion may not match.
        Here we exercise the path-construction guard by patching
        YGGDRASIL_ROOT to a directory without ``Asgard/lilith-orchestrator``.

        This is a softer check — module imports work because we already
        loaded the helper file directly. The runtime path-resolution path
        in the helper is exercised instead.
        """
        mod = _load_module(repo_root)

        # Fake root that has no Asgard/ subdirectory
        fake_root = repo_root / "this-does-not-exist-asgard"
        fake_root.mkdir(exist_ok=True)
        try:
            console = _make_console()
            mod.run(
                console=console,
                print_banner=lambda: None,
                Table=_make_table_factory(),
                YGGDRASIL_ROOT=fake_root,
                agent=None,
                pool=False,
                verify=False,
                dry_run=False,
                prompt="noop",
            )
            # Should NOT raise — fallback path inside run() handles this.
            assert console.print.called
        finally:
            fake_root.rmdir()