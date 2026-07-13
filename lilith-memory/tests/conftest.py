"""Test configuration for lilith-memory.

Adds the package directory to sys.path so that
`from lilith_memory.store import ...` works even when
the package is not installed via pip/uv.
"""

import sys
from pathlib import Path


# Ensure lilith_memory is importable when running tests directly
_pkg_dir = str(Path(__file__).resolve().parent.parent)
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)
