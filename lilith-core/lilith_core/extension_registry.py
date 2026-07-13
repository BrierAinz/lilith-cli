"""Generic extension registry for lilith-core.

A typed key-value store scoped by :class:`ExtensionKind`, used to
catalog extensions (providers, skills, runtimes, agents, ...) with
consistent register/resolve/get/unregister semantics and explicit
error types for misuse (duplicates, unknown keys).

This is deliberately *not* wired into any concrete subsystem (agent
registry, provider registry, sandbox registry, ...). It is a building
block other packages compose on top of. Wiring decisions belong to
lilith-orchestrator / lilith-cli / lilith-api -- kept out of scope
here on purpose so lilith-core stays a dependency-light foundation.

Design goals
------------

1. **Generic over value type** -- :class:`ExtensionRegistry` is
   parameterized as ``ExtensionRegistry[T]`` so a caller can lock in
   the value type at construction and rely on the type checker.
2. **Kind-scoped namespaces** -- the same name can coexist across
   different ``ExtensionKind`` values (e.g. ``"openai"`` as provider
   *and* as skill). Lookups are always kind-scoped.
3. **Fail-loud semantics** -- :meth:`register` raises on duplicate
   unless ``overwrite=True``; :meth:`resolve` raises on unknown.
   :meth:`get` returns a default for missing keys (non-raising).
4. **Introspectable** -- :meth:`list` returns the keys for a kind (or
   all kinds); :meth:`clear` resets one or all namespaces.

Inspiration
-----------
Pattern borrowed from the Eter-Agents extension-loader concept
(``research/emerging-agents-2026-06-28.md``, medium-priority
recommendation: a small typed registry trait instead of bespoke dicts
scattered through each subsystem).
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar


class ExtensionKind(str, Enum):
    """Kinds of extension the registry knows about.

    Members:
        PROVIDER: LLM providers (text-completion / chat backends).
        SKILL: Skill packages (lilith-skills surface area).
        RUNTIME: Runtime adapters (sandbox / router / execution loop).
        AGENT: Agent definitions or agent card bindings.
    """

    PROVIDER = "provider"
    SKILL = "skill"
    RUNTIME = "runtime"
    AGENT = "agent"


# Value type bound at registry construction.
T = TypeVar("T")


class ExtensionRegistryError(Exception):
    """Base for every error raised by :class:`ExtensionRegistry`."""


class DuplicateExtensionError(ExtensionRegistryError):
    """Raised when registering a name that already exists in a kind.

    Attributes:
        kind: The ExtensionKind namespace involved.
        name: The duplicate name.
    """

    def __init__(self, kind: ExtensionKind, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(
            f"Extension '{name}' already registered under {kind.value!r}; "
            f"pass overwrite=True to replace."
        )


class UnknownExtensionError(ExtensionRegistryError, KeyError):
    """Raised when resolving or unregistering an unknown name.

    Subclasses :class:`KeyError` so ``except KeyError`` continues to
    work in code that hasn't been updated to the richer hierarchy.

    Attributes:
        kind: The ExtensionKind namespace involved.
        name: The name that wasn't found.
    """

    def __init__(self, kind: ExtensionKind, name: str) -> None:
        self.kind = kind
        self.name = name
        KeyError.__init__(self, (kind.value, name))
        ExtensionRegistryError.__init__(
            self,
            f"Extension '{name}' not registered under {kind.value!r}",
        )


class ExtensionRegistry(Generic[T]):
    """Typed, kind-scoped registry for arbitrary extension values.

    Usage::

        reg: ExtensionRegistry[MyProvider] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "openai", MyProvider())
        provider = reg.resolve(ExtensionKind.PROVIDER, "openai")

    The same name may exist under different ``ExtensionKind`` values
    without conflict -- kind is part of the key.
    """

    def __init__(self) -> None:
        # Nested dict keeps the typechecker's ``T`` happy: each inner
        # value is typed as ``T`` at the callsite even though storage
        # is heterogeneous behind the scenes.
        self._store: dict[ExtensionKind, dict[str, T]] = {
            kind: {} for kind in ExtensionKind
        }

    # ── Mutators ─────────────────────────────────────────────────────

    def register(
        self,
        kind: ExtensionKind,
        name: str,
        value: T,
        *,
        overwrite: bool = False,
    ) -> None:
        """Insert *value* under ``(kind, name)``.

        Raises :class:`DuplicateExtensionError` if the name already
        exists in *kind* and *overwrite* is ``False``.
        """
        bucket = self._store[kind]
        if name in bucket and not overwrite:
            raise DuplicateExtensionError(kind, name)
        bucket[name] = value

    def unregister(self, kind: ExtensionKind, name: str) -> None:
        """Remove ``(kind, name)``.

        Raises :class:`UnknownExtensionError` if the name does not
        exist.
        """
        bucket = self._store[kind]
        if name not in bucket:
            raise UnknownExtensionError(kind, name)
        del bucket[name]

    def clear(self, kind: ExtensionKind | None = None) -> None:
        """Reset one kind's namespace (or all of them).

        After ``reg.clear()`` the registry is empty again; after
        ``reg.clear(ExtensionKind.SKILL)`` only the skill bucket is
        cleared.
        """
        if kind is None:
            for bucket in self._store.values():
                bucket.clear()
            return
        self._store[kind].clear()

    # ── Readers ──────────────────────────────────────────────────────

    def resolve(self, kind: ExtensionKind, name: str) -> T:
        """Return the value registered under ``(kind, name)``.

        Raises :class:`UnknownExtensionError` if missing.
        """
        bucket = self._store[kind]
        if name not in bucket:
            raise UnknownExtensionError(kind, name)
        return bucket[name]

    def get(self, kind: ExtensionKind, name: str, default: T | None = None) -> T | None:
        """Return the value under ``(kind, name)`` or *default* if absent.

        Unlike :meth:`resolve`, this does NOT raise on miss -- use it
        for callers that want a graceful default.
        """
        return self._store[kind].get(name, default)

    def list(
        self, kind: ExtensionKind | None = None
    ) -> dict[ExtensionKind, list[str]] | list[str]:
        """List known names.

        When *kind* is given, returns a flat ``list[str]`` of names in
        that bucket. When *kind* is ``None``, returns a dict mapping
        each kind to its name list (kinds with empty buckets are
        included for completeness).
        """
        if kind is None:
            return {k: list(v.keys()) for k, v in self._store.items()}
        return list(self._store[kind].keys())

    # ── Introspection ────────────────────────────────────────────────

    def __contains__(self, key: tuple[ExtensionKind, str]) -> bool:
        """Support ``(kind, name) in registry`` membership tests."""
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        k, n = key
        return n in self._store.get(k, {})

    def __len__(self) -> int:
        """Total number of registered entries across all kinds."""
        return sum(len(bucket) for bucket in self._store.values())


__all__ = [
    "DuplicateExtensionError",
    "ExtensionKind",
    "ExtensionRegistry",
    "ExtensionRegistryError",
    "UnknownExtensionError",
]
