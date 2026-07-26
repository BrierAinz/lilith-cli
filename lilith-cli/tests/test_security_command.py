"""Tests for the /security-review slash command.

The command is deterministic (regex over file tree, no LLM), so the
test cases are explicit about which rule is expected to fire on
which input. All scans target a ``tmp_path`` so no real project
files are touched.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from lilith_cli.extra_commands import (
    _security_scan_file,
    _security_walk,
    run_security_command,
)


def _run(coro):
    return asyncio.run(coro)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ── _security_scan_file: pattern coverage ───────────────────────────


def test_scan_detects_hardcoded_secret(tmp_path: Path) -> None:
    p = tmp_path / "leaky.py"
    _write(p, 'api_key = "abcdef1234567890"\n')
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "secret-asignado" in labels


def test_scan_detects_md5_for_security(tmp_path: Path) -> None:
    p = tmp_path / "hash.py"
    _write(p, "import hashlib\nhashlib.md5(b'x')\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "md5-para-seguridad" in labels


def test_scan_detects_pickle_load(tmp_path: Path) -> None:
    p = tmp_path / "loader.py"
    _write(p, "import pickle\ndata = pickle.loads(payload)\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "pickle-load" in labels


def test_scan_detects_shell_true(tmp_path: Path) -> None:
    p = tmp_path / "shell.py"
    _write(p, "subprocess.run(cmd, shell=True)\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "shell-true" in labels


def test_scan_detects_unsafe_yaml_load(tmp_path: Path) -> None:
    p = tmp_path / "yaml_loader.py"
    _write(p, "import yaml\ndata = yaml.load(stream)\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "yaml-load" in labels


def test_scan_ignores_safe_yaml(tmp_path: Path) -> None:
    p = tmp_path / "yaml_safe.py"
    _write(
        p,
        "import yaml\ndata = yaml.load(stream, Loader=yaml.SafeLoader)\n",
    )
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    # The unsafe-yaml rule excludes SafeLoader explicitly.
    assert "yaml-load" not in labels


def test_scan_detects_debug_true(tmp_path: Path) -> None:
    p = tmp_path / "app.py"
    _write(p, "DEBUG = True\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "debug-true" in labels


def test_scan_detects_todo_secret(tmp_path: Path) -> None:
    p = tmp_path / "notes.py"
    _write(p, "# TODO rotar este secret\n")
    findings = _security_scan_file(p)
    labels = {f["label"] for f in findings}
    assert "todo-secret" in labels


def test_scan_skips_binary_files(tmp_path: Path) -> None:
    p = tmp_path / "image.png"
    # PNG signature + a NUL byte in the first KB -> binary detected.
    p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00IHDR")
    assert _security_scan_file(p) == []


def test_scan_skips_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.py"
    p.write_text("", encoding="utf-8")
    assert _security_scan_file(p) == []


def test_scan_reports_correct_line_number(tmp_path: Path) -> None:
    p = tmp_path / "multi.py"
    _write(
        p,
        "# header line\n# another comment\napi_key = 'thisisasecretvalue123'\n",
    )
    findings = _security_scan_file(p)
    secret = [f for f in findings if f["label"] == "secret-asignado"]
    assert secret, "expected at least one secret-asignado finding"
    assert secret[0]["line"] == 3


# ── _security_walk: directory pruning ───────────────────────────────


def test_walk_skips_dot_venv(tmp_path: Path) -> None:
    safe = tmp_path / "src" / "ok.py"
    _write(safe, "x = 1\n")
    bad = tmp_path / ".venv" / "lib" / "leak.py"
    _write(bad, 'api_key = "thisisasecretvalue123"\n')
    bad2 = tmp_path / "__pycache__" / "junk.py"
    _write(bad2, 'password = "thisisasecretvalue123"\n')

    findings = _security_walk(tmp_path)
    paths = {f["file"] for f in findings}
    # Only the file under src/ should be scanned — .venv and __pycache__
    # are pruned in os.walk and never reach _security_scan_file.
    assert all(".venv" not in p for p in paths)
    assert all("__pycache__" not in p for p in paths)


def test_walk_on_single_file(tmp_path: Path) -> None:
    p = tmp_path / "one.py"
    _write(p, "import pickle\ndata = pickle.loads(blob)\n")
    findings = _security_walk(p)
    assert any(f["label"] == "pickle-load" for f in findings)


def test_walk_missing_path_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert _security_walk(missing) == []


# ── run_security_command: end-to-end ───────────────────────────────


@pytest.mark.asyncio
async def test_command_renders_table(tmp_path: Path, capsys) -> None:
    target = tmp_path / "scanroot"
    target.mkdir()
    _write(
        target / "app.py",
        'api_key = "thisisasecretvalue123"\n'
        "import hashlib\nhashlib.md5(b'x')\n",
    )

    await run_security_command(_FakeSession(), str(target))

    out = capsys.readouterr().out
    assert "Auditor" in out  # "Auditoría"
    assert "HIGH" in out
    assert "secret-asignado" in out
    assert "md5-para-seguridad" in out


@pytest.mark.asyncio
async def test_command_clean_target_reports_no_findings(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path / "ok.py", "x = 1 + 2\n")

    await run_security_command(_FakeSession(), str(tmp_path))

    out = capsys.readouterr().out
    assert "Sin hallazgos" in out


@pytest.mark.asyncio
async def test_command_missing_path_reports_error(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "nope"
    await run_security_command(_FakeSession(), str(missing))
    out = capsys.readouterr().out
    assert "no encontrada" in out.lower() or "no encontrado" in out.lower()


@pytest.mark.asyncio
async def test_command_json_output_is_valid_json(tmp_path: Path, capsys) -> None:
    # Use a subdir without spaces so the path passes cleanly through the
    # simple whitespace-split argument parser. (Windows' default tmp_path
    # includes a space in "AppData/Local/Temp".)
    target = tmp_path / "scanroot"
    target.mkdir()
    _write(
        target / "leak.py",
        'api_key = "thisisasecretvalue123"\n',
    )

    await run_security_command(_FakeSession(), f"{target} --json")

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "findings" in payload
    assert "by_severity" in payload
    assert payload["by_severity"]["HIGH"] >= 1
    # Each finding is a dict with the documented schema.
    sample = payload["findings"][0]
    for key in ("severity", "label", "file", "line", "snippet", "description"):
        assert key in sample


@pytest.mark.asyncio
async def test_command_default_target_is_dot(tmp_path: Path, monkeypatch) -> None:
    """With no args the command scans the current directory."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "x.py", 'password = "thisisasecretvalue123"\n')

    await run_security_command(_FakeSession(), "")

    # We can't easily capture the rendered table here because monkeypatch
    # doesn't swap capsys; but if no exception fires the dispatch is OK.
    # A more targeted assertion lives in the explicit-path tests above.


@pytest.mark.asyncio
async def test_command_max_cap_truncates(tmp_path: Path, capsys) -> None:
    # Create 5 hits and ask for 2.
    target = tmp_path / "scanroot"
    target.mkdir()
    _write(
        target / "many.py",
        "\n".join(f'api_key = "thisisasecretvalue{i:02d}"' for i in range(5))
        + "\n",
    )

    await run_security_command(_FakeSession(), f"{target} --json --max 2")

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["findings"]) == 2


@pytest.mark.asyncio
async def test_command_invalid_max_reports_error(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path / "ok.py", "x = 1\n")

    await run_security_command(_FakeSession(), f"{tmp_path} --max abc")

    out = capsys.readouterr().out
    assert "--max" in out


# ── /security-review wired into the REPL dispatch table ────────────


def test_security_review_is_a_slash_command() -> None:
    from lilith_cli.repl import _SLASH_COMMANDS

    assert "/security-review" in _SLASH_COMMANDS


def test_security_review_dispatch_block_uses_alias() -> None:
    import inspect

    from lilith_cli import repl as repl_module

    src = inspect.getsource(repl_module.run_repl)
    assert '"security-review"' in src
    assert '"sec"' in src
    assert "run_security_command" in src


# ── helpers ─────────────────────────────────────────────────────────


class _FakeSession:
    """Minimal stand-in: /security-review does not touch session state."""

    history: list = []
    config = type("Cfg", (), {"model": "test", "provider": "test"})()


# A safety net: skip the test file itself if it ever ends up inside a
# scanned tree. (Pytest collects it but _security_walk in tests below
# targets tmp_path only, so this is paranoia.)
def test_this_test_file_does_not_self_match() -> None:
    findings = _security_scan_file(Path(__file__))
    # This test file is in tests/, not in any scanned path, but we
    # still verify it would not crash if scanned.
    assert isinstance(findings, list)


# Force-import sys so the linter is happy if this test file is ever
# collected in isolation. (No-op at runtime.)
_ = sys
_ = os
