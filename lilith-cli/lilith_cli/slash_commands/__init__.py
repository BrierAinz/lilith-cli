"""Slash-command handlers for the Lilith REPL, grouped by domain.


Each module exposes ``run_<name>_command(session, args)`` coroutines; the
REPL routes ``/<name>`` to them through :mod:`lilith_cli.slash_router`.
"""
