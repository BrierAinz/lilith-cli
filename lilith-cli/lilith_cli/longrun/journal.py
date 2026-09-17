"""Bitacora append-only de una guardia larga.

El historial del agente se recorta por presupuesto de caracteres
(``agent._trim_history_to_budget``) y los mensajes mas viejos se tiran con
``pop(0)`` **sin dejar rastro**. En una guardia de mas de 24 horas eso borra en
silencio el objetivo, el criterio de fin y todo lo aprendido al principio.

Este modulo es el unico sitio donde esa memoria persiste: un JSONL append-only
en disco, con ``fsync`` por entrada (para que una caida no pierda la ultima),
un ``seq`` monotono deducido del propio fichero (para que sobreviva a que el
proceso muera y se relance) y un ``digest`` acotado que se reinyecta como
mensaje de sistema en cada turno.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact, redact_text

KINDS = (
    "goal",
    "finding",
    "decision",
    "failure",
    "verification",
    "escalation",
    "compaction",
    "leg",
)

# Primeros N caracteres de cada resultado de herramienta que se absorbe.
EXCERPT_CHARS = 200

_ELLIPSIS = " [...]"
_GOAL_PREFIX = "OBJETIVO: "


# -- Secretos --------------------------------------------------------
#
# Policy rule: se informa ruta y tipo,
# nunca el valor. La bitacora absorbe resultados de herramientas enteros, asi
# que es justo el sitio por donde una clave entraria al disco.
#
# La redaccion vive en ``longrun/redaction.py`` y es la MISMA que usa la capa
# de evidencia. Antes habia una aqui, propia y mas debil: solo miraba el texto
# de cada cadena, nunca el nombre de la clave del diccionario, asi que
# ``append(..., evidence={"api_key": "AKIA..."})`` escribia la clave entera en
# claro. Una sola regla dura no puede tener dos implementaciones, porque la que
# manda acaba siendo la floja.


def _now_z() -> str:
    """Instante actual en ISO 8601 UTC con sufijo Z."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _truncate(text: str, limit: int) -> str:
    """Recorta ``text`` a ``limit`` caracteres marcando el corte."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(_ELLIPSIS):
        return text[:limit]
    return text[: limit - len(_ELLIPSIS)] + _ELLIPSIS


def _text_of(entry: dict) -> str:
    value = entry.get("text")
    return value if isinstance(value, str) else str(value)


def _seq_of(entry: dict) -> int:
    value = entry.get("seq")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _render(entry: dict) -> str:
    return f"[{entry.get('kind')}] {_text_of(entry)}"


class Journal:
    """Bitacora JSONL append-only de una guardia.

    ``corrupt_lines`` cuenta las lineas que la ultima lectura tuvo que
    descartar: un fichero append-only puede quedar truncado a mitad de linea
    por una caida, y eso no debe romper la lectura ni pasar inadvertido.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.corrupt_lines = 0

    # -- Escritura ---------------------------------------------------

    def append(self, kind: str, text: str, *, evidence: dict | None = None) -> dict:
        """Anade una entrada y la sincroniza a disco. Devuelve la entrada."""
        if kind not in KINDS:
            raise ValueError(
                f"tipo de entrada desconocido: {kind!r}. "
                f"Los tipos validos son: {', '.join(KINDS)}."
            )

        entry = {
            "at": _now_z(),
            "kind": kind,
            "text": redact_text(text if isinstance(text, str) else str(text)),
            "evidence": redact(evidence) if isinstance(evidence, dict) else None,
            "seq": self._next_seq(),
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            # Una guardia larga se cae a mitad; sin fsync la ultima entrada
            # (justo la que explica por que se cayo) se queda en el cache.
            os.fsync(handle.fileno())
        return entry

    # -- Lectura -----------------------------------------------------

    def entries(self, *, kind: str | None = None) -> list[dict]:
        """Devuelve las entradas en orden de escritura, opcionalmente filtradas."""
        loaded, corrupt = self._load()
        self.corrupt_lines = corrupt
        if kind is None:
            return loaded
        return [entry for entry in loaded if entry.get("kind") == kind]

    def _load(self) -> tuple[list[dict], int]:
        loaded: list[dict] = []
        corrupt = 0
        if not self.path.exists():
            return loaded, corrupt
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    # Una linea en blanco no es perdida de informacion; no se
                    # cuenta como corrupta.
                    continue
                try:
                    parsed = json.loads(line)
                except ValueError:
                    corrupt += 1
                    continue
                if not isinstance(parsed, dict) or "kind" not in parsed:
                    corrupt += 1
                    continue
                loaded.append(parsed)
        return loaded, corrupt

    def _next_seq(self) -> int:
        """Siguiente ``seq``, deducido del fichero y no de un contador vivo.

        El proceso muere y se relanza: ese es todo el punto de la pieza.
        """
        loaded, _ = self._load()
        highest = 0
        for entry in loaded:
            highest = max(highest, _seq_of(entry))
        return highest + 1

    # -- Digest ------------------------------------------------------

    def digest(self, max_chars: int) -> str:
        """Texto acotado que se reinyecta como mensaje de sistema cada turno.

        Nunca omite el objetivo (lo trunca antes) y nunca devuelve mas de
        ``max_chars`` caracteres. Nunca lanza por presupuesto corto.
        """
        if max_chars <= 0:
            return ""

        loaded = self.entries()
        goals = [entry for entry in loaded if entry.get("kind") == "goal"]

        lines: list[str] = []
        used = 0
        goal_entry = goals[-1] if goals else None
        if goal_entry is not None:
            goal_line = _GOAL_PREFIX + _text_of(goal_entry)
            if len(goal_line) > max_chars:
                # Cabe el objetivo truncado y nada mas: preferimos un objetivo a
                # medias antes que una guardia que no sepa que esta haciendo.
                return _truncate(goal_line, max_chars)
            lines.append(goal_line)
            used = len(goal_line)

        for entry in self._by_priority(loaded, goal_entry):
            line = _render(entry)
            cost = len(line) + (1 if lines else 0)
            if used + cost > max_chars:
                continue
            lines.append(line)
            used += cost

        return "\n".join(lines)

    def _by_priority(self, loaded: list[dict], goal_entry: dict | None) -> list[dict]:
        """Ordena las entradas no-objetivo de mas a menos conservable."""
        last_verification = max(
            (_seq_of(e) for e in loaded if e.get("kind") == "verification"),
            default=None,
        )

        def is_open_failure(entry: dict) -> bool:
            if entry.get("kind") != "failure":
                return False
            if last_verification is None:
                return True
            return _seq_of(entry) > last_verification

        rest = [entry for entry in loaded if entry is not goal_entry]
        newest_first = sorted(rest, key=_seq_of, reverse=True)

        tier1 = [
            entry
            for entry in newest_first
            if entry.get("kind") == "escalation" or is_open_failure(entry)
        ]
        tier2 = [entry for entry in newest_first if entry.get("kind") == "decision"]
        tier3 = [entry for entry in newest_first if entry.get("kind") == "finding"]
        chosen = {id(entry) for entry in tier1 + tier2 + tier3}
        tier4 = [entry for entry in newest_first if id(entry) not in chosen]
        return tier1 + tier2 + tier3 + tier4

    # -- Compactacion ------------------------------------------------

    def compact_storage(
        self, *, max_entries: int = 500, keep_recent: int = 100
    ) -> dict:
        """Archive the full JSONL, then atomically shrink the active journal."""
        loaded = self.entries()
        if len(loaded) <= max(1, int(max_entries)):
            return {"compacted": False, "reason": "below_threshold", "entries": len(loaded)}
        if self.corrupt_lines:
            return {
                "compacted": False,
                "reason": "corrupt_lines_present",
                "corrupt_lines": self.corrupt_lines,
            }
        raw = self.path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_dir = self.path.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive = archive_dir / f"journal-{stamp}-{digest[:12]}.jsonl"
        if not archive.exists():
            with archive.open("wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())

        last_verification = max(
            (_seq_of(row) for row in loaded if row.get("kind") == "verification"),
            default=0,
        )
        preserve = {id(row) for row in loaded[-max(1, int(keep_recent)):]}
        goals = [row for row in loaded if row.get("kind") == "goal"]
        if goals:
            preserve.add(id(goals[-1]))
        for row in loaded:
            kind = row.get("kind")
            if kind == "escalation":
                preserve.add(id(row))
            elif kind == "failure" and _seq_of(row) > last_verification:
                preserve.add(id(row))
        verifications = [row for row in loaded if row.get("kind") == "verification"]
        if verifications:
            preserve.add(id(verifications[-1]))
        decisions = [row for row in loaded if row.get("kind") == "decision"]
        for row in decisions[-50:]:
            preserve.add(id(row))
        retained = sorted(
            (row for row in loaded if id(row) in preserve), key=_seq_of
        )
        summary = {
            "at": _now_z(),
            "kind": "compaction",
            "text": (
                f"Storage compaction archived {len(loaded)} entries and retained "
                f"{len(retained)} active entries."
            ),
            "evidence": {
                "archive": str(archive),
                "sha256": digest,
                "original_entries": len(loaded),
                "retained_entries": len(retained),
            },
            "seq": max((_seq_of(row) for row in loaded), default=0) + 1,
        }
        temp = self.path.with_suffix(self.path.suffix + ".compact.tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            for row in [*retained, summary]:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        return {
            "compacted": True,
            "archive": str(archive),
            "sha256": digest,
            "original_entries": len(loaded),
            "retained_entries": len(retained) + 1,
        }
    # -- Compactacion ------------------------------------------------

    def absorb_dropped(self, messages: list[dict]) -> dict | None:
        """Deja rastro de los mensajes que el recorte esta a punto de tirar.

        Devuelve la entrada ``compaction`` escrita, o ``None`` si no habia nada
        que descartar. Nada se descarta sin dejar rastro: esa es toda su razon
        de ser.
        """
        if not messages:
            return None

        by_role: dict[str, int] = {}
        names: list[str] = []
        id_to_name: dict[str, str] = {}

        def remember(name: Any) -> str | None:
            if not isinstance(name, str) or not name:
                return None
            if name not in names:
                names.append(name)
            return name

        for message in messages:
            if not isinstance(message, dict):
                by_role["desconocido"] = by_role.get("desconocido", 0) + 1
                continue
            role = message.get("role")
            role = role if isinstance(role, str) and role else "desconocido"
            by_role[role] = by_role.get(role, 0) + 1

            remember(message.get("name"))
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                raw_name = (
                    function.get("name") if isinstance(function, dict) else None
                ) or call.get("name")
                name = remember(raw_name)
                call_id = call.get("id")
                if name and isinstance(call_id, str):
                    id_to_name[call_id] = name

        results: list[dict] = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            call_id = message.get("tool_call_id")
            name = message.get("name")
            if not isinstance(name, str) or not name:
                # Un mensaje de rol "tool" no lleva nombre en este CLI: se
                # resuelve por el tool_call_id que declaro el assistant.
                name = id_to_name.get(call_id) if isinstance(call_id, str) else None
            remember(name)
            results.append(
                {
                    "herramienta": name or "desconocida",
                    "tool_call_id": call_id if isinstance(call_id, str) else None,
                    "extracto": content[:EXCERPT_CHARS],
                }
            )

        recount = ", ".join(f"{role} {count}" for role, count in by_role.items())
        text = f"Recorte de contexto: {len(messages)} mensajes descartados ({recount})."
        if names:
            text += f" Herramientas: {', '.join(names)}."

        evidence = {
            "mensajes": len(messages),
            "por_rol": by_role,
            "herramientas": list(names),
            "resultados": results,
        }
        return self.append("compaction", text, evidence=evidence)
