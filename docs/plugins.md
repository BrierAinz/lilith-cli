# Plugins

Lilith has two extension points: **command plugins** add slash commands to the
REPL, and **IDE plugins** run inside `lilith ide` for one project.

## Command plugins (REPL)

Slash commands are `SlashRoute(name, handler, aliases)` entries; the built-in
table lives in `lilith-cli/lilith_cli/slash_router.py`. A handler is a
coroutine `async def handler(session, args: str) -> None`.

An installed package adds commands with a `lilith_cli.slash_commands` entry
point that resolves to a sequence of routes:

```toml
# pyproject.toml of your package
[project.entry-points."lilith_cli.slash_commands"]
greetings = "my_package.lilith_commands:ROUTES"
```

```python
# my_package/lilith_commands.py
from lilith_cli.render import console
from lilith_cli.slash_router import SlashRoute


async def run_hello_command(session, args: str) -> None:
    console.print(f"Hola, {args or 'mundo'}")


ROUTES = (SlashRoute("hello", run_hello_command, ("hi",)),)
```

- Plugin commands appear in Tab completion, `/how` and `/macro`.
- A plugin cannot replace a built-in name; the conflicting route is skipped
  with a warning.
- A plugin that fails to import is skipped with a warning.
- `LILITH_DISABLED_COMMAND_PLUGINS=name1,name2` turns plugins off by entry
  point name. The bundled `utilities` plugin (`/calc`, `/uuid`, `/qr`,
  `/timer`, ...) can be turned off the same way.

## IDE plugins

IDE plugins are Python files in `.yggdrasil/plugins/` at the root of the
project opened with `lilith ide`. The API is documented in
`lilith-cli/lilith_cli/ide/plugins.py`, and `lilith-cli/examples/plugins/`
has working examples.

A module declares its entry point in one of three ways (first match wins):
a module-level `plugin` object, a `Plugin` class, or a `register(app)`
function. Hooks receive the running app (`app.root`, `app._chat_system()`,
`app.notify()`, `app.current_file`).

### Trust

Project plugins are code that ships with the repository, so the IDE does
**not** run them until you trust the project:

- `/plugins` in the IDE chat lists the plugin directory and whether the
  project is trusted.
- `/plugins trust` trusts the current project and loads its plugins. The
  decision is stored in `~/.yggdrasil/trusted_plugin_projects.json`.
- `LILITH_TRUST_PROJECT_PLUGINS=1` trusts every project (for example in a
  throwaway container).
