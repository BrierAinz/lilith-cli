"""Render reproducible Hoguera previews with synthetic sessions only."""

import argparse
import asyncio
import os
from pathlib import Path
import tempfile

from lilith_cli.hearth_ui import HearthApp
from lilith_cli.hearth_screens import TaskScreen, MemoryScreen
from textual.widgets import Input, TextArea


async def render(output: Path) -> None:
    # Render the color design even when the calling automation uses NO_COLOR.
    os.environ.pop("NO_COLOR", None)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lilith-preview-") as scratch:
        root = Path(scratch)
        os.environ["LILITH_PREFERENCES_DB"] = str(root / "preferences.sqlite3")
        from lilith_cli.work_memory import save_preference
        await save_preference("design_style", "Nórdico, Souls y anime")
        await save_preference("communication", "Español natural y directo")
        examples = [
            {"name": "conv_parser", "project_root": str(root), "model": "modelo configurado",
             "message_count": 12, "timestamp": "2026-09-07", "goal": {
                 "objective": "Cerrar la saga del parser", "status": "active"}},
            {"name": "conv_tests", "project_root": str(root), "model": "modelo configurado",
             "message_count": 8, "timestamp": "2026-09-07", "goal": {
                 "objective": "Verificar recuperación de sesiones", "status": "completed"}},
            {"name": "conv_art", "project_root": str(root), "model": "modelo configurado",
             "message_count": 5, "timestamp": "2026-09-06", "goal": {
                 "objective": "Definir identidad del taller", "status": "paused"}},
        ]
        for width, height in [(120, 40), (80, 30)]:
            app = HearthApp(root, sessions=examples, preferences=root / "ui.yaml")
            app.reduced_motion = True
            async with app.run_test(size=(width, height)) as pilot:
                await pilot.pause()
                app.save_screenshot(filename=f"hearth-{width}x{height}.svg", path=str(output))
                screen = TaskScreen(root)
                await app.push_screen(screen)
                screen.query_one("#objective", TextArea).load_text("Corrige la búsqueda y añade pruebas para consultas vacías y límites explícitos.")
                screen.query_one("#task-files", Input).value = "search.js,tests/search.test.mjs"
                screen.query_one("#verify-command", Input).value = "node --test tests/search.test.mjs"
                await pilot.pause()
                app.save_screenshot(filename=f"task-{width}x{height}.svg", path=str(output))
                app.pop_screen()
                await app.push_screen(MemoryScreen(root))
                await pilot.pause()
                app.save_screenshot(filename=f"memory-{width}x{height}.svg", path=str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(render(parser.parse_args().output))
