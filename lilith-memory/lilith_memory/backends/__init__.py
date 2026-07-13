"""Lilith Memory backend layer — pluggable storage adapters."""

from .base import MemoryBackend
from .sqlite_backend import SQLiteBackend


try:
    from .chroma_backend import ChromaBackend
except ImportError:  # chromadb not installed
    ChromaBackend = None  # type: ignore[assignment,misc]

try:
    from .mem0_backend import Mem0Backend
except ImportError:  # mem0ai not installed
    Mem0Backend = None  # type: ignore[assignment,misc]


__all__ = ["MemoryBackend", "SQLiteBackend"]
if ChromaBackend is not None:
    __all__.append("ChromaBackend")
if Mem0Backend is not None:
    __all__.append("Mem0Backend")
