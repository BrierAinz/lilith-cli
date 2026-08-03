"""Traer documentación de la web al RAG local (Mimir).

Mimir indexa **solo archivos ``.md`` del disco**: los documentos sueltos de la
raíz del hub y todo lo que cuelgue de ``Svartalfheim/`` (ver ``INDEX_ROOTS`` en
``Vanaheim/Agents/Mimir/cli.py``). Eso deja un hueco concreto: no hay forma de
que ``lilith ask`` conteste sobre documentación externa —docs de ComfyUI, model
cards de HuggingFace, un README de un repo ajeno— porque nada la baja a disco
en el formato que Mimir come.

Este tool cierra ese hueco sin tocar Mimir: crawlea una URL con crawl4ai,
guarda el markdown bajo ``Svartalfheim/Docs/externo/`` y desde ahí lo indexa el
mismo pipeline de siempre.

crawl4ai se importa PEREZOSAMENTE a propósito: arrastra playwright, litellm y
tokenizers, y no tiene sentido que eso sea dependencia dura de todo
``lilith-tools`` cuando lo usa un solo tool. Si falta, el error dice cómo
instalarlo en vez de reventar al importar el paquete.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

# Subcarpeta de Svartalfheim donde cae lo traído de afuera. Separada del resto
# para que se vea de un vistazo qué es material externo y qué es propio.
SUBCARPETA = Path("Svartalfheim") / "Docs" / "externo"

_INSTALAR = (
    "crawl4ai no está instalado. En el venv de Asgard:\n"
    "  uv pip install crawl4ai\n"
    "  .venv\\Scripts\\python.exe -m playwright install chromium\n"
    "(el segundo comando baja el navegador headless que crawl4ai necesita)"
)


def _resolve_yggdrasil_root() -> Path:
    """Raíz del workspace: env var, o el ancestro que tenga ``ygg.py``.

    Mismo criterio que ``lilith_cli.main._resolve_yggdrasil_root``. El fallback
    por profundidad fija vale solo para el layout histórico.
    """
    env_root = os.environ.get("YGGDRASIL_ROOT")
    if env_root:
        return Path(env_root)
    for parent in Path(__file__).resolve().parents:
        if (parent / "ygg.py").is_file():
            return parent
    return Path(__file__).resolve().parents[3]


def _slug(texto: str) -> str:
    """Nombre de archivo seguro y estable a partir de un texto o URL."""
    base = re.sub(r"[^\w\s-]", "", texto).strip().lower()
    base = re.sub(r"[-\s]+", "-", base)
    return base[:64].strip("-")


def _slug_de_url(url: str) -> str:
    """Deriva un nombre legible de la URL: dominio + camino."""
    partes = urlparse(url)
    host = (partes.netloc or "").replace("www.", "")
    camino = (partes.path or "").strip("/")
    crudo = f"{host}-{camino}" if camino else host
    return _slug(crudo) or "documento"


async def _crawl(url: str) -> tuple[bool, str, str]:
    """Devuelve (exito, markdown, error). Import perezoso adentro."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    config = BrowserConfig(headless=True, browser_type="chromium")
    async with AsyncWebCrawler(config=config) as crawler:
        resultado = await crawler.arun(url=url)
        markdown = str(getattr(resultado, "markdown", "") or "")
        if not getattr(resultado, "success", False):
            return False, "", str(getattr(resultado, "error_message", "")) or "el crawl falló"
        return True, markdown, ""


@ToolRegistry.register
class MimirIngestUrlTool(BaseTool):
    """Baja una página web como markdown al índice de Mimir."""

    name = "mimir_ingest_url"
    description = (
        "Trae una página web al RAG local: la convierte a markdown y la guarda "
        "en Svartalfheim/Docs/externo/, que es donde Mimir indexa. Sirve para "
        "que `lilith ask` pueda responder sobre documentación externa (docs de "
        "ComfyUI, model cards de HuggingFace, READMEs ajenos), que hoy no está "
        "en el índice porque Mimir solo lee .md del disco. Después de traer "
        "documentos hay que reindexar (`lilith ask --index <consulta>`). "
        "Requiere crawl4ai instalado."
    )
    parameters = {
        "url": {
            "type": "string",
            "description": "URL completa de la página a traer (http/https).",
            "required": True,
        },
        "nombre": {
            "type": "string",
            "description": (
                "Nombre del archivo sin extensión. Si se omite, se deriva del "
                "dominio y el camino de la URL."
            ),
            "default": "",
        },
        "sobrescribir": {
            "type": "boolean",
            "description": (
                "Si ya existe un documento con ese nombre, reemplazarlo. Por "
                "defecto False: se avisa y no se toca nada."
            ),
            "default": False,
        },
    }

    def execute(
        self,
        url: str = "",
        nombre: str = "",
        sobrescribir: bool = False,
    ) -> ToolResult:
        url = (url or "").strip()
        if not url:
            return ToolResult(success=False, data=None, error="url vacía")

        partes = urlparse(url)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            return ToolResult(
                success=False,
                data=None,
                error=f"url inválida: {url!r} (se espera http:// o https://)",
            )

        slug = _slug(nombre) if nombre else _slug_de_url(url)
        if not slug:
            return ToolResult(
                success=False,
                data=None,
                error=f"no se pudo derivar un nombre de archivo de {nombre or url!r}",
            )

        destino_dir = _resolve_yggdrasil_root() / SUBCARPETA
        destino = destino_dir / f"{slug}.md"
        if destino.exists() and not sobrescribir:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"ya existe {destino.name}. Pasá sobrescribir=true para "
                    "reemplazarlo, o un nombre distinto."
                ),
            )

        try:
            exito, markdown, error = asyncio.run(_crawl(url))
        except ImportError:
            return ToolResult(success=False, data=None, error=_INSTALAR)
        except Exception as exc:  # noqa: BLE001 - el detalle importa más que el tipo
            return ToolResult(
                success=False,
                data=None,
                error=f"error crawleando {url}: {type(exc).__name__}: {exc}",
            )

        if not exito:
            return ToolResult(success=False, data=None, error=f"{url}: {error}")
        if not markdown.strip():
            return ToolResult(
                success=False,
                data=None,
                error=f"{url} no devolvió contenido (¿página vacía o bloqueada?)",
            )

        # Cabecera con la procedencia: sin esto, dentro de un mes nadie sabe de
        # dónde salió el documento ni si sigue vigente.
        cabecera = (
            f"<!-- traído de {url} el {datetime.now().strftime('%Y-%m-%d %H:%M')} "
            f"por mimir_ingest_url -->\n\n"
            f"> Fuente: {url}\n\n"
        )
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino.write_text(cabecera + markdown, encoding="utf-8")

        return ToolResult(
            success=True,
            data={
                "ruta": str(destino),
                "slug": slug,
                "caracteres": len(markdown),
                "url": url,
                "nota": (
                    "Reindexá para que entre en las búsquedas: "
                    "`lilith ask --index <consulta>`"
                ),
            },
            error="",
        )
