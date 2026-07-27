"""Tests para el comando /pr de Lilith CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lilith_cli.extra_commands import (
    _pr_compare_url,
    _pr_detect_branch,
    _pr_parse_option_value,
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


# ── _pr_parse_option_value: parsing puro ─────────────────────────────


def test_pr_parse_option_value_simple():
    assert _pr_parse_option_value("--title Hola mundo", "--title") == "Hola"
    assert _pr_parse_option_value("--body una desc", "--body") == "una"


def test_pr_parse_option_value_quoted():
    assert (
        _pr_parse_option_value('--title "Fix login bug"', "--title")
        == "Fix login bug"
    )
    assert (
        _pr_parse_option_value("--body 'Closes #42'", "--body") == "Closes #42"
    )


def test_pr_parse_option_value_equals_form():
    assert _pr_parse_option_value("--title=Hola", "--title") == "Hola"
    assert _pr_parse_option_value("--body=una desc", "--body") == "una desc"


def test_pr_parse_option_value_absent_returns_none():
    assert _pr_parse_option_value("--draft", "--title") is None
    assert _pr_parse_option_value("", "--title") is None


def test_pr_parse_option_value_next_is_flag_returns_none():
    """Si el siguiente token empieza con --, no consumimos ese flag como valor."""
    assert _pr_parse_option_value("--title --draft", "--title") is None


def test_pr_parse_option_value_ignores_later_occurrences():
    """Solo se respeta el primero."""
    assert (
        _pr_parse_option_value("--title primero --title segundo", "--title")
        == "primero"
    )


# ── run_pr_command: --title y --body se propagan a gh ─────────────────


@pytest.mark.asyncio
async def test_pr_command_passes_title_and_body_to_gh():
    """Con --title y --body, gh recibe esos flags y NO --fill."""

    def fake_run(cmd, *args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "feat/x\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "git@github.com:owner/repo.git\n"
        elif isinstance(cmd, list) and cmd[:1] == ["gh"] or (
            len(cmd) > 1 and cmd[1] == "pr"
        ):
            result.stdout = "https://github.com/owner/repo/pull/123\n"
        else:
            result.stdout = ""
        return result

    call_log: list[list] = []

    def tracking_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        return fake_run(cmd, *args, **kwargs)

    session = DummySession()
    with patch(
        "lilith_cli.extra_commands.subprocess.run", side_effect=tracking_run
    ), patch(
        "lilith_cli.extra_commands.shutil.which", return_value="/fake/gh"
    ), patch(
        "lilith_cli.extra_commands.console.print"
    ):
        await run_pr_command(
            session, '--title "Mi PR" --body "Descripcion del PR"'
        )

    gh_calls = [c for c in call_log if isinstance(c, list) and len(c) > 1 and c[1] == "pr"]
    assert gh_calls, f"Esperaba al menos una llamada a gh; log: {call_log}"
    last_gh = gh_calls[-1]
    # Title y body fueron propagados tal cual.
    assert "--title" in last_gh
    assert "Mi PR" in last_gh
    assert "--body" in last_gh
    assert "Descripcion del PR" in last_gh
    # --fill NO debe estar si pasamos title/body explícitos.
    assert "--fill" not in last_gh, (
        f"--fill no debe coexistir con --title/--body; gh_cmd fue: {last_gh}"
    )


@pytest.mark.asyncio
async def test_pr_command_only_title_uses_fill_false():
    """Con solo --title (sin --body), NO debe inyectarse --fill."""

    def fake_run(cmd, *args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "feat/x\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "git@github.com:owner/repo.git\n"
        else:
            result.stdout = "https://github.com/owner/repo/pull/124\n"
        return result

    call_log: list[list] = []

    def tracking_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        return fake_run(cmd, *args, **kwargs)

    session = DummySession()
    with patch(
        "lilith_cli.extra_commands.subprocess.run", side_effect=tracking_run
    ), patch(
        "lilith_cli.extra_commands.shutil.which", return_value="/fake/gh"
    ), patch(
        "lilith_cli.extra_commands.console.print"
    ):
        await run_pr_command(session, '--title "Solo title"')

    gh_calls = [c for c in call_log if isinstance(c, list) and len(c) > 1 and c[1] == "pr"]
    assert gh_calls
    last_gh = gh_calls[-1]
    assert "--title" in last_gh
    assert "Solo title" in last_gh
    # Sin body, --fill tampoco debe aparecer (porque el usuario fue explícito).
    assert "--fill" not in last_gh


@pytest.mark.asyncio
async def test_pr_command_without_title_or_body_uses_fill():
    """Sin --title/--body, --fill debe seguir presente (compatibilidad)."""

    def fake_run(cmd, *args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            result.stdout = "feat/x\n"
        elif cmd[:3] == ["git", "remote", "get-url"]:
            result.stdout = "git@github.com:owner/repo.git\n"
        else:
            result.stdout = "https://github.com/owner/repo/pull/125\n"
        return result

    call_log: list[list] = []

    def tracking_run(cmd, *args, **kwargs):
        call_log.append(cmd)
        return fake_run(cmd, *args, **kwargs)

    session = DummySession()
    with patch(
        "lilith_cli.extra_commands.subprocess.run", side_effect=tracking_run
    ), patch(
        "lilith_cli.extra_commands.shutil.which", return_value="/fake/gh"
    ), patch(
        "lilith_cli.extra_commands.console.print"
    ):
        await run_pr_command(session, "")

    gh_calls = [c for c in call_log if isinstance(c, list) and len(c) > 1 and c[1] == "pr"]
    assert gh_calls
    last_gh = gh_calls[-1]
    assert "--fill" in last_gh, (
        f"Sin title/body, --fill debe seguir presente (compatibilidad); gh: {last_gh}"
    )
    assert "--title" not in last_gh
    assert "--body" not in last_gh
