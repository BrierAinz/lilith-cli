"""/ingest slash command: trae una página web al RAG de Mimir.

El tool ``MimirIngestUrlTool`` (en ``lilith_tools.crawl_tools``) ya hace el
trabajo pesado: crawlea una URL con crawl4ai, la convierte a markdown y la
guarda en ``Svartalfheim/Docs/externo/``, que es donde Mimir indexa.

Acá lo único que hacemos es darle una puerta de entrada desde el REPL para
no tener que salir del chat a usar ``lilith ask --index`` cada vez. El
import del tool es perezoso: ``lilith-tools`` no está garantizado en el
sys.path en todos los contextos (tests aislados, sandbox del router, etc.)
y un import a nivel de módulo rompería el arranque del REPL entero.

Subcomandos / formas:

* ``/ingest <url>``                     → trae la página (nombre derivado)
* ``/ingest <url> --nombre <slug>``     → nombre de archivo explícito
* ``/ingest <url> --sobrescribir``      → reemplaza si ya existe
* ``/ingest <url> --reindex``           → tras traerla, reindexa Mimir

El reindex usa el mismo camino que ``lilith ask --index <consulta>``
(``ops_knowledge._run_mimir_main`` con el subcomando ``index`` de Mimir),
no un atajo inventado.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from .render import console, render_error

if TYPE_CHECKING:
    from .session_runtime import SessionRuntime


def _print_usage() -> None:
    console.print(
        "\n[bold realm]᛭ /ingest — trae una página web al RAG de Mimir[/]\n\n"
        "  [bold cyan]/ingest <url>[/]                          trae la página\n"
        "  [bold cyan]/ingest <url> --nombre <slug>[/]          nombre de archivo explícito\n"
        "  [bold cyan]/ingest <url> --sobrescribir[/]           reemplaza si ya existe\n"
        "  [bold cyan]/ingest <url> --reindex[/]               tras traerla, reindexa Mimir\n"
    )


def _parse_args(args: str) -> tuple[str, str, bool, bool] | None:
    """Parsea ``args`` y devuelve ``(url, nombre, sobrescribir, reindex)``.

    Devuelve ``None`` si los args no son utilizables (sin URL o URL
    malformada); el handler muestra el mensaje de uso y sale sin llamar al
    tool, como pide la spec.
    """
    texto = (args or "").strip()
    if not texto:
        return None

    # ``shlex`` para tolerar URLs con ``&`` y otros caracteres que rompen
    # un ``split()`` ingenuo. ``posix=True`` para que las comillas se
    # respeten como en una shell.
    try:
        tokens = shlex.split(texto, posix=True)
    except ValueError:
        # Comillas sin cerrar: dejamos al tool reportar el error de URL.
        tokens = texto.split()

    if not tokens:
        return None

    url = tokens[0].strip().strip("\"'")
    if not (url.startswith("http://") or url.startswith("https://")):
        return None

    nombre = ""
    sobrescribir = False
    reindex = False
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--nombre" and i + 1 < len(tokens):
            nombre = tokens[i + 1]
            i += 2
        elif tok == "--sobrescribir":
            sobrescribir = True
            i += 1
        elif tok == "--reindex":
            reindex = True
            i += 1
        else:
            # Flags desconocidos: el spec dice "o algo que no sea URL
            # http/https" → no es URL válida. Pero un flag pegado a la URL
            # sin espacio ya se descarta arriba; acá cae un argumento
            # suelto que no reconocemos. Lo ignoramos silenciosamente para
            # no romper un typo del usuario que igual queremos ayudar.
            i += 1

    return url, nombre, sobrescribir, reindex


async def run_ingest_command(session: "SessionRuntime", args: str) -> None:  # noqa: ARG001
    """/ingest — trae una URL como markdown al índice de Mimir."""
    parsed = _parse_args(args)
    if parsed is None:
        _print_usage()
        return

    url, nombre, sobrescribir, reindex = parsed

    # Import perezoso: lilith-tools puede no estar disponible y el REPL
    # no debe morir por importarlo a nivel de módulo. Mismo patrón que
    # ``run_apply_command`` y el resto del código del proyecto.
    try:
        from lilith_tools.crawl_tools import MimirIngestUrlTool
    except Exception as exc:  # noqa: BLE001
        render_error(f"No se pudo cargar mimir_ingest_url: {exc}")
        return

    # Indicador de "trayendo…": crawl4ai + playwright pueden tardar
    # segundos, y sin esto el usuario no sabe si quedó colgado.
    from rich.status import Status

    label_text = "Trayendo la página con crawl4ai…"
    with Status(label_text, console=console, spinner="dots", speed=0.8):
        try:
            resultado = MimirIngestUrlTool().execute(
                url=url,
                nombre=nombre,
                sobrescribir=sobrescribir,
            )
        except Exception as exc:  # noqa: BLE001
            # El tool ya devuelve ToolResult(success=False) para errores
            # de negocio; un raise de acá es un bug del wrapper (p.ej.
            # asyncio.run con un loop ya corriendo).
            render_error(f"Error inesperado del tool: {type(exc).__name__}: {exc}")
            return

    if not resultado.success:
        # Los mensajes de ``MimirIngestUrlTool`` ya son accionables
        # (instrucciones de instalación, razón del fallo, etc.).
        render_error(resultado.error or "fallo desconocido del tool")
        return

    data = resultado.data or {}
    ruta = data.get("ruta", "")
    caracteres = data.get("caracteres", 0)
    slug = data.get("slug", "")
    nota = data.get("nota", "")

    console.print(f"[success]✓ Traído[/] [bold]{slug}.md[/] → [dim]{ruta}[/]")
    console.print(f"  [dim]{caracteres} caracteres[/]")

    if reindex:
        console.print("\n[bold]Reindexando Mimir…[/]")
        # Mismo camino que ``lilith ask --index <consulta>`` (módulo
        # ``ops_knowledge``): cargamos la CLI de Mimir por file path y
        # llamamos a su subcomando ``index``. Sin este reuso, dos rutas
        # distintas a la misma operación derivarían inevitablemente.
        try:
            from lilith_cli.ops_knowledge import _run_mimir_main, load_mimir_cli
        except Exception as exc:  # noqa: BLE001
            render_error(
                f"No se pudo preparar el reindex de Mimir: {exc}. "
                "El documento YA quedó guardado arriba — corré "
                "`lilith ask --index <consulta>` a mano cuando puedas."
            )
            return
        try:
            mimir = load_mimir_cli()
        except (FileNotFoundError, RuntimeError) as exc:
            render_error(
                f"Mimir no disponible para reindexar: {exc}. "
                "El documento YA quedó guardado arriba."
            )
            return
        rc = _run_mimir_main(mimir, ["index", "--root", str(_ygg_root())])
        if rc not in (0, None):
            render_error(
                f"Mimir index falló (rc={rc}). "
                "El documento YA quedó guardado arriba — reintentá con "
                "`lilith ask --index <consulta>`."
            )
            return
        console.print("[success]✓ Mimir reindexado.[/]")
        return

    if nota:
        console.print(f"  [dim]{nota}[/]")


def _ygg_root():
    """Raíz del workspace; helper importado perezoso para no traccinarlo."""
    from lilith_cli.main import _resolve_yggdrasil_root

    return _resolve_yggdrasil_root()