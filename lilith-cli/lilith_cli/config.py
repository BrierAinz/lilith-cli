"""YAML configuration loader for Yggdrasil CLI v6.0.

Reads ``~/.yggdrasil/config.yaml``, supports ``${ENV_VAR}`` interpolation
for secrets, and auto-creates the config directory and default file on
first run.
"""

from __future__ import annotations

import os
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# Lilith's supported inference surface is intentionally small. Keep these
# constants in config.py so every entry point and migration shares one catalog.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("deepseek", "grok", "sakana")
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "grok": "grok-4.20-0309-non-reasoning",
    "sakana": "fugu-ultra",
}
SUPPORTED_MODELS: dict[str, frozenset[str]] = {
    "deepseek": frozenset({"deepseek-v4-flash", "deepseek-v4-pro"}),
    "grok": frozenset(
        {
            "grok-4.20-0309-non-reasoning",
            "grok-4.20-0309-reasoning",
            "grok-4.20-multi-agent-0309",
            "grok-4.3",
            "grok-4.5",
        }
    ),
    "sakana": frozenset(
        {
            "fugu",
            "fugu-ultra",
            "fugu-ultra-v1.0",
            "fugu-ultra-v1.1",
        }
    ),
}
PROVIDER_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com/v1",
    "grok": "https://api.x.ai/v1",
    "sakana": "https://api.sakana.ai/v1",
}
PROVIDER_ENV_KEYS: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "grok": "XAI_API_KEY",
    "sakana": "SAKANA_API_KEY",
}


def require_supported_provider(name: str) -> str:
    """Return a normalized provider name or reject unsupported backends."""
    normalized = str(name or "").strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        available = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Proveedor no soportado: {name!r}. Disponibles: {available}")
    return normalized


def require_supported_model(provider: str, model: str) -> str:
    """Validate that *model* belongs to Lilith's current provider catalog."""
    normalized_provider = require_supported_provider(provider)
    normalized_model = str(model or "").strip()
    if normalized_model not in SUPPORTED_MODELS[normalized_provider]:
        available = ", ".join(sorted(SUPPORTED_MODELS[normalized_provider]))
        raise ValueError(
            f"Modelo no soportado para {normalized_provider}: {model!r}. "
            f"Disponibles: {available}"
        )
    return normalized_model


# ── Pydantic models ────────────────────────────────────────────────


class ToolsConfig(BaseModel):
    """Feature flags for each tool category."""

    filesystem: bool = True
    coding: bool = True
    web_search: bool = True
    browser: bool = True
    system: bool = True
    tool_timeout: int = 30
    retry_count: int = 2
    retry_backoff: float = 1.0


class MemoryConfig(BaseModel):
    """Memory store configuration."""

    enabled: bool = True
    db_path: str = "~/.yggdrasil/memory.db"


class HistoryConfig(BaseModel):
    """Conversation history configuration."""

    max_turns: int = 50
    save: bool = True


class ProviderProfile(BaseModel):
    """Optional per-provider profile overrides."""

    enabled: bool = True
    optional: bool = False
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    doctor_timeout: float = Field(default=5.0, gt=0, le=60)
    circuit_breaker_failures: int = Field(default=2, ge=1, le=20)
    circuit_breaker_cooldown: float = Field(default=60.0, ge=1, le=86400)


class MCPServerConfig(BaseModel):
    """One MCP (Model Context Protocol) server entry.

    Only the stdio transport is wired in this tanda; the ``mcp`` Python
    SDK (already a transitive dep of ``lilith-tools``) spawns the
    server process and exchanges JSON-RPC 2.0 over its stdin/stdout.

    Each enabled server is started lazily at REPL boot (after the
    welcome banner) and its tools are mounted into the global
    :class:`lilith_tools.ToolRegistry` with synthetic names
    ``mcp_<server>_<tool>``. A broken subprocess never aborts the
    session — the manager logs the failure and exposes it through
    ``/mcp list``.
    """

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    enabled: bool = True
    # Per-call timeout in seconds. The synthetic tool's
    # ``timeout_seconds`` is set to the same value so the agent loop
    # honours it as a floor.
    timeout: float = 30.0


class YggdrasilConfig(BaseModel):
    """Root configuration model for the Yggdrasil CLI agent.

    Lilith supports the OpenAI-compatible DeepSeek, xAI and Sakana Fugu
    endpoints. DeepSeek remains the economical installation default; the
    active provider is selected from the user's configuration.
    """

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODELS[DEFAULT_PROVIDER]
    api_key: str | None = None
    base_url: str | None = None
    system_prompt: str = (
            "You are Lilith, the orchestrator of the Yggdrasil ecosystem. "
            "You spawn, coordinate, and synthesise the work of at least five "
            "sub-agents (Hela, Mimir, Skadi, …); you do not perform every task "
            "yourself. Delegate, gather, and decide. You are wise, precise, and "
            "concise. You think step-by-step and use tools when appropriate. "
            "Where Ancient Meets Digital.\n"
            "\n"
            "## v8 Verified Orchestration — use it proactively\n"
            "\n"
            "1. At session start, call `orchestration_state get` to resume any "
            "pending plan, then call `resume_expired` before starting new work.\n"
            "2. Register tasks with explicit `success_criteria`, `budget`, and "
            "`idempotency_key` via `orchestration_state add_task` / `update_task` "
            "BEFORE delegating.\n"
            "3. Delegate with `delegate_subagent` — pick the preset and knobs: "
            "`agentic=true` for work that writes files (mini-loop, sandboxed), "
            "`structured=true` for reports (validated schema), "
            "`max_tokens` to override the preset limit.\n"
            "4. For recurring workflows, use `skill_run` (or `/skills`) instead of "
            "re-deriving prompts; list the catalog first.\n"
            "5. Before execution, `claim` the task; write `checkpoint` records, "
            "renew long leases, and `release` or `cancel` every claimed task.\n"
            "6. Choose routes from task shape plus `post_mortems`; record why. "
            "Use `memory_save` with fact_type, namespace, source and provenance "
            "for stable knowledge, and `memory_evidence` when facts change.\n"
            "7. Safeguards: respect provider circuit breakers and task budgets. "
            "If the same tool fails twice in a row, change "
            "strategy or escalate; do not retry a third time the same way.\n"
            "8. For large files, write the head with `file_write` and append "
            "chunks with `file_append` — never try to inline huge blobs.\n"
            "9. Verify every success criterion with tests or artifact evidence "
            "before completion; inspect `events`/`timeline` by correlation ID "
            "and report verified results, not intent."
        )
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    providers: dict[str, ProviderProfile] = Field(default_factory=dict)
    # MCP servers whose tools should be mounted into the global
    # ``ToolRegistry`` at REPL boot. See ``MCPServerConfig`` for the
    # stdio-only contract. Disabled servers stay in the config but are
    # not started; ``/mcp reload <name>`` brings them up later. The
    # default is ``None`` rather than ``{}`` so that an explicit
    # ``mcp_servers:`` key in YAML (even an empty mapping or ``null``)
    # doesn't trip Pydantic with a ``None is not a dict`` error.
    mcp_servers: dict[str, MCPServerConfig] | None = None

    @property
    def effective_mcp_servers(self) -> dict[str, MCPServerConfig]:
        """Return ``mcp_servers`` as a (possibly empty) dict.

        The underlying field is ``dict | None`` because we want YAML
        keys that are explicitly set to ``null`` (or an empty mapping)
        to round-trip without a Pydantic validation error. Every
        consumer in ``lilith_cli`` uses this property so they can
        iterate unconditionally.
        """
        return self.mcp_servers or {}
    confirm_write: bool = True
    # Maximum tool-calling loop iterations per user message. Guards against
    # runaway loops; the last iteration receives a soft-warning system
    # message asking the model to wrap up.
    max_iterations: int = 10
    # Agent operating mode (default, plan-first, review-only, auto-edit).
    agent_mode: str = "default"

    # ── HTTP retry policy for the LLM wrapper ────────────────────────
    # The wrapper re-issues transient HTTP failures (429, 5xx, connection
    # resets) with exponential back-off plus jitter, and respects the
    # Retry-After header when the server provides one. Non-transient
    # failures (4xx other than 429, programming errors) are NOT retried.
    retry_max: int = 3
    retry_backoff_base: float = 1.0
    retry_backoff_max: float = 30.0
    # Jitter expressed as a fraction of the computed back-off (e.g.
    # 0.25 → ±25 % random spread). 0.0 disables jitter.
    retry_jitter: float = 0.25

    @field_validator("max_iterations")
    @classmethod
    def _validate_max_iterations(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_iterations must be >= 1")
        return value

    @field_validator("retry_max")
    @classmethod
    def _validate_retry_max(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry_max must be >= 0")
        return value

    @field_validator("retry_backoff_base")
    @classmethod
    def _validate_retry_backoff_base(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("retry_backoff_base must be > 0")
        return value

    @field_validator("retry_backoff_max")
    @classmethod
    def _validate_retry_backoff_max(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("retry_backoff_max must be > 0")
        return value

    @field_validator("retry_jitter")
    @classmethod
    def _validate_retry_jitter(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("retry_jitter must be in [0, 1]")
        return value

    model_config = {"extra": "ignore"}  # Allow extra keys for forward-compatibility


# ── Env-var interpolation ──────────────────────────────────────────

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ``${VAR}`` with the value of the environment
    variable *VAR*.  If the variable is not set the placeholder is left
    unchanged.
    """
    if isinstance(value, str):

        def _replacer(m: re.Match) -> str:
            env_val = os.environ.get(m.group(1))
            return env_val if env_val is not None else m.group(0)

        return _ENV_PATTERN.sub(_replacer, value)

    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]

    return value


# ── Default config YAML ─────────────────────────────────────────────

_DEFAULT_CONFIG_YAML = """\
# Yggdrasil CLI v6.0 configuration
# See https://github.com/BrierAinz/Yggdrasil for docs
#
# Lilith acts as the orchestrator: it spawns, coordinates and
# synthesises sub-agent work. Its inference surface is deliberately
# limited to DeepSeek, Grok and Sakana Fugu.

provider: deepseek
model: deepseek-v4-flash
api_key: ${DEEPSEEK_API_KEY}
base_url: https://api.deepseek.com/v1

system_prompt: >
  You are Lilith, the orchestrator of the Yggdrasil ecosystem.
  You spawn, coordinate, and synthesise the work of at least five
  sub-agents (Hela, Mimir, Skadi, …); you do not perform every task
  yourself. Delegate, gather, and decide. You are wise, precise, and
  concise. You think step-by-step and use tools when appropriate.
  Where Ancient Meets Digital.
  ## v8 Verified Orchestration — use it proactively
  1. At session start, call `orchestration_state get` to resume any pending plan, then call `resume_expired` before starting new work.
  2. Register tasks with explicit `success_criteria`, `budget`, and `idempotency_key` via `orchestration_state add_task` / `update_task` BEFORE delegating.
  3. Delegate with `delegate_subagent` — pick the preset and knobs: `agentic=true` for work that writes files (mini-loop, sandboxed), `structured=true` for reports (validated schema), `max_tokens` to override the preset limit.
  4. For recurring workflows, use `skill_run` (or `/skills`) instead of re-deriving prompts; list the catalog first.
  5. Before execution, `claim` the task; write `checkpoint` records, renew long leases, and `release` or `cancel` every claimed task.
  6. Choose routes from task shape plus `post_mortems`; record why. Use `memory_save` with fact_type, namespace, source and provenance for stable knowledge, and `memory_evidence` when facts change.
  7. Safeguards: respect provider circuit breakers and task budgets. If the same tool fails twice in a row, change strategy or escalate; do not retry a third time the same way.
  8. For large files, write the head with `file_write` and append chunks with `file_append` — never try to inline huge blobs.
  9. Verify every success criterion with tests or artifact evidence before completion; inspect `events`/`timeline` by correlation ID and report verified results, not intent.

temperature: 0.7
max_tokens: 4096

tools:
  filesystem: true
  coding: true
  web_search: true
  browser: true
  system: true
  tool_timeout: 30
  retry_count: 2
  retry_backoff: 1.0

memory:
  enabled: true
  db_path: ~/.yggdrasil/memory.db

history:
  max_turns: 50
  save: true

# Safety: require diff preview before destructive file_write/file_edit.
confirm_write: true

# Maximum tool-calling loop iterations per user message (>=1).
max_iterations: 10

# Agent operating mode (default, plan-first, review-only, auto-edit).
agent_mode: default

# ── HTTP retry policy for the LLM wrapper ────────────────────────
# Transient failures (429, 5xx, connection resets) are retried with
# exponential back-off + jitter, and the Retry-After header is
# honoured when the server provides one. Non-transient failures
# (4xx other than 429) are NOT retried.
retry_max: 3
retry_backoff_base: 1.0
retry_backoff_max: 30.0
retry_jitter: 0.25

# ── Provider profiles ──────────────────────────────────────────
# Each profile maps a short name (used by `--provider <name>` or by
# the sub-agent system) to its own base_url / api_key / model.
# Lilith's main session can use these three provider profiles.
providers:

  # ── DeepSeek ──
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
    model: deepseek-v4-flash

  # ── xAI / Grok ──
  grok:
    api_key: ${XAI_API_KEY}
    base_url: https://api.x.ai/v1
    model: grok-4.20-0309-non-reasoning

  # ── Sakana Fugu ──
  sakana:
    api_key: ${SAKANA_API_KEY}
    base_url: https://api.sakana.ai/v1
    model: fugu-ultra

# ── MCP servers (stdio only this tanda) ───────────────────────────
# Each entry spawns a subprocess at REPL boot and mounts every tool
# the server advertises into the global ``ToolRegistry`` as
# ``mcp_<server>_<tool>``. Set ``enabled: false`` to keep the entry
# around without starting it; ``/mcp reload <server>`` brings it up.
# NOTE: this section is a sibling of ``providers:`` at the top level;
# indenting it under ``providers:`` will make pydantic reject the file.
mcp_servers:

  # Example: a local fake server bundled with lilith-tools for tests.
  # Uncomment to try it in a REPL session:
  # fake:
  #   command: python
  #   args: ["-m", "lilith_tools.fake_mcp_server"]
  #   enabled: false
  #   timeout: 10.0
"""

# ── Config directory / file helpers ─────────────────────────────────

CONFIG_DIR = Path.home() / ".yggdrasil"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


def _ensure_config_dir() -> None:
    """Create the config directory and write a default config file if
    none exists.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")


def find_project_config() -> Path | None:
    """Walk up from the current working directory looking for ``.lilith/config.yaml``.

    Returns the path if found, None otherwise. The project config can override
    settings from the global config (loaded by ``load_config``).
    """
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".lilith" / "config.yaml"
        if candidate.is_file():
            logger.debug("Found project config at %s", candidate)
            return candidate
    return None


def _merge_yaml_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, with *override* winning.

    Empty / None values in *override* are skipped so they don't wipe out
    real values in *base*.
    """
    result = dict(base)
    for key, value in (override or {}).items():
        if value is None or value == "" or value == {}:
            continue
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _merge_yaml_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _restrict_provider_config(
    raw_yaml: dict[str, Any],
    *,
    use_environment: bool = True,
) -> dict[str, Any]:
    """Migrate provider settings to the current provider allow-list.

    Old configuration files may still select Kimi, MiniMax or local
    profiles. They are ignored in memory and never reach an HTTP client. The
    on-disk file remains untouched until the user explicitly saves or migrates
    it, which avoids rewriting unrelated settings during a normal startup.
    """
    data = dict(raw_yaml)
    raw_providers = data.get("providers")
    if not isinstance(raw_providers, dict):
        raw_providers = {}

    filtered: dict[str, dict[str, Any]] = {}
    removed: list[str] = []
    for raw_name, raw_profile in raw_providers.items():
        name = str(raw_name).strip().lower()
        if name not in SUPPORTED_PROVIDERS:
            removed.append(str(raw_name))
            continue
        profile = dict(raw_profile) if isinstance(raw_profile, dict) else {}
        if not profile.get("api_key"):
            profile["api_key"] = (
                os.environ.get(PROVIDER_ENV_KEYS[name])
                if use_environment
                else f"${{{PROVIDER_ENV_KEYS[name]}}}"
            )
        if profile.get("model") not in SUPPORTED_MODELS[name]:
            profile["model"] = DEFAULT_MODELS[name]
        profile["base_url"] = PROVIDER_BASE_URLS[name]
        filtered[name] = profile

    for name in SUPPORTED_PROVIDERS:
        if name in filtered:
            continue
        filtered[name] = {
            "api_key": (
                os.environ.get(PROVIDER_ENV_KEYS[name])
                if use_environment
                else f"${{{PROVIDER_ENV_KEYS[name]}}}"
            ),
            "base_url": PROVIDER_BASE_URLS[name],
            "model": DEFAULT_MODELS[name],
        }

    selected = str(data.get("provider") or DEFAULT_PROVIDER).strip().lower()
    if selected not in SUPPORTED_PROVIDERS:
        selected = DEFAULT_PROVIDER
    profile = filtered[selected]
    model = str(data.get("model") or "").strip()
    if model not in SUPPORTED_MODELS[selected]:
        model = str(profile.get("model") or DEFAULT_MODELS[selected])

    data["provider"] = selected
    data["model"] = model
    data["providers"] = filtered
    data["api_key"] = profile.get("api_key")
    data["base_url"] = PROVIDER_BASE_URLS[selected]

    if removed:
        logger.info("Ignoring unsupported provider profiles: %s", ", ".join(sorted(removed)))
    return data


def load_config(config_path: Path | str | None = None) -> YggdrasilConfig:
    """Load and parse the YAML config, returning a validated
    :class:`YggdrasilConfig`.

    Parameters
    ----------
    config_path:
        Explicit path to a YAML file.  Falls back to
        ``~/.yggdrasil/config.yaml``.

    """
    path = Path(config_path) if config_path else CONFIG_FILE

    if not path.exists():
        # Bootstrap the default config so the user can edit it.
        _ensure_config_dir()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")

    # ── Load .env before interpolation so ${VAR} placeholders resolve ──
    # Order matters:
    #   1. ~/.env (canonical Lilith, overrides everything)
    #   2. ~/.hermes/.env (Hermes home, lower priority — non-overriding)
    #   3. %APPDATA%/Local/hermes/.env (Hermes install, lowest priority)
    # The canonical ~/.env wins because Lilith writes there as the source
    # of truth. Without override=False on the second/third files, stale
    # values from Hermes-managed env files would clobber the canonical key.
    _canonical = Path.home() / ".env"
    if _canonical.exists():
        try:
            from dotenv import load_dotenv as _ld

            _ld(_canonical, override=True)
        except Exception:
            pass

    for env_path in (
        Path.home() / ".hermes" / ".env",
        Path(os.environ.get("APPDATA", "")) / "Local" / "hermes" / ".env",
    ):
        if env_path.exists():
            try:
                from dotenv import load_dotenv as _ld

                _ld(env_path, override=False)
            except Exception:
                pass

    raw_text = path.read_text(encoding="utf-8")
    raw_yaml: dict[str, Any] = yaml.safe_load(raw_text) or {}

    # Interpolate environment variables.
    raw_yaml = _interpolate_env(raw_yaml)

    # Expand ~ in db_path.
    if "memory" in raw_yaml and isinstance(raw_yaml["memory"], dict):
        db_path = raw_yaml["memory"].get("db_path")
        if db_path and isinstance(db_path, str):
            raw_yaml["memory"]["db_path"] = str(Path(db_path).expanduser())

    # ── Merge project-level config (.lilith/config.yaml walking up from cwd) ──
    # Only applied when the user didn't pass an explicit config_path.
    if config_path is None:
        project_path = find_project_config()
        if project_path is not None:
            try:
                project_yaml = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                if isinstance(project_yaml, dict):
                    raw_yaml = _merge_yaml_dicts(raw_yaml, project_yaml)
                    logger.debug("Merged project config from %s", project_path)
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("Could not load project config %s: %s", project_path, exc)

    raw_yaml = _restrict_provider_config(raw_yaml)
    return YggdrasilConfig(**raw_yaml)


def save_config(config: YggdrasilConfig, config_path: Path | str | None = None) -> None:
    """Serialize a :class:`YggdrasilConfig` back to YAML on disk.

    Environment-variable placeholders are **not** round-tripped;
    actual values are written.  This is intentional: the file is a
    snapshot, not a template.
    """
    path = Path(config_path) if config_path else CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    # Convert paths back to strings for YAML serialisation.
    db_path = data.get("memory", {}).get("db_path")
    if db_path:
        data["memory"]["db_path"] = str(db_path)

    path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True), encoding="utf-8")


def migrate_provider_config_file(config_path: Path | str | None = None) -> Path:
    """Persist the provider migration without resolving secret placeholders."""
    path = Path(config_path) if config_path else CONFIG_FILE
    raw_yaml = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    migrated = _restrict_provider_config(raw_yaml, use_environment=False)
    path.write_text(
        yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def activate_provider_config_file(
    provider: str,
    config_path: Path | str | None = None,
    *,
    model: str | None = None,
) -> Path:
    """Persist one supported provider as the active Lilith route.

    The file is migrated first without resolving environment placeholders, so
    this operation never copies an API key from the process into YAML.
    """
    selected = require_supported_provider(provider)
    selected_model = require_supported_model(
        selected,
        model or DEFAULT_MODELS[selected],
    )
    path = Path(config_path) if config_path else CONFIG_FILE
    raw_yaml = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    activated = _restrict_provider_config(raw_yaml, use_environment=False)
    profile = activated["providers"][selected]
    profile["model"] = selected_model
    profile["base_url"] = PROVIDER_BASE_URLS[selected]
    if not profile.get("api_key"):
        profile["api_key"] = f"${{{PROVIDER_ENV_KEYS[selected]}}}"
    activated["provider"] = selected
    activated["model"] = selected_model
    activated["api_key"] = profile["api_key"]
    activated["base_url"] = PROVIDER_BASE_URLS[selected]
    path.write_text(
        yaml.safe_dump(activated, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
