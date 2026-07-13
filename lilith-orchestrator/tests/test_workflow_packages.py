"""Tests for WorkflowPackage — multi-file persisted workflow bundles.

Inspired by Neurosurfer's workflow package system. Covers:
- Manifest serialization (from_dict / to_dict)
- Package load/save roundtrip
- Per-step overrides from agents/<name>.yaml
- Registry list/get/save/delete/exists
- Error paths (missing manifest, invalid YAML, bad entrypoint)
- Default workflows root (uses .ygg/workflows/ or $YGG_WORKFLOWS_DIR)
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from lilith_orchestrator.workflow import (
    GateType,
    OnFailure,
    QualityGate,
    WorkflowDefinition,
    WorkflowStep,
)
from lilith_orchestrator.workflow_packages import (
    DEFAULT_ENTRYPOINT,
    MANIFEST_FILENAME,
    PackageLoadError,
    WorkflowPackage,
    WorkflowPackageManifest,
    WorkflowPackageNotFoundError,
    WorkflowPackageRegistry,
    default_workflows_root,
    load_package,
    save_package,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _write_yaml_via_yaml_or_json(path: Path, data: dict) -> None:
    """Write a dict to disk using PyYAML if available, else JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml  # type: ignore[import-untyped]

        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except ImportError:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _make_workflow(name: str = "demo", n_steps: int = 2) -> WorkflowDefinition:
    """Build a minimal but non-trivial WorkflowDefinition for tests."""
    steps = [
        WorkflowStep(
            name=f"step{i}",
            intent="code" if i % 2 == 0 else "research",
            description=f"step {i}",
            tools=["t1", "t2"] if i == 0 else [],
            gate=QualityGate(
                type=GateType.CONTENT_CHECK, min_length=10 if i == 0 else 0
            ),
            retry=1 if i == 0 else 0,
            timeout=60,
        )
        for i in range(n_steps)
    ]
    return WorkflowDefinition(
        name=name,
        description=f"workflow {name}",
        version="2.0",
        steps=steps,
        on_failure=OnFailure.SKIP,
        max_retries=3,
        timeout=120,
    )


def _make_pkg(tmp_path: Path, name: str = "demo", tags: list[str] | None = None) -> WorkflowPackage:
    """Build a WorkflowPackage around a freshly minted WorkflowDefinition."""
    return WorkflowPackage(
        manifest=WorkflowPackageManifest(
            name=name,
            version="1.5.0",
            description=f"package {name}",
            tags=tags or ["alpha", "beta"],
            author="skadi",
            entrypoint=DEFAULT_ENTRYPOINT,
        ),
        workflow=_make_workflow(name),
        path=tmp_path / name,
    )


# ── Manifest ─────────────────────────────────────────────────────────────────


class TestWorkflowPackageManifest:
    def test_minimal_manifest(self) -> None:
        m = WorkflowPackageManifest.from_dict({"name": "x"})
        assert m.name == "x"
        assert m.version == "1.0.0"
        assert m.entrypoint == DEFAULT_ENTRYPOINT
        assert m.tags == []
        assert m.description == ""

    def test_full_manifest(self) -> None:
        data = {
            "name": "code-review",
            "version": "2.1.0",
            "description": "Multi-step code review",
            "entrypoint": "graph.yaml",
            "tags": ["code", "review"],
            "author": "skadi",
            "created_at": "2026-06-30T00:00:00Z",
        }
        m = WorkflowPackageManifest.from_dict(data)
        assert m.version == "2.1.0"
        assert m.tags == ["code", "review"]
        assert m.author == "skadi"
        # Roundtrip
        assert m.to_dict()["name"] == "code-review"
        # Should preserve None values when omitted
        d = m.to_dict()
        assert "author" in d
        assert "created_at" in d

    def test_missing_name_raises(self) -> None:
        with pytest.raises(KeyError):
            WorkflowPackageManifest.from_dict({"description": "no name"})

    def test_to_dict_strips_optionals(self) -> None:
        m = WorkflowPackageManifest(name="x")
        d = m.to_dict()
        # None optionals should NOT appear in serialized form
        assert "author" not in d
        assert "created_at" not in d
        # Tags empty list should also be stripped
        assert "tags" not in d

    def test_custom_entrypoint(self) -> None:
        m = WorkflowPackageManifest.from_dict({"name": "x", "entrypoint": "main.yaml"})
        assert m.entrypoint == "main.yaml"


# ── WorkflowDefinition / WorkflowStep roundtrip ─────────────────────────────


class TestWorkflowRoundtrip:
    def test_workflow_step_to_from(self) -> None:
        s = WorkflowStep(
            name="s",
            intent="code",
            tools=["t1"],
            gate=QualityGate(type=GateType.CONTENT_CHECK, min_length=10),
        )
        d = s.to_dict()
        s2 = WorkflowStep.from_dict(d)
        assert s2.name == "s"
        assert s2.intent == "code"
        assert s2.tools == ["t1"]
        assert s2.gate.type == GateType.CONTENT_CHECK
        assert s2.gate.min_length == 10

    def test_workflow_definition_roundtrip(self) -> None:
        w = _make_workflow("roundtrip")
        w2 = WorkflowDefinition.from_dict(w.to_dict())
        assert w2.name == w.name
        assert w2.description == w.description
        assert w2.version == w.version
        assert w2.on_failure == w.on_failure
        assert w2.max_retries == w.max_retries
        assert len(w2.steps) == len(w.steps)
        for a, b in zip(w.steps, w2.steps):
            assert a.name == b.name
            assert a.intent == b.intent
            assert a.tools == b.tools
            assert a.gate.type == b.gate.type

    def test_step_to_dict_omits_empty_gate(self) -> None:
        s = WorkflowStep(name="s")  # default gate is NONE
        d = s.to_dict()
        assert "gate" not in d

    def test_gate_to_dict_strips_defaults(self) -> None:
        g = QualityGate(type=GateType.NONE)
        d = g.to_dict()
        assert d == {"type": "none"}

    def test_gate_to_dict_includes_all_fields(self) -> None:
        g = QualityGate(
            type=GateType.CONTENT_CHECK,
            min_length=50,
            required_keywords=["foo"],
            forbidden_keywords=["bar"],
            description="check it",
        )
        d = g.to_dict()
        assert d["min_length"] == 50
        assert d["required_keywords"] == ["foo"]
        assert d["forbidden_keywords"] == ["bar"]
        assert d["description"] == "check it"


# ── Package save / load ─────────────────────────────────────────────────────


class TestPackageSaveLoad:
    def test_save_creates_layout(self, tmp_path: Path) -> None:
        pkg = _make_pkg(tmp_path, "demo")
        dest = save_package(pkg, tmp_path / "demo")
        assert dest.is_dir()
        assert (dest / MANIFEST_FILENAME).exists()
        assert (dest / DEFAULT_ENTRYPOINT).exists()
        assert (dest / "README.md").exists()

    def test_roundtrip(self, tmp_path: Path) -> None:
        pkg = _make_pkg(tmp_path, "roundtrip", tags=["t1"])
        save_package(pkg, tmp_path / "roundtrip")
        loaded = load_package(tmp_path / "roundtrip")
        assert loaded.name == "roundtrip"
        assert loaded.manifest.version == "1.5.0"
        assert loaded.manifest.tags == ["t1"]
        assert loaded.workflow.name == "roundtrip"
        assert len(loaded.workflow.steps) == 2

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        pkg = _make_pkg(tmp_path, "v1")
        save_package(pkg, tmp_path / "v1")
        # Save a different package under same name → should wipe + rewrite
        pkg2 = _make_pkg(tmp_path, "v1")  # same name but different workflow version
        save_package(pkg2, tmp_path / "v1")
        loaded = load_package(tmp_path / "v1")
        assert loaded.workflow.version == "2.0"

    def test_save_destination_must_be_dir(self, tmp_path: Path) -> None:
        # If destination exists as a FILE, raise PackageLoadError
        fake = tmp_path / "iamafile.txt"
        fake.write_text("not a dir", encoding="utf-8")
        pkg = _make_pkg(tmp_path)
        with pytest.raises(PackageLoadError):
            save_package(pkg, fake)

    def test_save_default_uses_workflows_root(self, tmp_path: Path, monkeypatch) -> None:
        # When dest is None, save_package uses default_workflows_root()
        monkeypatch.setenv("YGG_WORKFLOWS_DIR", str(tmp_path))
        pkg = _make_pkg(tmp_path, "bydefault")
        p = save_package(pkg, None)
        assert p.parent == tmp_path
        assert (p / MANIFEST_FILENAME).exists()


# ── Package load errors ─────────────────────────────────────────────────────


class TestPackageLoadErrors:
    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(PackageLoadError, match="directory not found"):
            load_package(tmp_path / "nope")

    def test_missing_manifest(self, tmp_path: Path) -> None:
        d = tmp_path / "broken"
        d.mkdir()
        with pytest.raises(PackageLoadError, match="Missing 'workflow.yaml'"):
            load_package(d)

    def test_invalid_manifest_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / "bad"
        d.mkdir()
        (d / MANIFEST_FILENAME).write_text("name: : :\n  - not valid", encoding="utf-8")
        with pytest.raises(PackageLoadError, match="Cannot parse"):
            load_package(d)

    def test_manifest_missing_name(self, tmp_path: Path) -> None:
        d = tmp_path / "noname"
        d.mkdir()
        _write_yaml_via_yaml_or_json(d / MANIFEST_FILENAME, {"description": "no name"})
        with pytest.raises(PackageLoadError, match="Invalid"):
            load_package(d)

    def test_missing_entrypoint(self, tmp_path: Path) -> None:
        d = tmp_path / "noentry"
        d.mkdir()
        _write_yaml_via_yaml_or_json(
            d / MANIFEST_FILENAME,
            {"name": "x", "entrypoint": "missing.yaml"},
        )
        with pytest.raises(PackageLoadError, match="entrypoint 'missing.yaml' not found"):
            load_package(d)

    def test_invalid_graph_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / "badgraph"
        d.mkdir()
        _write_yaml_via_yaml_or_json(d / MANIFEST_FILENAME, {"name": "x"})
        (d / DEFAULT_ENTRYPOINT).write_text("not yaml: : : at all", encoding="utf-8")
        with pytest.raises(PackageLoadError, match="Cannot parse"):
            load_package(d)


# ── Per-step overrides ──────────────────────────────────────────────────────


class TestAgentOverrides:
    def _make_with_overrides(self, tmp_path: Path) -> Path:
        """Create a package where step0 has an agents/step0.yaml override."""
        d = tmp_path / "withover"
        d.mkdir()
        _write_yaml_via_yaml_or_json(
            d / MANIFEST_FILENAME,
            {"name": "withover", "description": "with overrides"},
        )
        _write_yaml_via_yaml_or_json(
            d / DEFAULT_ENTRYPOINT,
            {
                "name": "withover",
                "steps": [
                    {"name": "step0", "intent": "code", "tools": ["t1"]},
                    {"name": "step1", "intent": "research"},
                ],
            },
        )
        agents_dir = d / "agents"
        agents_dir.mkdir()
        _write_yaml_via_yaml_or_json(
            agents_dir / "step0.yaml",
            {
                "intent": "creative",
                "retry": 5,
                "tools": ["tA", "tB"],
                "unknown_field": "preserved",
            },
        )
        return d

    def test_overrides_applied_to_matching_step(self, tmp_path: Path) -> None:
        d = self._make_with_overrides(tmp_path)
        pkg = load_package(d)
        step0 = pkg.workflow.steps[0]
        assert step0.intent == "creative"
        assert step0.retry == 5
        assert step0.tools == ["tA", "tB"]
        # Originals preserved on non-overridden step
        step1 = pkg.workflow.steps[1]
        assert step1.intent == "research"
        assert step1.retry == 0

    def test_unknown_override_keys_go_to_metadata(self, tmp_path: Path) -> None:
        d = self._make_with_overrides(tmp_path)
        pkg = load_package(d)
        # unknown_field should land in workflow.metadata["step_extras"][step0]
        extras = pkg.workflow.metadata.get("step_extras", {})
        assert "step0" in extras
        assert extras["step0"].get("override_unknown_field") == "preserved"

    def test_agents_dir_recorded(self, tmp_path: Path) -> None:
        d = self._make_with_overrides(tmp_path)
        pkg = load_package(d)
        assert pkg.agents_dir is not None
        assert pkg.agents_dir.name == "agents"

    def test_no_agents_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "nooverrides"
        d.mkdir()
        _write_yaml_via_yaml_or_json(d / MANIFEST_FILENAME, {"name": "nooverrides"})
        _write_yaml_via_yaml_or_json(
            d / DEFAULT_ENTRYPOINT,
            {"name": "nooverrides", "steps": [{"name": "only"}]},
        )
        pkg = load_package(d)
        assert pkg.agents_dir is None


# ── Registry ─────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_list_empty(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        assert reg.list() == []

    def test_list_excludes_dirs_without_manifest(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        (tmp_path / "no_manifest_dir").mkdir()
        assert "no_manifest_dir" not in reg.list()

    def test_save_and_get(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        pkg = _make_pkg(tmp_path, "alpha")
        reg.save(pkg)
        assert reg.exists("alpha")
        assert "alpha" in reg.list()
        loaded = reg.get("alpha")
        assert loaded.name == "alpha"

    def test_save_creates_under_root(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        reg.save(_make_pkg(tmp_path, "beta"))
        assert (tmp_path / "beta").is_dir()

    def test_get_missing_raises(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        with pytest.raises(WorkflowPackageNotFoundError):
            reg.get("ghost")

    def test_delete(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        reg.save(_make_pkg(tmp_path, "killme"))
        assert reg.delete("killme") is True
        assert reg.exists("killme") is False
        # Idempotent: deleting again returns False
        assert reg.delete("killme") is False

    def test_path_returns_dir(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        assert reg.path("foo") == tmp_path / "foo"

    def test_summary_lists_packages(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        reg.save(_make_pkg(tmp_path, "a", tags=["x"]))
        reg.save(_make_pkg(tmp_path, "b"))
        summary = reg.summary()
        names = [s["name"] for s in summary]
        assert "a" in names and "b" in names
        for s in summary:
            assert s["step_count"] >= 1
            assert "version" in s

    def test_summary_handles_corrupt_manifest(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / MANIFEST_FILENAME).write_text("not a mapping", encoding="utf-8")
        summary = reg.summary()
        assert any("error" in s for s in summary)

    def test_save_overwrites_in_place(self, tmp_path: Path) -> None:
        reg = WorkflowPackageRegistry(root=tmp_path)
        reg.save(_make_pkg(tmp_path, "multi"))
        # Save again — should be idempotent
        reg.save(_make_pkg(tmp_path, "multi"))
        assert reg.list() == ["multi"]


# ── Default workflows root ──────────────────────────────────────────────────


class TestDefaultWorkflowsRoot:
    def test_env_override(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("YGG_WORKFLOWS_DIR", str(tmp_path / "custom"))
        root = default_workflows_root()
        assert root == tmp_path / "custom"
        assert root.is_dir()

    def test_fallback_to_ygg_workflows(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("YGG_WORKFLOWS_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        root = default_workflows_root()
        assert root == (tmp_path / ".ygg" / "workflows").resolve()
        assert root.is_dir()

    def test_default_entrypoint_constant(self) -> None:
        assert DEFAULT_ENTRYPOINT == "graph.yaml"


# ── Package dataclass convenience ───────────────────────────────────────────


class TestPackageDataclass:
    def test_properties(self, tmp_path: Path) -> None:
        pkg = _make_pkg(tmp_path, "props", tags=["t1", "t2"])
        assert pkg.name == "props"
        assert pkg.version == "1.5.0"
        assert pkg.description == "package props"
        assert pkg.tags == ["t1", "t2"]

    def test_to_dict_for_logging(self, tmp_path: Path) -> None:
        pkg = _make_pkg(tmp_path)
        d = pkg.to_dict()
        assert d["manifest"]["name"] == "demo"
        assert d["path"] == str(tmp_path / "demo")
        assert d["workflow"] is not None  # WorkflowDefinition has to_dict