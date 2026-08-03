"""Tests for lilith_tools.registry dynamic loading."""
import pytest
from pathlib import Path

from lilith_tools.base import BaseTool, ToolResult
from lilith_tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def clean_registry():
    """Reset the registry before each test."""
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


# ── Static registration (backward compat) ──────────────────────


def test_register_decorator():
    @ToolRegistry.register
    class MyTool(BaseTool):
        name = "my_tool"
        description = "Test tool"

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="ok")

    assert ToolRegistry.get("my_tool") is MyTool


def test_list_tools():
    @ToolRegistry.register
    class A(BaseTool):
        name = "a"
        description = "A"

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="")

    tools = ToolRegistry.list_tools()
    assert "a" in tools
    assert tools["a"] == "A"


def test_clear():
    @ToolRegistry.register
    class X(BaseTool):
        name = "x"
        description = "X"

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="")

    ToolRegistry.clear()
    assert ToolRegistry.get("x") is None


# ── Dynamic loading ────────────────────────────────────────────


def test_load_from_path(tmp_path: Path):
    tool_file = tmp_path / "my_dynamic_tool.py"
    tool_file.write_text(
        """
from lilith_tools.base import BaseTool, ToolResult

class DynamicTool(BaseTool):
    name = "dynamic"
    description = "Loaded dynamically"

    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data="dyn")
""",
        encoding="utf-8",
    )
    count = ToolRegistry.load_from_path(tool_file)
    assert count == 1
    assert ToolRegistry.get("dynamic") is not None
    assert "dynamic" in ToolRegistry.loaded_modules()[0] or any("dynamic" in m for m in ToolRegistry.loaded_modules())


def test_load_from_path_no_tools(tmp_path: Path):
    # Module without any BaseTool subclass
    tool_file = tmp_path / "no_tools.py"
    tool_file.write_text("x = 1\n", encoding="utf-8")
    count = ToolRegistry.load_from_path(tool_file)
    assert count == 0


def test_load_from_path_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ToolRegistry.load_from_path(tmp_path / "nope.py")


def test_unload():
    @ToolRegistry.register
    class A(BaseTool):
        name = "a"
        description = "A"

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="")

    assert ToolRegistry.unload("a") is True
    assert ToolRegistry.get("a") is None


def test_unload_missing():
    assert ToolRegistry.unload("nonexistent") is False


def test_loaded_modules_tracking(tmp_path: Path):
    f1 = tmp_path / "t1.py"
    f1.write_text(
        "from lilith_tools.base import BaseTool, ToolResult\n"
        "class T1(BaseTool):\n"
        "    name='t1'\n"
        "    description='T1'\n"
        "    def execute(self, **kw): return ToolResult(success=True, data='')\n",
        encoding="utf-8",
    )
    ToolRegistry.load_from_path(f1, module_name="custom_name_1")
    modules = ToolRegistry.loaded_modules()
    assert "custom_name_1" in modules


# ── Directory discovery ────────────────────────────────────────


def test_discover_from_dir(tmp_path: Path):
    (tmp_path / "t1.py").write_text(
        "from lilith_tools.base import BaseTool, ToolResult\n"
        "class T1(BaseTool):\n"
        "    name='t1'\n"
        "    description='T1'\n"
        "    def execute(self, **kw): return ToolResult(success=True, data='')\n",
        encoding="utf-8",
    )
    (tmp_path / "t2.py").write_text(
        "from lilith_tools.base import BaseTool, ToolResult\n"
        "class T2(BaseTool):\n"
        "    name='t2'\n"
        "    description='T2'\n"
        "    def execute(self, **kw): return ToolResult(success=True, data='')\n",
        encoding="utf-8",
    )
    total = ToolRegistry.discover_from_dir(tmp_path)
    assert total == 2
    assert ToolRegistry.get("t1") is not None
    assert ToolRegistry.get("t2") is not None


def test_discover_from_dir_skips_underscore_files(tmp_path: Path):
    (tmp_path / "_internal.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "t1.py").write_text(
        "from lilith_tools.base import BaseTool, ToolResult\n"
        "class T1(BaseTool):\n"
        "    name='t1'\n"
        "    description='T1'\n"
        "    def execute(self, **kw): return ToolResult(success=True, data='')\n",
        encoding="utf-8",
    )
    total = ToolRegistry.discover_from_dir(tmp_path)
    assert total == 1  # only t1, not _internal


def test_discover_from_dir_recursive(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text(
        "from lilith_tools.base import BaseTool, ToolResult\n"
        "class Nested(BaseTool):\n"
        "    name='nested'\n"
        "    description='Nested'\n"
        "    def execute(self, **kw): return ToolResult(success=True, data='')\n",
        encoding="utf-8",
    )
    total = ToolRegistry.discover_from_dir(tmp_path, recursive=True)
    assert total == 1
    assert ToolRegistry.get("nested") is not None


def test_discover_from_dir_not_a_dir(tmp_path: Path):
    f = tmp_path / "file.py"
    f.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        ToolRegistry.discover_from_dir(f)


# ── Stats ──────────────────────────────────────────────────────


def test_stats():
    @ToolRegistry.register
    class A(BaseTool):
        name = "a"
        description = "A"

        def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=True, data="")

    s = ToolRegistry.stats()
    assert s["total_tools"] == 1
    assert "a" in s["tools"]
    assert s["loaded_modules"] == 0
