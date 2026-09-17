"""Small-model execution profiles and a packaged, progressively loaded skill kit."""

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

import httpx
from cyclopts import App

kit_app = App(name="kit", help="Skills locales, perfiles de ejecución y herramientas disponibles.")
SKILLS_ROOT = Path(__file__).parent / "builtin_skills"
PROFILES = {
    "agent": {
        "context_chars": 120000,
        "result_chars": 24000,
        "iterations": 32,
        "output_tokens": 8192,
        "tool_prefixes": {"mcp_"},
        "tools": {
            "directory_list", "grep_files", "file_read", "file_write", "file_edit", "file_append",
            "run_test", "run_linter", "web_search", "skill_catalog", "skill_read", "mission_skill_run",
            "memory_recall", "memory_save", "memory_evidence", "ask_operator", "mission_conclave",
            "mission_delegate", "cli_jobs_recent", "cli_job_reference", "cli_job_inspect",
            "mission_authority", "mission_compute", "mission_court", "mission_learning", "mission_desktop_observe",
            "mission_desktop_act", "mission_admin_exec", "mission_prepare",
            "mission_status", "mission_complete", "mission_resume_expired", "orchestration_state",
            "package_guard", "security_scan",
        },
    },
    "coordinator": {
        "context_chars": 64000, "result_chars": 8000, "iterations": 12, "output_tokens": 4096,
        "tool_prefixes": {"mcp_"},
        "tools": {
            "directory_list", "file_read", "mission_delegate", "mission_conclave",
            "mission_court", "mission_compute", "mission_status",
            "cli_jobs_recent", "cli_job_reference",
        },
    },
    "compact": {
        "context_chars": 24000, "result_chars": 6000, "iterations": 8, "output_tokens": 2048,
        "tool_prefixes": set(), "tools": {"file_read", "file_write"},
    },
    "standard": {
        "context_chars": 120000, "result_chars": 24000, "iterations": 12, "output_tokens": 4096,
        "tool_prefixes": set(), "tools": {"file_read", "file_write", "file_edit", "file_append"},
    },
    "reader": {
        "context_chars": 24000, "result_chars": 12000, "iterations": 1, "output_tokens": 2048,
        "tool_prefixes": set(), "tools": set(),
    },
}


def catalog():
    from lilith_skills.loader import SkillLoader
    return {skill.name: skill for skill in SkillLoader(SKILLS_ROOT).scan()}


@kit_app.command(name="list")
def list_skills():
    print(json.dumps([{ "name": name, "description": skill.description, "version": skill.version,
                      "sha256": hashlib.sha256(skill.content.encode()).hexdigest()}
                     for name, skill in catalog().items()], ensure_ascii=True, indent=2))


@kit_app.command(name="show")
def show_skill(name: str):
    if name not in catalog():
        raise SystemExit("Skill desconocida")
    print(catalog()[name].content)


@kit_app.command(name="profiles")
def profiles():
    print(json.dumps({name: dict(values, tools=sorted(values["tools"])) for name, values in PROFILES.items()}, indent=2))


@kit_app.command(name="doctor")
def doctor():
    found = {name: resolve_tool(name) for name in ("git", "rg", "node", "uv", "ruff")}
    print(json.dumps({"python": sys.executable, "tools": found, "skills": len(catalog()),
                      "provider": "No se hacen llamadas al modelo en este diagnóstico"}, ensure_ascii=True, indent=2))


def resolve_tool(name: str):
    # Python-family tools must come from the active environment before PATH.
    # On Windows, PATH can resolve `python` to the Microsoft Store alias even
    # while Lilith itself is running from a real virtualenv interpreter.
    if name in ("ruff", "pytest", "python"):
        candidate = Path(sys.executable).parent / (name + ".exe")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if name == "node" and Path("C:/Program Files/nodejs/node.exe").is_file():
        return "C:/Program Files/nodejs/node.exe"
    return None


@kit_app.command(name="probe")
def probe(config: str | None = None):
    """Una prueba sintética de tool calling del proveedor activo; no ejecuta herramientas."""
    from .config import load_config
    from .providers import LLMProviderWrapper
    cfg = load_config(config).model_copy(deep=True)
    cfg.retry_max = 0
    selected = cfg.providers.get(cfg.provider.lower())
    if selected and cfg.provider.lower() == "deepseek":
        selected.thinking_enabled = False
    async def run():
        provider = LLMProviderWrapper(cfg)
        try:
            response = await asyncio.wait_for(provider.complete(
                [{"role": "user", "content": "Call probe with value LILITH_PROBE. Do not answer in text."}],
                tools=[{"type": "function", "function": {"name": "probe", "description": "Synthetic capability check",
                    "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                                   "required": ["value"], "additionalProperties": False}}}], max_tokens=256), timeout=30)
            calls = response.get("tool_calls") or []
            ok = len(calls) == 1 and getattr(calls[0], "name", None) == "probe" and getattr(calls[0], "arguments", None) == {"value": "LILITH_PROBE"}
            return {"provider": cfg.provider, "tool_call_sample_passed": ok,
                    "suggested_profile": "compact" if ok else "reader", "tools_executed": 0,
                    "note": "Una muestra de formato, no una garantía de calidad o fiabilidad."}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError, httpx.HTTPError) as exc:
            return {"provider": cfg.provider, "tool_call_sample_passed": False,
                    "suggested_profile": "reader", "error": type(exc).__name__, "tools_executed": 0}
        finally:
            await provider.close()
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if not result["tool_call_sample_passed"]:
        raise SystemExit(1)


def configure_session(session, profile: str, skill: str | None = None):
    if profile not in PROFILES:
        raise ValueError("Perfil desconocido: usa agent, compact, standard, reader o coordinator")
    if profile == "agent":
        from . import agent_skills  # noqa: F401 - register read-only catalog tools
    settings = PROFILES[profile]
    session._execution_profile = profile
    session._context_char_limit = settings["context_chars"]
    session._tool_result_char_limit = settings["result_chars"]
    session._strict_tool_arguments = True
    session._serial_tools = True
    session.config.max_iterations = min(session.config.max_iterations, settings["iterations"])
    session.config.temperature = min(session.config.temperature, 0.3)
    session.config.max_tokens = min(session.config.max_tokens, settings["output_tokens"])
    selected = session.config.providers.get(session.config.provider.lower())
    if selected:
        selected.max_tokens = min(selected.max_tokens or settings["output_tokens"], settings["output_tokens"])
        if profile in ("compact", "reader") and session.config.provider.lower() == "deepseek":
            selected.thinking_enabled = False
    session._profile_allowed_tools = set(settings["tools"])
    session._profile_allowed_prefixes = tuple(sorted(settings.get("tool_prefixes", set())))
    session._tools_cache = None
    session.system_prompt += "\nTrabaja en pasos pequeños. Usa únicamente las herramientas disponibles. Una respuesta vacía o una prueba sin salida no es éxito."
    if profile == "reader":
        session.config.tools_enabled = False
        session._tools_enabled = False
    if profile == "coordinator":
        session.system_prompt += (
            "\nEres el agente principal. Divide el trabajo en encargos acotados cuando sea útil. "
            "No cambies de modelo ni proveedor por una respuesta fallida. Ante timeout de un colaborador, "
            "consulta cli_jobs_recent y cli_job_reference; no generes una request_id nueva para reintentar. "
            "Una salida 0 o una respuesta de otro agente no verifica el trabajo: revisa la evidencia. "
            "No confundas detener la espera con cancelar el worker. Respeta las decisiones de gasto, "
            "GPU, contenido privado y publicación del operador."
        )
    if skill:
        skills = catalog()
        if skill not in skills:
            raise ValueError("Skill desconocida: " + skill)
        session.system_prompt += "\n\n" + skills[skill].content
