"""Tests para el comando /pr de Lilith CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import (
    _pr_compare_url,
    _pr_detect_branch,
    run_pr_command,
)


class DummyConfig:
    model = "test"
    provider = "test"
    providers: dict = {}
    api_key = ""
    system_prompt = ""

    def model_dump(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "providers": self.providers,
            "api_key": self.api_key,
        }


class DummySession:
    def __init__(self):
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


# ── _pr_compare_url: pura, sin IO ────────────────────────────────────


def test_pr_compare_url_https():
    assert (
        _pr_compare_url(
            "https://github.com/BrierAinz/lilith-cli.git", "main", "feat/pr"
        )
        == "https://github.com/BrierAinz/lilith-cli/compare/main...feat/pr?expand=1"
    )


def test_pr_compare_url_ssh():
    assert (
        _pr_compare_url("git@github.com:BrierAinz/lilith-cli.git", "main", "feat/x")
        == "https://github.com/BrierAinz/lilith-cli/compare/main...feat/x?expand=1"
    )


def test_pr_compare_url_rejects_non_github():
    # /pr solo soporta GitHub hoy; otros hosts devuelven None.
    assert _pr_compare_url("https://gitlab.com/foo/bar.git", "main", "x") is None
    assert _pr_compare_url("", "main", "x") is None
    assert _pr_compare_url("file:///tmp/repo", "main", "x") is None


# ── _pr_detect_branch: comportamiento con subprocess mockeado ────────


def test_pr_detect_branch_returns_branch():
    """Si `git rev-parse` devuelve 'feat/x', _pr_detect_branch devuelve 'feat/x'."""
    fake = MagicMock()
    fake.stdout = "feat/x\n"
    with patch("lilith_cli.extra_commands.subprocess.run", return_value=fake):
        assert _pr_detect_branch() == "feat/x"


def test_pr_detect_branch_returns_none_on_detached():
    """Una rama detached (HEAD) devuelve None en vez de 'HEAD'."""
    fake = MagicMock()
    fake.stdout = "HEAD\n"
    with patch("lilith_cli.extra_commands.subprocess.run", return_value=fake):
        assert _pr_detect_branch() is None


def test_pr_detect_branch_returns_none_on_error():
    """Si git falla, devolvemos None sin re-lanzar."""
    import subprocess as _sp

    with patch(
        "lilith_cli.extra_commands.subprocess.run",
        side_effect=_sp.CalledProcessError(128, "git"),
    ):
        assert _pr_detect_branch() is None


# ── run_pr_command: flujo de error (sin push real) ──────────────────


@pytest.mark.asyncio
async def test_pr_command_aborts_when_no_branch():
    """Sin rama detectable, /pr aborta con render_error antes de pushear."""
    fake = MagicMock()
    fake.stdout = "HEAD\n"  # detached
    errors: list[str] = []

    def capture_err(text: str = ""):
        errors.append(str(text))

    session = DummySession()
    with patch("lilith_cli.extra_commands.subprocess.run", return_value=fake), patch(
        "lilith_cli.extra_commands.render_error", side_effect=capture_err
    ):
        await run_pr_command(session, "")

    assert any("rama" in e.lower() for e in errors), (
        f"Esperaba error mencionando 'rama'; obtuve: {errors}"
    )


@pytest.mark.asyncio
async def test_pr_command_aborts_when_same_branch():
    """Si la rama actual es igual a la base, aborta sin pushear."""
    call_log: list[list] = []

    def fake_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        result = MagicMock()
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "main\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "git@github.com:owner/repo.git\n"
        else:
            result.stdout = ""
            result.returncode = 0
        return result

    errors: list[str] = []

    def capture_err(text: str = ""):
        errors.append(str(text))

    session = DummySession()
    with patch("lilith_cli.extra_commands.subprocess.run", side_effect=fake_run), patch(
        "lilith_cli.extra_commands.render_error", side_effect=capture_err
    ):
        await run_pr_command(session, "main")

    assert any("misma" in e.lower() for e in errors), (
        f"Esperaba error de 'misma'; obtuve: {errors}"
    )
    # Y NO se intentó hacer push.
    assert not any(c[:2] == ["git", "push"] for c in call_log), (
        f"No debería haberse llamado git push; log: {call_log}"
    )


@pytest.mark.asyncio
async def test_pr_command_rejects_non_github_remote():
    """Con un remoto no-GitHub, /pr aborta antes de hacer push."""
    def fake_run(cmd, *args, **kwargs):
        result = MagicMock()
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "feat/x\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "https://gitlab.com/owner/repo.git\n"
        else:
            result.stdout = ""
            result.returncode = 0
        return result

    call_log: list[list] = []

    def tracking_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        return fake_run(cmd, *args, **kwargs)

    errors: list[str] = []

    def capture_err(text: str = ""):
        errors.append(str(text))

    session = DummySession()
    with patch(
        "lilith_cli.extra_commands.subprocess.run", side_effect=tracking_run
    ), patch("lilith_cli.extra_commands.render_error", side_effect=capture_err):
        await run_pr_command(session, "")

    assert any("github" in e.lower() for e in errors), (
        f"Esperaba error mencionando GitHub; obtuve: {errors}"
    )
    assert not any(c[:2] == ["git", "push"] for c in call_log), (
        f"No debería haberse llamado git push; log: {call_log}"
    )


@pytest.mark.asyncio
async def test_pr_command_dry_run_skips_push_and_gh():
    """`/pr --dry-run` muestra plan pero NO ejecuta push ni gh."""
    call_log: list[list] = []

    def fake_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        result = MagicMock()
        result.returncode = 0
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "feat/x\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "git@github.com:owner/repo.git\n"
        else:
            result.stdout = ""
        return result

    prints: list[str] = []

    def capture(text: str = ""):
        prints.append(str(text))

    session = DummySession()
    with patch(
        "lilith_cli.extra_commands.subprocess.run", side_effect=fake_run
    ), patch("lilith_cli.extra_commands.shutil.which", return_value="/fake/gh"), patch(
        "lilith_cli.extra_commands.console.print", side_effect=capture
    ):
        await run_pr_command(session, "--dry-run")

    rendered = "\n".join(prints)
    assert "feat/x" in rendered
    assert "compare/main...feat/x" in rendered
    assert "dry-run" in rendered.lower()
    # dry-run NUNCA debe invocar push ni gh.
    assert not any(c[:2] == ["git", "push"] for c in call_log), (
        f"dry-run no debe pushear; log: {call_log}"
    )
    assert not any(isinstance(c, list) and c[:1] == ["gh"] for c in call_log), (
        f"dry-run no debe invocar gh; log: {call_log}"
    )
