"""Tests for the ``/batch`` slash command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from lilith_cli import batch_command
from lilith_cli.batch_command import (
    _BATCH_STORE,
    _parse_file,
    _parse_inline,
    run_batch_command,
)


class FakeSession:
    """Minimal stand-in for AgentSession used by run_batch_command."""


class _DummyConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        self.messages.append(" ".join(str(a) for a in args))


@pytest.fixture
def store_path(tmp_path: Path) -> Iterator[Path]:
    """Redirect the batch store to a temp file for isolation."""
    path = tmp_path / "batches.json"
    _BATCH_STORE.batch_dir = tmp_path
    _BATCH_STORE.batch_file = path
    try:
        yield path
    finally:
        _BATCH_STORE.batch_dir = batch_command._BATCH_DIR
        _BATCH_STORE.batch_file = batch_command._BATCH_FILE


# ── pure helpers ──────────────────────────────────────────────────────


class TestParseInline:
    def test_basic_split(self) -> None:
        assert _parse_inline("hola;;chau;;resume") == ["hola", "chau", "resume"]

    def test_trims_whitespace(self) -> None:
        assert _parse_inline("  hola ;;  chau  ") == ["hola", "chau"]

    def test_drops_empty_segments(self) -> None:
        assert _parse_inline("hola;;;;chau") == ["hola", "chau"]

    def test_single_prompt(self) -> None:
        assert _parse_inline("solo uno") == ["solo uno"]


class TestParseFile:
    def test_reads_prompts(self, tmp_path: Path) -> None:
        p = tmp_path / "prompts.txt"
        p.write_text(
            "# comentario\n\nhola\nchau\n# otro\nresume\n",
            encoding="utf-8",
        )
        assert _parse_file(str(p)) == ["hola", "chau", "resume"]

    def test_accepts_file_prefix(self, tmp_path: Path) -> None:
        p = tmp_path / "prompts.txt"
        p.write_text("a\nb\n", encoding="utf-8")
        assert _parse_file(f"file {p}") == ["a", "b"]

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            _parse_file(str(tmp_path / "missing.txt"))


# ── store roundtrip ───────────────────────────────────────────────────


class TestBatchStore:
    def test_save_and_load(self, store_path: Path) -> None:
        _BATCH_STORE.save({"review": ["uno", "dos"]})
        loaded = _BATCH_STORE.load()
        assert loaded == {"review": ["uno", "dos"]}

    def test_load_empty(self, store_path: Path) -> None:
        assert _BATCH_STORE.load() == {}

    def test_load_corrupt_returns_empty(self, store_path: Path) -> None:
        store_path.write_text("not-json", encoding="utf-8")
        assert _BATCH_STORE.load() == {}

    def test_load_skips_non_dict(self, store_path: Path) -> None:
        store_path.write_text(json.dumps(["nope"]), encoding="utf-8")
        assert _BATCH_STORE.load() == {}


# ── async dispatch ────────────────────────────────────────────────────


class TestRunBatch:
    async def test_inline_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[str] = []

        async def fake_oneshot(_session: object, prompt: str) -> None:
            called.append(prompt)

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)
        await run_batch_command(FakeSession(), "hola;;chau")
        assert called == ["hola", "chau"]

    async def test_run_saved(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _BATCH_STORE.save({"rev": ["uno", "dos"]})
        called: list[str] = []

        async def fake_oneshot(_session: object, prompt: str) -> None:
            called.append(prompt)

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)
        await run_batch_command(FakeSession(), "run rev")
        assert called == ["uno", "dos"]

    async def test_run_continues_after_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []

        async def fake_oneshot(_session: object, prompt: str) -> None:
            called.append(prompt)
            if prompt == "boom":
                raise RuntimeError("model down")

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)
        await run_batch_command(FakeSession(), "uno;;boom;;dos")
        assert called == ["uno", "boom", "dos"]

    async def test_save_then_show(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No LLM calls expected for save/show; stub run_oneshot defensively.
        async def fake_oneshot(_session: object, _prompt: str) -> None:
            raise AssertionError("should not be called")

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)

        await run_batch_command(
            FakeSession(), "save review hola;;chau;;resume"
        )
        loaded = _BATCH_STORE.load()
        assert loaded == {"review": ["hola", "chau", "resume"]}

        await run_batch_command(FakeSession(), "show review")

    async def test_save_with_file_prefix(
        self, store_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts = tmp_path / "p.txt"
        prompts.write_text("a\nb\n", encoding="utf-8")

        async def fake_oneshot(_session: object, _prompt: str) -> None:
            raise AssertionError("save shouldn't invoke LLM")

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)

        await run_batch_command(
            FakeSession(), f"save batchA file {prompts}"
        )
        assert _BATCH_STORE.load() == {"batchA": ["a", "b"]}

    async def test_run_unknown_name_renders_error(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_oneshot(_session: object, _prompt: str) -> None:
            raise AssertionError("run_unknown shouldn't invoke LLM")

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)
        await run_batch_command(FakeSession(), "run fantasma")

    async def test_delete(
        self, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _BATCH_STORE.save({"tmp": ["x"]})

        async def fake_oneshot(_session: object, _prompt: str) -> None:
            raise AssertionError("delete shouldn't invoke LLM")

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)

        await run_batch_command(FakeSession(), "delete tmp")
        assert _BATCH_STORE.load() == {}

    async def test_empty_body_shows_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[str] = []

        async def fake_oneshot(_session: object, prompt: str) -> None:
            called.append(prompt)

        monkeypatch.setattr("lilith_cli.repl.run_oneshot", fake_oneshot)
        await run_batch_command(FakeSession(), "")
        # Empty args just lists — no prompts dispatched.
        assert called == []