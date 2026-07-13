"""Tests for the mem0 backend adapter."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest


if TYPE_CHECKING:
    from pathlib import Path


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_mem0_available: bool = False
try:
    import mem0 as _mem0_module

    _mem0_available = True
except ImportError:
    _mem0_module = None  # type: ignore[assignment]

skip_no_mem0 = pytest.mark.skipif(
    not _mem0_available,
    reason="mem0ai is not installed — run `pip install lilith-memory[mem0]`",
)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_mem0_import_or_skip():
    """Verify that the import guard works (may skip in CI without mem0)."""
    if not _mem0_available:
        pytest.skip("mem0ai not installed")
    assert _mem0_module is not None


@skip_no_mem0
def test_mem0_backend_init(tmp_path: Path):
    """Mem0Backend should initialise without errors when mem0 is available."""
    from lilith_memory.backends import Mem0Backend

    with (
        patch.dict("os.environ", {"MEM0_API_KEY": ""}, clear=False),
        patch("mem0.Memory.from_config") as mock_from_config,
    ):
        mock_from_config.return_value = MagicMock()
        backend = Mem0Backend(db_path=tmp_path / "test.db")
        assert backend is not None


@pytest.mark.asyncio
async def test_add_and_search_mock(tmp_path: Path):
    """Test add and search with a mocked mem0.Memory instance."""
    mock_mem_module = MagicMock()
    mock_instance = MagicMock()
    mock_mem_module.Memory.from_config.return_value = mock_instance

    # add() returns a dict with an "id"
    mock_instance.add.return_value = {"id": "mem-001"}

    # search() returns results
    mock_instance.search.return_value = {
        "results": [
            {
                "id": "mem-001",
                "memory": "Hello world",
                "metadata": {"source": "test"},
                "score": 0.95,
            },
        ],
    }

    with patch.dict(
        "sys.modules",
        {"mem0": mock_mem_module, "mem0.Memory": mock_mem_module.Memory},
    ):
        # We must also patch the already-imported reference inside mem0_backend
        from lilith_memory.backends.mem0_backend import Mem0Backend

        backend = Mem0Backend(db_path=tmp_path / "mock.db")

        # add
        entry_id = await backend.add("Hello world", metadata={"source": "test"})
        assert entry_id == "mem-001"

        # search
        results = await backend.search("Hello", limit=5)
        assert len(results) == 1
        assert results[0]["content"] == "Hello world"


def test_fallback_to_sqlite(tmp_path: Path):
    """When mem0ai is not importable, ImportError should be raised with a hint."""
    # Remove mem0 from importable modules to trigger the ImportError path
    with patch.dict("sys.modules", {"mem0": None}):
        # Need a fresh import to hit the constructor guard
        import lilith_memory.backends.mem0_backend as mod

        importlib.reload(mod)
        mem0_backend_cls = mod.Mem0Backend

        with pytest.raises(ImportError, match="mem0ai is required"):
            mem0_backend_cls(db_path=tmp_path / "fb.db")
