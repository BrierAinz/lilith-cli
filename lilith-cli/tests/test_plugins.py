"""Tests for the Lilith IDE plugin system."""

from __future__ import annotations

import shutil
from pathlib import Path

from lilith_cli.ide.plugins import LilithPlugin, PluginManager

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def _make_plugin_dir(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / ".yggdrasil" / "plugins"
    plugin_dir.mkdir(parents=True)
    return plugin_dir


class FakeApp:
    """Minimal stand-in for LilithIDEApp exposing the stable plugin surface."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.current_file = None
        self.messages: list[str] = []
        self.toasts: list[tuple[str, str, str]] = []

    def _chat_system(self, text: str) -> None:
        self.messages.append(text)

    def notify(self, message, title: str = "", *, severity: str = "information", timeout: float = 3.0) -> None:
        self.toasts.append((str(message), title, severity))


class TestPluginManager:
    """Unit tests for plugin discovery and loading."""

    def test_empty_plugin_dir(self, tmp_path):
        mgr = PluginManager(tmp_path)
        assert mgr.discover() == []
        assert mgr.load_all() == []

    def test_discover_python_files(self, tmp_path):
        plugin_dir = tmp_path / ".yggdrasil" / "plugins"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "hello.py").write_text("def register(app): pass", encoding="utf-8")
        (plugin_dir / "readme.txt").write_text("not a plugin", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        discovered = mgr.discover()
        assert len(discovered) == 1
        assert discovered[0].stem == "hello"

    def test_load_plugin_with_register(self, tmp_path):
        plugin_dir = tmp_path / ".yggdrasil" / "plugins"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "counter.py").write_text(
            "calls = []\ndef register(app):\n    calls.append(app)",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        plugins = mgr.load_all()
        assert len(plugins) == 1
        assert plugins[0].name == "counter"

        fake_app = object()
        loaded = mgr.register_all(fake_app)
        assert loaded == ["counter"]

    def test_load_plugin_without_register_is_skipped(self, tmp_path):
        plugin_dir = tmp_path / ".yggdrasil" / "plugins"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "noop.py").write_text("x = 1", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        assert mgr.load_all() == []

    def test_broken_plugin_is_skipped(self, tmp_path):
        plugin_dir = tmp_path / ".yggdrasil" / "plugins"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "broken.py").write_text("syntax error!!!", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        assert mgr.load_all() == []


class TestStableContract:
    """Tests for the v1.0 stable plugin contract (class-based plugins, errors, emit)."""

    def test_underscore_files_are_ignored(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "_helpers.py").write_text("def register(app): pass", encoding="utf-8")
        (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        (plugin_dir / "real.py").write_text("def register(app): pass", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        assert [p.stem for p in mgr.discover()] == ["real"]

    def test_class_based_plugin_with_metadata(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "classy.py").write_text(
            "from lilith_cli.ide.plugins import LilithPlugin\n"
            "class Plugin(LilithPlugin):\n"
            "    name = 'mi-plugin'\n"
            "    version = '2.0.0'\n"
            "    description = 'demo'\n"
            "    def on_load(self, app):\n"
            "        app._chat_system('classy on_load')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        plugins = mgr.load_all()
        assert len(plugins) == 1
        assert plugins[0].name == "mi-plugin"
        assert plugins[0].version == "2.0.0"
        assert plugins[0].description == "demo"
        assert isinstance(plugins[0].instance, LilithPlugin)

        app = FakeApp(tmp_path)
        assert mgr.register_all(app) == ["mi-plugin"]
        assert app.messages == ["classy on_load"]

    def test_duck_typed_plugin_instance(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "ducky.py").write_text(
            "class _Duck:\n"
            "    name = 'pato'\n"
            "    def on_load(self, app):\n"
            "        app._chat_system('cuac')\n"
            "plugin = _Duck()\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        plugins = mgr.load_all()
        assert [p.name for p in plugins] == ["pato"]
        app = FakeApp(tmp_path)
        assert mgr.register_all(app) == ["pato"]
        assert app.messages == ["cuac"]

    def test_plugin_instance_takes_precedence_over_register(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "both.py").write_text(
            "class _P:\n"
            "    name = 'instancia'\n"
            "    def on_load(self, app):\n"
            "        app._chat_system('instancia')\n"
            "plugin = _P()\n"
            "def register(app):\n"
            "    app._chat_system('funcion')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        assert mgr.register_all(app) == ["instancia"]
        assert app.messages == ["instancia"]

    def test_broken_import_is_recorded_and_others_still_load(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "aaa_broken.py").write_text("raise RuntimeError('boom import')", encoding="utf-8")
        (plugin_dir / "zzz_good.py").write_text("def register(app): app._chat_system('ok')", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        plugins = mgr.load_all()
        assert [p.name for p in plugins] == ["zzz_good"]
        assert any(e.plugin == "aaa_broken" and e.phase == "load" and "boom import" in e.message for e in mgr.errors)

    def test_register_exception_does_not_break_other_plugins(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "aaa_raises.py").write_text(
            "def register(app):\n    raise ValueError('boom register')",
            encoding="utf-8",
        )
        (plugin_dir / "zzz_good.py").write_text(
            "def register(app):\n    app._chat_system('sano')",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        assert len(mgr.load_all()) == 2
        app = FakeApp(tmp_path)
        loaded = mgr.register_all(app)
        assert loaded == ["zzz_good"]
        assert app.messages == ["sano"]
        assert any(e.plugin == "aaa_raises" and e.phase == "register" and "boom register" in e.message for e in mgr.errors)

    def test_on_load_exception_in_class_plugin_is_contained(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "kaboom.py").write_text(
            "from lilith_cli.ide.plugins import LilithPlugin\n"
            "class Plugin(LilithPlugin):\n"
            "    name = 'kaboom'\n"
            "    def on_load(self, app):\n"
            "        raise RuntimeError('boom on_load')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        assert mgr.register_all(FakeApp(tmp_path)) == []
        assert any(e.plugin == "kaboom" and e.phase == "register" for e in mgr.errors)

    def test_plugin_constructor_exception_is_contained(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "badctor.py").write_text(
            "class Plugin:\n"
            "    def __init__(self):\n"
            "        raise RuntimeError('boom ctor')\n"
            "    def on_load(self, app):\n"
            "        pass\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        assert mgr.load_all() == []
        assert any(e.plugin == "badctor" and e.phase == "load" for e in mgr.errors)

    def test_missing_entry_point_is_recorded(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "empty.py").write_text("x = 1", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        assert mgr.load_all() == []
        assert any(e.plugin == "empty" and e.phase == "load" for e in mgr.errors)

    def test_get_returns_loaded_plugin_by_name(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "uno.py").write_text("def register(app): pass", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        assert mgr.get("uno") is not None
        assert mgr.get("uno").name == "uno"
        assert mgr.get("nope") is None


class TestEmit:
    """Tests for PluginManager.emit hook dispatch and error isolation."""

    def test_emit_dispatches_to_instance_hook(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "saver.py").write_text(
            "from lilith_cli.ide.plugins import LilithPlugin\n"
            "class Plugin(LilithPlugin):\n"
            "    name = 'saver'\n"
            "    def on_file_save(self, app, path):\n"
            "        app._chat_system(f'guardado {path}')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        handled = mgr.emit("file_save", app, "foo.py")
        assert handled == ["saver"]
        assert app.messages == ["guardado foo.py"]

    def test_emit_dispatches_to_module_level_hook(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "modhook.py").write_text(
            "def register(app): pass\n"
            "def on_file_open(app, path):\n"
            "    app._chat_system(f'abierto {path}')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        assert mgr.emit("file_open", app, "bar.py") == ["modhook"]
        assert app.messages == ["abierto bar.py"]

    def test_emit_isolates_hook_exceptions(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "aaa_bad.py").write_text(
            "def register(app): pass\n"
            "def on_ping(app):\n"
            "    raise RuntimeError('boom hook')\n",
            encoding="utf-8",
        )
        (plugin_dir / "zzz_ok.py").write_text(
            "def register(app): pass\n"
            "def on_ping(app):\n"
            "    app._chat_system('pong')\n",
            encoding="utf-8",
        )
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        assert mgr.emit("ping", app) == ["zzz_ok"]
        assert app.messages == ["pong"]
        assert any(e.plugin == "aaa_bad" and e.phase == "hook:ping" for e in mgr.errors)

    def test_emit_unknown_event_is_noop(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "quiet.py").write_text("def register(app): pass", encoding="utf-8")
        mgr = PluginManager(tmp_path)
        mgr.load_all()
        assert mgr.emit("no_such_event", FakeApp(tmp_path)) == []
        assert mgr.errors == []


class TestExamplePlugins:
    """The shipped examples in examples/plugins/ must work with the real loader."""

    def _install_examples(self, tmp_path: Path) -> PluginManager:
        plugin_dir = _make_plugin_dir(tmp_path)
        for example in EXAMPLES_DIR.glob("*.py"):
            shutil.copy(example, plugin_dir / example.name)
        return PluginManager(tmp_path)

    def test_examples_exist(self):
        names = {p.name for p in EXAMPLES_DIR.glob("*.py")}
        assert {"skald_greeting.py", "todo_runes.py"} <= names

    def test_examples_load_and_register(self, tmp_path):
        (tmp_path / "main.py").write_text(
            "# TODO: primero\n# FIXME: segundo\nprint('hola')\n",
            encoding="utf-8",
        )
        (tmp_path / "notas.md").write_text("- TODO revisar\n", encoding="utf-8")
        mgr = self._install_examples(tmp_path)
        plugins = mgr.load_all()
        assert sorted(p.name for p in plugins) == ["skald_greeting", "todo_runes"]

        app = FakeApp(tmp_path)
        loaded = mgr.register_all(app)
        assert sorted(loaded) == ["skald_greeting", "todo_runes"]
        assert mgr.errors == []

        chat = "\n".join(app.messages)
        assert "Skald" in chat
        assert "Todo Runes" in chat
        assert "3 marcadores" in chat  # 2 en main.py + 1 en notas.md

    def test_todo_runes_clean_project(self, tmp_path):
        (tmp_path / "limpio.py").write_text("print('nada pendiente')\n", encoding="utf-8")
        mgr = self._install_examples(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        mgr.register_all(app)
        assert any("proyecto limpio" in msg for msg in app.messages)

    def test_todo_runes_file_save_hook(self, tmp_path):
        target = tmp_path / "main.py"
        target.write_text("# TODO: pendiente\n", encoding="utf-8")
        mgr = self._install_examples(tmp_path)
        mgr.load_all()
        app = FakeApp(tmp_path)
        mgr.register_all(app)
        app.messages.clear()
        handled = mgr.emit("file_save", app, target)
        assert "todo_runes" in handled
        assert any("1 marcadores" in msg for msg in app.messages)
