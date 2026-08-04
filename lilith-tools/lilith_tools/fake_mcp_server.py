"""Minimal in-process MCP server used only to smoke-test the client.

NOT a test fixture — kept under ``lilith_tools/`` so the manager can be
exercised manually with ``python -m lilith_tools.fake_mcp_server``.
The real test fixture lives in ``lilith-tools/tests/fixtures/fake_mcp_server.py``
and is what the test suite imports.
"""

from __future__ import annotations

import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


async def _echo(arguments: dict) -> list[TextContent]:
    msg = str(arguments.get("message", ""))
    return [TextContent(type="text", text=f"echo: {msg}")]


async def serve() -> None:
    server = Server("fake-mcp")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [
            Tool(
                name="echo",
                description="Devuelve el mensaje con prefijo 'echo:'",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                    },
                    "required": ["message"],
                },
            )
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict):
        if name == "echo":
            return await _echo(arguments or {})
        raise ValueError(f"unknown tool: {name}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        sys.exit(0)