"""YAML configuration loader for Yggdrasil CLI v6.0.

Reads ``~/.yggdrasil/config.yaml``, supports ``${ENV_VAR}`` interpolation
for secrets, and auto-creates the config directory and default file on
first run.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


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


class CampaignBudgetConfig(BaseModel):
    """Aggregate autonomy limits. ``None`` means unlimited."""

    enabled: bool = True
    daily_max_calls: int | None = Field(default=None, ge=1)
    daily_max_tokens: int | None = Field(default=None, ge=1)
    daily_max_usd: float | None = Field(default=0.0, ge=0)
    campaign_max_calls: int | None = Field(default=None, ge=1)
    campaign_max_tokens: int | None = Field(default=None, ge=1)
    campaign_max_usd: float | None = Field(default=0.0, ge=0)
    provider_daily_max_calls: dict[str, int] = Field(default_factory=dict)
    provider_daily_max_tokens: dict[str, int] = Field(default_factory=dict)
    provider_daily_max_usd: dict[str, float] = Field(default_factory=dict)


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
    thinking_enabled: bool | None = None


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
    """Compatibility-named root config for the canonical Lilith CLI."""

    provider: str = "fabric"
    model: str = "router"
    api_key: str | None = None
    base_url: str | None = None
    system_prompt: str = """You are Lilith, Queen / Prime Orchestrator beside Ainz / Overlord.
Own each mission end-to-end: inspect, choose a route, execute, verify, learn, and report.

## Mission operating contract
1. Inspect the real workspace and evidence before planning changes.
2. Ask the operator only when a material choice changes scope, risk, cost, canon, or irreversible outcome; present 2-4 options with one recommendation.
3. Register durable work with mission_prepare, explicit success_criteria, budget, verification, and recovery.
4. Assemble the Court with mission_court and mission_compute; delegate via mission_delegate or mission_conclave. Court identities are independent from providers.
5. Reuse verified skills through mission_skill_run and MCP tools instead of re-deriving routine workflows.
6. Treat reversible work on D: and governed admin work on C: as autonomous; use mission_authority. External publication, money, credentials, or truly irreversible actions require the Overlord gate.
7. For long work, use longrun, leases, heartbeat, checkpoint, and resume semantics. Never duplicate an effect whose outcome is unknown.
8. If an action fails, diagnose the cause, change strategy, repair, and retry within budget instead of asking about ordinary failures.
9. Learn from post_mortems and memory_evidence; respect circuit breakers, compute health, aggregate budgets, and rollback evidence.
10. Use file_write/file_edit/file_append carefully, preserve exact bytes when required, and keep changes inside mission scope.
11. Call mission_complete only after verify or deterministic artifact/test evidence. Campaign Mode may queue related descendant missions within the same authority and budget.

Visible Court: Demiurge, Pandora's Actor, Sebas, Cocytus, Shalltear, Aura, and Mare.
Legacy adapter names are implementation details, not agent identities.
"""
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    providers: dict[str, ProviderProfile] = Field(default_factory=dict)
    campaign_budgets: CampaignBudgetConfig = Field(default_factory=CampaignBudgetConfig)
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
    # Terminal kill switch set by ``--no-tools``. Category flags remain for
    # selective configuration, but this value denies the entire tool surface.
    tools_enabled: bool = True
    # Agent operating mode (default, plan-first, review-only, auto-edit).
    agent_mode: str = "default"
    execution_profile: str = "standard"
    # Paneles en vivo de herramientas y delegacion. Apagados por omision:
    # el operador pidio el 2026-09-15 "solo su pensamiento y lo que dice al
    # final", y esos paneles son el proceso. Ponlo en true para verlos.
    show_tool_panels: bool = False
    require_calibration_for_edits: bool = False
    calibration_report: str | None = None
    # Local Yggdrasil Fabric transport. The shared token is referenced by name
    # and never persisted in this model or printed by diagnostics.
    fabric_url: str = "http://127.0.0.1:8788"
    fabric_token_env: str = "YGGDRASIL_FABRIC_TOKEN"
    fabric_capability: str = "code.read"

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
# Lilith CLI canonical configuration
# Queen / Mission / Court architecture. Yggdrasil is not the current control plane.

provider: fabric
model: router

system_prompt: |
  You are Lilith, Queen / Prime Orchestrator beside Ainz / Overlord.
  Own each mission end-to-end: inspect, choose a route, execute, verify, learn, and report.

  ## Mission operating contract
  1. Inspect the real workspace and evidence before planning changes.
  2. Ask only for material choices; present 2-4 options with one recommendation.
  3. Register durable work with mission_prepare, success_criteria, budget, verification, and recovery.
  4. Assemble Court with mission_court and mission_compute; use mission_delegate or mission_conclave.
  5. Reuse verified skills with mission_skill_run and MCP tools.
  6. Reversible D: and governed C: admin work is autonomous; use mission_authority. External publication, money, credentials, and irreversible actions keep the Overlord gate.
  7. Long work uses longrun, leases, heartbeat, checkpoint, and resume semantics.
  8. On ordinary failure, diagnose, change strategy, repair, and retry within budget.
  9. Learn from post_mortems and memory_evidence; respect circuit breakers, compute health, aggregate budgets, and rollback evidence.
  10. Use file_write, file_edit, and file_append carefully and preserve exact bytes when required.
  11. mission_complete requires verify or deterministic evidence; Campaign Mode may queue bounded descendants.

  Visible Court: Demiurge, Pandora's Actor, Sebas, Cocytus, Shalltear, Aura, and Mare.
  Legacy adapter names are implementation details, not agent identities.

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

# Aggregate Campaign Runtime budgets. Null means unlimited.
# USD defaults to zero aggregate paid spend; free/subscription routes remain usable.
campaign_budgets:
  enabled: true
  daily_max_calls: null
  daily_max_tokens: null
  daily_max_usd: 0.0
  campaign_max_calls: null
  campaign_max_tokens: null
  campaign_max_usd: 0.0
  provider_daily_max_calls: {}
  provider_daily_max_tokens: {}
  provider_daily_max_usd: {}

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
# Lilith's main session uses the top-level ``provider`` above.
# Court compute is routed independently from these compatibility profiles.
providers:

  # ── Sub-agent profile: MiniMax (Anthropic-compatible) ──
  # Used by sub-agents that need a strong general model. Verified
  # models: MiniMax-M3, M2.7, M2.7-highspeed.
  minimax:
    api_key: ${MINIMAX_API_KEY}
    base_url: https://api.minimax.io/anthropic
    model: MiniMax-M3

  # ── Sub-agent profile: OpenCode Go (GLM-5.2) ──
  # OpenCode's gateway exposes many models; we hard-pin to glm-5.2.
  opencode-go:
    api_key: ${OPENCODE_API_KEY}
    base_url: https://opencode.ai/zen/go/v1
    model: glm-5.2

  # Other examples (preserved from the previous default config):
  # anthropic:
  #   api_key: ${ANTHROPIC_API_KEY}
  #   model: claude-sonnet-4-20250514
  # ollama:
  #   base_url: http://localhost:11434
  #   model: llama3
  # local:
  # local:
  #   base_url: http://localhost:1234/v1
  #   model: local-model

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

    override = os.environ.get("LILITH_PROVIDER_OVERRIDE", "").strip().lower()
    if override:
        raw_yaml["provider"] = override
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
