"""Snapshot tests for the Lilith IDE TUI (pytest-textual-snapshot).

Renders the real ``LilithIDEApp`` headless and compares SVG screenshots
stored under ``tests/__snapshots__/``. Regenerate with::

    python -m pytest tests/test_ide_snapshots.py --snapshot-update

Determinism notes (why each knob exists):

* ``NO_COLOR``/``ANSI_COLORS_DISABLED`` are cleared, because the snapshots
  were recorded in colour: with colour off Textual adds the ``nocolor``
  pseudo-class and every SVG mismatches, which looks like five broken tests
  but is only the caller's environment leaking in.
* The Header clock renders wall-clock time, so it is hidden in ``run_before``.
* ``IDEConfig.load``/``save`` are patched so the user's real
  ``~/.yggdrasil/ide.yaml`` (theme, reopened files) never leaks into the
  render, and nothing is written to the user's home during the test.
* ``LSPManager.get_client`` is patched to return ``None`` so opening a
  ``.py`` file never spawns a real language server found on the machine.
* The file-tree root node is relabelled to a fixed name because
  ``DirectoryTree`` renders the absolute ``tmp_path``, which changes on
  every run.
* The welcome message is written by a typewriter worker (one line every
  0.15 s), so ``run_before`` polls the chat log until it stops growing.
* Background workers (auto-reload, auto-save, splash auto-dismiss) are
  cancelled right before the screenshot so nothing mutates the UI between
  ``run_before`` returning and the SVG export.
* ``.yggdrasil/`` is pre-created in the project fixture so the directory
  listing does not race ``RealmManager.save()`` during ``on_mount``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_PKG_DIR = str(Path(__file__).resolve().parent.parent)
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from textual.widgets import Input, RichLog

from lilith_cli.ide import IDEConfig, LilithIDEApp
from lilith_cli.ide.lsp.manager import LSPManager
from lilith_cli.ide.screens.splash import SplashScreen

TERMINAL_SIZE = (120, 36)

_MAIN_PY = '''"""Modulo principal de ejemplo."""


def greet(name: str) -> str:
    """Devuelve un saludo."""
    return f"Hola, {name}!"


if __name__ == "__main__":
    print(greet("Yggdrasil"))
'''

_README_MD = """# Proyecto de ejemplo

Pequenio arbol de archivos usado por los snapshot tests del IDE.
"""

_NOTES_TXT = "Notas de ejemplo para el snapshot del IDE.\n"


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A small, fixed project tree for the IDE to open."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(_MAIN_PY, encoding="utf-8")
    (tmp_path / "README.md").write_text(_README_MD, encoding="utf-8")
    (tmp_path / "notes.txt").write_text(_NOTES_TXT, encoding="utf-8")
    # Pre-create the realm dir so the file-tree listing does not race the
    # RealmManager.save() call that happens in LilithIDEApp.on_mount().
    (tmp_path / ".yggdrasil").mkdir()
    (tmp_path / ".yggdrasil" / "realm.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def deterministic_ide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the app from the user's machine (colour env, config, LSP)."""
    # The snapshots were recorded in colour. If the caller's environment
    # disables colour, Textual adds the ``nocolor`` pseudo-class and every
    # SVG differs, so all five tests fail for a reason that has nothing to
    # do with the IDE. Agent runners and CI commonly export NO_COLOR.
    for var in ("NO_COLOR", "ANSI_COLORS_DISABLED"):
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setattr(IDEConfig, "load", classmethod(lambda cls, path=None: cls()))
    monkeypatch.setattr(IDEConfig, "save", lambda self, path=None: None)

    async def _no_client(self, language):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(LSPManager, "get_client", _no_client)


# ── Helpers ─────────────────────────────────────────────────────────


async def _wait_for(condition, timeout: float = 8.0, interval: float = 0.05) -> bool:
    """Poll *condition* until truthy or *timeout* elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


async def _settle(pilot) -> None:
    """Bring the freshly mounted app to a deterministic, stable state."""
    app = pilot.app

    # The header clock renders the current time — hide it.
    try:
        app.query_one("HeaderClock").display = False
    except Exception:
        pass

    # Wait until the welcome typewriter worker finished (writes stop).
    log = app.query_one("#chat-log", RichLog)
    state = {"last": -1, "stable": 0}

    def _typewriter_done() -> bool:
        n = len(log.lines)
        if n == state["last"] and n >= 3:
            state["stable"] += 1
        else:
            state["stable"] = 0
        state["last"] = n
        return state["stable"] >= 2

    await _wait_for(_typewriter_done, interval=0.25)

    # The file-tree root label is the absolute tmp_path — replace it with a
    # fixed name once the directory listing has loaded.
    tree = app.query_one("#file-tree")
    await _wait_for(lambda: len(tree.root.children) > 0)
    tree.root.set_label("proyecto")

    await pilot.pause()


def _freeze_inputs(app) -> None:
    """Disable cursor blink on visible inputs (blink phase is time-based)."""
    for widget in app.screen.query(Input):
        widget.cursor_blink = False


def _stop_workers(app) -> None:
    """Cancel background workers so nothing repaints before the export."""
    app.workers.cancel_all()


def _hide_cursor_position(app) -> None:
    """Oculta el lado derecho de la barra de estado antes de capturar.

    Ese Static muestra "Ln N, Col N" cuando ``_current_editor()`` devuelve
    algo, y eso solo pasa una vez que la pestana recien abierta quedo
    activa. El detalle es que ese asentamiento ocurre DESPUES de que
    ``run_before`` retorna, mientras Textual drena sus mensajes pendientes
    y justo antes de exportar el SVG: o sea, entra en la captura por una
    carrera que el test no controla. Con el mismo commit salia verde en
    3.11 y rojo en 3.12, y el diff del reporte era exactamente esa linea:

        esperado: 🌿 local / local-model  Ln 1, Col 1  Tokens ↑0 ↓0 Σ0
        real:     🌿 local / local-model  Tokens ↑0 ↓0 Σ0

    Esperar o forzar el repintado no alcanza, porque el valor depende de
    ese asentamiento posterior. Se oculta el widget, que es el mismo
    criterio que este archivo ya aplica al reloj del header: sacar de la
    foto lo que varia con el tiempo en vez de intentar sincronizarlo. Lo
    que el test verifica (el modal de ir-a-linea sobre un editor abierto)
    queda intacto.
    """
    try:
        app.query_one("#status-right").display = False
    except Exception:
        pass


async def _wait_workers_idle(pilot, timeout: float = 5.0) -> None:
    """Espera a que los workers one-shot terminen, sin dormir a ciegas.

    Esperar un tiempo fijo a que un worker actualice la UI es una carrera:
    si el runner esta cargado y no llega a tiempo, el snapshot captura la
    pantalla a medio actualizar y el test falla de forma intermitente. Ya
    paso en CI, donde el mismo commit fallo en 3.12 y paso en 3.11.

    Consultar el estado real de los workers lo vuelve determinista. El
    timeout es una red: si alguno no termina nunca, el test sigue (y
    fallara por el snapshot, que es informacion util) en vez de colgar
    la suite.
    """
    from textual.worker import WorkerState

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        busy = [
            w
            for w in pilot.app.workers
            if w.state in (WorkerState.PENDING, WorkerState.RUNNING)
        ]
        if not busy:
            return
        await pilot.pause()
        await asyncio.sleep(0.02)


# ── Snapshot tests ──────────────────────────────────────────────────


def test_ide_main_window(snap_compare, fake_session, project_root):
    """The IDE freshly opened over a small project (no splash)."""
    app = LilithIDEApp(fake_session, root=project_root, show_splash=False)

    async def run_before(pilot) -> None:
        await _settle(pilot)
        _freeze_inputs(pilot.app)
        _stop_workers(pilot.app)

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=run_before)


def test_ide_splash_screen(snap_compare, fake_session, project_root):
    """The Yggdrasil splash modal shown on startup."""
    app = LilithIDEApp(fake_session, root=project_root, show_splash=True)

    async def run_before(pilot) -> None:
        app = pilot.app
        splash = app.screen
        assert isinstance(splash, SplashScreen)
        # Cancel the 2.5 s auto-dismiss timer first so the splash cannot
        # disappear while we wait for the rest of the UI to settle.
        for worker in list(app.workers):
            if worker.node is splash:
                worker.cancel()
        await _settle(pilot)
        _freeze_inputs(app)
        _stop_workers(app)

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=run_before)


def test_ide_file_search_modal(snap_compare, fake_session, project_root):
    """Ctrl+P file search modal filtered by a typed query.

    A query is typed because the modal's initial ``on_mount`` population is
    racy (its ``clear()`` is scheduled after the ``append()`` calls and wipes
    them), so the just-opened list renders empty. Typing goes through the
    ``Input.Changed`` repopulation path, which is what users actually see.
    """
    app = LilithIDEApp(fake_session, root=project_root, show_splash=False)

    async def run_before(pilot) -> None:
        await _settle(pilot)
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("m", "a", "i", "n")
        await pilot.pause()
        _freeze_inputs(pilot.app)
        _stop_workers(pilot.app)

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=run_before)


def test_ide_command_palette_modal(snap_compare, fake_session, project_root):
    """Ctrl+Shift+P command palette filtered by a typed query.

    Same rationale as the file search test: typing a query exercises the
    deterministic ``Input.Changed`` repopulation path instead of the racy
    initial ``on_mount`` fill.
    """
    app = LilithIDEApp(fake_session, root=project_root, show_splash=False)

    async def run_before(pilot) -> None:
        await _settle(pilot)
        await pilot.press("ctrl+shift+p")
        await pilot.pause()
        await pilot.press(*"archivo")
        await pilot.pause()
        _freeze_inputs(pilot.app)
        _stop_workers(pilot.app)

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=run_before)


def test_ide_goto_line_modal(snap_compare, fake_session, project_root):
    """Ctrl+G go-to-line modal on top of an open editor tab."""
    app = LilithIDEApp(fake_session, root=project_root, show_splash=False)

    async def run_before(pilot) -> None:
        await _settle(pilot)
        # Go-to-line needs an active editor, so open a file first.
        pilot.app._open_file(project_root / "src" / "main.py")
        await pilot.pause()
        # Let the one-shot git-info worker finish updating the info bar
        # (git fails fast outside a repo, leaving just the relative path).
        await _wait_workers_idle(pilot)
        # La posicion del cursor entra en la barra despues de que run_before
        # retorna, asi que es carrera pura: se saca de la foto.
        _hide_cursor_position(pilot.app)
        await pilot.press("ctrl+g")
        await pilot.pause()
        _freeze_inputs(pilot.app)
        _stop_workers(pilot.app)

    assert snap_compare(app, terminal_size=TERMINAL_SIZE, run_before=run_before)
