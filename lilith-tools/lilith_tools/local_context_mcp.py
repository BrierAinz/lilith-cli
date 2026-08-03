"""MCP server exposing local_context tools to any MCP-compatible client.

Implements the Model Context Protocol (stdio transport) so AI agents
in other processes can call:
  - local_processes
  - local_ports
  - local_git_status
  - local_git_log
  - local_docker_ps
  - local_env
  - local_disk_usage
  - local_python_info

Inspired by iwomm-mcp (dicoy/iwomm-mcp) — same pattern: expose local
dev context as MCP tools so agents don't need copy-paste.

Usage:
    python -m lilith_tools.local_context_mcp

Or programmatically:
    from lilith_tools.local_context_mcp import create_server
    server = create_server()
    # ... run with stdio transport
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp.server import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .local_context import (
    LocalDiskUsageTool,
    LocalDockerPsTool,
    LocalEnvTool,
    LocalGitLogTool,
    LocalGitStatusTool,
    LocalPortsTool,
    LocalProcessesTool,
    LocalPythonInfoTool,
)

logger = logging.getLogger("lilith.tools.local_context_mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# Tool instances — reused across MCP requests
_TOOLS: dict[str, Any] = {
    "local_processes": LocalProcessesTool(),
    "local_ports": LocalPortsTool(),
    "local_git_status": LocalGitStatusTool(),
    "local_git_log": LocalGitLogTool(),
    "local_docker_ps": LocalDockerPsTool(),
    "local_env": LocalEnvTool(),
    "local_disk_usage": LocalDiskUsageTool(),
    "local_python_info": LocalPythonInfoTool(),
}


def _tool_to_mcp_schema(tool: Any) -> Tool:
    """Convert a BaseTool to an MCP Tool descriptor."""
    params = tool.parameters or {}
    # MCP requires JSON Schema properties; we use permissive typing
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, spec in params.items():
        ptype = spec.get("type", "string")
        # MCP JSON Schema uses standard types
        json_type = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
        }.get(ptype, "string")
        properties[pname] = {"type": json_type}
        if spec.get("required", False):
            required.append(pname)
    return Tool(
        name=tool.name,
        description=tool.description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


def create_server() -> Server:
    """Create an MCP Server wired to the local_context tools."""
    server = Server("lilith-local-context")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [_tool_to_mcp_schema(t) for t in _TOOLS.values()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        tool = _TOOLS.get(name)
        if tool is None:
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": f"unknown tool: {name}"}),
            )]
        try:
            result = tool.execute(**(arguments or {}))
            payload = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
            }
            return [TextContent(type="text", text=json.dumps(payload, default=str))]
        except Exception as e:
            logger.exception(f"Tool {name} crashed")
            return [TextContent(
                type="text",
                text=json.dumps({"success": False, "error": f"crash: {e!r}"}),
            )]

    return server


async def _run() -> None:
    """Run the MCP server over stdio.

    We deliberately do NOT use ``Server.run()``. The library dispatches
    every incoming request via ``tg.start_soon()`` and cancels the task
    group the moment ``session.incoming_messages`` is exhausted (i.e.
    stdin EOF). Under Linux CI that races with ``communicate(input=...)``,
    which closes stdin as soon as it finishes writing the batch: the
    ``tools/call`` handler is cancelled before its ``respond()``
    completes, and the client never sees the reply.

    Instead we iterate ``session.incoming_messages`` and ``await`` each
    handler inline. The anyio memory streams use backpressure (buffer
    size 0), so messages flow end-to-end
    (stdin -> stdin_reader -> _receive_loop -> our loop -> handler ->
    write_stream -> stdout_writer -> stdout) and every response is
    flushed before the next one is even accepted. By the time EOF
    propagates back to our loop, every queued request has already been
    served.
    """
    server = create_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        async with AsyncExitStack() as stack:
            lifespan_context = await stack.enter_async_context(server.lifespan(server))
            session = await stack.enter_async_context(
                ServerSession(read_stream, write_stream, init_options)
            )
            async for message in session.incoming_messages:
                await server._handle_message(message, session, lifespan_context)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lilith local-context MCP server")
    parser.parse_args()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
