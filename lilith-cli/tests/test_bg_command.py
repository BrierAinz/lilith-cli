"""Tests for the /bg slash command (background-process manager)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lilith_cli.bg_command import run_bg_command


class DummyConfig:
    def __init__(self) -> None:
        self.model = "test"
        self.provider = "test"
        self.providers = {}
        self.api_key = ""
        self.system_prompt = ""


class DummySession:
    def __init__(self) -> None:
        self.config = DummyConfig()
        self.memory = None
        self.history = []
        self.provider = None
        self.system_prompt = ""


@pytest.mark.asyncio
async def test_bg_usage_sin_argumentos_muestra_ayuda():
    """/bg sin subcomando imprime el bloque de uso."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    session = DummySession()
    with patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "")

    output = "\n".join(prints)
    assert "Uso: /bg" in output
    assert "list" in output
    assert "start" in output
    assert "stop" in output


@pytest.mark.asyncio
async def test_bg_list_vacio_renderiza_tabla():
    """/bg list con manager vacio no rompe y dibuja la tabla."""
    prints: list[object] = []

    def capture(text: object = "") -> None:
        prints.append(text)

    class FakeManager:
        def list(self):
            return []

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "list")

    # ``console.print`` recibe el Rich Table; verificamos que se invoco al
    # menos una vez (la tabla vacia con el placeholder "(sin procesos
    # registrados)" vive dentro del Table, no en ``prints``).
    assert len(prints) >= 1


@pytest.mark.asyncio
async def test_bg_start_sin_separador_da_error():
    """/bg start sin ``--`` muestra mensaje de uso en espanol."""
    errors: list[str] = []

    def capture(text: str = "") -> None:
        errors.append(str(text))

    session = DummySession()
    with patch("lilith_cli.bg_command.render_error", side_effect=capture):
        await run_bg_command(session, "start foo")

    assert any("/bg start <nombre> -- <comando>" in e for e in errors)


@pytest.mark.asyncio
async def test_bg_start_inicia_y_mensaje_exito():
    """/bg start <nombre> -- <cmd> delega en ProcessManager.start."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    class FakeManager:
        started: list[tuple[str, str]] = []

        def start(self, name, command):
            self.started.append((name, command))
            return 4242

    session = DummySession()
    fake = FakeManager()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=fake
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(
            session, "start devserver -- python -m http.server 8080"
        )

    assert fake.started == [("devserver", "python -m http.server 8080")]
    output = "\n".join(prints)
    assert "devserver" in output
    assert "4242" in output


@pytest.mark.asyncio
async def test_bg_start_falla_cuando_manager_devuelve_none():
    """/bg start que no logra arrancar muestra error en espanol."""
    errors: list[str] = []

    def capture(text: str = "") -> None:
        errors.append(str(text))

    class FakeManager:
        def start(self, name, command):
            return None

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.render_error", side_effect=capture):
        await run_bg_command(session, "start broken -- python -c pass")

    assert any("No se pudo iniciar" in e for e in errors)


@pytest.mark.asyncio
async def test_bg_status_sin_nombre_da_error():
    """/bg status sin nombre muestra mensaje de uso."""
    errors: list[str] = []

    def capture(text: str = "") -> None:
        errors.append(str(text))

    session = DummySession()
    with patch("lilith_cli.bg_command.render_error", side_effect=capture):
        await run_bg_command(session, "status")

    assert any("Uso: /bg status" in e for e in errors)


@pytest.mark.asyncio
async def test_bg_status_muestra_pid_y_log_de_proceso_vivo():
    """/bg status <nombre> imprime PID, log y comando cuando esta vivo."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    class FakeManager:
        def status(self, name):
            return {
                "name": name,
                "pid": 9001,
                "alive": True,
                "port": None,
                "log_file": f"~/.yggdrasil/processes/logs/{name}.log",
                "command": "python -m http.server 8080",
            }

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "status devserver")

    output = "\n".join(prints)
    assert "devserver" in output
    assert "vivo" in output
    assert "9001" in output


@pytest.mark.asyncio
async def test_bg_stop_exitoso_mensaje_en_espanol():
    """/bg stop <nombre> imprime confirmacion tras detenerse."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    class FakeManager:
        def stop(self, name):
            return True

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "stop devserver")

    output = "\n".join(prints)
    assert "devserver" in output
    assert "detenido" in output


@pytest.mark.asyncio
async def test_bg_log_imprime_contenido_y_encabezado():
    """/bg log <nombre> imprime las ultimas N lineas del log."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    class FakeManager:
        def get_log(self, name, lines=50):
            return "linea1\nlinea2\nlinea3"

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "log devserver --lines 10")

    output = "\n".join(prints)
    assert "linea1" in output
    assert "linea3" in output
    assert "devserver" in output


@pytest.mark.asyncio
async def test_bg_cleanup_lista_vacia_muestra_dim_nada():
    """/bg cleanup sin zombies imprime mensaje neutro."""
    prints: list[str] = []

    def capture(text: str = "") -> None:
        prints.append(str(text))

    class FakeManager:
        def cleanup(self):
            return []

    session = DummySession()
    with patch(
        "lilith_cli.bg_command.ProcessManager", return_value=FakeManager()
    ), patch("lilith_cli.bg_command.console.print", side_effect=capture):
        await run_bg_command(session, "cleanup")

    assert any("Nada que limpiar" in p for p in prints)


@pytest.mark.asyncio
async def test_bg_subcomando_desconocido_muestra_error_y_ayuda():
    """/bg <subcmd> desconocido renderiza error + bloque de uso."""
    errors: list[str] = []

    def capture(text: str = "") -> None:
        errors.append(str(text))

    session = DummySession()
    with patch("lilith_cli.bg_command.render_error", side_effect=capture):
        await run_bg_command(session, "frobnicate")

    output = "\n".join(errors)
    assert "frobnicate" in output
    assert "Uso: /bg" in output