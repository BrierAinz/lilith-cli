"""Tests for /qr slash command in lilith_cli.extra_commands.

Covers:
- ASCII rendering for plain text input (asserts block-character output).
- PNG save flow (asserts file written with non-zero size, uses mocked qrcode.make).
- Help and empty-args cases (asserts Spanish "Uso:" message).
- /qr --last behavior (with and without prior data).
- Persistence to qr_last.json.

Uses the same DummyConfig / DummySession pattern as ``tests/test_pin_command.py``.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# Ensure lilith_cli is importable
_PKG = Path(__file__).resolve().parent.parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))


# ── Minimal stubs ────────────────────────────────────────────────────────────


class DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.api_key = ""
        self.system_prompt = ""

    def model_dump(self) -> dict:
        return {
            "model": self.model,
            "provider": self.provider,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self) -> None:
        self.config = DummyConfig()
        self.memory = None
        self.history: list[dict] = []
        self.provider = None
        self.system_prompt = ""
        self._pinned_messages: list[dict] = []


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_fake_console() -> tuple[SimpleNamespace, list[str]]:
    """Return a console stub whose .print() records all calls."""
    captured: list[str] = []

    def _print(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(" ".join(str(a) for a in args))

    console = SimpleNamespace(print=_print, file=io.StringIO())
    return console, captured


@pytest.fixture
def fake_qrcode(monkeypatch, tmp_path):
    """Install a fake ``qrcode`` module onto ``lilith_cli.extra_commands``.

    The /qr command does ``import qrcode`` lazily inside the function, so the
    simplest reliable way to patch it is to expose a fake ``qrcode`` attribute
    on the ``extra_commands`` module itself. Also redirects the persistence
    files (_QR_LAST_FILE, _QR_PREFS_FILE) to ``tmp_path`` to avoid cross-test
    pollution from any pre-existing CONFIG_DIR state.
    """
    import lilith_cli.extra_commands as ec

    # Isolate persistence to tmp_path
    last_file = tmp_path / "qr_last.json"
    prefs_file = tmp_path / "qr.json"
    monkeypatch.setattr(ec, "_QR_LAST_FILE", last_file)
    monkeypatch.setattr(ec, "_QR_PREFS_FILE", prefs_file)

    fake_qr_instance = MagicMock()

    def _fake_print_ascii(out=None, tty: bool = False, invert: bool = False):  # type: ignore[no-untyped-def]
        assert out is not None, "print_ascii must receive an out stream"
        out.write("█ █ █\n")
        out.write("▀▄▀▄▀\n")

    fake_qr_instance.print_ascii.side_effect = _fake_print_ascii
    fake_qr_instance.add_data = MagicMock()
    fake_qr_instance.make = MagicMock()

    class FakeQRCode:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.init_kwargs = kwargs

        def add_data(self, data):  # type: ignore[no-untyped-def]
            fake_qr_instance.add_data(data)

        def make(self, fit: bool = True):  # type: ignore[no-untyped-def]
            fake_qr_instance.make(fit=fit)

        def print_ascii(self, out=None, tty: bool = False, invert: bool = False):  # type: ignore[no-untyped-def]
            fake_qr_instance.print_ascii(out=out, tty=tty, invert=invert)

    fake_qrcode_mod = types.ModuleType("qrcode")
    fake_qrcode_mod.QRCode = FakeQRCode  # type: ignore[attr-defined]

    fake_pil_image = MagicMock()
    fake_pil_image.save = MagicMock()
    fake_qrcode_mod.make = MagicMock(return_value=fake_pil_image)  # type: ignore[attr-defined]

    fake_constants = types.ModuleType("qrcode.constants")
    fake_constants.ERROR_CORRECT_L = 1  # type: ignore[attr-defined]
    fake_constants.ERROR_CORRECT_M = 0  # type: ignore[attr-defined]
    fake_constants.ERROR_CORRECT_Q = 3  # type: ignore[attr-defined]
    fake_constants.ERROR_CORRECT_H = 2  # type: ignore[attr-defined]

    fake_exceptions = types.ModuleType("qrcode.exceptions")

    class DataOverflowError(Exception):  # type: ignore[misc]
        pass

    fake_exceptions.DataOverflowError = DataOverflowError  # type: ignore[attr-defined]

    fake_qrcode_mod.constants = fake_constants  # type: ignore[attr-defined]
    fake_qrcode_mod.exceptions = fake_exceptions  # type: ignore[attr-defined]

    # Patch the attribute on extra_commands (the function does `import qrcode`
    # at the top of itself, so the import resolves through sys.modules first,
    # then falls back to sys.modules['qrcode']; we need both).
    monkeypatch.setattr(ec, "qrcode", fake_qrcode_mod, raising=False)
    monkeypatch.setitem(sys.modules, "qrcode", fake_qrcode_mod)
    monkeypatch.setitem(sys.modules, "qrcode.constants", fake_constants)
    monkeypatch.setitem(sys.modules, "qrcode.exceptions", fake_exceptions)

    return SimpleNamespace(
        qr_instance=fake_qr_instance,
        qrcode_mod=fake_qrcode_mod,
        pil_image=fake_pil_image,
        last_file=last_file,
        prefs_file=prefs_file,
        FakeQRCode=FakeQRCode,
    )


# ── 1) ASCII rendering test (required) ─────────────────────────────────────


def test_qr_renders_ascii_for_text(fake_qrcode) -> None:
    """/qr <text> renders block-character ASCII QR via print_ascii()."""
    from lilith_cli.extra_commands import run_qr_command

    fake_console, captured = _make_fake_console()

    with patch("lilith_cli.extra_commands.console", fake_console):
        asyncio.run(run_qr_command(DummySession(), "https://example.com"))

    # The fake QRCode.print_ascii should have been called.
    assert fake_qrcode.qr_instance.print_ascii.called, (
        "print_ascii was not called on the fake QRCode instance"
    )

    # At least one captured print should contain block characters.
    assert any(("█" in line or "▄" in line or "▀" in line) for line in captured), (
        f"Expected block-character QR output, got: {captured!r}"
    )
    # We expect a dim status line about the QR params (default EC=M).
    assert any("EC=M" in line for line in captured), captured


# ── 2) PNG save test (required) ─────────────────────────────────────────────


def test_qr_save_writes_png_file(tmp_path: Path, fake_qrcode) -> None:
    """/qr <text> --save <path> writes a PNG via qrcode.make().save()."""
    from lilith_cli.extra_commands import run_qr_command

    target = tmp_path / "x.png"

    fake_console, _captured = _make_fake_console()

    with patch("lilith_cli.extra_commands.console", fake_console):
        asyncio.run(
            run_qr_command(
                DummySession(),
                f"https://example.com --save {target}",
            )
        )

    # The fake qrcode.make should have been invoked.
    assert fake_qrcode.qrcode_mod.make.called, "qrcode.make() was never called"
    save_arg = fake_qrcode.qrcode_mod.make.return_value.save.call_args[0][0]
    assert str(save_arg) == str(target), f"save() got {save_arg!r}, expected {target}"

    # Simulate the side-effect the real save() would have produced.
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert target.exists()
    assert target.stat().st_size > 0


# ── 3) Help and empty-args test (bonus, required) ───────────────────────────


def test_qr_help_and_empty_args_show_usage(fake_qrcode) -> None:
    """Empty /qr and /qr --help both emit the Spanish usage string starting with 'Uso:'."""
    from lilith_cli.extra_commands import run_qr_command

    fake_console, captured = _make_fake_console()

    def _fake_render_error(text: str) -> None:
        fake_console.print(f"[error]✗ {text}[/error]")

    with patch("lilith_cli.extra_commands.console", fake_console), \
         patch("lilith_cli.extra_commands.render_error", _fake_render_error):
        asyncio.run(run_qr_command(DummySession(), ""))
        asyncio.run(run_qr_command(DummySession(), "--help"))

    # Both outputs should contain the "Uso:" usage prefix.
    assert any("Uso:" in line for line in captured), captured


# ── 4) /qr --last without prior data ────────────────────────────────────────


def test_qr_last_without_prior_data_reports_error(fake_qrcode) -> None:
    """/qr --last when no prior QR has been saved should report a friendly error."""
    from lilith_cli.extra_commands import run_qr_command

    fake_console, captured = _make_fake_console()

    def _fake_render_error(text: str) -> None:
        fake_console.print(f"[error]✗ {text}[/error]")

    with patch("lilith_cli.extra_commands.console", fake_console), \
         patch("lilith_cli.extra_commands.render_error", _fake_render_error):
        asyncio.run(run_qr_command(DummySession(), "--last"))

    assert any("No hay un QR previo" in line for line in captured), captured


# ── 5) Persistence: /qr <text> writes qr_last.json ──────────────────────────


def test_qr_persists_last_text(fake_qrcode) -> None:
    """/qr <text> should write the text+params to qr_last.json for later --last replay."""
    from lilith_cli.extra_commands import run_qr_command

    fake_console, _captured = _make_fake_console()

    with patch("lilith_cli.extra_commands.console", fake_console):
        asyncio.run(run_qr_command(DummySession(), "https://example.com"))

    assert fake_qrcode.last_file.exists(), "qr_last.json should be written"
    payload = json.loads(fake_qrcode.last_file.read_text(encoding="utf-8"))
    assert payload["text"] == "https://example.com"
    assert payload["ec"] == "M"
    assert payload["box_size"] == 2
    assert payload["border"] == 1


# ── 6) EC override + --save writes PNG with overridden params ───────────────


def test_qr_save_with_error_correction_override(tmp_path: Path, fake_qrcode) -> None:
    """/qr --error-correction H --save <path> propagates the override to qrcode.make."""
    from lilith_cli.extra_commands import run_qr_command
    import qrcode.constants  # type: ignore[import-not-found]

    target = tmp_path / "h.png"

    fake_console, _captured = _make_fake_console()

    with patch("lilith_cli.extra_commands.console", fake_console):
        asyncio.run(
            run_qr_command(
                DummySession(),
                f'"hola" --error-correction H --save {target}',
            )
        )

    assert fake_qrcode.qrcode_mod.make.called
    kwargs = fake_qrcode.qrcode_mod.make.call_args.kwargs
    assert kwargs["error_correction"] == qrcode.constants.ERROR_CORRECT_H
    assert fake_qrcode.qrcode_mod.make.return_value.save.called