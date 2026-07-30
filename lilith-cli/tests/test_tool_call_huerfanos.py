"""Reparación del pairing tool_call↔tool_result en historiales truncados.

El truncamiento por ``max_turns`` en ``_build_messages`` corta por cantidad de
mensajes sin respetar los grupos ``assistant(tool_calls) → tool_results``. Eso
dejaba (a) tool results sin su assistant o (b) assistants con grupos de tools
incompletos, y el proveedor (k3) rechazaba el request con
``tool_call_id is not found`` (400) → la sesión interactiva crasheaba.
"""

from lilith_cli.agent import _drop_orphan_tool_messages


def _assistant(ids, content=""):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "x", "arguments": "{}"}}
            for i in ids
        ],
    }


def _tool(tid):
    return {"role": "tool", "tool_call_id": tid, "content": "resultado", "name": "x"}


def test_descarta_tool_result_huerfano_al_inicio():
    # Escenario real: el truncamiento dejó un tool result como primer mensaje,
    # sin el assistant que lo declaró.
    h = [_tool("a"), {"role": "user", "content": "hola"}, _assistant(["b"]), _tool("b")]

    out = _drop_orphan_tool_messages(h)

    assert _tool("a") not in out  # huérfano descartado
    assert _assistant(["b"]) in out  # grupo completo intacto
    assert _tool("b") in out


def test_grupo_incompleto_se_degrada_y_descarta_results_parciales():
    # assistant con 2 tool_calls pero solo 1 result → grupo incompleto.
    h = [_assistant(["a", "b"], content="analizando"), _tool("a")]

    out = _drop_orphan_tool_messages(h)

    # El assistant se degrada a mensaje sin tool_calls (conserva el texto)...
    assert {"role": "assistant", "content": "analizando"} in out
    # ...y su result parcial se descarta (quedaría huérfano si no).
    assert _tool("a") not in out
    # Ningún tool message sobrevive.
    assert all(m.get("role") != "tool" for m in out)


def test_grupo_incompleto_sin_content_se_omite():
    h = [{"role": "user", "content": "x"}, _assistant(["a"]), _tool("a"), _tool("b_huerfano_no_declarado")]
    # (a completo; el segundo tool no está declarado por ningún assistant)
    out = _drop_orphan_tool_messages(h)
    assert _tool("a") in out
    assert not any(m.get("tool_call_id") == "b_huerfano_no_declarado" for m in out)


def test_historial_bien_formado_no_cambia():
    h = [
        {"role": "user", "content": "x"},
        _assistant(["a"]),
        _tool("a"),
        {"role": "assistant", "content": "ok"},
    ]
    assert _drop_orphan_tool_messages(h) == h


def test_payload_resultante_es_valido_para_la_api():
    # Invariante: en la salida, todo tool message tiene un assistant previo que
    # declara su tool_call_id (lo que la API exige).
    h = [_tool("x"), _assistant(["a", "b"]), _tool("a"), _tool("b"), _tool("z")]
    out = _drop_orphan_tool_messages(h)

    declarados = set()
    for m in out:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            declarados.update(tc["id"] for tc in m["tool_calls"])
        elif m.get("role") == "tool":
            assert m["tool_call_id"] in declarados, "tool result sin assistant previo"
