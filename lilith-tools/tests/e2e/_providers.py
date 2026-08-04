"""Helper: per-preset env-key map. Kept as a module so future e2e
suites can import it without inheriting pytest's conftest semantics.

Adding a new provider/preset? Add one row to ``keys_by_preset``.
"""

from __future__ import annotations

import os

# Each preset may require one or more API keys; all must be present.
keys_by_preset: dict[str, tuple[str, ...]] = {
    "ejecutor-kimi": ("KIMI_API_KEY",),
    "investigador-minimax": ("MINIMAX_API_KEY",),
    "batch-deepseek": ("DEEPSEEK_API_KEY",),
    "orquestador-fugu": ("SAKANA_API_KEY",),
    "opencode-glm52": ("OPENCODE_API_KEY",),
    "grok-research": ("XAI_API_KEY",),
    "hf-glm52": ("HF_TOKEN",),
}


def missing_keys_for(*preset_names: str) -> list[str]:
    """Return the env keys (sorted, unique) that are unset for any of ``preset_names``."""
    seen: set[str] = set()
    missing: list[str] = []
    for name in preset_names:
        for var in keys_by_preset.get(name, ()):
            if var not in seen:
                seen.add(var)
                if not os.environ.get(var):
                    missing.append(var)
    return sorted(missing)
