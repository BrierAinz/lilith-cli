"""Blender automation tools for agentic 3D modeling and rendering.

Provides two tools:

* :class:`BlenderExecTool` (``blender_exec``) — runs an arbitrary ``bpy``
  script in headless Blender (background mode, factory-startup) and
  returns captured stdout/stderr truncated to ~8 KB. Use it for modeling,
  scene inspection, geometry queries, asset generation, etc.
* :class:`BlenderRenderTool` (``blender_render``) — renders one frame of
  the active camera to PNG. Intended as a quick visual-feedback loop for
  an agent working on 3D models.

Both tools locate Blender by:
    1. ``$BLENDER_PATH`` environment variable, if set and an existing
       executable.
    2. ``blender`` on ``PATH`` (``shutil.which``).
    3. Globbing ``C:\\Program Files\\Blender Foundation\\Blender*\\blender.exe``
       and picking the version with the highest numeric major/minor/release
       tuple (e.g. ``4.3.2`` beats ``3.6.5``).

Subprocesses are launched with ``subprocess.run`` and capture stdout +
stderr. On Windows we pass ``creationflags=CREATE_NO_WINDOW`` so no
console window pops up. Failures are returned as ``ToolResult(success=False,...)``
with actionable messages; never raised to the caller.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult
from .registry import ToolRegistry


# Cap subprocess output so a noisy/render-spammy Blender run can't blow the
# context window. The agent just needs the last few KB of stdout/stderr.
OUTPUT_BYTE_LIMIT = 8 * 1024

# Path used by the official Blender Windows installer.
_DEFAULT_WINDOWS_GLOB = r"C:\Program Files\Blender Foundation\Blender*\blender.exe"


def _truncate(text: str) -> str:
    """Truncate *text* to ``OUTPUT_BYTE_LIMIT`` bytes, keeping UTF-8 valid."""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= OUTPUT_BYTE_LIMIT:
        return text
    truncated = encoded[:OUTPUT_BYTE_LIMIT]
    return truncated.decode("utf-8", errors="replace") + "\n... [output truncated]"


def _find_blender_windows() -> str | None:
    """Pick the newest blender.exe under the default Windows install path."""
    base = Path(r"C:\Program Files\Blender Foundation")
    if not base.is_dir():
        return None
    matches = sorted(base.glob("Blender*\\blender.exe"))
    matches = [p for p in matches if p.is_file()]
    if not matches:
        return None

    def version_key(path: Path) -> tuple[int, int, int]:
        m = re.search(r"Blender[\\/]([\d.]+)", str(path))
        if not m:
            return (0, 0, 0)
        parts = re.findall(r"\d+", m.group(1))
        nums = [int(p) for p in parts[:3]]
        while len(nums) < 3:
            nums.append(0)
        return (nums[0], nums[1], nums[2])

    matches.sort(key=version_key, reverse=True)
    return str(matches[0])


def _locate_blender() -> str | None:
    """Return the absolute path to a usable ``blender`` executable, or None."""
    # 1. Explicit override.
    env_path = os.environ.get("BLENDER_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return str(candidate)
        # Allow pointing at a directory containing ``blender[.exe]``.
        if candidate.is_dir():
            for name in ("blender.exe", "blender"):
                exe = candidate / name
                if exe.is_file():
                    return str(exe)

    # 2. PATH.
    on_path = shutil.which("blender")
    if on_path:
        return on_path

    # 3. Default Windows install dir.
    if sys.platform.startswith("win") or os.name == "nt":
        return _find_blender_windows()
    return None


def _blender_unavailable_message() -> str:
    """Return a user-actionable error string when Blender can't be found."""
    return (
        "Blender no esta instalado o no se encontro en una ruta conocida. "
        "Para usar blender_exec / blender_render:\n"
        "  - Instala Blender (https://www.blender.org/download/) y agrega "
        "blender.exe al PATH, O\n"
        "  - Define la variable de entorno BLENDER_PATH apuntando al "
        "ejecutable, p.ej. set BLENDER_PATH=C:\\Program Files\\Blender "
        "Foundation\\Blender 4.3\\blender.exe"
    )


def _run_blender(
    blender_exe: str,
    args: list[str],
    timeout: int,
) -> tuple[str, str, int]:
    """Run Blender with *args*; return (stdout, stderr, returncode).

    Output is truncated to ``OUTPUT_BYTE_LIMIT`` bytes per stream.
    ``CREATE_NO_WINDOW`` is set on Windows so no console pops up.
    """
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        # Avoid spawning a console window for a background UI process.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.run([blender_exe, *args], **kwargs)
    return _truncate(proc.stdout or ""), _truncate(proc.stderr or ""), proc.returncode


def _resolve_timeout(
    cls_timeout: int,
    user_timeout: Any,
    fallback: int = 180,
) -> int:
    """Pick a valid integer timeout; prefer user input, then class attr."""
    if user_timeout is None:
        candidate = cls_timeout if isinstance(cls_timeout, int) and cls_timeout > 0 else fallback
    else:
        try:
            candidate = int(user_timeout)
        except (TypeError, ValueError):
            candidate = fallback
        if candidate <= 0:
            candidate = fallback
    return candidate


def _validate_script(script: str | None) -> str | None:
    """Return None if *script* is fine, else a friendly error string."""
    if script is None:
        return "'script' es requerido y no puede ser None"
    if not isinstance(script, str):
        return "'script' debe ser una cadena de codigo Python bpy"
    if not script.strip():
        return "'script' no puede estar vacio"
    return None


@ToolRegistry.register
class BlenderExecTool(BaseTool):
    """Run an arbitrary ``bpy`` Python script in a headless Blender instance.

    Long-running: Blender scripts can take a while (geometry processing,
    asset baking). The class ``timeout_seconds`` attribute is set to 180s —
    the agent runner honors this as the default tool timeout floor.
    """

    name = "blender_exec"
    timeout_seconds = 180
    description = (
        "Ejecuta codigo Python bpy en Blender headless (--background "
        "--factory-startup) y devuelve stdout/stderr truncados a ~8KB. "
        "Usala para modelado, inspeccion de escenas, generacion de assets "
        "y cualquier operacion bpy 3D. Requiere Blender instalado o "
        "BLENDER_PATH apuntando al ejecutable."
    )
    parameters = {
        "script": {
            "type": "string",
            "required": True,
            "description": (
                "Codigo Python bpy a ejecutar dentro de Blender (bpy, bmesh, "
                "mathutils disponibles). Obligatorio."
            ),
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "description": "Timeout en segundos (default = class timeout_seconds = 180).",
        },
        "blend_file": {
            "type": "string",
            "required": False,
            "description": (
                "Archivo .blend opcional para abrir antes de ejecutar el script "
                "(se pasa como argumento posicional a blender)."
            ),
        },
        "output_path": {
            "type": "string",
            "required": False,
            "description": (
                "Ruta donde el script bpy debe escribir su salida principal "
                "(solo informativo: la tool no lo valida, lo reporta en data)."
            ),
        },
    }

    def execute(
        self,
        script: str | None = None,
        timeout: Any = None,
        blend_file: str | None = None,
        output_path: str | None = None,
        **_: Any,
    ) -> ToolResult:
        """Run Blender with the given bpy script and return captured output."""
        # ── Validation ────────────────────────────────────────────────
        err = _validate_script(script)
        if err is not None:
            return ToolResult(success=False, data=None, error=err)
        assert script is not None  # narrowing for type-checkers

        if blend_file is not None and not str(blend_file).strip():
            return ToolResult(
                success=False,
                data=None,
                error="'blend_file' no puede ser una cadena vacia",
            )

        # ── Locate Blender ────────────────────────────────────────────
        blender_exe = _locate_blender()
        if blender_exe is None:
            return ToolResult(
                success=False, data=None, error=_blender_unavailable_message()
            )

        # ── Write script to a tempfile and run ───────────────────────
        resolved_timeout = _resolve_timeout(self.timeout_seconds, timeout)

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                prefix="lilith_blender_",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(script)
                tmp_path = tmp.name

            args: list[str] = [
                "--background",
                "--factory-startup",
                "--python",
                tmp_path,
            ]
            if blend_file:
                # Append blend_file as positional so Blender opens it before
                # running the script.
                args.append(str(blend_file))

            try:
                stdout, stderr, rc = _run_blender(
                    blender_exe, args, resolved_timeout
                )
            except subprocess.TimeoutExpired as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=(
                        f"Blender excedio el timeout de {resolved_timeout}s "
                        f"(stdout parcial: {(exc.stdout or b'')[:512]!r})"
                    ),
                )
            except FileNotFoundError as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=_blender_unavailable_message() + f" [{exc}]",
                )
            except OSError as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"No se pudo ejecutar Blender ({blender_exe}): {exc}",
                )
        finally:
            if tmp_path is not None:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

        data: dict[str, Any] = {
            "blender_exe": blender_exe,
            "timeout": resolved_timeout,
            "returncode": rc,
            "stdout": stdout,
            "stderr": stderr,
            "blend_file": blend_file,
        }
        if output_path is not None:
            data["output_path"] = output_path

        if rc != 0:
            err_msg = (
                f"Blender finalizo con codigo de salida {rc}. "
                "Revisa stderr arriba para ver el traceback de bpy."
            )
            if stderr:
                err_msg += f"\nUltimo stderr:\n{stderr[-512:]}"
            return ToolResult(success=False, data=data, error=err_msg)
        return ToolResult(success=True, data=data, error="")


@ToolRegistry.register
class BlenderRenderTool(BaseTool):
    """Render one frame of the active scene's camera to ``output_path`` (PNG).

    Builds a small bpy harness that sets the scene's render filepath, runs
    ``bpy.ops.render.render``, and saves the image. Returns the rendered
    file's absolute path on success so the agent can use it as visual
    feedback (e.g. attach to a chat message, pass to a vision tool).
    """

    name = "blender_render"
    timeout_seconds = 180
    description = (
        "Renderiza un frame (camara activa) a PNG en output_path y devuelve "
        "la ruta absoluta de la imagen. Pensada como feedback visual rapido "
        "para el agente durante modelado 3D. Acepta un blend_file opcional "
        "para abrir y un script bpy extra para preparar la escena antes del "
        "render. Requiere Blender instalado o BLENDER_PATH."
    )
    parameters = {
        "output_path": {
            "type": "string",
            "required": True,
            "description": (
                "Ruta absoluta del PNG de salida. Si no tiene extension, se "
                "le agrega '.png'. El directorio padre se crea si no existe."
            ),
        },
        "blend_file": {
            "type": "string",
            "required": False,
            "description": ".blend opcional para abrir antes del render.",
        },
        "script": {
            "type": "string",
            "required": False,
            "description": (
                "Codigo bpy extra para preparar la escena antes del render "
                "(p.ej. configurar camara, materials, lighting)."
            ),
        },
        "timeout": {
            "type": "integer",
            "required": False,
            "description": "Timeout en segundos (default 180).",
        },
    }

    # bpy harness executed inside headless Blender. ``__OUTPUT__`` and
    # ``__USER_SCRIPT__`` are simple sentinels substituted via str.replace
    # (we avoid str.format to dodge stray ``{...}`` in user-supplied code).
    _HARNESS = (
        "import bpy\n"
        "import os\n"
        "import sys\n"
        "import traceback\n"
        "\n"
        "OUTPUT = __OUTPUT__\n"
        "USER_SCRIPT = __USER_SCRIPT__\n"
        "\n"
        "try:\n"
        "    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT)) or '.', exist_ok=True)\n"
        "    scene = bpy.context.scene\n"
        "    if scene is None:\n"
        "        sys.stderr.write('No active scene available to render\\n')\n"
        "        sys.exit(3)\n"
        "    if USER_SCRIPT:\n"
        "        try:\n"
        "            exec(USER_SCRIPT, {u'__name__': u'__lilith_user__'})\n"
        "        except Exception:\n"
        "            traceback.print_exc()\n"
        "            sys.exit(4)\n"
        "    scene.render.filepath = OUTPUT\n"
        "    # Force PNG output regardless of caller-provided extension.\n"
        "    scene.render.image_settings.file_format = 'PNG'\n"
        "    bpy.ops.render.render(write_still=True)\n"
        "except Exception:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(5)\n"
    )

    def execute(
        self,
        output_path: str | None = None,
        blend_file: str | None = None,
        script: str | None = None,
        timeout: Any = None,
        **_: Any,
    ) -> ToolResult:
        """Render one frame to *output_path* and return its absolute path."""
        # ── Validation ────────────────────────────────────────────────
        if not output_path or not isinstance(output_path, str) or not output_path.strip():
            return ToolResult(
                success=False,
                data=None,
                error="'output_path' es requerido y debe ser una ruta no vacia",
            )
        if blend_file is not None and not str(blend_file).strip():
            return ToolResult(
                success=False,
                data=None,
                error="'blend_file' no puede ser una cadena vacia",
            )
        if script is not None and not isinstance(script, str):
            return ToolResult(
                success=False,
                data=None,
                error="'script' debe ser una cadena de codigo Python bpy",
            )
        if script is not None and not script.strip():
            return ToolResult(
                success=False,
                data=None,
                error="'script' no puede estar vacio",
            )

        # Normalize output_path: ensure parent dir exists, add .png if missing.
        out = Path(output_path)
        if out.suffix == "":
            out = out.with_suffix(".png")
        # If the user passed e.g. "foo.jpg", keep as PNG too (--).
        # Easiest: always force .png so downstream callers can rely on the ext.
        out = out.with_suffix(".png")
        out = out.resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        output_str = str(out)

        # ── Locate Blender ────────────────────────────────────────────
        blender_exe = _locate_blender()
        if blender_exe is None:
            return ToolResult(
                success=False, data=None, error=_blender_unavailable_message()
            )

        resolved_timeout = _resolve_timeout(self.timeout_seconds, timeout)

        harness = (
            self._HARNESS.replace("__OUTPUT__", repr(output_str)).replace(
                "__USER_SCRIPT__", repr(script or "")
            )
        )

        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                prefix="lilith_blender_render_",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(harness)
                tmp_path = tmp.name

            args: list[str] = [
                "--background",
                "--factory-startup",
                "--python",
                tmp_path,
            ]
            if blend_file:
                args.append(str(blend_file))

            try:
                stdout, stderr, rc = _run_blender(
                    blender_exe, args, resolved_timeout
                )
            except subprocess.TimeoutExpired as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=(
                        f"Render de Blender excedio el timeout de "
                        f"{resolved_timeout}s (stdout parcial: "
                        f"{(exc.stdout or b'')[:512]!r})"
                    ),
                )
            except FileNotFoundError as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=_blender_unavailable_message() + f" [{exc}]",
                )
            except OSError as exc:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"No se pudo ejecutar Blender ({blender_exe}): {exc}",
                )
        finally:
            if tmp_path is not None:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

        rendered_exists = out.is_file()
        data: dict[str, Any] = {
            "blender_exe": blender_exe,
            "timeout": resolved_timeout,
            "returncode": rc,
            "stdout": stdout,
            "stderr": stderr,
            "output_path": output_str,
            "rendered": rendered_exists,
            "blend_file": blend_file,
        }

        if rc != 0 or not rendered_exists:
            last = stderr[-512:] if stderr else ""
            return ToolResult(
                success=False,
                data=data,
                error=(
                    f"Render fallo (rc={rc}, rendered={rendered_exists}). "
                    + (f"Ultimo stderr:\n{last}" if last else "")
                ),
            )

        return ToolResult(success=True, data=data, error="")
