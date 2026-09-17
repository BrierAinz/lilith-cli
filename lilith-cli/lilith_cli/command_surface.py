"""Discovery metadata shared by help, completion and spelling suggestions.

Routing remains in the REPL and CommandRegistry: aliases retain their existing
handlers, including legacy handlers that differ from their long spelling.
"""

from __future__ import annotations

from difflib import get_close_matches

from rich.markup import escape

# REPL routes take precedence over registry aliases.
REPL_ALIASES = {
    "h": "help", "?": "help", "diffstaged": "diff-staged",
    "diffunstaged": "diff-unstaged", "diffbranch": "diff-branch",
    "s": "search", "undo-diff": "undo-peek", "peeks": "undo-peek",
    "w": "watch", "rev": "reverse", "p": "pin",
    "cls": "clear-screen", "temp": "temperature", "notes": "note",
    "note-add": "note", "aside": "btw", "side": "btw",
    "cap": "capture", "l": "log", "sec": "security-review",
    "tr": "transcript",
}


def aliases() -> dict[str, str]:
    from .commands import CommandRegistry

    registry = CommandRegistry(None)
    registry.discover()
    result = {a: c for a, c in registry._aliases.items() if a != c}
    result.update(REPL_ALIASES)
    return result


def completion_words(words: list[str]) -> list[str]:
    """Keep every accepted spelling, with canonical names before aliases."""
    mapping = aliases()
    return sorted(set(words) | {f"/{a}" for a in mapping},
                  key=lambda word: (word[1:] in mapping, word))


def unknown_command(name: str) -> str:
    from .repl import _SLASH_COMMANDS

    mapping = aliases()
    names = {word[1:] for word in completion_words(_SLASH_COMMANDS)}
    candidates = get_close_matches(name, sorted(names), n=3, cutoff=0.6)
    suggestions = list(dict.fromkeys(mapping.get(n, n) for n in candidates))
    hint = ""
    if suggestions:
        hint = " ¿Quisiste decir " + ", ".join(f"/{n}" for n in suggestions) + "?"
    return (f"Comando desconocido: /{escape(name)}.{hint} "
            "Usa /help <familia> o /commands <texto> para buscar.")


def render_help(catalog: dict[str, list[tuple[str, str]]], args: str) -> None:
    from .commands import CommandRegistry
    from .render import console

    mapping = aliases()
    registry = CommandRegistry(None)
    registry.discover()
    entries: dict[str, tuple[str, str]] = {}
    for family, commands in catalog.items():
        for name, description in commands:
            canonical = mapping.get(name, name)
            if canonical not in entries or canonical == name:
                entries[canonical] = (family, description)
    for command in registry.list_commands():
        entries.setdefault(command.name, ("Configuration" if command.name == "agent"
                                           else "Session", command.description))

    # Fail closed on discovery drift: every accepted REPL spelling must be
    # represented, even before someone adds a curated description to /help.
    from .repl import _SLASH_COMMANDS

    for word in _SLASH_COMMANDS:
        name = word[1:]
        canonical = mapping.get(name, name)
        entries.setdefault(canonical, ("Other", "Comando disponible"))

    # These catalog descriptions previously contradicted the active handlers.
    entries["agent"] = ("Configuration", "Modo del agente [list | nombre]")
    entries["status"] = ("Information", "Estado del ecosistema y proveedor/modelo de sesión")
    entries["mission"] = ("Orchestration", "Estado local de Mission Kernel, corte y recursos de cómputo")
    entries["court"] = ("Orchestration", "Corte canónica de Lilith, health de compute y aprendizaje")
    entries["clear-screen"] = ("Session", "Limpiar terminal sin borrar historial")
    entries["resume"] = ("Session", "Restaurar una conversación guardada")
    entries["continue"] = ("Session", "Pedir al agente que continúe su respuesta anterior")
    entries["redo"] = ("Session", "Reenviar el último mensaje al modelo; no rehace archivos")
    entries["diff"] = ("Files & Git", "Previsualizar escritura/edición [write | edit], sin aplicar")

    query = args.strip().lower().lstrip("/")
    if not query:
        console.print("[bold]Comandos de Lilith[/] — empieza por una tarea")
        console.print("Session: /help (/h, /?) · /status · /quit (/q, /exit)")
        console.print("Conversación: /retry · /undo · /compact · /resume")
        console.print("Configuration: /model · /provider · /tools · /agent")
        console.print("Development: /plan · /file (/f) · /test · /review")
        console.print("Files & Git: /git · /diff-staged · /diff-unstaged · /apply")
        console.print("Information: /cost (/c) sesión · /costs delegaciones")
        console.print("Orquestación: /mission · /court · /state · /doctor")
        console.print("Atajos: /memory (/m) · /usage (/u) · /redo (/r)")
        console.print("Atajos: /search (/s) · /watch (/w) · /pin (/p) · /log (/l)")
        console.print("Limpiar: /clear borra historial · /clear-screen (/cls) terminal")
        console.print("Más familias: Utilities · Environment · System · Help")
        console.print("/help <familia> o /help diff: buscar nombres, alias y descripciones")
        console.print("/commands: catálogo completo con alias · /how <comando>: uso")
        return

    if query == "all":
        query = ""
    matches = []
    for name, (family, description) in entries.items():
        alternate = sorted(a for a, canonical in mapping.items() if canonical == name)
        haystack = " ".join([name, family, description, *alternate]).lower()
        if not query or query in haystack:
            matches.append((family, name, description, alternate))
    if not matches:
        console.print(f"Ningún comando coincide con '{escape(args.strip())}'. "
                      "Familias disponibles: " + ", ".join(catalog) +
                      ". Usa /commands para ver el catálogo.")
        return
    console.print("[bold]Comandos de Lilith[/]")
    console.print(f"{len(matches)} comandos en {len({m[0] for m in matches})} categorías; "
                  "alias entre paréntesis")
    current_family = None
    for family, name, description, alternate in sorted(matches):
        if family != current_family:
            console.print(f"[bold]{family}[/]")
            current_family = family
        alias_text = " (alias: " + ", ".join(f"/{a}" for a in alternate) + ")" if alternate else ""
        console.print(f"  /{name}{alias_text} — {escape(description)}")
