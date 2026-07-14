"""Tests for lilith_core.tools — frozen catalog + validator.

These tests protect two contracts:

1. **Self-consistency** -- the catalogs declared in
   :mod:`lilith_core.tools` are coherent: alias targets land on real
   BaseTool names; CAPABILITY_ONLY is derived consistently; the
   validate_* functions agree with is_real_tool / canonical_tool_name.

2. **Drift guard against lilith-tools** -- the hardcoded
   ``TOOL_NAMES`` set is verified against the *actual* BaseTool
   subclasses discoverable by importing :mod:`lilith_tools` at test
   time. If a new BaseTool is added in lilith-tools without updating
   this catalog, ``test_tool_catalog_matches_lilith_tools`` fails.
"""

from __future__ import annotations

import inspect
from typing import Iterable

import pytest

from lilith_core.tools import (
    CAPABILITY_ONLY,
    CARD_TOOL_ALIASES,
    CARD_TOOL_VOCAB,
    TOOL_NAMES,
    ToolNameValidation,
    canonical_tool_name,
    is_known_card_tool,
    is_real_tool,
    validate_tool_names,
)


# ── Catalog self-consistency ───────────────────────────────────────────────


class TestCatalogSelfConsistency:
    """The catalog agrees with itself."""

    def test_tool_names_is_frozenset(self) -> None:
        assert isinstance(TOOL_NAMES, frozenset)
        assert all(isinstance(n, str) for n in TOOL_NAMES)
        assert all(n for n in TOOL_NAMES)

    def test_card_tool_vocab_is_frozenset(self) -> None:
        assert isinstance(CARD_TOOL_VOCAB, frozenset)
        assert all(isinstance(n, str) and n for n in CARD_TOOL_VOCAB)

    def test_card_tool_aliases_targets_in_tool_names(self) -> None:
        """Every alias target must be a real BaseTool."""
        for card_name, target in CARD_TOOL_ALIASES.items():
            assert target in TOOL_NAMES, (
                f"alias {card_name!r} -> {target!r} but {target!r} "
                f"is not in TOOL_NAMES"
            )

    def test_card_tool_aliases_keys_in_vocab(self) -> None:
        """Every alias key must be in the card vocabulary."""
        for card_name in CARD_TOOL_ALIASES:
            assert card_name in CARD_TOOL_VOCAB, (
                f"alias key {card_name!r} not in CARD_TOOL_VOCAB"
            )

    def test_capability_only_disjoint_from_concrete(self) -> None:
        """Names classified capability-only must not alias to a
        concrete BaseTool.
        """
        for n in CAPABILITY_ONLY:
            assert n not in TOOL_NAMES, (
                f"{n!r} is capability-only but also in TOOL_NAMES"
            )
            target = CARD_TOOL_ALIASES.get(n)
            assert target is None or target == n or target not in TOOL_NAMES, (
                f"{n!r} aliases to {target!r} (a real tool) but is in "
                f"CAPABILITY_ONLY"
            )

    def test_capability_only_subset_of_vocab(self) -> None:
        assert CAPABILITY_ONLY <= CARD_TOOL_VOCAB


# ── Drift guard against lilith-tools ───────────────────────────────────────


#: Submodules of lilith_tools that house BaseTool subclasses. Excludes
#: non-tool helpers (cve, isolation, registry, heimdall_integration
#: adapter, the ygg API client, ...). When a new BaseTool-bearing
#: submodule is added, extend this list.
_LILITH_TOOL_SUBMODULES: tuple[str, ...] = (
    "browser",
    "chunk_recall",
    "coding",
    "filesystem",
    "local_context",
    "package_guard",
    "security",
    "system",
    "vision",
    "web_search",
)


def _discover_real_lilith_tool_names() -> set[str]:
    """Import lilith_tools at runtime and return the set of
    BaseTool.name values actually present. Falls back to an empty
    set (and skips the drift test) if lilith_tools is not available
    in the test environment.
    """
    try:
        from lilith_tools import (  # type: ignore[import-not-found]
            browser,  # noqa: F401
            chunk_recall,  # noqa: F401
            coding,  # noqa: F401
            filesystem,  # noqa: F401
            local_context,  # noqa: F401
            package_guard,  # noqa: F401
            security,  # noqa: F401
            system,  # noqa: F401
            vision,  # noqa: F401
            web_search,  # noqa: F401
        )
        from lilith_tools.base import BaseTool  # type: ignore[import-not-found]
        import lilith_tools  # type: ignore[import-not-found]
    except Exception:
        return set()

    names: set[str] = set()
    for modname in _LILITH_TOOL_SUBMODULES:
        try:
            mod = getattr(lilith_tools, modname)
        except AttributeError:
            continue
        for _n, obj in inspect.getmembers(mod, inspect.isclass):
            try:
                if obj is BaseTool:
                    continue
                if not issubclass(obj, BaseTool):
                    continue
                if obj.name:
                    names.add(obj.name)
            except TypeError:
                continue
    return names


@pytest.mark.skipif(
    not _discover_real_lilith_tool_names(),
    reason="lilith_tools not installed in this environment",
)
class TestCatalogMatchesLilithTools:
    """Hardcoded catalog must equal real BaseTool subclasses."""

    def test_tool_catalog_matches_lilith_tools(self) -> None:
        real = _discover_real_lilith_tool_names()
        assert TOOL_NAMES == frozenset(real), (
            f"TOOL_NAMES drifts from lilith-tools BaseTool subclasses.\n"
            f"  Catalog has ({sorted(TOOL_NAMES - real)} extra)\n"
            f"  Real code has ({sorted(real - TOOL_NAMES)} missing)\n"
            f"If you just added a BaseTool, update "
            f"lilith_core.tools.TOOL_NAMES."
        )


# ── Predicate helpers ──────────────────────────────────────────────────────


class TestPredicateHelpers:
    """is_real_tool / is_known_card_tool / canonical_tool_name."""

    @pytest.mark.parametrize("name", sorted(TOOL_NAMES))
    def test_is_real_tool_true_for_concrete(self, name: str) -> None:
        assert is_real_tool(name)

    @pytest.mark.parametrize("name", ["definitely-not-a-real-tool", ""])
    def test_is_real_tool_false_for_unknown(self, name: str) -> None:
        assert not is_real_tool(name)

    def test_is_known_card_tool_for_card_vocab(self) -> None:
        for n in CARD_TOOL_VOCAB:
            assert is_known_card_tool(n), n

    def test_is_known_card_tool_for_concrete(self) -> None:
        for n in TOOL_NAMES:
            assert is_known_card_tool(n), n

    def test_is_known_card_tool_false_for_garbage(self) -> None:
        assert not is_known_card_tool("invented_tool_xyz")

    def test_canonical_tool_name_aliased(self) -> None:
        assert canonical_tool_name("read_file") == "file_read"
        assert canonical_tool_name("web_search") == "web_search"

    def test_canonical_tool_name_capability_only(self) -> None:
        # terminal, write_file, patch, etc. have no concrete resolution
        assert canonical_tool_name("terminal") is None
        assert canonical_tool_name("write_file") is None
        assert canonical_tool_name("patch") is None

    def test_canonical_tool_name_unknown(self) -> None:
        assert canonical_tool_name("nonsense_tool") is None


# ── validate_tool_names ────────────────────────────────────────────────────


class TestValidateToolNames:
    """Bulk validator: two-tier (unknown vs capability-only) behavior."""

    def test_empty_input(self) -> None:
        res = validate_tool_names([])
        assert res == ToolNameValidation()
        assert res.ok

    def test_concrete_only(self) -> None:
        # Pick a name that IS concrete and not in CARD_TOOL_VOCAB.
        direct_concrete = sorted(TOOL_NAMES - CARD_TOOL_VOCAB)[0]
        res = validate_tool_names([direct_concrete])
        assert res.ok
        assert direct_concrete in res.concrete
        assert not res.unknown
        assert not res.capability_only

    def test_aliased_resolves_to_concrete(self) -> None:
        res = validate_tool_names(["read_file", "web_search"])
        assert res.ok
        assert res.concrete == frozenset({"file_read", "web_search"})
        assert not res.capability_only

    def test_capability_only_passed_by_default(self) -> None:
        res = validate_tool_names(["terminal", "patch", "write_file"])
        assert res.ok
        assert res.capability_only == frozenset({"terminal", "patch", "write_file"})
        assert not res.concrete

    def test_capability_only_rejected_when_strict(self) -> None:
        res = validate_tool_names(
            ["terminal", "patch"], allow_capabilities=False
        )
        assert not res.ok
        assert res.unknown == frozenset({"terminal", "patch"})
        assert not res.capability_only

    def test_unknown_always_rejected(self) -> None:
        res = validate_tool_names(["invented_tool"], allow_capabilities=True)
        assert not res.ok
        assert res.unknown == frozenset({"invented_tool"})

    def test_unknown_rejected_with_allow_capabilities_false(self) -> None:
        res = validate_tool_names(["invented_tool"], allow_capabilities=False)
        assert not res.ok
        assert "invented_tool" in res.unknown

    def test_mixed_inputs_partitioned(self) -> None:
        names = [
            "read_file",        # alias -> file_read (concrete)
            "web_search",       # alias -> self (concrete)
            "terminal",         # capability-only
            "invented",         # unknown
        ]
        res = validate_tool_names(names, allow_capabilities=True)
        assert res.concrete == frozenset({"file_read", "web_search"})
        assert res.capability_only == frozenset({"terminal"})
        assert res.unknown == frozenset({"invented"})
        assert res.known == frozenset({"file_read", "web_search", "terminal"})
        assert not res.ok

    def test_mixed_inputs_strict(self) -> None:
        names = ["terminal", "invented"]
        res = validate_tool_names(names, allow_capabilities=False)
        # Both end up in unknown under strict mode
        assert res.unknown == frozenset({"terminal", "invented"})
        assert not res.ok

    def test_empty_strings_ignored(self) -> None:
        # YAML occasionally leaves blanks; they should be filtered out.
        res = validate_tool_names(["", "terminal", None])  # type: ignore[list-item]
        assert res.ok
        assert res.capability_only == frozenset({"terminal"})

    def test_duplicates_collapse(self) -> None:
        res = validate_tool_names(["terminal", "terminal"])
        assert len(res.capability_only) == 1

    def test_result_dataclass_is_frozen(self) -> None:
        res = validate_tool_names(["terminal"])
        with pytest.raises((AttributeError, TypeError)):
            res.concrete = frozenset()  # type: ignore[misc]


# ── Catalog counts (informational, also catches accidental mass-rename) ─────


class TestCatalogCounts:
    """Lock the count so accidental deletions/duplications fail loudly."""

    def test_tool_names_count(self) -> None:
        # Lock-in: 26 BaseTool subclasses shipped today in lilith-tools
        # (batch_edit + browser + chunk_recall/ingest/store_stats + coding +
        # 4 fs + grep_files + 8 local_context + package_guard +
        # security_scan + 2 system + 2 vision + web_search). If this
        # changes, the spec also changes intentionally -- update both
        # this number and TOOL_NAMES.
        assert len(TOOL_NAMES) == 26

    def test_card_vocab_count(self) -> None:
        # 7 card-level tool names declared in the spec
        assert len(CARD_TOOL_VOCAB) == 7

    def test_alias_count(self) -> None:
        # Only the 2 aliases listed in the spec; guard against creep.
        assert len(CARD_TOOL_ALIASES) == 2

    def test_capability_only_count(self) -> None:
        # 7 vocab - 2 aliased (read_file, web_search) = 5 capability-only.
        assert len(CAPABILITY_ONLY) == 5
        assert CAPABILITY_ONLY == frozenset(
            {"terminal", "write_file", "patch", "search_files", "session_search"}
        )
