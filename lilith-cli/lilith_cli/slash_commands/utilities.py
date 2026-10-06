"""Bundled command plugin: small utilities that are not part of coding work.

/calc, /uuid, /hash, /base64, /epoch, /now, /random, /quote, /reverse,
/lines, /qr, /timer and /voice. :mod:`lilith_cli.slash_router` loads this
bundle unless ``utilities`` is listed in ``LILITH_DISABLED_COMMAND_PLUGINS``.
"""

from __future__ import annotations

from ..slash_router import SlashRoute
from ..utility_commands import (
    run_base64_command,
    run_calc_command,
    run_epoch_command,
    run_hash_command,
    run_lines_command,
    run_now_command,
    run_quote_command,
    run_random_command,
    run_reverse_command,
    run_uuid_command,
)
from .extras import run_qr_command, run_timer_command, run_voice_command

ROUTES: tuple[SlashRoute, ...] = (
    SlashRoute("calc", run_calc_command),
    SlashRoute("uuid", run_uuid_command),
    SlashRoute("hash", run_hash_command),
    SlashRoute("base64", run_base64_command),
    SlashRoute("epoch", run_epoch_command),
    SlashRoute("now", run_now_command),
    SlashRoute("random", run_random_command),
    SlashRoute("quote", run_quote_command),
    SlashRoute("reverse", run_reverse_command, ("rev",)),
    SlashRoute("lines", run_lines_command),
    SlashRoute("qr", run_qr_command),
    SlashRoute("timer", run_timer_command),
    SlashRoute("voice", run_voice_command),
)
