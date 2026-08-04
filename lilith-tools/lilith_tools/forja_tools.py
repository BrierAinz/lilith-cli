"""Tool para invocar la Forja de Yggdrasil (generación de imágenes local).

Habla con la API de agentes de la Forja (F14 del proyecto Forjayggdrasil):
``POST /api/v1/agent/generate`` es síncrono — encola en ComfyUI y espera el
resultado, devolviendo los archivos generados. La API es fail-closed: exige
``FORJA_AGENT_TOKEN`` tanto en el entorno del servidor como en el de este
proceso (header ``X-Forja-Token``).
"""

import mimetypes
import os

import requests

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

_DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Extensiones que aceptamos como imagen de control. No hace falta chequear
# el MIME real con Pillow: si el servidor rechaza, devuelve un 4xx con detail
# accionable. La lista corta cubre lo que la Forja recibe de un LLM (jpg/png/webp).
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _resolve_base_url() -> str:
    return os.environ.get("FORJA_API_URL", _DEFAULT_BASE_URL).rstrip("/")


def _require_token() -> tuple[str | None, str]:
    """Devuelve (token, '') si está, o (None, mensaje_de_error)."""
    token = os.environ.get("FORJA_AGENT_TOKEN")
    if not token:
        return None, (
            "FORJA_AGENT_TOKEN no está en el entorno. La API de agentes de la "
            "Forja es fail-closed: definí el token (User env) y reiniciá."
        )
    return token, ""


def _auth_headers(token: str) -> dict:
    return {"X-Forja-Token": token}


def _format_non_200(resp) -> str:
    """Extrae un mensaje útil del body de error de la API."""
    try:
        detail = resp.json().get("detail", resp.text)
    except Exception:
        detail = resp.text
    return f"La Forja respondió {resp.status_code}: {detail}"


def _connection_error_msg(base_url: str, exc: Exception) -> str:
    return (
        f"No se pudo conectar con la Forja en {base_url}: {exc}. "
        "¿Está corriendo? (scripts/forja.ps1 start en Forjayggdrasil)"
    )


@ToolRegistry.register
class ForjaGenerateTool(BaseTool):
    """Genera una imagen en la Forja local y espera el resultado.

    Soporta ControlNet: si se pasa ``control_image`` (ruta local), se sube al
    endpoint de upload-control y se manda la señal resultante en el body del
    generate. ``control_type`` elige qué ControlNet cargar (canny, depth,
    openpose, etc. — ver ``forja_controlnets``); para tipos que no sean canny
    la imagen debe llegar YA preprocesada, así que el cliente no activa el
    preprocesado automático salvo que ``control_type == 'canny'``.
    """

    name = "forja_generate"
    description = (
        "Genera una imagen con la Forja de Yggdrasil (ComfyUI local) y ESPERA el "
        "resultado (segundos a minutos según steps/tamaño). Requiere la Forja "
        "corriendo (scripts/forja.ps1 start) y FORJA_AGENT_TOKEN en el entorno. "
        "Parametros: prompt (str, requerido), negative_prompt, width, height, "
        "steps, model (nombre del registry; default el primero), seed, timeout_s. "
        "ControlNet (opcional): control_image (ruta local a una imagen), "
        "control_type (str: canny, depth, openpose, scribble, lineart, softedge, "
        "normal, seg, tile, mlsd — usá forja_controlnets para ver los "
        "disponibles), control_strength (float 0.1-1.5, default 1.0). Para "
        "control_type='canny' la Forja extrae bordes automáticamente; para los "
        "demás tipos la imagen ya tiene que venir preprocesada."
    )
    parameters = {
        "prompt": {"type": "string", "description": "Prompt de la imagen", "required": True},
        "negative_prompt": {"type": "string", "description": "Prompt negativo", "default": ""},
        # Sin "default" a propósito: si el campo NO se manda, la Forja aplica el
        # del personaje (cuando se usa `character`) y, si no hay, el suyo
        # (1024/1024/30). Declarar un default acá hacía que el tool lo mandara
        # SIEMPRE, y un campo explícito le gana al personaje por diseño: los
        # defaults del personaje no se aplicaban nunca desde este canal.
        "width": {"type": "integer", "description": "Ancho en px (omitir = 1024, o el del personaje)"},
        "height": {"type": "integer", "description": "Alto en px (omitir = 1024, o el del personaje)"},
        "steps": {"type": "integer", "description": "Pasos de sampling (omitir = 30, o el del personaje)"},
        "model": {"type": "string", "description": "Checkpoint del registry (opcional)"},
        "character": {
            "type": "string",
            "description": (
                "Id de un personaje de la biblioteca de la Forja (ver "
                "forja_characters). La Forja le antepone su trigger al prompt y "
                "resuelve su checkpoint, sus LoRAs y su negativo. Lo que mandes "
                "explícito acá le gana al personaje."
            ),
            "default": "",
        },
        "seed": {"type": "integer", "description": "Seed fija (opcional; sin ella es aleatoria)"},
        "timeout_s": {
            "type": "number",
            "description": "Máximo de espera del resultado en segundos (5-600)",
            "default": 180,
        },
        "control_image": {
            "type": "string",
            "description": (
                "Ruta local a una imagen de control (jpg/png/webp). Si se pasa, "
                "se sube a /api/v1/generate/upload-control y se adjunta como "
                "señal de ControlNet. Vacío = sin ControlNet."
            ),
            "default": "",
        },
        "control_type": {
            "type": "string",
            "description": (
                "Tipo de ControlNet a cargar (canny, depth, openpose, ...). "
                "Usá forja_controlnets para ver los disponibles. Vacío = no "
                "forzar tipo (el servidor toma el primero del registry, lo "
                "cual suele ser incorrecto si hay más de uno instalado)."
            ),
            "default": "",
        },
        "control_strength": {
            "type": "number",
            "description": "Intensidad de la señal de ControlNet (0.1-1.5, default 1.0).",
            "default": 1.0,
        },
    }

    def execute(
        self,
        prompt: str = "",
        negative_prompt: str = "",
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        model: str | None = None,
        seed: int | None = None,
        character: str = "",
        timeout_s: float = 180.0,
        control_image: str = "",
        control_type: str = "",
        control_strength: float = 1.0,
    ) -> ToolResult:
        if not prompt or not prompt.strip():
            return ToolResult(success=False, data=None, error="prompt vacío")

        token, err = _require_token()
        if not token:
            return ToolResult(success=False, data=None, error=err)

        base_url = _resolve_base_url()
        timeout_s = max(5.0, min(600.0, float(timeout_s)))

        body: dict = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            # El campo de la API es ``timeout`` (AgentGenerateRequest). Mandar
            # ``timeout_s`` no daba error: pydantic descarta los campos extra
            # en silencio y el servidor se quedaba con su default de 600 s.
            "timeout": timeout_s,
        }
        # width/height/steps solo viajan si el llamador los pidió. La Forja
        # decide si un campo lo mandó el cliente mirando `model_fields_set`, y
        # lo explícito le gana al personaje: mandarlos siempre (aunque valgan
        # justo el default del servidor) dejaba los defaults del personaje sin
        # efecto. Omitidos, el resultado es el mismo de antes cuando no hay
        # personaje, porque los defaults del servidor son los mismos.
        if width is not None:
            body["width"] = width
        if height is not None:
            body["height"] = height
        if steps is not None:
            body["steps"] = steps
        if model:
            body["model"] = model
        if seed is not None:
            body["seed"] = seed
        if character and character.strip():
            body["character"] = character.strip()

        if control_image:
            uploaded = self._upload_control_image(base_url, token, control_image)
            if not uploaded.success:
                return uploaded  # propagamos el ToolResult de error tal cual
            body = self._attach_control_signal(body, uploaded.data, control_type, control_strength)

        try:
            resp = requests.post(
                f"{base_url}/api/v1/agent/generate",
                json=body,
                headers=_auth_headers(token),
                # Margen sobre el timeout del servidor para no cortar antes.
                timeout=timeout_s + 30,
            )
        except requests.exceptions.RequestException as exc:
            return ToolResult(
                success=False,
                data=None,
                error=_connection_error_msg(base_url, exc),
            )

        if resp.status_code != 200:
            return ToolResult(success=False, data=None, error=_format_non_200(resp))

        data = resp.json()
        image_urls = [f"{base_url}{u}" for u in data.get("image_urls", [])]
        return ToolResult(
            success=True,
            data={
                "prompt_id": data.get("prompt_id"),
                "images": data.get("images", []),
                "image_urls": image_urls,
                "seed": data.get("seed"),
                "elapsed_s": data.get("elapsed_s"),
            },
        )

    # --- helpers de ControlNet ---------------------------------------------

    @staticmethod
    def _upload_control_image(base_url: str, token: str, path: str) -> ToolResult:
        """Sube la imagen al endpoint de upload-control. Devuelve ToolResult(success,
        data={'image_name': '...'}) o success=False con el error correspondiente."""
        if not os.path.isfile(path):
            return ToolResult(
                success=False,
                data=None,
                error=f"control_image no existe o no es archivo: {path}",
            )

        ext = os.path.splitext(path)[1].lower()
        if ext not in _IMAGE_EXTS:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"control_image no parece una imagen soportada "
                    f"(extensión '{ext}'; se acepta {sorted(_IMAGE_EXTS)}): {path}"
                ),
            )

        # mime por extensión: el server normalmente acepta image/*; si no
        # matchea, caemos a image/png como default razonable.
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "image/png"

        try:
            with open(path, "rb") as fh:
                resp = requests.post(
                    f"{base_url}/api/v1/generate/upload-control",
                    files={"image": (os.path.basename(path), fh, mime)},
                    headers=_auth_headers(token),
                    timeout=60,
                )
        except requests.exceptions.RequestException as exc:
            return ToolResult(
                success=False,
                data=None,
                error=_connection_error_msg(base_url, exc),
            )

        if resp.status_code != 200:
            return ToolResult(success=False, data=None, error=_format_non_200(resp))

        payload = resp.json()
        image_name = payload.get("image_name")
        if not image_name:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"upload-control devolvió 200 pero sin 'image_name' "
                    f"(body={payload})"
                ),
            )
        return ToolResult(success=True, data={"image_name": image_name}, error="")

    @staticmethod
    def _attach_control_signal(
        body: dict, uploaded: dict, control_type: str, control_strength: float
    ) -> dict:
        """Construye la señal de ControlNet y la mete en ``controls``. Solo se
        añade ``preprocess='canny'`` cuando control_type == 'canny'; en los
        demás casos la imagen debe llegar YA preprocesada al servidor."""
        # Rango del slider de la Forja (ControlSection: min 0.1, max 1.5).
        # Clampar a [0, 1] dejaba fuera del alcance del agente los valores
        # 1.0-1.5, que la UI si permite y el motor acepta.
        strength = max(0.1, min(1.5, float(control_strength)))
        signal: dict = {
            "image_name": uploaded["image_name"],
            "strength": strength,
        }
        if control_type:
            signal["type"] = control_type
            if control_type == "canny":
                signal["preprocess"] = "canny"
            else:
                # Para tipos que no sean canny la imagen debe llegar ya
                # preprocesada; si no, mandamos preprocess explícitamente a null
                # para que el server NO aplique canny encima de un depth map.
                signal["preprocess"] = None
        else:
            # Sin type, el server carga el primer ControlNet del registry.
            # No seteamos preprocess: el comportamiento por defecto del server
            # es el correcto cuando el primer CN es canny, y es el que ya
            # existía antes de este feature.
            pass
        body["controls"] = [signal]
        return body


@ToolRegistry.register
class ForjaCharactersTool(BaseTool):
    """Lista los personajes guardados en la Forja.

    Sin esto, el parámetro ``character`` de ``forja_generate`` obliga a
    adivinar el id: un id inexistente devuelve 422 con la lista, pero
    gastar una generación fallida para descubrirla es absurdo.
    """

    name = "forja_characters"
    description = (
        "Lista los personajes de la Forja (GET /api/v1/characters). Devuelve "
        "una lista de {id, name, description, trigger, model}. Usalo para saber "
        "qué pasarle al parámetro `character` de forja_generate. Mismo patrón "
        "de auth que forja_generate (X-Forja-Token vía FORJA_AGENT_TOKEN)."
    )
    parameters = {}

    def execute(self) -> ToolResult:
        token, err = _require_token()
        if not token:
            return ToolResult(success=False, data=None, error=err)

        base_url = _resolve_base_url()

        try:
            resp = requests.get(
                f"{base_url}/api/v1/characters",
                headers=_auth_headers(token),
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            return ToolResult(
                success=False,
                data=None,
                error=_connection_error_msg(base_url, exc),
            )

        if resp.status_code != 200:
            return ToolResult(success=False, data=None, error=_format_non_200(resp))

        data = resp.json()
        # La API envuelve la lista en {"characters": [...]}; si algún día se
        # aplana, devolvemos la lista cruda como fallback.
        personajes = data.get("characters", data) if isinstance(data, dict) else data
        # Se recorta a lo que el agente necesita para elegir: el resto
        # (loras, defaults, negative) es cableado interno de la Forja y solo
        # ensucia el contexto.
        return ToolResult(
            success=True,
            data=[
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "description": p.get("description", ""),
                    "trigger": p.get("trigger", ""),
                    "model": p.get("model"),
                }
                for p in (personajes or [])
                if isinstance(p, dict)
            ],
            error="",
        )


@ToolRegistry.register
class ForjaControlnetsTool(BaseTool):
    """Lista los ControlNets instalados en la Forja.

    Útil para que el agente sepa qué ``control_type`` puede pasarle a
    ``forja_generate`` antes de pedir uno (un type sin instalar devuelve 422
    con detail accionable).
    """

    name = "forja_controlnets"
    description = (
        "Lista los ControlNets instalados en la Forja (GET "
        "/api/v1/models/controlnets). Devuelve una lista de {name, type}. "
        "Usalo antes de forja_generate con control_image para saber qué "
        "control_type pasar. Mismo patrón de auth que forja_generate "
        "(X-Forja-Token vía FORJA_AGENT_TOKEN)."
    )
    parameters = {}

    def execute(self) -> ToolResult:
        token, err = _require_token()
        if not token:
            return ToolResult(success=False, data=None, error=err)

        base_url = _resolve_base_url()

        try:
            resp = requests.get(
                f"{base_url}/api/v1/models/controlnets",
                headers=_auth_headers(token),
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            return ToolResult(
                success=False,
                data=None,
                error=_connection_error_msg(base_url, exc),
            )

        if resp.status_code != 200:
            return ToolResult(success=False, data=None, error=_format_non_200(resp))

        data = resp.json()
        # La API actual envuelve la lista en {"controlnets": [...]}. Si en el
        # futuro se aplana, devolvemos la lista cruda como fallback.
        controlnets = data.get("controlnets", data) if isinstance(data, dict) else data
        return ToolResult(success=True, data=list(controlnets or []), error="")
