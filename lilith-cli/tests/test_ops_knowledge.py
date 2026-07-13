"""Tests for lilith_cli.ops_knowledge — operator console (plan-29 A2).

Scope:
  - ``memory search|recent`` is exercised against a real
    :class:`lilith_memory.store.MemoryStore` on a ``tmp_path`` SQLite
    file (no mocks — the store is deterministic, and we want the
    integration boundary verified).
  - ``ask`` is exercised against a minimal Mimir index built in a
    ``tmp_path`` root containing two ``.md`` files (real SemanticChunker
    + FTS5 backend, no mocks).  When --index is omitted, the index
    must already exist (we build it in the fixture).
  - Missing index / missing DB paths produce friendly error messages,
    not stacktraces, with the documented exit codes.
  - Cyclopts registration: ``ask`` and ``memory`` show up at the top
    level alongside ``agents`` and ``bus``.

All temp trees live inside the test's ``tmp_path`` and are removed by
pytest automatically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# Mirror conftest.py locally because tests in this file may be collected
# by other pytest entry points (e.g. a broader scope runner).
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def memory_db(tmp_path: Path) -> Path:
    """Create a populated MemoryStore DB on tmp_path and return its path.

    Populates both arbitrary sessions (for ``search`` coverage) AND the
    ``default`` session (so ``recent()`` has something to return — the
    MemoryStore contract is ``recent = recall("default", ...)``).
    """
    from lilith_memory.store import MemoryStore

    db = tmp_path / "memory.db"
    store = MemoryStore(db)
    store.store(
        session_id="ops-1",
        role="user",
        content="User mentioned the anti-fantasma rule is in REGLAS.md.",
    )
    store.store(
        session_id="ops-1",
        role="assistant",
        content="Confirmed — anti-fantasma lives in Svartalfheim/REGLAS.md.",
    )
    store.store(
        session_id="ops-2",
        role="user",
        content="Phantom bugs come from citing symbols that do not exist.",
    )
    store.store(
        session_id="ops-2",
        role="assistant",
        content="Whenever you cite a symbol, verify it; report if missing.",
    )
    # Default-session entries (so ``recent()`` has something to report).
    store.store(
        session_id="default",
        role="user",
        content="Operator asked for recent memories.",
    )
    store.store(
        session_id="default",
        role="assistant",
        content="Here are the most recent entries.",
    )
    return db


@pytest.fixture
def mimir_tree(tmp_path: Path) -> Path:
    """Create a minimal knowledge root under tmp_path with two .md files."""
    docs = tmp_path / "Svartalfheim" / "plans"
    docs.mkdir(parents=True)
    (docs / "photon.md").write_text(
        "# Photons\n\n"
        "A photon is the quantum of light. Photons carry the electromagnetic force.\n"
        "Anti-fantasma: every photon quote must trace to a cited chunk.\n",
        encoding="utf-8",
    )
    (docs / "neutrino.md").write_text(
        "# Neutrinos\n\nNeutrinos are nearly massless and rarely interact with matter.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mimir_index_db(tmp_path: Path) -> Path:
    """Path inside tmp_path for the Mimir index SQLite file."""
    return tmp_path / "mimir_index.db"


# ── Importability & version bump ────────────────────────────────────


def test_ops_knowledge_module_imports():
    """ops_knowledge should expose ask, memory_app, default_memory_db_path."""
    from lilith_cli import ops_knowledge

    assert callable(ops_knowledge.ask)
    assert callable(ops_knowledge.default_memory_db_path)
    # memory_app is a cyclopts App.
    name = ops_knowledge.memory_app.name
    assert name == "memory" or (isinstance(name, tuple) and name[0] == "memory")


# ── Mimir loader ────────────────────────────────────────────────────


def test_load_mimir_cli_returns_working_module(monkeypatch, tmp_path: Path):
    """load_mimir_cli should return the Mimir module with cmd_* functions."""
    from lilith_cli import main as cli_main
    from lilith_cli import ops_knowledge

    # Mirror the test_resolve_yggdrasil_root trick: relocate __file__ so
    # _resolve_yggdrasil_root() lands inside tmp_path, then point
    # _mimir_cli_path at a fake cli.py that exposes cmd_index/cmd_ask.
    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))

    mimir_dir = fake_root / "Vanaheim" / "Agents" / "Mimir"
    mimir_dir.mkdir(parents=True)
    (mimir_dir / "cli.py").write_text(
        "def cmd_index(args):\n    return 0\ndef cmd_ask(args):\n    return 0\n",
        encoding="utf-8",
    )

    mod = ops_knowledge.load_mimir_cli()
    assert callable(getattr(mod, "cmd_index", None))
    assert callable(getattr(mod, "cmd_ask", None))


def test_load_mimir_cli_missing_file(monkeypatch, tmp_path: Path, capsys):
    """Missing cli.py should raise FileNotFoundError (caller handles it)."""
    from lilith_cli import main as cli_main
    from lilith_cli import ops_knowledge

    fake_root = tmp_path / "Yggdrasil"
    fake_main = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_main.parent.mkdir(parents=True)
    fake_main.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_main))
    # Intentionally do NOT create Vanaheim/Agents/Mimir/cli.py.

    with pytest.raises(FileNotFoundError):
        ops_knowledge.load_mimir_cli()


# ── ask: missing-index branch ──────────────────────────────────────


def test_ask_missing_index_prints_hint_and_exits(mimir_tree: Path, capsys):
    """ask with no index on disk should exit 2 with a friendly hint.

    Note: we deliberately do **not** monkeypatch ``cli_main.__file__`` —
    we want the loader to read from the real workspace (so Mimir is
    found), and we override the per-call ``root`` and ``db`` paths so
    the workspace index doesn't accidentally satisfy the check.
    """
    from lilith_cli import ops_knowledge

    non_existing_db = mimir_tree / "does_not_exist.db"
    assert not non_existing_db.exists()

    with pytest.raises(SystemExit) as excinfo:
        ops_knowledge.ask(
            query="anything",
            k=3,
            vector=False,
            index=False,
            db=non_existing_db,
            root=mimir_tree,
        )
    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "No Mimir index" in out
    assert "lilith ask --index" in out


# ── ask: real index end-to-end ──────────────────────────────────────


def test_ask_against_real_mimir_index(mimir_tree: Path, capsys):
    """ask should retrieve the photon chunk when the index is built."""
    from lilith_cli import ops_knowledge

    # 1) Build the Mimir index in our fixture tree.
    mimir = ops_knowledge.load_mimir_cli()
    db, meta = mimir._index_paths(mimir_tree, None)
    assert mimir._index_fts(mimir_tree, db, meta, full_reset=True) == 0
    assert db.exists()

    # 2) Run ask against it.
    ops_knowledge.ask(
        query="anti-fantasma photon",
        k=3,
        vector=False,
        index=False,
        db=db,
        root=mimir_tree,
    )
    out = capsys.readouterr().out
    # Mimir's renderer prints a "# Query:" header + source citation.
    assert "Query:" in out
    assert "photon.md" in out or "Svartalfheim/plans/photon.md" in out


def test_ask_with_index_flag_rebuilds_then_queries(mimir_tree: Path, capsys):
    """ask --index should rebuild the index then query successfully."""
    from lilith_cli import ops_knowledge

    db = mimir_tree / "mimir_index.db"
    assert not db.exists()

    ops_knowledge.ask(
        query="neutrino",
        k=2,
        vector=False,
        index=True,  # ← rebuild before ask
        db=db,
        root=mimir_tree,
    )
    out = capsys.readouterr().out
    assert db.exists(), "ask --index should have built the DB"
    assert "neutrino.md" in out or "Neutrinos" in out


# ── memory: missing-DB branch ───────────────────────────────────────


def test_memory_search_missing_db_exits(monkeypatch, tmp_path: Path, capsys):
    """memory search with no DB should exit 1 with a friendly hint."""
    from lilith_cli import ops_knowledge

    # Force default_memory_db_path() to look at a path that does not exist.
    fake_db = tmp_path / "ghost_memory.db"
    assert not fake_db.exists()

    with pytest.raises(SystemExit) as excinfo:
        ops_knowledge.search(query="phantom", limit=5, db=fake_db)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Memory DB not found" in out
    assert "--db" in out


def test_memory_recent_missing_db_exits(monkeypatch, tmp_path: Path, capsys):
    """memory recent with no DB should exit 1 with a friendly hint."""
    from lilith_cli import ops_knowledge

    fake_db = tmp_path / "ghost_memory.db"

    with pytest.raises(SystemExit) as excinfo:
        ops_knowledge.recent(limit=10, db=fake_db)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Memory DB not found" in out


# ── memory: real-store end-to-end ──────────────────────────────────


def test_memory_search_hits_real_store(memory_db: Path, capsys):
    """memory search should return matching rows from the real MemoryStore."""
    from lilith_cli import ops_knowledge

    ops_knowledge.search(query="anti-fantasma", limit=5, db=memory_db)
    out = capsys.readouterr().out
    # Two of the four fixture entries mention "anti-fantasma".
    assert "anti-fantasma" in out
    assert "Svartalfheim/REGLAS.md" in out
    assert "2 hit(s)" in out


def test_memory_search_uses_requester_operator(memory_db: Path, monkeypatch):
    """memory search must pass requester='operator' to MemoryStore.search."""
    from lilith_cli import ops_knowledge

    seen: dict[str, object] = {}

    def fake_search(self, query, limit, requester=None, **kwargs):
        seen["query"] = query
        seen["limit"] = limit
        seen["requester"] = requester
        return []

    from lilith_memory.store import MemoryStore

    monkeypatch.setattr(MemoryStore, "search", fake_search)
    ops_knowledge.search(query="phantom", limit=7, db=memory_db)
    assert seen["query"] == "phantom"
    assert seen["limit"] == 7
    assert seen["requester"] == "operator"


def test_memory_recent_returns_latest_first(memory_db: Path, capsys):
    """memory recent should list the freshest rows on the default session."""
    from lilith_cli import ops_knowledge

    ops_knowledge.recent(limit=2, db=memory_db)
    out = capsys.readouterr().out
    # We stored 2 entries on session_id="default"; with limit=2 we get 2.
    assert "2 entry(ies)" in out
    # Header rows from Rich.
    assert "Session" in out
    assert "Content" in out
    # The default-session rows we stored should appear.
    assert "Operator asked for recent memories" in out or "Here are the most recent" in out


def test_memory_recent_uses_requester_operator(memory_db: Path, monkeypatch):
    """memory recent must pass requester='operator' to MemoryStore.recent."""
    from lilith_cli import ops_knowledge

    seen: dict[str, object] = {}

    def fake_recent(self, limit, requester=None, **kwargs):
        seen["limit"] = limit
        seen["requester"] = requester
        return []

    from lilith_memory.store import MemoryStore

    monkeypatch.setattr(MemoryStore, "recent", fake_recent)
    ops_knowledge.recent(limit=42, db=memory_db)
    assert seen["limit"] == 42
    assert seen["requester"] == "operator"


def test_memory_search_no_match_prints_dim(memory_db: Path, capsys):
    """memory search with no match should print a dim 'No memories …' line."""
    from lilith_cli import ops_knowledge

    ops_knowledge.search(query="xyznotthere", limit=10, db=memory_db)
    out = capsys.readouterr().out
    assert "No memories matching" in out
    assert "xyznotthere" in out


# ── Cyclopts registration ───────────────────────────────────────────


def test_app_registers_ask_and_memory():
    """main.app should expose ``ask`` and the ``memory`` sub-app."""
    from lilith_cli.main import app

    registered = set(app._registered_commands)
    assert "ask" in registered, f"ask missing from {registered}"
    assert "memory" in registered, f"memory missing from {registered}"
    # A1 commands must still be present.
    assert "agents" in registered
    assert "bus" in registered


def test_memory_app_subcommands():
    """memory_app should expose search and recent."""
    from lilith_cli.ops_knowledge import memory_app

    registered = set(memory_app._registered_commands)
    assert {"search", "recent"} <= registered
