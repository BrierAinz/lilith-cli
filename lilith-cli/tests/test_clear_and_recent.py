"""Tests for the /cls (clear screen) and /recent commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.extra_commands import run_clear_screen_command, run_recent_command


class _Sess:
    """Stand-in for AgentSession."""

    def __init__(self) -> None:
        # _file_edit_history can be missing on a bare session.
        self._file_edit_history = []


# ── /cls ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cls_invokes_os_system_when_tty(_Sess_tty=True):
    """/cls on a real TTY shells out to the platform's clear command
    (cls on Windows, clear on POSIX). The actual command is intercepted
    by patching os.system so we can assert it was called with the right
    argument without actually clearing the user's screen."""
    sess = _Sess()

    with patch("lilith_cli.extra_commands.os") as mock_os:
        mock_os.name = "nt"
        mock_os.system = __import__("os").system  # but we override below
        # Patch sys.stdout.isatty as well.
        with patch("sys.stdout.isatty", return_value=True):
            with patch("os.system") as mock_system:
                mock_system.return_value = 0
                await run_clear_screen_command(sess, "")

                mock_system.assert_called_once()
                # On Windows it should call "cls"; on POSIX it should
                # call "clear". We mocked os.name = "nt" so we expect "cls".
                args = mock_system.call_args.args
                assert args[0] in ("cls", "clear")


@pytest.mark.asyncio
async def test_cls_emits_newlines_when_not_a_tty(capsys):
    """/cls in a non-TTY (piped output, tests, IDE captures) emits ~50
    newlines to push prior content off-screen rather than silently
    doing nothing."""
    sess = _Sess()
    with patch("sys.stdout.isatty", return_value=False):
        with patch("os.system") as mock_system:
            await run_clear_screen_command(sess, "")
            mock_system.assert_not_called()

    out = capsys.readouterr().out
    # 50 newlines
    assert out.count("\n") >= 50


@pytest.mark.asyncio
async def test_cls_does_not_touch_session_history():
    """/cls must NOT clear session.history or any telemetry list."""
    sess = _Sess()
    sess._file_edit_history = [{"path": "a.py", "tool": "file_write", "timestamp": "now"}]
    sess.history = [{"role": "user", "content": "hi"}]

    with patch("sys.stdout.isatty", return_value=False):
        await run_clear_screen_command(sess, "")

    # Both untouched.
    assert sess._file_edit_history == [{"path": "a.py", "tool": "file_write", "timestamp": "now"}]
    assert sess.history == [{"role": "user", "content": "hi"}]


# ── /recent ──────────────────────────────────────────────────────────


def _entry(path: str, tool: str = "file_write", ts: str = "2026-07-18T10:00:00+00:00"):
    return {"path": path, "tool": tool, "timestamp": ts}


@pytest.mark.asyncio
async def test_recent_empty_session(capsys, tmp_path):
    """/recent with no edits yet says so explicitly."""
    sess = _Sess()
    await run_recent_command(sess, "")

    out = capsys.readouterr().out
    assert "No hay archivos editados" in out


@pytest.mark.asyncio
async def test_recent_lists_recent_first(capsys, tmp_path, monkeypatch):
    """/recent lists the most recent edits first, with tool name and size."""
    monkeypatch.chdir(tmp_path)
    f1 = tmp_path / "first.py"
    f1.write_text("a" * 100, encoding="utf-8")
    f2 = tmp_path / "second.py"
    f2.write_text("b" * 2048, encoding="utf-8")

    sess = _Sess()
    sess._file_edit_history = [
        _entry("first.py", tool="file_write", ts="2026-07-18T09:00:00+00:00"),
        _entry("second.py", tool="file_edit", ts="2026-07-18T10:00:00+00:00"),
    ]

    await run_recent_command(sess, "")

    out = capsys.readouterr().out
    # Rich may truncate long paths, so check basename + table ordering.
    assert "first.py" in out
    assert "second.py" in out
    # Most recent (second.py, file_edit) appears before first.py.
    second_pos = out.index("second.py")
    first_pos = out.index("first.py")
    assert second_pos < first_pos
    # Tool column shows both.
    assert "file_write" in out
    assert "file_edit" in out
    # Sizes: f2 = 2048 bytes → ~2.0 KB; f1 = 100 bytes → 100 B.
    assert "2.0 KB" in out
    assert "100 B" in out


@pytest.mark.asyncio
async def test_recent_dedupes_repeated_edits(capsys, tmp_path, monkeypatch):
    """Three edits to the same file collapse to one entry (the latest)."""
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "loop.py"
    f.write_text("x", encoding="utf-8")

    sess = _Sess()
    sess._file_edit_history = [
        _entry("loop.py", ts="2026-07-18T08:00:00+00:00"),
        _entry("loop.py", ts="2026-07-18T09:00:00+00:00"),
        _entry("loop.py", ts="2026-07-18T10:00:00+00:00"),
    ]

    await run_recent_command(sess, "")

    out = capsys.readouterr().out
    # Only one row of loop.py.
    assert out.count("loop.py") == 1


@pytest.mark.asyncio
async def test_recent_respects_count_argument(capsys, tmp_path, monkeypatch):
    """/recent 1 shows just the most recent unique file."""
    monkeypatch.chdir(tmp_path)
    files = []
    for i in range(5):
        f = tmp_path / f"file_{i}.py"
        f.write_text("x", encoding="utf-8")
        files.append(f)

    sess = _Sess()
    sess._file_edit_history = [_entry(f"file_{i}.py", ts=f"2026-07-18T{i:02d}:00:00+00:00") for i in range(5)]

    await run_recent_command(sess, "1")

    out = capsys.readouterr().out
    # Only the last one should appear.
    assert "file_4.py" in out
    assert "file_3.py" not in out
    assert "file_0.py" not in out


@pytest.mark.asyncio
async def test_recent_invalid_count_errors(capsys):
    """/recent abc must render an error, not crash on int('abc')."""
    sess = _Sess()
    sess._file_edit_history = [_entry("a.py")]

    await run_recent_command(sess, "abc")

    out = capsys.readouterr().out
    assert "Uso:" in out
    assert "abc" in out


@pytest.mark.asyncio
async def test_recent_clamps_huge_count(capsys, tmp_path):
    """/recent 9999 is clamped to 50 instead of crashing on slicing."""
    sess = _Sess()
    sess._file_edit_history = [_entry(f"f{i}.py") for i in range(3)]

    await run_recent_command(sess, "9999")

    # No crash; renders 3 rows.
    out = capsys.readouterr().out
    assert "f0.py" in out


@pytest.mark.asyncio
async def test_recent_clear_empties_history(capsys):
    """/recent clear empties _file_edit_history and prints success."""
    sess = _Sess()
    sess._file_edit_history = [_entry("a.py"), _entry("b.py")]

    await run_recent_command(sess, "clear")

    assert sess._file_edit_history == []
    out = capsys.readouterr().out
    assert "vaciado" in out.lower()


@pytest.mark.asyncio
async def test_recent_missing_telemetry_attribute(capsys):
    """/recent on a session without _file_edit_history prints the
    'telemetry not active' hint instead of crashing."""
    class _BareSess:
        pass

    sess = _BareSess()
    await run_recent_command(sess, "")

    out = capsys.readouterr().out
    assert "Telemetría" in out or "no activa" in out


@pytest.mark.asyncio
async def test_recent_handles_missing_files(capsys, tmp_path, monkeypatch):
    """/recent must show '—' for files deleted from disk, not crash."""
    # Use chdir into tmp_path so the displayed paths are short enough
    # for the 80-column test terminal. Real-world usage has full paths
    # which Rich wraps automatically.
    monkeypatch.chdir(tmp_path)

    sess = _Sess()
    sess._file_edit_history = [
        _entry("gone.py", ts="2026-07-18T10:00:00+00:00"),
    ]

    await run_recent_command(sess, "")

    out = capsys.readouterr().out
    assert "gone.py" in out
    assert "—" in out
