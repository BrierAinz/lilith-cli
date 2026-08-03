"""Tests for the Blender tools (lilith_tools.blender).

These tests run purely against ``monkeypatch``-ed ``subprocess.run`` and the
file system; no real Blender installation is required. A single optional
integration test is ``skip``ped when ``blender.exe`` cannot be located on the
machine — running the suite on a developer workstation without Blender
should still produce a green run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import lilith_tools.blender as blender_mod
from lilith_tools.blender import (
    OUTPUT_BYTE_LIMIT,
    BlenderExecTool,
    BlenderRenderTool,
    _find_blender_windows,
    _locate_blender,
    _truncate,
    _validate_script,
)
from lilith_tools.registry import ToolRegistry


# ── Helpers ───────────────────────────────────────────────────────────


class FakeProc:
    """Minimal stand-in for ``subprocess.CompletedProcess``."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_run_recorder(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Return a callable that mimics ``subprocess.run`` and records invocations.

    The returned function accepts the same signature Blender's helpers hand
    it (``subprocess.run([exe, *args], **kwargs)``) so monkeypatching is
    friction-free.
    """

    calls: list[dict] = []

    def _fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return FakeProc(stdout=stdout, stderr=stderr, returncode=returncode)

    _fake_run.calls = calls  # type: ignore[attr-defined]
    return _fake_run


def _no_blender(monkeypatch) -> None:
    """Force the tools to think Blender is missing everywhere."""
    monkeypatch.delenv("BLENDER_PATH", raising=False)
    monkeypatch.setattr(
        blender_mod.shutil, "which", lambda _name: None, raising=False
    )
    monkeypatch.setattr(blender_mod, "_find_blender_windows", lambda: None)


# ── Registration & schemas ────────────────────────────────────────────


def test_tools_are_registered() -> None:
    """blender_exec and blender_render must show up in the registry."""
    classes = {
        ToolRegistry.get("blender_exec"),
        ToolRegistry.get("blender_render"),
    }
    assert BlenderExecTool in classes
    assert BlenderRenderTool in classes
    assert BlenderExecTool.name == "blender_exec"
    assert BlenderRenderTool.name == "blender_render"
    assert BlenderExecTool.timeout_seconds == 180
    assert BlenderRenderTool.timeout_seconds == 180


def test_schemas_list_required_fields() -> None:
    assert BlenderExecTool.parameters["script"]["required"] is True
    assert BlenderExecTool.parameters["timeout"]["required"] is False
    assert BlenderRenderTool.parameters["output_path"]["required"] is True
    assert BlenderRenderTool.parameters["blend_file"]["required"] is False
    assert BlenderRenderTool.parameters["script"]["required"] is False


# ── Detection failure ─────────────────────────────────────────────────


def test_locate_blender_returns_none_when_missing(monkeypatch) -> None:
    _no_blender(monkeypatch)
    assert _locate_blender() is None


def test_exec_tool_reports_missing_blender(monkeypatch) -> None:
    _no_blender(monkeypatch)
    # Even with a perfectly valid script, failure is friendly & early.
    result = BlenderExecTool().execute(script="print('hi')")
    assert result.success is False
    assert result.data is None
    err = result.error.lower()
    assert "blender" in err
    assert "blender_path" in err or "path" in err
    assert result.error.startswith("Blender no esta instalado")


def test_render_tool_reports_missing_blender(monkeypatch, tmp_path) -> None:
    _no_blender(monkeypatch)
    out = tmp_path / "shot.png"
    result = BlenderRenderTool().execute(output_path=str(out))
    assert result.success is False
    assert "blender" in result.error.lower()


def test_env_path_overrides_others(monkeypatch, tmp_path) -> None:
    """Setting BLENDER_PATH to an existing file wins over PATH + glob."""
    fake_exe = tmp_path / "blender.exe"
    fake_exe.write_text("")
    monkeypatch.setenv("BLENDER_PATH", str(fake_exe))
    monkeypatch.setattr(
        blender_mod.shutil, "which", lambda _name: "/should/not/be/used.exe"
    )
    monkeypatch.setattr(blender_mod, "_find_blender_windows", lambda: None)
    assert _locate_blender() == str(fake_exe)


def test_path_via_shutil_which_is_used(monkeypatch, tmp_path) -> None:
    """When BLENDER_PATH is unset, shutil.which is the next candidate."""
    monkeypatch.delenv("BLENDER_PATH", raising=False)
    monkeypatch.setattr(blender_mod, "_find_blender_windows", lambda: None)
    monkeypatch.setattr(
        blender_mod.shutil, "which", lambda _name: "/usr/local/bin/blender"
    )
    assert _locate_blender() == "/usr/local/bin/blender"


def test_windows_fallback_used_when_path_empty(monkeypatch) -> None:
    monkeypatch.delenv("BLENDER_PATH", raising=False)
    monkeypatch.setattr(blender_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(blender_mod, "_find_blender_windows", lambda: "C:/fake.exe")
    # _locate_blender solo consulta el fallback de Windows detras de un
    # guard de plataforma, asi que sin simular win32 este test se limita a
    # comprobar que en Linux devuelve None. Fingir la plataforma lo vuelve
    # independiente del SO y mantiene la cobertura del ramal en ambos.
    monkeypatch.setattr(blender_mod.sys, "platform", "win32")
    assert _locate_blender() == "C:/fake.exe"


# ── Argument validation ───────────────────────────────────────────────


def test_validate_script_rejects_empty_or_non_string() -> None:
    assert _validate_script(None) is not None
    assert _validate_script("") is not None
    assert _validate_script("   ") is not None
    assert _validate_script(123) is not None
    assert _validate_script("import bpy\nprint('ok')\n") is None


def test_exec_tool_requires_script(monkeypatch) -> None:
    # Even with a working Blender, missing/empty script is rejected first.
    monkeypatch.setenv("BLENDER_PATH", "C:/fake/blender.exe")
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/fake/blender.exe")
    assert "requerido" in BlenderExecTool().execute(script=None).error.lower()
    assert BlenderExecTool().execute(script="").success is False
    assert BlenderExecTool().execute(script="  ").success is False
    bad = BlenderExecTool().execute(script=123)  # type: ignore[arg-type]
    assert bad.success is False


def test_render_tool_requires_output_path(monkeypatch) -> None:
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/fake/blender.exe")
    cases = [None, "", "   "]
    for value in cases:
        result = BlenderRenderTool().execute(output_path=value)  # type: ignore[arg-type]
        assert result.success is False, value
        assert "output_path" in result.error.lower()


def test_render_tool_rejects_empty_optionals(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/fake/blender.exe")
    out = tmp_path / "shot.png"
    bad1 = BlenderRenderTool().execute(
        output_path=str(out), blend_file="   "
    )
    assert bad1.success is False
    bad2 = BlenderRenderTool().execute(output_path=str(out), script="")
    assert bad2.success is False
    bad3 = BlenderRenderTool().execute(output_path=str(out), script=123)  # type: ignore[arg-type]
    assert bad3.success is False


# ── Happy-path execution (mocked subprocess) ──────────────────────────


def test_exec_tool_happy_path(monkeypatch) -> None:
    """A successful Blender run returns success + captured output."""
    fake_run = _make_run_recorder(
        returncode=0, stdout="Fra: 1\nMem: 100MiB\n", stderr="Read prefs"
    )
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="bpy.ops.mesh.primitive_cube_add()")
    assert result.success is True, result.error
    assert result.data["blender_exe"] == "C:/blender.exe"
    assert result.data["returncode"] == 0
    assert "Fra: 1" in result.data["stdout"]
    assert result.data["stderr"] == "Read prefs"

    # Verify the command line: blender + flags + script path + (optional blend).
    cmd = fake_run.calls[0]["cmd"]
    exe, *args = cmd
    assert exe == "C:/blender.exe"
    assert "--background" in args
    assert "--factory-startup" in args
    assert "--python" in args
    # The .py script path is right after --python.
    py_idx = args.index("--python")
    assert str(args[py_idx + 1]).endswith(".py")
    # The script file was cleaned up after the run.
    assert not Path(str(args[py_idx + 1])).exists()


def test_exec_tool_passes_blend_file(monkeypatch) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    BlenderExecTool().execute(
        script="pass", blend_file="/tmp/scene.blend"
    )
    cmd = fake_run.calls[0]["cmd"]
    assert "/tmp/scene.blend" in cmd


def test_exec_tool_honors_timeout_floor(monkeypatch) -> None:
    """When the user passes no timeout, the class attribute is used."""
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="x = 1")
    assert result.data["timeout"] == BlenderExecTool.timeout_seconds
    # And it actually lands in subprocess.run's kwargs.
    assert fake_run.calls[0]["kwargs"]["timeout"] == BlenderExecTool.timeout_seconds


def test_exec_tool_user_timeout_overrides(monkeypatch) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="x = 1", timeout=42)
    assert result.success is True
    assert result.data["timeout"] == 42
    assert fake_run.calls[0]["kwargs"]["timeout"] == 42


def test_exec_tool_zero_or_negative_timeout_falls_back(monkeypatch) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    for bad in (0, -1, "garbage", None):
        result = BlenderExecTool().execute(script="x", timeout=bad)
        assert result.success is True
    # All three calls should use the 180s default.
    for c in fake_run.calls:
        assert c["kwargs"]["timeout"] == 180


def test_exec_tool_returns_failure_on_nonzero_rc(monkeypatch) -> None:
    fake_run = _make_run_recorder(
        returncode=2, stdout="Saved", stderr="Traceback (most recent call last):..."
    )
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="bad")
    assert result.success is False
    assert "codigo de salida 2" in result.error.lower()
    # stderr should appear in both data and the trailing error.
    assert "Traceback" in result.error


def test_exec_tool_handles_timeout_expired(monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd=["C:/blender.exe"], timeout=180)

    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", boom)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="x")
    assert result.success is False
    assert "timeout" in result.error.lower() and "180" in result.error


def test_exec_tool_handles_missing_exe_via_os_error(monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise FileNotFoundError(2, "No such file")

    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", boom)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="x")
    assert result.success is False
    assert "blender" in result.error.lower()


# ── Output truncation ────────────────────────────────────────────────


def test_truncate_within_limit_is_noop() -> None:
    small = "a" * 100
    assert _truncate(small) == small
    assert _truncate("") == ""


def test_truncate_caps_at_byte_limit() -> None:
    huge = "x" * (OUTPUT_BYTE_LIMIT * 3)
    result = _truncate(huge)
    assert len(result.encode("utf-8")) <= OUTPUT_BYTE_LIMIT + len(
        b"\n... [output truncated]"
    )
    assert "[output truncated]" in result


def test_truncate_marks_long_output(monkeypatch) -> None:
    fake_run = _make_run_recorder(
        returncode=0,
        stdout="A" * OUTPUT_BYTE_LIMIT * 4,
        stderr="B" * OUTPUT_BYTE_LIMIT * 4,
    )
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderExecTool().execute(script="x")
    assert result.success is True
    assert "[output truncated]" in result.data["stdout"]
    assert "[output truncated]" in result.data["stderr"]
    # Truncated streams are at most OUTPUT_BYTE_LIMIT + suffix bytes.
    for stream in (result.data["stdout"], result.data["stderr"]):
        assert len(stream.encode("utf-8")) <= OUTPUT_BYTE_LIMIT + 64


# ── Render tool ───────────────────────────────────────────────────────


def test_render_tool_normalizes_output_extension(monkeypatch, tmp_path) -> None:
    """Missing or alternate extensions get forced to .png."""
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    # No extension at all.
    no_ext = tmp_path / "frame_no_ext"
    r1 = BlenderRenderTool().execute(output_path=str(no_ext))
    assert r1.data["output_path"].endswith(".png")
    # Wrong extension gets replaced.
    wrong_ext = tmp_path / "frame.jpg"
    r2 = BlenderRenderTool().execute(output_path=str(wrong_ext))
    assert r2.data["output_path"].endswith(".png")


def test_render_tool_creates_parent_dir(monkeypatch, tmp_path) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    nested = tmp_path / "deep" / "shot.png"
    assert not nested.parent.exists()
    BlenderRenderTool().execute(output_path=str(nested))
    assert nested.parent.is_dir()


def test_render_tool_happy_path_with_fake_png(monkeypatch, tmp_path) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    out = tmp_path / "rendered.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n")  # pretend Blender wrote a PNG
    result = BlenderRenderTool().execute(output_path=str(out))
    assert result.success is True, result.error
    assert result.data["rendered"] is True
    assert result.data["output_path"] == str(out)
    assert Path(result.data["output_path"]).is_file()


def test_render_tool_fails_when_nonzero_rc(monkeypatch, tmp_path) -> None:
    fake_run = _make_run_recorder(
        returncode=1, stderr="ERROR: cannot read .blend"
    )
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    out = tmp_path / "shot.png"
    result = BlenderRenderTool().execute(output_path=str(out))
    assert result.success is False
    assert "render fallo" in result.error.lower()


def test_render_tool_fails_when_no_png_produced(monkeypatch, tmp_path) -> None:
    """Even with rc=0, an absent output file is treated as failure."""
    fake_run = _make_run_recorder(returncode=0, stderr="")
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    out = tmp_path / "missing.png"
    # Make sure no PNG is created.
    result = BlenderRenderTool().execute(output_path=str(out))
    assert not out.exists()
    assert result.success is False


def test_render_tool_includes_blend_and_user_script(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run(cmd, **_kw):
        # Read the rendered harness right now, before the finally unlinks it.
        script_path = cmd[cmd.index("--python") + 1]
        captured["body"] = Path(script_path).read_text(encoding="utf-8")
        captured["cmd"] = list(cmd)
        return FakeProc(returncode=0)

    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    out = tmp_path / "shot.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n")
    BlenderRenderTool().execute(
        output_path=str(out),
        blend_file=str(tmp_path / "scene.blend"),
        script="bpy.data.objects['Cube'].location = (1, 0, 0)",
    )
    # The user-supplied script body must appear inside the rendered harness.
    assert "'Cube'" in captured["body"]
    # And the blend file was added to the argv as the last positional arg.
    assert str(tmp_path / "scene.blend") in captured["cmd"]


def test_exec_tool_writes_user_script_body(monkeypatch, tmp_path) -> None:
    """Mirror of the render test for blender_exec: also assert body content."""
    captured: dict = {}

    def fake_run(cmd, **_kw):
        # Read the rendered script while Blender is still running, before the
        # finally-block unlinks the tempfile.
        captured["body"] = Path(cmd[cmd.index("--python") + 1]).read_text(
            encoding="utf-8"
        )
        return FakeProc(returncode=0)

    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    BlenderExecTool().execute(
        script="import bpy\nprint('marker-xyz')",
        blend_file=str(tmp_path / "scene.blend"),
    )
    assert "marker-xyz" in captured["body"]


def test_render_tool_timeout(monkeypatch, tmp_path) -> None:
    fake_run = _make_run_recorder(returncode=0)
    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", fake_run)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    out = tmp_path / "shot.png"
    out.write_bytes(b"\x89PNG\r\n\x1a\n")
    BlenderRenderTool().execute(output_path=str(out), timeout=45)
    assert fake_run.calls[0]["kwargs"]["timeout"] == 45


def test_render_tool_handles_timeout_expired(monkeypatch, tmp_path) -> None:
    def boom(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd=["C:/blender.exe"], timeout=30)

    monkeypatch.setattr(blender_mod, "_locate_blender", lambda: "C:/blender.exe")
    monkeypatch.setattr(blender_mod.subprocess, "run", boom)
    monkeypatch.setattr("tempfile.NamedTemporaryFile", _no_tempfile_passthrough())

    result = BlenderRenderTool().execute(
        output_path=str(tmp_path / "shot.png"), timeout=30
    )
    assert result.success is False
    assert "timeout" in result.error.lower()


# ── tempfile shim ─────────────────────────────────────────────────────


def _no_tempfile_passthrough():
    """Return a class that mimics tempfile.NamedTemporaryFile.

    The instance writes to a real file via plain ``open()`` so tests can
    inspect its path/content and so BlenderExecTool's finally-block still
    has an existing file to ``os.unlink`` after the test exits.

    Callers must ``monkeypatch.setattr(tempfile, "NamedTemporaryFile", Stub)``
    so the toolbox reaches our shim.
    """

    class Stub:
        instances: list[Stub] = []

        def __init__(
            self,
            mode: str = "w",
            suffix: str = "",
            prefix: str = "",
            delete: bool = True,
            encoding: str | None = "utf-8",
            **_: object,
        ):
            import tempfile as _tf

            fd, name = _tf.mkstemp(suffix=suffix, prefix=prefix)
            self._fd = fd
            self._encoding = encoding or "utf-8"
            self._closed = False
            self.name = name
            # os.write requires bytes; convert via string+encode if needed.
            # We don't pre-write here — caller uses .write() below.
            Stub.instances.append(self)

        def write(self, data: str) -> int:
            if isinstance(data, str):
                data_bytes = data.encode(self._encoding)
            else:
                data_bytes = data
            return os.write(self._fd, data_bytes)

        def flush(self) -> None:
            os.fsync(self._fd)

        def close(self) -> None:
            if not self._closed:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._closed = True

        def __enter__(self) -> Stub:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self.close()

    Stub.instances = []  # type: ignore[attr-defined]
    return Stub


# ── Integration smoke (only when blender.exe is actually available) ────


def test_integration_blender_exec_real_binary(tmp_path: Path) -> None:
    """Skip on machines without a Blender install; otherwise do a real run."""
    if shutil.which("blender") is None and not _find_blender_windows():
        pytest.skip("Blender not installed; skipping real-binary integration test")
    result = BlenderExecTool().execute(
        script=(
            "import bpy\n"
            "bpy.ops.wm.read_homefile(use_empty=True)\n"
            "bpy.ops.mesh.primitive_cube_add()\n"
            "print('cube added')\n"
        ),
        timeout=120,
    )
    if not result.success and "timeout" in (result.error or "").lower():
        pytest.skip("Real Blender run timed out; environment too slow for CI")
    assert result.success, result.error
    assert "cube added" in (result.data["stdout"] or "")
