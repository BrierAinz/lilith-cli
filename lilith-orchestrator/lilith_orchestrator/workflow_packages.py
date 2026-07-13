"""Workflow Packages — multi-file persisted workflow bundles.

Inspired by Neurosurfer's workflow package system (workflow.yaml + graph.yaml +
per-agent overrides). A workflow package is a directory on disk that holds:

    <name>/
      workflow.yaml   ← manifest (name, version, description, entrypoint, tags, ...)
      graph.yaml      ← workflow definition (the actual steps/edges/outputs)
      agents/<id>.yaml ← optional per-step overrides (merged into matching steps)
      README.md       ← optional human-readable docs
      schemas.py      ← optional pydantic output-schema classes referenced by steps

The loader reads the manifest, then the graph file named by `entrypoint`
(default `graph.yaml`), then merges any per-step overrides from `agents/`
into matching steps by name. The result is a `WorkflowPackage` dataclass
that can be passed to `WorkflowEngine.run()` or registered for later use.

Use case: lets you version a complex workflow (with helpers, overrides, docs)
as a single directory you can `git tag`, share, or ship inside `.ygg/workflows/`.

Example::

    from lilith_orchestrator.workflow_packages import WorkflowPackageRegistry

    registry = WorkflowPackageRegistry()  # defaults to .ygg/workflows/
    registry.save(my_pkg)                  # writes to .ygg/workflows/<name>/
    pkg = registry.get("code-review")
    result = engine.run(pkg.workflow)

This module is intentionally filesystem-only (no DB) so packages are git-friendly.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lilith_orchestrator.workflow import GateType, WorkflowDefinition, WorkflowStep

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ENTRYPOINT",
    "PackageLoadError",
    "WorkflowPackage",
    "WorkflowPackageManifest",
    "WorkflowPackageRegistry",
    "WorkflowPackageNotFoundError",
    "default_workflows_root",
    "load_package",
    "save_package",
]


DEFAULT_ENTRYPOINT = "graph.yaml"
MANIFEST_FILENAME = "workflow.yaml"
AGENTS_DIRNAME = "agents"


# ── Manifest ────────────────────────────────────────────────────────────────


@dataclass
class WorkflowPackageManifest:
    """Top-level metadata stored in ``workflow.yaml``.

    Attributes:
        name: Unique workflow name (must match the directory name).
        version: Semver-ish version string (default "1.0.0").
        description: Human-readable description.
        entrypoint: Filename of the graph YAML inside the package (default
            ``graph.yaml``).
        tags: Free-form tags for filtering/search.
        author: Optional author/owner identifier.
        created_at: ISO-8601 timestamp (informational only).
    """

    name: str
    version: str = "1.0.0"
    description: str = ""
    entrypoint: str = DEFAULT_ENTRYPOINT
    tags: list[str] = field(default_factory=list)
    author: str | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowPackageManifest:
        """Build a manifest from a parsed YAML dict.

        Raises:
            KeyError: If the mandatory ``name`` field is missing.
            TypeError: If a field has the wrong type.
        """
        if "name" not in data:
            raise KeyError("workflow.yaml is missing required field 'name'")
        return cls(
            name=str(data["name"]),
            version=str(data.get("version", "1.0.0")),
            description=str(data.get("description", "")),
            entrypoint=str(data.get("entrypoint", DEFAULT_ENTRYPOINT)),
            tags=list(data.get("tags") or []),
            author=data.get("author"),
            created_at=data.get("created_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML dumping."""
        out: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entrypoint": self.entrypoint,
        }
        if self.tags:
            out["tags"] = list(self.tags)
        if self.author:
            out["author"] = self.author
        if self.created_at:
            out["created_at"] = self.created_at
        return out


# ── Package ──────────────────────────────────────────────────────────────────


@dataclass
class WorkflowPackage:
    """In-memory representation of a multi-file workflow package.

    Attributes:
        manifest: The package manifest metadata.
        workflow: The parsed `WorkflowDefinition` (graph steps/gates).
        path: Absolute path to the package directory on disk.
        agents_dir: Optional path to the per-agent overrides directory
            (may not exist on disk if there are no overrides).
    """

    manifest: WorkflowPackageManifest
    workflow: WorkflowDefinition
    path: Path
    agents_dir: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def tags(self) -> list[str]:
        return list(self.manifest.tags)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the package for inspection/logging (NOT for round-trip)."""
        return {
            "manifest": self.manifest.to_dict(),
            "workflow": self.workflow.to_dict() if hasattr(self.workflow, "to_dict") else None,
            "path": str(self.path),
            "agents_dir": str(self.agents_dir) if self.agents_dir else None,
        }


# ── Errors ───────────────────────────────────────────────────────────────────


class PackageLoadError(Exception):
    """Raised when a workflow package directory cannot be loaded."""


class WorkflowPackageNotFoundError(KeyError):
    """Raised when the registry is asked for a package that doesn't exist."""


# ── Filesystem helpers ───────────────────────────────────────────────────────


def default_workflows_root() -> Path:
    """Return the default workflows root, creating it if necessary.

    Resolution order:
        1. ``$YGG_WORKFLOWS_DIR`` if set.
        2. ``.ygg/workflows/`` relative to the current working directory.

    Returns:
        Absolute path to the workflows root directory.
    """
    import os

    env = os.environ.get("YGG_WORKFLOWS_DIR")
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = (Path.cwd() / ".ygg" / "workflows").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── Loaders / savers ─────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its parsed contents.

    Uses PyYAML when available; falls back to the workflow engine's minimal
    parser (sufficient for the simple key/list/mapping structures used in
    workflow manifests and graph files).
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text)
    except ImportError:
        # Lazy import to avoid circular dependency
        from lilith_orchestrator.workflow import WorkflowEngine

        data = WorkflowEngine()._parse_yaml_minimal(text)
    return data or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a dict to a YAML file, falling back to JSON when PyYAML is absent."""
    text: str
    try:
        import yaml  # type: ignore[import-untyped]

        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    except ImportError:
        import json

        text = json.dumps(data, indent=2, ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_package(path: str | Path) -> WorkflowPackage:
    """Load a :class:`WorkflowPackage` from a directory on disk.

    Steps:
        1. Read ``workflow.yaml`` → :class:`WorkflowPackageManifest`.
        2. Read the graph file named by ``manifest.entrypoint``
           (default ``graph.yaml``) → :class:`WorkflowDefinition`.
        3. Apply any per-step overrides from ``agents/<step_name>.yaml``
           (merged on top of matching steps by step name).
        4. Return a :class:`WorkflowPackage`.

    Raises:
        PackageLoadError: If the directory is missing, malformed, or the
            manifest/graph files cannot be parsed.
    """
    pkg_dir = Path(path)
    if not pkg_dir.is_dir():
        raise PackageLoadError(f"Workflow package directory not found: {pkg_dir}")

    # ── manifest ────────────────────────────────────────────────────────────
    manifest_file = pkg_dir / MANIFEST_FILENAME
    if not manifest_file.exists():
        raise PackageLoadError(
            f"Missing '{MANIFEST_FILENAME}' in package directory: {pkg_dir}"
        )
    try:
        raw_manifest = _read_yaml(manifest_file)
    except Exception as exc:  # yaml.YAMLError or json.JSONDecodeError
        raise PackageLoadError(f"Cannot parse {MANIFEST_FILENAME}: {exc}") from exc

    try:
        manifest = WorkflowPackageManifest.from_dict(raw_manifest)
    except (KeyError, TypeError) as exc:
        raise PackageLoadError(f"Invalid {MANIFEST_FILENAME}: {exc}") from exc

    # ── graph ───────────────────────────────────────────────────────────────
    graph_file = pkg_dir / manifest.entrypoint
    if not graph_file.exists():
        raise PackageLoadError(
            f"Manifest entrypoint '{manifest.entrypoint}' not found in package: {pkg_dir}"
        )
    try:
        raw_graph = _read_yaml(graph_file)
    except Exception as exc:
        raise PackageLoadError(
            f"Cannot parse {manifest.entrypoint}: {exc}"
        ) from exc

    if not isinstance(raw_graph, dict):
        raise PackageLoadError(
            f"{manifest.entrypoint} must contain a YAML mapping at the top level"
        )

    # Ensure the manifest name wins over the workflow's name
    if "name" not in raw_graph:
        raw_graph["name"] = manifest.name

    try:
        workflow = WorkflowDefinition.from_dict(raw_graph)
    except Exception as exc:
        raise PackageLoadError(f"Cannot build WorkflowDefinition: {exc}") from exc

    # ── per-step overrides (agents/<step_name>.yaml) ────────────────────────
    agents_dir = pkg_dir / AGENTS_DIRNAME
    if agents_dir.is_dir():
        overrides = _load_agent_overrides(agents_dir)
        workflow = _apply_overrides(workflow, overrides)

    return WorkflowPackage(
        manifest=manifest,
        workflow=workflow,
        path=pkg_dir.resolve(),
        agents_dir=agents_dir.resolve() if agents_dir.is_dir() else None,
    )


def _load_agent_overrides(agents_dir: Path) -> dict[str, dict[str, Any]]:
    """Read every ``<name>.yaml`` under ``agents/`` into a name→dict mapping."""
    overrides: dict[str, dict[str, Any]] = {}
    for f in sorted(agents_dir.glob("*.yaml")):
        step_name = f.stem
        try:
            data = _read_yaml(f)
        except Exception as exc:
            logger.warning("Skipping invalid override file %s: %s", f, exc)
            continue
        if isinstance(data, dict):
            overrides[step_name] = data
        else:
            logger.warning("Override file %s must be a mapping, skipping", f)
    return overrides


def _apply_overrides(
    workflow: WorkflowDefinition,
    overrides: dict[str, dict[str, Any]],
) -> WorkflowDefinition:
    """Merge per-step overrides into matching steps by name.

    Supported override keys:
        - ``intent``: replaced wholesale
        - ``description``: replaced wholesale
        - ``tools``: replaced wholesale (list)
        - ``retry``: replaced wholesale (int)
        - ``timeout``: replaced wholesale (int/float)
        - ``gate``: replaced wholesale (dict, fed to QualityGate.from_dict)
        - ``parallel``: replaced wholesale (bool)

    Unknown keys are stashed in ``step.workflow_metadata`` (an attr on the
    WorkflowDefinition's ``metadata`` keyed by step name) so callers can
    pass package-specific hints without breaking the loader. WorkflowStep
    does not carry its own ``metadata`` field, so unknown override keys
    are recorded at the workflow level.
    """
    known_keys = {
        "intent",
        "description",
        "tools",
        "retry",
        "timeout",
        "gate",
        "parallel",
    }
    extra_meta: dict[str, dict[str, Any]] = {}
    new_steps = []
    for step in workflow.steps:
        override = overrides.get(step.name)
        if not override:
            new_steps.append(step)
            continue
        # Collect unknown override keys → workflow-level metadata
        for k, v in override.items():
            if k not in known_keys and k != "name":
                extra_meta.setdefault(step.name, {})[f"override_{k}"] = v
        # Build a new step with overridden fields; keep originals otherwise.
        new_step_dict: dict[str, Any] = {
            "name": step.name,
            "agent": step.agent,
            "intent": override.get("intent", step.intent),
            "description": override.get("description", step.description),
            "tools": override.get("tools", step.tools),
            "retry": override.get("retry", step.retry),
            "timeout": override.get("timeout", step.timeout),
            "parallel": override.get("parallel", step.parallel),
            "input_key": step.input_key,
            "output_key": step.output_key,
        }
        # If gate override is None, keep the original gate.
        if "gate" in override and override["gate"] is not None:
            new_step_dict["gate"] = override["gate"]
        elif step.gate and step.gate.type != GateType.NONE:
            new_step_dict["gate"] = step.gate.to_dict()
        # Only set gate key if we actually have one to preserve
        if "gate" not in new_step_dict or new_step_dict["gate"] is None:
            # Use from_dict's default handling by passing nothing
            new_step_dict.pop("gate", None)
        new_steps.append(WorkflowStep.from_dict(new_step_dict))
    workflow.steps = new_steps
    if extra_meta:
        # Stash unknown override keys in workflow.metadata["step_extras"]
        existing = workflow.metadata.get("step_extras", {}) if workflow.metadata else {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(extra_meta)
        workflow.metadata = dict(workflow.metadata)
        workflow.metadata["step_extras"] = existing
    return workflow


def save_package(pkg: WorkflowPackage, dest: str | Path | None = None) -> Path:
    """Persist a :class:`WorkflowPackage` to disk.

    Args:
        pkg: The package to save.
        dest: Target directory. If ``None``, defaults to
            ``.ygg/workflows/<pkg.name>/``. If the directory already
            exists it is wiped first (registry behaviour).

    Returns:
        Absolute path to the saved package directory.

    Raises:
        PackageLoadError: If the destination path is not a directory.
    """
    if dest is None:
        root = default_workflows_root()
        dest_path = root / pkg.manifest.name
    else:
        dest_path = Path(dest)

    if dest_path.exists() and not dest_path.is_dir():
        raise PackageLoadError(
            f"Destination exists and is not a directory: {dest_path}"
        )

    # Clean slate — registry semantics: same name overwrites
    if dest_path.exists():
        shutil.rmtree(dest_path)
    dest_path.mkdir(parents=True)

    # Manifest
    manifest_file = dest_path / MANIFEST_FILENAME
    _write_yaml(manifest_file, pkg.manifest.to_dict())

    # Graph
    graph_file = dest_path / pkg.manifest.entrypoint
    workflow_dict = pkg.workflow.to_dict() if hasattr(pkg.workflow, "to_dict") else None
    if workflow_dict is None:
        raise PackageLoadError(
            "WorkflowDefinition has no to_dict() — cannot serialize package"
        )
    _write_yaml(graph_file, workflow_dict)

    # README stub (if not present) — pure convention, no enforcement
    readme = dest_path / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {pkg.manifest.name}\n\n"
            f"{pkg.manifest.description}\n\n"
            f"Version: {pkg.manifest.version}\n\n"
            f"Tags: {', '.join(pkg.manifest.tags) or '(none)'}\n",
            encoding="utf-8",
        )

    return dest_path.resolve()


# ── Registry ─────────────────────────────────────────────────────────────────


class WorkflowPackageRegistry:
    """Persistent store of workflow packages on disk.

    Packages live under ``<root>/<name>/`` and each must contain a
    ``workflow.yaml`` manifest (its presence is the canonical marker that
    makes a directory a recognised workflow package).

    Args:
        root: Root directory for stored packages. Defaults to the
            ``.ygg/workflows/`` directory in the current working directory
            (overridable via ``$YGG_WORKFLOWS_DIR``).
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root).expanduser().resolve() if root else default_workflows_root()
        self._root.mkdir(parents=True, exist_ok=True)

    # ── read ─────────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return self._root

    def list(self) -> list[str]:
        """Return sorted names of all registered workflow packages."""
        return sorted(
            p.name
            for p in self._root.iterdir()
            if p.is_dir() and (p / MANIFEST_FILENAME).exists()
        )

    def path(self, name: str) -> Path:
        """Return the on-disk directory for *name* (may not exist yet)."""
        return self._root / name

    def exists(self, name: str) -> bool:
        return (self._root / name / MANIFEST_FILENAME).exists()

    def get(self, name: str) -> WorkflowPackage:
        """Load the named package from disk.

        Raises:
            WorkflowPackageNotFoundError: If no package with that name exists.
            PackageLoadError: If the package is corrupt.
        """
        pkg_dir = self._root / name
        if not (pkg_dir / MANIFEST_FILENAME).exists():
            raise WorkflowPackageNotFoundError(
                f"No workflow package named '{name}'. "
                f"Available: {self.list() or ['(none)']}"
            )
        return load_package(pkg_dir)

    # ── write ────────────────────────────────────────────────────────────────

    def save(self, pkg: WorkflowPackage) -> Path:
        """Register (or overwrite) *pkg* in the registry.

        The package is copied to ``<root>/<pkg.name>/``.
        Returns the destination path.
        """
        dest = self._root / pkg.manifest.name
        return save_package(pkg, dest)

    def delete(self, name: str) -> bool:
        """Remove a package by name. Returns ``True`` if removed, ``False`` if missing."""
        pkg_dir = self._root / name
        if not pkg_dir.is_dir():
            return False
        shutil.rmtree(pkg_dir)
        return True

    # ── inspection ───────────────────────────────────────────────────────────

    def summary(self) -> list[dict[str, Any]]:
        """Return a lightweight summary of every registered package.

        Each entry has name, version, description, tags, and step count
        — but does not load the full WorkflowDefinition (fast for large
        registries).
        """
        out: list[dict[str, Any]] = []
        for name in self.list():
            manifest_file = self._root / name / MANIFEST_FILENAME
            try:
                raw = _read_yaml(manifest_file)
                m = WorkflowPackageManifest.from_dict(raw)
            except Exception:
                out.append({"name": name, "error": "invalid manifest"})
                continue
            # Count steps by peeking at graph file
            graph_file = self._root / name / m.entrypoint
            step_count = 0
            if graph_file.exists():
                try:
                    g = _read_yaml(graph_file)
                    if isinstance(g, dict):
                        steps = g.get("steps") or []
                        step_count = len(steps) if isinstance(steps, list) else 0
                except Exception:
                    step_count = -1
            out.append(
                {
                    "name": m.name,
                    "version": m.version,
                    "description": m.description,
                    "tags": list(m.tags),
                    "step_count": step_count,
                }
            )
        return out