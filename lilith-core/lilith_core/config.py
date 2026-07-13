"""Configuration loader for Yggdrasil ecosystem."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass
class YggdrasilConfig:
    """Root configuration for the Yggdrasil ecosystem."""

    root: Path = field(default_factory=lambda: Path.home() / ".lilith")
    version: str = "5.1.0"
    log_level: str = "INFO"
    log_file: str = "yggdrasil.log"

    # LLM
    model: str = "auto"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    orchestrator_port: int = 8001
    memory_port: int = 8002

    # LLM Providers
    lm_studio_url: str = "http://localhost:1234/v1"
    openai_api_base: str | None = None
    openai_api_key: str | None = None
    temperature: float = 0.7
    max_context: int = 8192

    # Security
    blocked_commands: list = field(
        default_factory=lambda: ["rm -rf", "format", "cipher", "shutdown"]
    )
    sensitive_files: list = field(default_factory=lambda: [".env", "*.key", "*.pem", "*.p12"])

    # Backwards-compat: accept root_path as alias for root
    def __init__(
        self, root_path: Path | str | None = None, root: Path | str | None = None, **kwargs
    ):
        # Determine root: root_path takes precedence for backwards compat
        resolved_root = root_path or root
        if resolved_root is not None:
            kwargs["root"] = Path(resolved_root)
        else:
            kwargs["root"] = Path.home() / ".lilith"

        # Set defaults for fields not provided
        defaults = self._field_defaults()
        for k, v in defaults.items():
            if k not in kwargs and k != "root":
                kwargs[k] = v

        # Assign all fields
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)

        # Create root directory
        self.root.mkdir(parents=True, exist_ok=True)

        # Load persisted values from config.json
        if self.config_file.exists():
            with open(self.config_file, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                object.__setattr__(self, k, v)

        # Post-init logic
        self._post_init()

    @classmethod
    def _field_defaults(cls) -> dict[str, Any]:
        """Return default values for all fields."""
        return {
            "version": "5.1.0",
            "log_level": "INFO",
            "log_file": "yggdrasil.log",
            "model": "auto",
            "api_host": "0.0.0.0",
            "api_port": 8000,
            "orchestrator_port": 8001,
            "memory_port": 8002,
            "lm_studio_url": "http://localhost:1234/v1",
            "openai_api_base": None,
            "openai_api_key": None,
            "temperature": 0.7,
            "max_context": 8192,
            "blocked_commands": ["rm -rf", "format", "cipher", "shutdown"],
            "sensitive_files": [".env", "*.key", "*.pem", "*.p12"],
        }

    def _post_init(self):
        """Run post-initialization logic."""
        if self.root.exists():
            load_dotenv(self.root / ".env")
        self.openai_api_base = os.getenv("OPENAI_API_BASE", self.openai_api_base)
        self.openai_api_key = os.getenv("OPENAI_API_KEY", self.openai_api_key)

    @property
    def config_file(self) -> Path:
        """Path to the config JSON file."""
        return self.root / "config.json"

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by key."""
        return getattr(self, key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a config value and persist to disk."""
        object.__setattr__(self, key, value)
        self.root.mkdir(parents=True, exist_ok=True)
        data = {}
        if self.config_file.exists():
            with open(self.config_file, encoding="utf-8") as f:
                data = json.load(f)
        data[key] = value
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _defaults(self) -> dict[str, Any]:
        """Return default configuration values (4 core keys)."""
        all_defaults = self._field_defaults()
        return {
            k: all_defaults[k] for k in ["model", "lm_studio_url", "max_context", "temperature"]
        }

    @classmethod
    def load(cls, path: Path | None = None) -> "YggdrasilConfig":
        """Load config from YAML or defaults."""
        cfg = cls()
        if path and path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            for k, v in data.items():
                if hasattr(cfg, k):
                    object.__setattr__(cfg, k, v)
        return cfg

    @property
    def asgard(self) -> Path:
        return self.root / "Asgard"

    @property
    def realms(self) -> list[Path]:
        return [
            self.root / r
            for r in [
                "Asgard",
                "Alfheim",
                "Vanaheim",
                "Muspelheim",
                "Niflheim",
                "Svartalfheim",
                "Midgard",
                "Helheim",
                "Jotunheim",
            ]
        ]


_config: YggdrasilConfig | None = None


def get_config() -> YggdrasilConfig:
    """Get or create global config singleton."""
    global _config
    if _config is None:
        _config = YggdrasilConfig.load()
    return _config


# Backwards-compat alias
Config = YggdrasilConfig
