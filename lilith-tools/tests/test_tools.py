"""Tests for basic tool registration and system tools."""

from lilith_tools.filesystem import FileReadTool
from lilith_tools.registry import ToolRegistry
from lilith_tools.system import SystemInfoTool, SystemTimeTool


# ToolRegistry uses a class-level mutable dict. Other test modules
# (test_router.py) use autouse fixtures that clear it. Re-register
# the real tools before each test in this module so assertions work
# regardless of test execution order.
_REAL_TOOLS = {
    "system_info": SystemInfoTool,
    "system_time": SystemTimeTool,
    "file_read": FileReadTool,
}


def _ensure_registered() -> None:
    """Re-register real tools if another test cleared the registry."""
    for name, cls in _REAL_TOOLS.items():
        ToolRegistry._tools.setdefault(name, cls)


_ensure_registered()


def test_tool_registration():
    _ensure_registered()
    assert "system_info" in ToolRegistry.list_tools()
    assert "system_time" in ToolRegistry.list_tools()
    assert "file_read" in ToolRegistry.list_tools()


def test_system_info_tool():
    tool = SystemInfoTool()
    result = tool.execute()
    assert result.success
    assert "os" in result.data
