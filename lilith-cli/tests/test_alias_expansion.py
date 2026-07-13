"""Tests de ``_expand_user_alias`` (expansión de aliases de usuario en el REPL).

Los aliases se definen con /alias set y viven en aliases.json; el REPL los
expande un solo nivel antes del dispatch. Los built-ins nunca pueden ser
sombreados por un alias.
"""

from __future__ import annotations

import pytest

import lilith_cli.repl as repl


@pytest.fixture
def aliases(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(repl, "_load_aliases", lambda: store)
    return store


def test_expands_alias_with_slash_target(aliases):
    aliases["gs"] = "/git status"
    assert repl._expand_user_alias("gs", "") == ("git", "status")


def test_expands_alias_without_leading_slash(aliases):
    aliases["gs"] = "git status"
    assert repl._expand_user_alias("gs", "") == ("git", "status")


def test_appends_extra_args_after_alias_args(aliases):
    aliases["gs"] = "/git status"
    assert repl._expand_user_alias("gs", "--short") == ("git", "status --short")


def test_builtin_command_never_shadowed(aliases):
    aliases["model"] = "/quit"
    assert repl._expand_user_alias("model", "") is None


def test_unknown_name_returns_none(aliases):
    assert repl._expand_user_alias("nope", "") is None


def test_empty_target_ignored(aliases):
    aliases["x"] = "  /  "
    assert repl._expand_user_alias("x", "") is None


def test_expansion_is_single_level(aliases):
    # a → b y b → c: expandir "a" produce "b" y NO sigue hasta "c".
    aliases["a"] = "/b"
    aliases["b"] = "/c"
    assert repl._expand_user_alias("a", "") == ("b", "")
