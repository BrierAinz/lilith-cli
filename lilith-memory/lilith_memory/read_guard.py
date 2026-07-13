"""Read-time authorization seam for memory recall results."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar


T = TypeVar("T")
ReadPolicy = Callable[[T, str | None], bool]


def allow_all(item: T, requester: str | None) -> bool:
    """Default read policy: preserve current behavior."""
    return True


def guard(
    results: Iterable[T],
    *,
    requester: str | None,
    policy: ReadPolicy[T] | None,
) -> list[T]:
    """Filter recall/search/get results against the active read policy.

    The guard is intentionally storage-agnostic: stores pass already-built
    result objects through this function immediately before returning them.
    With ``policy=None`` it uses ``allow_all`` and returns the same results,
    preserving existing behavior. A real policy can later inspect each item
    with the current requester and deny stale permissions at read time without
    changing the storage backends.
    """
    active_policy = policy or allow_all
    return [item for item in results if active_policy(item, requester)]


__all__ = ["ReadPolicy", "allow_all", "guard"]
