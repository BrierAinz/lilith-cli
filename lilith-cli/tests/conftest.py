"""Test configuration for lilith-cli.

Adds the package directory to sys.path so that
`from lilith_cli.main import ...` works without pip install.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# Ensure lilith_cli is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)


@pytest.fixture
def fake_session():
    """Return a lightweight AgentSession with a mocked provider."""
    from lilith_cli.agent import AgentSession
    from lilith_cli.config import YggdrasilConfig

    cfg = YggdrasilConfig(provider="local", model="local-model")
    session = AgentSession(cfg)
    session.provider = MagicMock()
    session.provider.stream = AsyncMock(return_value=iter([]))
    return session
