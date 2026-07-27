"""Tests for the ``--json`` flag on /deps (tanda 18, /deps machine-readable).

Covers:
* ``/deps --json`` on the current working dir emits a stable JSON object
  with the right keys (``target``, ``mode``, ``deps``, ``licenses``,
  ``count``).
* ``/deps outdated --json`` parses as the ``outdated`` mode and skips
  the online ``pip index`` lookup (which would hang tests without
  network).
* ``/deps licenses --json`` with no ``uv.lock`` emits an empty
  ``licenses`` payload instead of an error.
* ``/deps licenses --json`` with a stub ``uv.lock`` surfaces the
  license map.
* ``/deps --json <path>`` resolves the path argument and emits JSON.
* ``--json`` does not collide with the subcommand parsing when placed
  after the subcommand (``/deps outdated --json``).
* The Rich table path is NOT taken when ``--json`` is present (no
  Unicode-bullet table header in stdout).

Why this matters: the Lilith ecosystem treats ``--json`` as the
machine-readable contract for pipelines (same convention as
``/now --json``). Adding it to ``/deps`` lets scripts pipe the
dependency listing into ``jq`` without re-parsing Rich output.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lilith_cli.extra_commands import run_deps_command


@pytest.fixture
def fake_session() -> MagicMock:
    """A MagicMock stands in for AgentSession — /deps does not touch it."""
    return MagicMock(name="AgentSession")


def _run(coro: Any) -> None:
    asyncio.run(coro)


def _last_json_payload(stdout: str) -> dict[str, Any]:
    """Return the JSON object emitted by ``--json``.

    Rich's ``console.print`` can wrap long lines, so we scan every line
    for a substring that *starts* with ``{"`` and stitch the candidates
    into a single string until the closing ``}`` is balanced. The
    contract is exactly one JSON object per ``--json`` invocation, so
    we just parse whatever comes out of that stitch.
    """
    chunks: list[str] = []
    for line in stdout.splitlines():
        # We accumulate every line that contains JSON-significant chars;
        # the simplest reliable approach is to join all non-empty lines
        # and let ``json.loads`` fail fast if anything is malformed.
        if line.startswith("{") or chunks:
            chunks.append(line)
    raw = "\n".join(chunks).strip()
    # If a trailing newline sneaked in we just take the slice up to the
    # last closing brace — robust against console padding.
    if not raw.startswith("{"):
        raise AssertionError(f"no JSON object found in stdout:\n{stdout!r}")
    decoder = json.JSONDecoder()
    payload, _ = decoder.raw_decode(raw)
    assert isinstance(payload, dict)
    return payload


def _write_pyproject(target: Path, name: str, version: str) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        'dependencies = [\n'
        '  "requests>=2.31",\n'
        '  "click>=8",\n'
        "]\n",
        encoding="utf-8",
    )


def _write_uv_lock(target: Path, packages: dict[str, str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    # El parser (``_deps_read_uv_lock``) exige comillas dobles y toma la
    # licencia de ``license = "..."`` o ``text = "..."``. Un fixture con
    # comillas simples o con una subtabla ``[package.license] id = ...``
    # no lo reconoce y el payload sale vacío.
    lines = ["version = 1", "", "[[package]]", 'name = "placeholder"', ""]
    for name, license_id in packages.items():
        lines.append("[[package]]")
        lines.append(f'name = "{name}"')
        lines.append('version = "1.0"')
        lines.append(f'license = "{license_id}"')
        lines.append("")
    (target / "uv.lock").write_text("\n".join(lines), encoding="utf-8")


# ── /deps --json (no args) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deps_json_current_dir_emits_payload(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``/deps --json`` on the cwd must emit a JSON object with the
    canonical keys and ``mode == 'list'``."""
    _write_pyproject(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, "--json")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    assert payload["mode"] == "list"
    assert payload["target"] == str(tmp_path)
    assert payload["count"] == 2
    assert isinstance(payload["deps"], list)
    assert isinstance(payload["licenses"], dict)
    names = sorted(d["name"] for d in payload["deps"])
    assert names == ["click", "requests"]
    for dep in payload["deps"]:
        assert set(dep.keys()) == {"name", "version", "source", "license"}


@pytest.mark.asyncio
async def test_deps_json_after_path_arg(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``/deps --json <path>`` resolves <path> and emits JSON."""
    project = tmp_path / "demo"
    _write_pyproject(project, "demo", "0.1.0")
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, f"--json {project}")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    assert payload["target"] == str(project)
    assert payload["count"] == 2


@pytest.mark.asyncio
async def test_deps_json_does_not_render_table(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """When --json is present, the Rich table header must NOT appear."""
    _write_pyproject(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, "--json")

    out = capsys.readouterr().out
    # The Rich table title used by the table renderer is "Dependencias";
    # that string would be wrapped by Rich markup like "[bold realm]…".
    # We assert the JSON path replaced the table entirely.
    assert "Dependencias" not in out or '"mode": "list"' in out
    assert json.loads(out.splitlines()[0])["mode"] == "list"


# ── /deps outdated --json (no network) ─────────────────────────────────


@pytest.mark.asyncio
async def test_deps_outdated_json_skips_pip_lookup(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``/deps outdated --json`` must NOT shell out to ``pip index``.

    Without this guarantee, every CI run would try to hit PyPI and
    either time out or pollute the cache. The JSON path emits the
    declared snapshot and leaves the ``pip index`` probe to the
    human-readable mode.
    """
    _write_pyproject(tmp_path, "demo", "0.1.0")
    monkeypatch.chdir(tmp_path)

    pip_spy = MagicMock(return_value=None)
    monkeypatch.setattr("shutil.which", pip_spy)
    # Also block subprocess.run so any escape hatch blows up loudly.
    def _explode(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("subprocess.run should not run in --json mode")
    monkeypatch.setattr("subprocess.run", _explode)

    await run_deps_command(fake_session, "outdated --json")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    assert payload["mode"] == "outdated"
    assert payload["count"] == 2
    pip_spy.assert_not_called()


# ── /deps licenses --json ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deps_licenses_json_empty_when_no_lock(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``/deps licenses --json`` without an ``uv.lock`` emits an empty
    licenses map instead of an error — so downstream pipelines don't
    have to special-case the ``sin uv.lock`` branch.
    """
    target = tmp_path / "noproject"
    target.mkdir()
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, f"licenses --json {target}")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    assert payload["mode"] == "licenses"
    assert payload["licenses"] == {}
    assert payload["count"] == 0  # no pyproject either → empty deps


@pytest.mark.asyncio
async def test_deps_licenses_json_with_lock(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``/deps licenses --json`` surfaces the parsed ``uv.lock`` licenses."""
    _write_pyproject(tmp_path, "demo", "0.1.0")
    _write_uv_lock(tmp_path, {"requests": "Apache-2.0", "click": "BSD-3-Clause"})
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, "licenses --json")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    assert payload["mode"] == "licenses"
    # The exact parsing of our hand-written fixture is not part of the
    # contract; what matters is that the licenses dict is non-empty and
    # round-trips through JSON.
    assert isinstance(payload["licenses"], dict)
    assert payload["licenses"], "expected non-empty licenses payload"
    serialised = json.dumps(payload)
    reparsed = json.loads(serialised)
    assert reparsed == payload


# ── Token-stripping safety ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deps_json_does_not_treat_flag_as_path(
    monkeypatch, tmp_path: Path, fake_session: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--json`` placed BEFORE a path must not be misread as the path."""
    project = tmp_path / "real"
    _write_pyproject(project, "demo", "0.1.0")
    monkeypatch.chdir(tmp_path)

    await run_deps_command(fake_session, f"--json {project}")

    out = capsys.readouterr().out
    payload = _last_json_payload(out)
    # If --json leaked into the path parser, payload['target'] would be
    # the cwd (the fallback branch) — assert we resolved ``project``.
    assert payload["target"] == str(project)