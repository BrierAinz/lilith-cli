"""Validate saved usage before changing a live session."""

from __future__ import annotations

import math

TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def validated_usage(total: object, models: object) -> tuple[dict, dict]:
    if not isinstance(total, dict) or not isinstance(models, dict):
        raise ValueError("Contadores de uso inválidos.")  # noqa: TRY004 - snapshot validation contract

    def tokens(source):
        if not isinstance(source, dict):
            raise ValueError("Uso por modelo inválido.")  # noqa: TRY004 - snapshot validation contract
        result = {key: source.get(key, 0) for key in TOKEN_KEYS}
        if any(type(value) is not int or value < 0 for value in result.values()):
            raise ValueError("Los tokens deben ser enteros no negativos.")
        return result

    totals = tokens(total)
    per_model = {}
    for model, source in models.items():
        if not isinstance(model, str) or not model:
            raise ValueError("Identificador de modelo inválido.")
        entry = tokens(source)
        cost = source.get("cost", 0.0)
        if (
            type(cost) not in (int, float)
            or cost < 0
            or cost > 1e308
            or not math.isfinite(cost)
        ):
            raise ValueError("Costo de uso inválido.")
        entry["cost"] = cost
        per_model[model] = entry
    return totals, per_model


def merged_usage(current_total, current_models, saved_total, saved_models):
    total, models = validated_usage(current_total, current_models)
    incoming, incoming_models = validated_usage(saved_total, saved_models)
    for key in TOKEN_KEYS:
        total[key] += incoming[key]
    for model, entry in incoming_models.items():
        target = models.setdefault(model, {**dict.fromkeys(TOKEN_KEYS, 0), "cost": 0.0})
        for key in (*TOKEN_KEYS, "cost"):
            target[key] += entry[key]
        if target["cost"] > 1e308 or not math.isfinite(target["cost"]):
            raise ValueError("Costo acumulado fuera de rango.")
    return total, models
