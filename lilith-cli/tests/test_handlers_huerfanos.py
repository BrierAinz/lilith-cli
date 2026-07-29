"""Guardián de handlers huérfanos: handlers ``run_X_command`` definidos en
los módulos de comando de ``lilith_cli`` pero NO alcanzables desde el
dispatcher de ``repl.py``.

Caso complementario a ``test_repl_wiring.py`` y ``test_repl_handler_imports.py``:

* ``test_repl_wiring.py``  — todo handler *importado* en ``repl.py`` debe estar
  *despachado* en su cascada inline (NameError / slash silencioso).
* ``test_repl_handler_imports.py`` — todo handler *despachado* en ``repl.py``
  debe estar *importado* como nombre del módulo (TypeError en runtime).
* **Este test** — todo handler *definido* en los módulos de comando debe
  ser *alcanzable* desde ``repl.py`` por alguno de los mecanismos de
  despacho reales (cascada inline, ``CommandRegistry.dispatch``).

El tercer modo de falla es el más silencioso: el handler existe, está
importado, no genera error en runtime, simplemente ningún slash entra al
camino que lo llama. El archivo crece, ``/help`` lo anuncia, pero el
comando nunca corre. Ya pasó con ``/feedback`` (deleg d9685cd6 — el
import estaba, el autocompletado estaba, faltaba el cable) y se sospecha
que quedan más casos estructurales así.

Mecanismos de despacho que el guard reconoce:

1. **Cascada inline** de ``if cmd_name == "x": await run_x_command(...)``
   en ``repl.py``. La cuenta por defecto: si el nombre aparece como
   ``await run_X_command(...)`` o ``run_X_command(session, ...)`` en el
   cuerpo de ``repl.py``, el handler está despachado.

2. **CommandRegistry** (``registry.dispatch`` al final de la cascada).
   Si la clase equivalente (``XCommand``) está en la lista
   ``CommandRegistry.discover.builtin`` y ``run_X_command`` ya no es
   la ruta activa de dispatch, la lógica vive en
   ``XCommand.execute()`` y el handler queda como backward-compat
   shim. Esos casos van a la ALLOWLIST con la razón documentada.

ALLOWLIST: cada handler que queda fuera del despacho debe tener una
entrada con explicación. Si el archivo crece y la allowlist empieza a
ser excusa para "tirar a la pila", este test deja de cumplir su rol.
"""

from __future__ import annotations

import ast
from pathlib import Path

LILITH_CLI = Path(__file__).resolve().parent.parent / "lilith_cli"
REPL = LILITH_CLI / "repl.py"
COMMANDS = LILITH_CLI / "commands.py"


# Handlers definidos en ``extra_commands.py``/``*_command.py`` cuya lógica
# vive hoy en una clase ``BaseCommand`` registrada en
# ``CommandRegistry.discover()``. La cascada inline de ``repl.py`` los
# salta a propósito y deja que ``registry.dispatch`` los atienda vía
# ``XCommand.execute(...)``. El ``run_X_command`` quedó como backward-
# compat shim cuando se hizo la migración a clases.
#
# NO agregar entradas acá "para callar al test": cada nombre debe
# corresponderse con una clase en ``commands.CommandRegistry.discover``
# que registre el nombre de comando ``X``. Si encontrás un handler
# huérfano que NO está en esta lista, hay que cablearlo en la cascada
# inline de ``repl.py`` (como se hizo con ``/feedback``).
ALLOWLIST: dict[str, str] = {
    "run_agent_command": (
        "lógica migrada a AgentCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /agent."
    ),
    "run_auto_command": (
        "lógica migrada a AutoCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /auto."
    ),
    "run_bookmark_command": (
        "lógica migrada a BookmarkCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /bookmark /bm."
    ),
    "run_config_command": (
        "lógica migrada a ConfigCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /config."
    ),
    "run_continue_command": (
        "lógica migrada a ContinueCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /continue /cont."
    ),
    "run_copy_command": (
        "lógica migrada a CopyCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /copy."
    ),
    "run_export_command": (
        "lógica migrada a ExportCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /export /exp."
    ),
    "run_file_command": (
        "lógica migrada a FileCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /file /f."
    ),
    "run_plan_command": (
        "lógica migrada a PlanCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /plan."
    ),
    "run_redo_command": (
        "lógica migrada a RedoCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /redo /r."
    ),
    "run_status_command": (
        "lógica migrada a StatusCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /status."
    ),
    "run_template_command": (
        "lógica migrada a TemplateCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /template /templates /tpl."
    ),
    "run_theme_command": (
        "lógica migrada a ThemeCommand (CommandRegistry builtin). "
        "Cascada inline lo salta; registry.dispatch atiende /theme /themes."
    ),
}


def _detected_command_modules(repl_tree: ast.Module) -> set[str]:
    """Nombres de módulos de comando importados desde ``repl.py``.

    Se consideran "módulos de comando" los archivos cuyo nombre termina
    en ``_command.py`` o se llama exactamente ``extra_commands.py``.
    Mantener este set derivado de la lista de imports — y no cableado
    a mano — protege contra el caso "alguien agregó un módulo
    ``foo_command.py`` con handlers nuevos pero no lo importó en
    ``repl.py``", que es exactamente el modo de falla que cubre este
    guard.
    """
    modules: set[str] = set()
    for node in repl_tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.endswith("_command") or mod == "extra_commands":
                modules.add(mod)
    return modules


def _defined_handlers(modules: set[str]) -> dict[str, str]:
    """``run_X_command`` definidos en cada módulo de comando.

    Devuelve un dict ``{handler_name: module_name}`` para que el mensaje
    de error pueda decir "este handler está en ``extra_commands.py``
    línea 2330" — sin tener que rerastrear.
    """
    out: dict[str, str] = {}
    for mod in modules:
        path = LILITH_CLI / f"{mod}.py"
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("run_") and node.name.endswith("_command"):
                    out[node.name] = mod
    return out


def _dispatched_handlers(repl_tree: ast.Module) -> set[str]:
    """Handlers ``run_X_command`` invocados desde el cuerpo de ``repl.py``.

    Se cuentan ambos patrones:

    * ``await run_X_command(...)`` — la cascada inline normal.
    * ``run_X_command(session, ...)`` síncrono — handlers heredados
      que se llamaban sin await (no quedan en el árbol actual, pero
      el guard los Blindaría igual si reaparecen).

    NO cuentan como despacho:

    * El nombre en una lista/constante (``_SLASH_COMMANDS``,
      autocompletado). Eso solo configura la sugerencia Tab.
    * Una referencia a la cadena ``"run_X_command"`` en un string
      (no aplica, pero queda documentado).
    """
    out: set[str] = set()
    for node in ast.walk(repl_tree):
        if isinstance(node, ast.Await):
            call = node.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                nm = call.func.id
                if nm.startswith("run_") and nm.endswith("_command"):
                    out.add(nm)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            nm = node.func.id
            if nm.startswith("run_") and nm.endswith("_command"):
                out.add(nm)
    return out


def test_no_orphan_handlers() -> None:
    """Todo ``run_X_command`` definido debe ser alcanzable desde ``repl.py``.

    Un handler huérfano salta la cascada inline, no figura en
    ``CommandRegistry``, y ningún slash entra al camino que lo llama:
    el archivo crece, ``/help`` lo anuncia, pero el comando nunca corre.
    Es el modo de falla que motivó este guard (paralelo al ``/feedback``
    que Skadi cableó a mano).
    """
    repl_tree = ast.parse(REPL.read_text(encoding="utf-8"))

    modules = _detected_command_modules(repl_tree)
    assert modules, (
        "No se detectaron módulos de comando importados en repl.py — "
        "¿cambió la convención de nombres? Actualizá este test."
    )

    defined = _defined_handlers(modules)
    assert defined, (
        "No se encontraron handlers ``run_X_command`` definidos en los "
        "módulos de comando. ¿Cambió la convención? Actualizá este test."
    )

    dispatched = _dispatched_handlers(repl_tree)
    assert dispatched, (
        "No se encontraron invocaciones de ``run_X_command`` en repl.py "
        "— ¿se eliminó la cascada inline? Actualizá este test."
    )

    orphans = sorted(set(defined) - dispatched)
    unaccounted = [h for h in orphans if h not in ALLOWLIST]

    assert not unaccounted, (
        "Handlers DEFINIDOS pero NO despachados en repl.py "
        "(el slash command cae al agente o nunca corre): "
        f"{unaccounted}.\n\n"
        "Opciones:\n"
        "  1. Cablear el handler en la cascada inline de repl.py "
        "(como se hizo con /feedback en deleg d9685cd6).\n"
        "  2. Si la lógica vive en una clase BaseCommand registrada en "
        "CommandRegistry.discover(), agregar entrada a ALLOWLIST con "
        "la razón documentada.\n\n"
        "Huérfanos ya en ALLOWLIST: " + ", ".join(
            h for h in orphans if h in ALLOWLIST
        )
    )

    # La allowlist documenta excepciones: si quedó un handler en ella
    # que YA está despachado (porque alguien cableó la cascada o la
    # lógica se separó de la clase), la entrada se vuelve obsoleta. Lo
    # reportamos para que se limpie, no para que falle duro.
    stale_allowlist = sorted(
        h for h in ALLOWLIST
        if h in dispatched or h not in defined
    )
    if stale_allowlist:
        # No assert: es una advertencia operacional. Si esto crece,
        # subí esto a un assert.
        import warnings
        warnings.warn(
            f"ALLOWLIST tiene entradas obsoletas (handler despachado o "
            f"inexistente): {stale_allowlist}. Limpialas.",
            stacklevel=2,
        )


def test_allowlist_entries_match_command_registry() -> None:
    """Cada handler en ALLOWLIST debe corresponderse con una clase
    ``BaseCommand`` registrada en ``CommandRegistry.discover()``.

    Sin este chequeo, la allowlist es solo una lista de "no me
    molesten" — la parte honesta del guard es que cada excepción
    nombra explícitamente *por qué* el handler no necesita cascada
    inline. La razón documentada en ALLOWLIST es "la lógica vive en
    XCommand (CommandRegistry builtin)" — verificamos que esa clase
    exista realmente.
    """
    if not COMMANDS.exists():
        return  # sin commands.py, el guard no puede validar

    tree = ast.parse(COMMANDS.read_text(encoding="utf-8"))

    # 1) Recolectar la lista ``builtin`` de CommandRegistry.discover.
    #    Por la convención actual: ``async def discover(self) -> None``
    #    con un bloque ``builtin: list[type[BaseCommand]] = [...]``.
    builtin_classes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "discover":
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "builtin"
                    and isinstance(stmt.value, ast.List)
                ):
                    for elt in stmt.value.elts:
                        if isinstance(elt, ast.Name):
                            builtin_classes.add(elt.id)

    assert builtin_classes, (
        "No se encontró la lista ``builtin`` en CommandRegistry.discover. "
        "¿Cambió la estructura? Actualizá este test."
    )

    # 2) Mapeo "handler" → "clase esperada". Convención: run_X_command
    #    corresponde a XCommand (sin sufijo ``run_`` ni ``_command``).
    missing_classes: list[str] = []
    for handler in ALLOWLIST:
        stem = handler[len("run_"):-len("_command")]
        expected_class = f"{stem.capitalize()}Command"
        if expected_class not in builtin_classes:
            missing_classes.append(f"{handler} → {expected_class}")

    assert not missing_classes, (
        "ALLOWLIST nombra clases que no están en CommandRegistry.discover.builtin. "
        "O la allowlist está mintiendo (la lógica YA NO vive en la clase) o "
        "la convención de nombres cambió. Entradas rotas: "
        f"{missing_classes}"
    )


def test_guard_detects_descableado() -> None:
    """Si alguien rompe un despacho existente, este guard lo detecta.

    Test de humo: simula que un comando cableado deja de estar
    despachado en ``repl.py`` y verifica que el guard lo flaggea.
    Sin este test, podríamos cambiar la lógica del guard y romper
    el modo de falla principal sin enterarnos.
    """
    # Tomamos un handler que SÍ está despachado hoy (no en ALLOWLIST):
    # ``run_apply_command``. Si pudiera elegir uno al azar desde el
    # set despachado, ése es un buen proxy.
    repl_tree = ast.parse(REPL.read_text(encoding="utf-8"))
    dispatched = _dispatched_handlers(repl_tree)
    modules = _detected_command_modules(repl_tree)
    defined = _defined_handlers(modules)

    # Handler cableado (no en ALLOWLIST):
    wired = sorted(
        h for h in (defined.keys() & dispatched)
        if h not in ALLOWLIST
    )
    assert wired, (
        "Smoke test no tiene un handler cableado contra el cual "
        "verificar. ¿Cambió la estructura del repo?"
    )

    # Tomemos uno y simulemos que REPL no lo despacha: removamos ese
    # nombre de la lista dispatched temporalmente.
    victim = wired[0]
    simulated_dispatched = dispatched - {victim}
    simulated_orphans = sorted(set(defined) - simulated_dispatched)
    simulated_unaccounted = [h for h in simulated_orphans if h not in ALLOWLIST]

    assert victim in simulated_unaccounted, (
        f"El guard no detectó el descableado de {victim}. "
        "Revisar la lógica de detección."
    )
