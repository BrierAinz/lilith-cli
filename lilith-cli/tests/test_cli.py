"""Tests for lilith-cli — Yggdrasil Agent CLI."""

import sys
from pathlib import Path


# Ensure lilith_cli is importable
_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from lilith_cli import __version__


def test_version_constant():
    """Module-level __version__ should be '4.4.0' (aliases + wiring fixes)."""
    assert __version__ == "4.4.0"


def test_app_instance():
    """Cyclopts App should be created with correct name and version."""
    from lilith_cli.main import app

    # Cyclopts name can be a tuple or string
    name = app.name if isinstance(app.name, str) else app.name[0]
    assert name == "yggdrasil"
    assert app.version == "4.4.0"


def test_config_loads():
    """Config module should be importable and have expected attributes."""
    from lilith_cli.config import CONFIG_DIR, load_config, save_config

    assert callable(load_config)
    assert callable(save_config)
    assert isinstance(CONFIG_DIR, Path)


def test_is_wsl():
    """_is_wsl should return a boolean."""
    from lilith_cli.main import _is_wsl

    result = _is_wsl()
    assert isinstance(result, bool)


def test_resolve_yggdrasil_root(tmp_path, monkeypatch):
    """_resolve_yggdrasil_root should return a Path pointing to the workspace."""
    from lilith_cli import main as cli_main

    # The host (or cron runner) may export YGGDRASIL_ROOT; blank it so the
    # function falls through to its file-walk logic and respects our fake.
    monkeypatch.delenv("YGGDRASIL_ROOT", raising=False)

    fake_root = tmp_path / "Yggdrasil"
    fake_module = fake_root / "Asgard" / "lilith-cli" / "lilith_cli" / "main.py"
    fake_module.parent.mkdir(parents=True)
    (fake_root / "pyproject.toml").write_text(
        "[project]\nname = 'fake-yggdrasil'\n", encoding="utf-8"
    )
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli_main, "__file__", str(fake_module))

    root = cli_main._resolve_yggdrasil_root()
    assert isinstance(root, Path)
    assert root == fake_root
    assert (root / "pyproject.toml").exists()


def test_reconfigure_stdio_sets_utf8(monkeypatch):
    """_reconfigure_stdio should call reconfigure(encoding="utf-8", errors="replace")
    on both sys.stdout and sys.stderr.

    This is the in-process equivalent of ``PYTHONIOENCODING=utf-8``; without it,
    ``lilith.exe`` crashes with ``UnicodeEncodeError: 'charmap' codec can't
    encode character '\u25b8'`` when invoked from a cp1252 console (Windows
    Git Bash, cmd.exe, any pipe into a non-UTF-8 consumer).  See the docstring
    on ``_reconfigure_stdio`` for the full rationale.
    """
    from lilith_cli import main as cli_main

    calls = []

    class FakeStream:
        def __init__(self, name):
            self.name = name

        def reconfigure(self, **kwargs):
            calls.append((self.name, kwargs))

    cli_main.sys.stdout = FakeStream("stdout")
    cli_main.sys.stderr = FakeStream("stderr")

    cli_main._reconfigure_stdio()

    assert ("stdout", {"encoding": "utf-8", "errors": "replace"}) in calls
    assert ("stderr", {"encoding": "utf-8", "errors": "replace"}) in calls


def test_reconfigure_stdio_handles_missing_reconfigure():
    """_reconfigure_stdio must not crash if a stream has no ``reconfigure``.

    Some third-party wrappers (test harnesses, capture buffers) replace
    ``sys.stdout`` with objects that do not expose ``reconfigure``.  The helper
    must skip those silently rather than breaking the entire CLI on launch.
    """
    from lilith_cli import main as cli_main

    class StreamNoReconfigure:
        pass

    cli_main.sys.stdout = StreamNoReconfigure()
    cli_main.sys.stderr = StreamNoReconfigure()

    # Should not raise.
    cli_main._reconfigure_stdio()

    # Restore for subsequent tests.
    cli_main.sys.stdout = cli_main.sys.__stdout__
    cli_main.sys.stderr = cli_main.sys.__stderr__


def test_reconfigure_stdio_handles_reconfigure_failure():
    """_reconfigure_stdio must not crash if ``reconfigure`` raises.

    Some streams report a ``reconfigure`` method but raise (e.g. closed files,
    unsupported platform).  The helper must catch the exception and continue,
    not abort the entire CLI on launch.  The user can still set
    ``PYTHONIOENCODING=utf-8`` themselves.
    """
    from lilith_cli import main as cli_main

    class FailingStream:
        def reconfigure(self, **kwargs):
            raise OSError("stream closed")

    cli_main.sys.stdout = FailingStream()
    cli_main.sys.stderr = FailingStream()

    # Should not raise.
    cli_main._reconfigure_stdio()

    # Restore for subsequent tests.
    cli_main.sys.stdout = cli_main.sys.__stdout__
    cli_main.sys.stderr = cli_main.sys.__stderr__


def test_main_calls_reconfigure_first(monkeypatch):
    """main() must call _reconfigure_stdio() before app() runs.

    This is the bug we are preventing: if any code path between module import
    and the start of ``app()`` (e.g. cyclopts help text, Rich splash) writes to
    stdout, the stream must already be UTF-8.  Pinning the call order in a
    test guards against a future refactor that moves it.
    """
    from lilith_cli import main as cli_main

    call_order = []

    def fake_reconfigure():
        call_order.append("reconfigure")

    def fake_app():
        call_order.append("app")

    monkeypatch.setattr(cli_main, "_reconfigure_stdio", fake_reconfigure)
    monkeypatch.setattr(cli_main, "app", fake_app)

    cli_main.main()

    assert call_order == ["reconfigure", "app"], call_order
