"""Tests del tool forja_generate (integración Forja↔Yggdrasil, lado Yggdrasil)."""

from unittest.mock import Mock

import pytest
import requests
from lilith_tools import forja_tools
from lilith_tools.forja_tools import (
    ForjaCharactersTool,
    ForjaControlnetsTool,
    ForjaDesignBatchStatusTool,
    ForjaDesignBatchTool,
    ForjaGenerateTool,
    ForjaPromoteDesignTool,
)
from lilith_tools.registry import ToolRegistry


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("sin json")
        return self._payload


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("FORJA_AGENT_TOKEN", "token-test")
    monkeypatch.delenv("FORJA_API_URL", raising=False)


def test_registrado_en_el_registry():
    assert "forja_generate" in ToolRegistry.list_tools()


def test_design_batch_registrado_en_el_registry():
    tools = ToolRegistry.list_tools()
    assert "forja_design_batch" in tools
    assert "forja_design_batch_status" in tools
    assert "forja_promote_design" in tools


def test_design_batch_una_orden_espera_y_devuelve_finales_absolutos(
    with_token, monkeypatch
):
    captured: dict = {}
    states = iter(
        [
            {"run_id": "run-1", "status": "running", "requested": 2, "candidates": []},
            {
                "run_id": "run-1",
                "status": "completed",
                "requested": 2,
                "completed": 2,
                "review_board_url": "/api/v1/pipelines/runs/run-1/artifacts/board",
                "manifest_url": "/api/v1/pipelines/runs/run-1/artifacts/manifest",
                "candidates": [
                    {
                        "index": 1,
                        "final_url": "/api/v1/pipelines/runs/run-1/artifacts/final-1",
                        "source_url": "/api/v1/pipelines/runs/run-1/artifacts/source-1",
                    },
                    {
                        "index": 2,
                        "final_url": "/api/v1/pipelines/runs/run-1/artifacts/final-2",
                        "source_url": "/api/v1/pipelines/runs/run-1/artifacts/source-2",
                    },
                ],
            },
        ]
    )

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(
            202,
            {
                "run_id": "run-1",
                "status": "queued",
                "status_url": "/api/v1/agent/design-batches/run-1",
                "deduplicated": False,
            },
        )

    def fake_get(url, headers=None, timeout=None):
        captured["get_url"] = url
        return _FakeResponse(200, next(states))

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)
    monkeypatch.setattr(forja_tools.requests, "get", fake_get)
    monkeypatch.setattr(forja_tools.time, "sleep", lambda _seconds: None)

    result = ForjaDesignBatchTool().execute(
        brief="original frost wolf apparel graphic",
        count=2,
        product="black t-shirt",
        directions="bold crest\nminimal sigil",
    )

    assert result.success
    assert captured["url"].endswith("/api/v1/agent/design-batches")
    assert captured["json"]["directions"] == ["bold crest", "minimal sigil"]
    assert captured["json"]["count"] == 2
    assert captured["get_url"].endswith("/api/v1/agent/design-batches/run-1")
    assert result.data["status"] == "completed"
    assert result.data["candidates"][0]["final_url"].startswith("http://127.0.0.1:8000/")
    assert result.data["review_board_url"].startswith("http://127.0.0.1:8000/")


def test_design_batch_sin_espera_devuelve_run_persistente(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            202,
            {
                "run_id": "run-2",
                "status": "queued",
                "status_url": "/api/v1/agent/design-batches/run-2",
                "deduplicated": False,
            },
        ),
    )
    result = ForjaDesignBatchTool().execute(brief="raven crest", wait=False)
    assert result.success
    assert result.data["run_id"] == "run-2"
    assert result.data["status_url"] == (
        "http://127.0.0.1:8000/api/v1/agent/design-batches/run-2"
    )


def test_design_batch_status_recupera_lote_sin_esperar(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            200,
            {"run_id": "run-3", "status": "running", "requested": 10, "candidates": []},
        ),
    )
    result = ForjaDesignBatchStatusTool().execute(run_id="run-3", wait=False)
    assert result.success
    assert result.data["status"] == "running"
    assert result.data["status_url"].endswith("/api/v1/agent/design-batches/run-3")


def test_design_batch_rechaza_respuesta_de_creacion_no_json(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(202, None, text="<html>gateway</html>"),
    )

    result = ForjaDesignBatchTool().execute(brief="raven crest", wait=False)

    assert not result.success
    assert result.data is None
    assert "no JSON" in result.error


def test_design_batch_status_rechaza_respuesta_no_json(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(200, None, text="upstream vacio"),
    )

    result = ForjaDesignBatchStatusTool().execute(run_id="run-4", wait=False)

    assert not result.success
    assert result.data is None
    assert "no JSON" in result.error


def test_promote_design_prepara_atelier_en_una_llamada(with_token, monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(
            201,
            {
                "deduplicated": False,
                "source": {"candidate_index": 2, "sha256": "b" * 64},
                "design": {"design_id": "FORJA-ABCDEF12-02"},
                "run": {"run_id": "c" * 32, "status": "queued"},
                "approvals": [],
            },
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaPromoteDesignTool().execute(
        run_id="a" * 32,
        candidate_index=2,
        authority="Ainz",
        name="Frost Raven",
    )

    assert result.success
    assert result.data["design"]["design_id"] == "FORJA-ABCDEF12-02"
    assert captured["url"].endswith(f"/{'a' * 32}/candidates/2/promote")
    assert captured["json"] == {"authority": "Ainz", "name": "Frost Raven"}
    assert captured["timeout"] == 60


def test_promote_design_valida_run_e_indice_antes_de_http(with_token, monkeypatch):
    post = Mock()
    monkeypatch.setattr(forja_tools.requests, "post", post)

    bad_run = ForjaPromoteDesignTool().execute(run_id="no", candidate_index=1)
    bad_index = ForjaPromoteDesignTool().execute(run_id="a" * 32, candidate_index=21)

    assert not bad_run.success
    assert not bad_index.success
    post.assert_not_called()


def test_prompt_vacio_es_error(with_token):
    result = ForjaGenerateTool().execute(prompt="   ")
    assert not result.success
    assert "prompt" in result.error


def test_sin_token_explica_el_fail_closed(monkeypatch):
    monkeypatch.delenv("FORJA_AGENT_TOKEN", raising=False)
    result = ForjaGenerateTool().execute(prompt="un lobo de hielo")
    assert not result.success
    assert "FORJA_AGENT_TOKEN" in result.error


def test_generacion_feliz_devuelve_urls_absolutas(with_token, monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse(
            200,
            {
                "prompt_id": "pid-1",
                "images": ["Forja_00042_.png"],
                "image_urls": ["/api/v1/images/Forja_00042_.png"],
                "seed": 1234,
                "elapsed_s": 21.3,
            },
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(prompt="un lobo de hielo", steps=14, timeout_s=60)
    assert result.success
    assert result.data["images"] == ["Forja_00042_.png"]
    assert result.data["image_urls"] == [
        "http://127.0.0.1:8000/api/v1/images/Forja_00042_.png"
    ]
    assert result.data["seed"] == 1234
    assert captured["url"] == "http://127.0.0.1:8000/api/v1/agent/generate"
    assert captured["headers"]["X-Forja-Token"] == "token-test"
    # El campo de AgentGenerateRequest se llama ``timeout``: con ``timeout_s``
    # pydantic lo descartaba en silencio y el servidor usaba su default (600 s)
    # mientras el cliente cortaba a los 90.
    assert captured["json"]["timeout"] == 60
    assert "timeout_s" not in captured["json"]
    # Margen del cliente HTTP sobre el timeout del servidor.
    assert captured["timeout"] == 90


def test_timeout_clampado_viaja_en_el_campo_timeout(with_token, monkeypatch):
    """El clamp a [5, 600] es lo que ve el servidor, no el valor crudo."""
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"json": json, "timeout": timeout})
        return _FakeResponse(200, {"prompt_id": "pid-2", "images": [], "image_urls": []})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    ForjaGenerateTool().execute(prompt="x", timeout_s=99999)
    assert captured["json"]["timeout"] == 600
    assert captured["timeout"] == 630

    ForjaGenerateTool().execute(prompt="x", timeout_s=1)
    assert captured["json"]["timeout"] == 5


def test_characters_registrado_en_el_registry():
    assert "forja_characters" in ToolRegistry.list_tools()


def test_characters_sin_token_es_error_fail_closed(monkeypatch):
    monkeypatch.delenv("FORJA_AGENT_TOKEN", raising=False)
    result = ForjaCharactersTool().execute()
    assert not result.success
    assert "FORJA_AGENT_TOKEN" in result.error


def test_characters_recorta_a_lo_que_el_agente_necesita(with_token, monkeypatch):
    """Se devuelve lo justo para elegir: loras/defaults/negative solo ensucian."""
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers})
        return _FakeResponse(
            200,
            {
                "characters": [
                    {
                        "id": "notehyra",
                        "name": "Notehyra",
                        "description": "Influencer IA",
                        "trigger": "notehyra",
                        "model": "flux.safetensors",
                        "loras": [{"name": "x", "strength": 0.8}],
                        "negative": "blurry",
                        "defaults": {"steps": 20},
                    }
                ]
            },
        )

    monkeypatch.setattr(forja_tools.requests, "get", fake_get)

    result = ForjaCharactersTool().execute()

    assert result.success
    assert result.data == [
        {
            "id": "notehyra",
            "name": "Notehyra",
            "description": "Influencer IA",
            "trigger": "notehyra",
            "model": "flux.safetensors",
        }
    ]
    assert captured["url"] == "http://127.0.0.1:8000/api/v1/characters"
    assert captured["headers"]["X-Forja-Token"] == "token-test"


def test_characters_error_de_conexion_sugiere_levantar_la_forja(with_token, monkeypatch):
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("rechazada")

    monkeypatch.setattr(forja_tools.requests, "get", fake_get)
    result = ForjaCharactersTool().execute()
    assert not result.success
    assert "forja.ps1 start" in result.error


def test_character_viaja_y_no_pisa_los_defaults_del_personaje(with_token, monkeypatch):
    """El id del personaje viaja, y width/height/steps NO se mandan si no se piden.

    La Forja decide qué campos mandó el cliente con ``model_fields_set``, y lo
    explícito le gana al personaje. Si el tool mandara width/height/steps
    siempre —aunque valgan justo el default del servidor— los defaults del
    personaje no se aplicarían NUNCA desde este canal, en silencio.
    """
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return _FakeResponse(200, {"prompt_id": "pid-c", "images": [], "image_urls": []})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    ForjaGenerateTool().execute(prompt="un zorro", character="notehyra")

    assert captured["character"] == "notehyra"
    assert "width" not in captured
    assert "height" not in captured
    assert "steps" not in captured


def test_character_no_pisa_lo_que_se_pide_explicito(with_token, monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return _FakeResponse(200, {"prompt_id": "pid-d", "images": [], "image_urls": []})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    ForjaGenerateTool().execute(
        prompt="un zorro", character="notehyra", width=768, steps=12
    )

    assert captured["width"] == 768
    assert captured["steps"] == 12
    assert "height" not in captured


def test_character_vacio_no_viaja(with_token, monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return _FakeResponse(200, {"prompt_id": "pid-e", "images": [], "image_urls": []})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    ForjaGenerateTool().execute(prompt="un zorro", character="   ")

    assert "character" not in captured


def test_status_no_200_propaga_el_detail(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "post",
        lambda *a, **k: _FakeResponse(504, {"detail": "timeout esperando pid-9"}),
    )
    result = ForjaGenerateTool().execute(prompt="x")
    assert not result.success
    assert "504" in result.error
    assert "pid-9" in result.error


def test_error_de_conexion_sugiere_levantar_la_forja(with_token, monkeypatch):
    def fake_post(*a, **k):
        raise requests.exceptions.ConnectionError("rechazada")

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)
    result = ForjaGenerateTool().execute(prompt="x")
    assert not result.success
    assert "forja.ps1 start" in result.error


# --- forja_controlnets -----------------------------------------------------


def test_controlnets_registrado_en_el_registry():
    assert "forja_controlnets" in ToolRegistry.list_tools()


def test_controlnets_sin_token_es_error_fail_closed(monkeypatch):
    monkeypatch.delenv("FORJA_AGENT_TOKEN", raising=False)
    result = ForjaControlnetsTool().execute()
    assert not result.success
    assert "FORJA_AGENT_TOKEN" in result.error


def test_controlnets_devuelve_lista_normalizada(with_token, monkeypatch):
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _FakeResponse(
            200,
            {
                "controlnets": [
                    {"name": "control_v11p_sd15_canny", "type": "canny"},
                    {"name": "control_v11f1p_sd15_depth", "type": "depth"},
                ]
            },
        )

    monkeypatch.setattr(forja_tools.requests, "get", fake_get)

    result = ForjaControlnetsTool().execute()
    assert result.success
    assert result.data == [
        {"name": "control_v11p_sd15_canny", "type": "canny"},
        {"name": "control_v11f1p_sd15_depth", "type": "depth"},
    ]
    assert captured["url"] == "http://127.0.0.1:8000/api/v1/models/controlnets"
    assert captured["headers"]["X-Forja-Token"] == "token-test"


def test_controlnets_respuesta_vacia_es_lista_vacia(with_token, monkeypatch):
    def fake_get(*a, **k):
        return _FakeResponse(200, {"controlnets": []})

    monkeypatch.setattr(forja_tools.requests, "get", fake_get)
    result = ForjaControlnetsTool().execute()
    assert result.success
    assert result.data == []


def test_controlnets_error_de_conexion_sugiere_levantar_la_forja(with_token, monkeypatch):
    def fake_get(*a, **k):
        raise requests.exceptions.ConnectionError("rechazada")

    monkeypatch.setattr(forja_tools.requests, "get", fake_get)
    result = ForjaControlnetsTool().execute()
    assert not result.success
    assert "forja.ps1 start" in result.error


def test_controlnets_status_no_200_propaga_el_detail(with_token, monkeypatch):
    monkeypatch.setattr(
        forja_tools.requests,
        "get",
        lambda *a, **k: _FakeResponse(503, {"detail": "forja arrancando"}),
    )
    result = ForjaControlnetsTool().execute()
    assert not result.success
    assert "503" in result.error
    assert "forja arrancando" in result.error


# --- forja_generate con ControlNet ----------------------------------------


def _write_png(path, size=(2, 2)) -> None:
    """Escribe un PNG mínimo válido (1 px transparente) en ``path``."""
    import struct
    import zlib

    w, h = size
    # Firma PNG + IHDR + IDAT (un solo pixel transparente) + IEND.
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8bit RGBA
    ihdr_chunk = b"IHDR" + ihdr
    ihdr_crc = zlib.crc32(ihdr_chunk).to_bytes(4, "big")
    ihdr_full = struct.pack(">I", 13) + ihdr_chunk + ihdr_crc
    # raw scanline: filter byte 0 + w*4 bytes (RGBA)
    raw = b"\x00" + b"\x00\x00\x00\x00" * w
    idat_data = zlib.compress(raw * h)
    idat_chunk = b"IDAT" + idat_data
    idat_crc = zlib.crc32(idat_chunk).to_bytes(4, "big")
    idat_full = struct.pack(">I", len(idat_data)) + idat_chunk + idat_crc
    iend_crc = zlib.crc32(b"IEND").to_bytes(4, "big")
    iend_full = struct.pack(">I", 0) + b"IEND" + iend_crc
    path.write_bytes(sig + ihdr_full + idat_full + iend_full)


def test_generate_sin_control_image_no_hace_upload_ni_mete_controls(with_token, monkeypatch):
    """El flujo original (sin ControlNet) no debe romperse: cero requests.upload."""
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured.setdefault("calls", []).append((url, json))
        return _FakeResponse(
            200, {"prompt_id": "pid-x", "images": [], "image_urls": []}
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(prompt="x")
    assert result.success
    # Solo se llamó /agent/generate, NUNCA /upload-control.
    urls = [c[0] for c in captured["calls"]]
    assert urls == ["http://127.0.0.1:8000/api/v1/agent/generate"]
    body = captured["calls"][0][1]
    assert "controls" not in body
    assert "control" not in body


def test_generate_control_image_inexistente_es_error(with_token, tmp_path):
    missing = tmp_path / "no_existe.png"
    result = ForjaGenerateTool().execute(prompt="x", control_image=str(missing))
    assert not result.success
    assert "no existe" in result.error
    assert str(missing) in result.error


def test_generate_control_image_extension_invalida_es_error(with_token, tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    result = ForjaGenerateTool().execute(prompt="x", control_image=str(f))
    assert not result.success
    assert "extensión" in result.error or "imagen" in result.error.lower()


def test_generate_control_image_sube_y_manda_signal_canny(with_token, monkeypatch, tmp_path):
    """control_type='canny' → preprocess='canny' (el server extrae bordes)."""
    img = tmp_path / "ref.png"
    _write_png(img)

    captured: dict = {"posts": []}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        captured["posts"].append({"url": url, "json": json, "files": kwargs.get("files")})
        if "upload-control" in url:
            return _FakeResponse(200, {"image_name": "ctrl-abc"})
        return _FakeResponse(
            200,
            {
                "prompt_id": "pid-cn",
                "images": ["Forja_00001_.png"],
                "image_urls": ["/api/v1/images/Forja_00001_.png"],
            },
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="canny", control_strength=0.7
    )
    assert result.success, result.error
    assert len(captured["posts"]) == 2

    # 1ª llamada: upload-control multipart con campo 'image'.
    upload = captured["posts"][0]
    assert upload["url"] == "http://127.0.0.1:8000/api/v1/generate/upload-control"
    assert upload["json"] is None  # multipart, no body json
    assert upload["files"] is not None
    assert "image" in upload["files"]
    # El field de upload es ('filename', fh, mime).
    field = upload["files"]["image"]
    assert field[0] == "ref.png"
    assert field[2].startswith("image/")

    # 2ª llamada: /agent/generate con controls=[{...}].
    gen = captured["posts"][1]
    assert gen["url"] == "http://127.0.0.1:8000/api/v1/agent/generate"
    controls = gen["json"]["controls"]
    assert controls == [
        {
            "image_name": "ctrl-abc",
            "strength": 0.7,
            "type": "canny",
            "preprocess": "canny",
        }
    ]


def test_generate_control_tipo_no_canny_preprocess_en_null(with_token, monkeypatch, tmp_path):
    """Para depth/openpose/etc, preprocess debe ser null (no 'canny'): el server
    no debe aplicar canny encima de una imagen que YA es un depth map."""
    img = tmp_path / "depth.png"
    _write_png(img)

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        if "upload-control" in url:
            return _FakeResponse(200, {"image_name": "ctrl-depth"})
        captured["gen_body"] = json
        return _FakeResponse(
            200, {"prompt_id": "pid-d", "images": [], "image_urls": []}
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="depth"
    )
    assert result.success, result.error
    ctrl = captured["gen_body"]["controls"][0]
    assert ctrl["type"] == "depth"
    # Explícitamente null: la API entiende null como 'no aplicar preprocess'.
    assert "preprocess" in ctrl and ctrl["preprocess"] is None


def test_generate_sin_control_type_no_manda_preprocess(with_token, monkeypatch, tmp_path):
    """Sin type: el server toma el primer CN del registry. No seteamos preprocess
    a la fuerza para no pisar el comportamiento default previo al feature."""
    img = tmp_path / "u.png"
    _write_png(img)

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        if "upload-control" in url:
            return _FakeResponse(200, {"image_name": "ctrl-x"})
        captured["gen_body"] = json
        return _FakeResponse(
            200, {"prompt_id": "pid-n", "images": [], "image_urls": []}
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(prompt="x", control_image=str(img))
    assert result.success, result.error
    ctrl = captured["gen_body"]["controls"][0]
    assert "type" not in ctrl
    assert "preprocess" not in ctrl


def test_generate_control_strength_se_clampa_a_rango(with_token, monkeypatch, tmp_path):
    img = tmp_path / "u.png"
    _write_png(img)

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        if "upload-control" in url:
            return _FakeResponse(200, {"image_name": "ctrl-x"})
        captured["gen_body"] = json
        return _FakeResponse(
            200, {"prompt_id": "pid-s", "images": [], "image_urls": []}
        )

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    # El rango es el del slider de la Forja (0.1 - 1.5), no [0, 1]: 1.2 es un
    # valor legitimo que el agente tiene que poder pedir.
    ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="canny", control_strength=99.0
    )
    assert captured["gen_body"]["controls"][0]["strength"] == 1.5

    ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="canny", control_strength=-5.0
    )
    assert captured["gen_body"]["controls"][0]["strength"] == 0.1

    ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="canny", control_strength=1.2
    )
    assert captured["gen_body"]["controls"][0]["strength"] == 1.2


def test_generate_upload_falla_propaga_error_sin_generar(with_token, monkeypatch, tmp_path):
    """Si el upload devuelve 422, el tool aborta y NO llama a /agent/generate."""
    img = tmp_path / "u.png"
    _write_png(img)

    calls: list[str] = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _FakeResponse(422, {"detail": "imagen no válida"})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(prompt="x", control_image=str(img))
    assert not result.success
    assert "422" in result.error
    assert "imagen no válida" in result.error
    # Solo se intentó el upload; NUNCA el generate.
    assert calls == ["http://127.0.0.1:8000/api/v1/generate/upload-control"]


def test_generate_422_en_generate_con_controls_propaga_detail(
    with_token, monkeypatch, tmp_path
):
    img = tmp_path / "u.png"
    _write_png(img)

    def fake_post(url, json=None, **kwargs):
        if "upload-control" in url:
            return _FakeResponse(200, {"image_name": "ctrl-x"})
        # 422 desde el generate (p.ej. control_type no instalado).
        return _FakeResponse(422, {"detail": "controlnet 'pose' no instalado"})

    monkeypatch.setattr(forja_tools.requests, "post", fake_post)

    result = ForjaGenerateTool().execute(
        prompt="x", control_image=str(img), control_type="pose"
    )
    assert not result.success
    assert "422" in result.error
    assert "pose" in result.error
    assert "no instalado" in result.error
