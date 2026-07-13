"""Stable plugin API for the Lilith IDE (Yggdrasil / Asgard).

Overview
--------
Plugins are plain Python files placed in ``.yggdrasil/plugins/`` at the root
of the project the IDE was opened in. Files whose name starts with ``_`` are
ignored. On startup (``LilithIDEApp.on_mount``) the app calls
``PluginManager.load_all()`` followed by ``PluginManager.register_all(app)``.

A plugin module can declare its entry point in one of three ways, resolved
in this order (first match wins):

1. A module-level ``plugin`` object implementing the :class:`LilithPlugin`
   contract (an instance, either a real subclass or duck-typed).
2. A module-level class named ``Plugin`` (instantiated with no arguments).
3. A module-level function ``register(app)`` — the original, simplest form.

Lifecycle hooks
---------------
``on_load(app)``
    The only hook the running app invokes today. Called exactly once, right
    after the IDE mounts, with the live ``LilithIDEApp`` instance. For
    function-style plugins, ``register(app)`` *is* the ``on_load`` hook.

``on_file_open(app, path)`` / ``on_file_save(app, path)`` / ``on_unload(app)``
    Reserved hook names. :meth:`PluginManager.emit` can dispatch them (and
    any other ``on_<event>`` method or module-level function), but the
    current ``app.py`` does not emit these events yet. Implementing them is
    forward-compatible and harmless; they simply will not fire until the app
    wires the corresponding ``emit()`` calls.

What plugins can do with ``app``
--------------------------------
The ``app`` object is the running Textual application. Stable, useful
surface for plugins:

* ``app.root`` — project root as a resolved :class:`pathlib.Path`.
* ``app._chat_system(text)`` — write a dim system line to the chat log
  (accepts Rich markup).
* ``app.notify(message, title=..., severity=...)`` — show a toast.
* ``app.current_file`` — currently focused file (``Path | None``).
* ``app.plugin_manager`` — this manager (e.g. to inspect ``errors``).

Error handling
--------------
A broken plugin must never take down the IDE. Every phase (import, entry
point resolution, registration, hook dispatch) is wrapped: failures are
logged to the ``lilith.plugins`` logger, recorded in
:attr:`PluginManager.errors`, and the offending plugin is skipped while the
rest keep working.

Minimal example (``.yggdrasil/plugins/hello.py``)::

    def register(app):
        app._chat_system("Plugin hello.py cargado")

Class-based example::

    from lilith_cli.ide.plugins import LilithPlugin

    class Plugin(LilithPlugin):
        name = "mi-plugin"
        version = "1.0.0"
        description = "Hace algo útil."

        def on_load(self, app):
            app._chat_system(f"{self.name} listo")

See ``docs/plugins.md`` for the full guide and ``examples/plugins/`` for
working examples.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("lilith.plugins")

#: Version of the plugin API contract described in this module.
#: Bumped only on breaking changes to the contract.
PLUGIN_API_VERSION = "1.0"


class LilithPlugin:
    """Base class (and de-facto protocol) for class-based Lilith plugins.

    Subclassing is optional — any object with a callable ``on_load(app)``
    satisfies the contract — but subclassing gives you the metadata
    defaults and the reserved hook stubs for free.

    Class attributes (all optional):

    * ``name`` — display name; defaults to the plugin file's stem.
    * ``version`` — plugin's own version string (informational).
    * ``description`` — one-line summary (informational).
    """

    name: str = ""
    version: str = "0.1.0"
    description: str = ""

    def on_load(self, app: Any) -> None:
        """Called once when the IDE mounts and registers plugins.

        ``app`` is the live ``LilithIDEApp``. This is the only hook the
        current app invokes; do your setup (and any one-shot work) here.
        Exceptions raised here are caught, logged and recorded — they do
        not crash the IDE — but the plugin is reported as failed.
        """

    def on_unload(self, app: Any) -> None:
        """Reserved hook: cleanup before the IDE exits.

        Dispatched via ``PluginManager.emit("unload", app)``. The current
        app does not emit this event yet.
        """

    def on_file_open(self, app: Any, path: Path) -> None:
        """Reserved hook: a file was opened in the editor.

        Dispatched via ``PluginManager.emit("file_open", app, path)``. The
        current app does not emit this event yet.
        """

    def on_file_save(self, app: Any, path: Path) -> None:
        """Reserved hook: a file was saved from the editor.

        Dispatched via ``PluginManager.emit("file_save", app, path)``. The
        current app does not emit this event yet.
        """


@dataclass
class PluginError:
    """A recorded, non-fatal plugin failure.

    Attributes:
        plugin: Plugin name (file stem or declared ``name``).
        phase: Where it failed: ``"load"``, ``"register"`` or
            ``"hook:<event>"``.
        message: Stringified exception (or reason for the skip).
    """

    plugin: str
    phase: str
    message: str


@dataclass
class LoadedPlugin:
    """A successfully loaded plugin with its metadata.

    Attributes:
        name: Display name (declared ``name`` or the file stem).
        path: Absolute path of the plugin file.
        register: Callable invoked as ``register(app)`` during
            :meth:`PluginManager.register_all`. For class-based plugins this
            is bound to ``instance.on_load``.
        module: The imported module object.
        instance: The :class:`LilithPlugin`-like instance, or ``None`` for
            function-style plugins.
    """

    name: str
    path: Path
    register: Callable[[Any], None]
    module: Any = field(repr=False)
    instance: Any = field(default=None, repr=False)

    @property
    def version(self) -> str:
        """Plugin version declared on the instance (or ``""``)."""
        return str(getattr(self.instance, "version", "") or "")

    @property
    def description(self) -> str:
        """Plugin description declared on the instance (or ``""``)."""
        return str(getattr(self.instance, "description", "") or "")


class PluginManager:
    """Discover, load and drive Lilith IDE plugins for a project.

    Typical app-side usage (this is exactly what ``LilithIDEApp`` does)::

        manager = PluginManager(project_root)
        manager.load_all()
        loaded_names = manager.register_all(app)

    All failures are contained: they are logged, appended to
    :attr:`errors`, and never propagate to the caller.
    """

    #: Plugin directory, relative to the project root.
    PLUGIN_DIR = Path(".yggdrasil") / "plugins"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._plugins: list[LoadedPlugin] = []
        #: Non-fatal failures collected during load/register/emit.
        self.errors: list[PluginError] = []

    def plugin_dir(self) -> Path:
        """Absolute path of the plugin directory for this project."""
        return self.root / self.PLUGIN_DIR

    def discover(self) -> list[Path]:
        """Return all plugin candidates under the plugin directory.

        Candidates are ``*.py`` files sorted by name; files starting with
        ``_`` (e.g. ``__init__.py``, ``_helpers.py``) are ignored so plugins
        can keep private helper modules alongside themselves.
        """
        plugin_dir = self.plugin_dir()
        if not plugin_dir.exists():
            return []
        return sorted(
            p
            for p in plugin_dir.glob("*.py")
            if p.is_file() and not p.name.startswith("_")
        )

    def load_all(self) -> list[LoadedPlugin]:
        """Import every discovered plugin and resolve its entry point.

        Broken plugins (import errors, missing entry point, constructor
        failures) are logged, recorded in :attr:`errors` and skipped.
        Returns the successfully loaded plugins.
        """
        self._plugins = []
        self.errors = []
        for path in self.discover():
            plugin = self._load_one(path)
            if plugin:
                self._plugins.append(plugin)
        return list(self._plugins)

    def _load_one(self, path: Path) -> LoadedPlugin | None:
        """Import a single plugin file and resolve its entry point.

        Resolution order: module-level ``plugin`` instance, then a
        ``Plugin`` class (instantiated with no args), then a ``register``
        function. Returns ``None`` (after logging and recording the error)
        if the file cannot be imported or exposes no entry point.
        """
        try:
            spec = importlib.util.spec_from_file_location(
                f"lilith_plugin_{path.stem}", path
            )
            if not spec or not spec.loader:
                self._record_error(path.stem, "load", "no se pudo crear el spec de import")
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            self._record_error(path.stem, "load", exc)
            return None

        try:
            instance = self._resolve_instance(module)
        except Exception as exc:
            self._record_error(path.stem, "load", exc)
            return None

        if instance is not None:
            name = str(getattr(instance, "name", "") or "") or path.stem
            return LoadedPlugin(
                name=name,
                path=path,
                register=instance.on_load,
                module=module,
                instance=instance,
            )

        register = getattr(module, "register", None)
        if callable(register):
            return LoadedPlugin(
                name=path.stem,
                path=path,
                register=register,
                module=module,
            )

        self._record_error(
            path.stem,
            "load",
            "sin punto de entrada: definí register(app), una clase Plugin o un objeto plugin",
        )
        return None

    @staticmethod
    def _resolve_instance(module: Any) -> Any | None:
        """Return the plugin instance declared by ``module``, if any.

        Checks for a module-level ``plugin`` object with a callable
        ``on_load`` first, then a ``Plugin`` class to instantiate. Raises
        whatever the plugin's constructor raises (handled by the caller).
        """
        candidate = getattr(module, "plugin", None)
        if candidate is not None and callable(getattr(candidate, "on_load", None)):
            return candidate
        plugin_cls = getattr(module, "Plugin", None)
        if isinstance(plugin_cls, type):
            instance = plugin_cls()
            if callable(getattr(instance, "on_load", None)):
                return instance
        return None

    def register_all(self, app: Any) -> list[str]:
        """Invoke the ``on_load`` phase of every loaded plugin.

        Calls ``register(app)`` (function-style) or ``instance.on_load(app)``
        (class-style) on each plugin. A plugin that raises is logged,
        recorded in :attr:`errors` and skipped — the remaining plugins still
        run and the IDE keeps working. Returns the names of the plugins that
        registered successfully.
        """
        loaded: list[str] = []
        for plugin in self._plugins:
            try:
                plugin.register(app)
                loaded.append(plugin.name)
            except Exception as exc:
                self._record_error(plugin.name, "register", exc)
        return loaded

    def emit(self, event: str, app: Any, *args: Any, **kwargs: Any) -> list[str]:
        """Dispatch a lifecycle event to every plugin that handles it.

        Looks for a callable hook named ``on_<event>`` on each plugin's
        instance (class-style) or module (function-style) and calls it as
        ``hook(app, *args, **kwargs)``. A plugin whose hook raises is
        logged, recorded in :attr:`errors` and skipped; the event still
        reaches the remaining plugins. Returns the names of the plugins
        whose hook was invoked without error.

        Note: the current ``LilithIDEApp`` only drives the ``on_load``
        phase (via :meth:`register_all`); ``emit`` exists so the app — or
        tests — can fan out further events (``file_open``, ``file_save``,
        ``unload``, …) without touching this module again.
        """
        hook_name = f"on_{event}"
        handled: list[str] = []
        for plugin in self._plugins:
            hook = None
            if plugin.instance is not None:
                hook = getattr(plugin.instance, hook_name, None)
            if hook is None:
                hook = getattr(plugin.module, hook_name, None)
            if not callable(hook):
                continue
            try:
                hook(app, *args, **kwargs)
                handled.append(plugin.name)
            except Exception as exc:
                self._record_error(plugin.name, f"hook:{event}", exc)
        return handled

    def get(self, name: str) -> LoadedPlugin | None:
        """Return the loaded plugin called ``name``, or ``None``."""
        for plugin in self._plugins:
            if plugin.name == name:
                return plugin
        return None

    def list(self) -> list[LoadedPlugin]:
        """Return the loaded plugins (a copy, in load order)."""
        return list(self._plugins)

    def _record_error(self, plugin: str, phase: str, exc: Exception | str) -> None:
        """Log a plugin failure and keep it in :attr:`errors`."""
        message = str(exc)
        self.errors.append(PluginError(plugin=plugin, phase=phase, message=message))
        logger.warning("Plugin %r falló en fase %s: %s", plugin, phase, message)
