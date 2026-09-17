from types import SimpleNamespace

import pytest

from lilith_cli import installations
from lilith_cli.hearth import _write_yaml


def test_failed_candidate_leaves_active_version_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("LILITH_RELEASES_DIR", str(tmp_path))
    previous = "a" * 40
    candidate = "b" * 40
    _write_yaml(tmp_path / "active.yaml", {"active": previous})
    (tmp_path / candidate).mkdir()
    def run(argv, cwd):
        if argv[0] == "git":
            return SimpleNamespace(stdout=candidate)
        raise RuntimeError("injected installer failure")
    monkeypatch.setattr(installations, "_run", run)
    monkeypatch.setattr(installations.shutil, "which", lambda _: "uv")
    with pytest.raises(RuntimeError, match="installer failure"):
        installations.update(str(tmp_path))
    assert installations._state(tmp_path)["active"] == previous


def test_rollback_requires_successful_smoke_and_preserves_both_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("LILITH_RELEASES_DIR", str(tmp_path))
    current, previous = "a" * 40, "b" * 40
    for name in (current, previous):
        (tmp_path / name).mkdir()
    _write_yaml(tmp_path / "active.yaml", {"active": current, "previous": previous})
    monkeypatch.setattr(installations, "_run", lambda *args: SimpleNamespace(stdout="Lilith --help"))
    installations.rollback()
    assert installations._state(tmp_path)["active"] == previous
    assert (tmp_path / current).exists()
    assert (tmp_path / previous).exists()
