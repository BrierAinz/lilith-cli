from pathlib import Path

from lilith_cli import robust_kit


def test_python_resolution_prefers_active_environment_over_path_alias(
    monkeypatch, tmp_path: Path
) -> None:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    python = scripts / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setattr(robust_kit.sys, "executable", str(python))
    monkeypatch.setattr(
        robust_kit.shutil,
        "which",
        lambda name: r"C:\Users\Test\AppData\Local\Microsoft\WindowsApps\python.exe",
    )

    assert Path(robust_kit.resolve_tool("python")) == python
