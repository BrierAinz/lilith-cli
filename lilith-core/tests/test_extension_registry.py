"""Tests for lilith_core.extension_registry."""

from __future__ import annotations

import pytest

from lilith_core.extension_registry import (
    DuplicateExtensionError,
    ExtensionKind,
    ExtensionRegistry,
    ExtensionRegistryError,
    UnknownExtensionError,
)


# ── Lifecycle basics ────────────────────────────────────────────────────────


class TestRegisterAndResolve:
    def test_register_and_resolve(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "openai", "openai-impl")
        assert reg.resolve(ExtensionKind.PROVIDER, "openai") == "openai-impl"

    def test_register_default_no_overwrite_raises(self) -> None:
        reg: ExtensionRegistry[int] = ExtensionRegistry()
        reg.register(ExtensionKind.SKILL, "a", 1)
        with pytest.raises(DuplicateExtensionError):
            reg.register(ExtensionKind.SKILL, "a", 2)

    def test_register_with_overwrite_replaces(self) -> None:
        reg: ExtensionRegistry[int] = ExtensionRegistry()
        reg.register(ExtensionKind.SKILL, "a", 1)
        reg.register(ExtensionKind.SKILL, "a", 2, overwrite=True)
        assert reg.resolve(ExtensionKind.SKILL, "a") == 2

    def test_resolve_unknown_raises(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        with pytest.raises(UnknownExtensionError):
            reg.resolve(ExtensionKind.PROVIDER, "missing")

    def test_resolve_unknown_is_also_keyerror(self) -> None:
        """UnknownExtensionError subclasses KeyError for legacy callers."""
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        with pytest.raises(KeyError):
            reg.resolve(ExtensionKind.PROVIDER, "missing")

    def test_resolve_unknown_is_a_base_registry_error(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        with pytest.raises(ExtensionRegistryError):
            reg.resolve(ExtensionKind.PROVIDER, "missing")

    def test_get_returns_default_for_missing(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        assert reg.get(ExtensionKind.PROVIDER, "missing") is None
        assert reg.get(ExtensionKind.PROVIDER, "missing", default="fallback") == "fallback"


# ── Kind-scoping ────────────────────────────────────────────────────────────


class TestKindScoping:
    def test_same_name_different_kinds_coexist(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "openai", "provider-impl")
        reg.register(ExtensionKind.SKILL, "openai", "skill-impl")
        assert reg.resolve(ExtensionKind.PROVIDER, "openai") == "provider-impl"
        assert reg.resolve(ExtensionKind.SKILL, "openai") == "skill-impl"

    def test_duplicate_only_within_same_kind(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "dup", "a")
        reg.register(ExtensionKind.SKILL, "dup", "b")
        # Both succeed because they're in different buckets.
        assert reg.resolve(ExtensionKind.PROVIDER, "dup") == "a"
        assert reg.resolve(ExtensionKind.SKILL, "dup") == "b"

    def test_list_per_kind(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p1", "x")
        reg.register(ExtensionKind.PROVIDER, "p2", "x")
        reg.register(ExtensionKind.SKILL, "s1", "x")
        assert reg.list(ExtensionKind.PROVIDER) == ["p1", "p2"]
        assert reg.list(ExtensionKind.SKILL) == ["s1"]
        assert reg.list(ExtensionKind.RUNTIME) == []


# ── Removal and clearing ────────────────────────────────────────────────────


class TestRemovalAndClearing:
    def test_unregister_known(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p", "x")
        reg.unregister(ExtensionKind.PROVIDER, "p")
        assert reg.get(ExtensionKind.PROVIDER, "p") is None

    def test_unregister_unknown_raises(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        with pytest.raises(UnknownExtensionError):
            reg.unregister(ExtensionKind.PROVIDER, "ghost")

    def test_clear_one_kind(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p", "x")
        reg.register(ExtensionKind.SKILL, "s", "y")
        reg.clear(ExtensionKind.PROVIDER)
        assert reg.list(ExtensionKind.PROVIDER) == []
        assert reg.list(ExtensionKind.SKILL) == ["s"]

    def test_clear_all(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        for kind in ExtensionKind:
            reg.register(kind, "x", "y")
        reg.clear()
        for kind in ExtensionKind:
            assert reg.list(kind) == []
        assert len(reg) == 0

    def test_register_after_clear_works(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p", "1")
        reg.clear()
        # Should not raise -- register is fresh after clear.
        reg.register(ExtensionKind.PROVIDER, "p", "2")
        assert reg.resolve(ExtensionKind.PROVIDER, "p") == "2"


# ── Introspection ───────────────────────────────────────────────────────────


class TestIntrospection:
    def test_contains_tuple_true(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p", "x")
        assert (ExtensionKind.PROVIDER, "p") in reg

    def test_contains_tuple_false_missing_name(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        assert (ExtensionKind.PROVIDER, "missing") not in reg

    def test_contains_tuple_false_wrong_tuple(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        assert "not-a-tuple" not in reg
        assert (ExtensionKind.PROVIDER, "p", "extra") not in reg

    def test_len_counts_total(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        assert len(reg) == 0
        reg.register(ExtensionKind.PROVIDER, "a", "x")
        reg.register(ExtensionKind.PROVIDER, "b", "x")
        reg.register(ExtensionKind.SKILL, "c", "x")
        assert len(reg) == 3

    def test_list_all_kinds(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "p", "x")
        result = reg.list()
        assert isinstance(result, dict)
        assert set(result.keys()) == set(ExtensionKind)
        assert result[ExtensionKind.PROVIDER] == ["p"]
        assert result[ExtensionKind.SKILL] == []
        assert result[ExtensionKind.RUNTIME] == []
        assert result[ExtensionKind.AGENT] == []


# ── Type value discipline ───────────────────────────────────────────────────


class TestTypedValues:
    def test_registry_keeps_value_type_at_callsite(self) -> None:
        """The registry is generic; values can be anything per-kind."""
        reg: ExtensionRegistry[object] = ExtensionRegistry()
        reg.register(ExtensionKind.PROVIDER, "str-val", "hello")
        reg.register(ExtensionKind.PROVIDER, "int-val", 42)
        reg.register(ExtensionKind.PROVIDER, "dict-val", {"k": "v"})
        assert reg.resolve(ExtensionKind.PROVIDER, "str-val") == "hello"
        assert reg.resolve(ExtensionKind.PROVIDER, "int-val") == 42
        assert reg.resolve(ExtensionKind.PROVIDER, "dict-val") == {"k": "v"}

    def test_empty_registry_len_is_zero(self) -> None:
        reg: ExtensionRegistry[str] = ExtensionRegistry()
        assert len(reg) == 0


# ── Error type attributes ───────────────────────────────────────────────────


class TestErrorAttributes:
    def test_duplicate_error_carries_kind_and_name(self) -> None:
        try:
            raise DuplicateExtensionError(ExtensionKind.SKILL, "foo")
        except DuplicateExtensionError as exc:
            assert exc.kind is ExtensionKind.SKILL
            assert exc.name == "foo"
            assert "foo" in str(exc)
            assert ExtensionKind.SKILL.value in str(exc)

    def test_unknown_error_carries_kind_and_name(self) -> None:
        try:
            raise UnknownExtensionError(ExtensionKind.RUNTIME, "bar")
        except UnknownExtensionError as exc:
            assert exc.kind is ExtensionKind.RUNTIME
            assert exc.name == "bar"
            assert "bar" in str(exc)
            assert ExtensionKind.RUNTIME.value in str(exc)


# ── ExtensionKind enum ──────────────────────────────────────────────────────


class TestExtensionKind:
    def test_all_four_kinds_present(self) -> None:
        names = {k.value for k in ExtensionKind}
        assert names == {"provider", "skill", "runtime", "agent"}

    def test_extension_kind_is_str(self) -> None:
        # ``str, Enum`` lets us use values directly as strings.
        assert ExtensionKind.PROVIDER.value == "provider"
        assert str(ExtensionKind.PROVIDER) == "ExtensionKind.PROVIDER"
