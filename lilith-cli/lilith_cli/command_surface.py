"""Discovery metadata shared by help, completion and spelling suggestions.

Names come from :mod:`lilith_cli.slash_router` (REPL routes and command
plugins) and :class:`~lilith_cli.commands.CommandRegistry`. Routes take
precedence over registry aliases, so ``/rev`` stays ``/reverse``.
"""

from __future__ import annotations

from difflib import get_close_matches

from rich.markup import escape


def aliases() -> dict[str, str]:
    from .commands import CommandRegistry
    from .slash_router import route_aliases

    registry = CommandRegistry(None)
    registry.discover()
    result = {a: c for a, c in registry._aliases.items() if a != c}
    result.update(route_aliases())
    return result


def completion_words(words: list[str]) -> list[str]:
    """Keep every accepted spelling, with canonical names before aliases."""
    mapping = aliases()
    return sorted(set(words) | {f"/{a}" for a in mapping},
                  key=lambda word: (word[1:] in mapping, word))


def unknown_command(name: str) -> str:
    from .slash_router import slash_commands

    mapping = aliases()
    names = {word[1:] for word in completion_words(slash_commands())}
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
    # Catalog entries for commands that are not routable (a disabled command
    # plugin) are dropped.
    from .slash_router import slash_commands

    accepted = slash_commands()
    routable = {mapping.get(word[1:], word[1:]) for word in accepted}
    entries = {name: entry for name, entry in entries.items() if name in routable}
    for word in accepted:
        name = word[1:]
        canonical = mapping.get(name, name)
        entries.setdefault(canonical, ("Other", "Comando disponible"))

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
