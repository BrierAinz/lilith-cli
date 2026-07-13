"""MCP server exposing the PolicyEngine as MCP tools.

Implements the Model Context Protocol (stdio transport) so AI agents
in other processes can:
  - policy_check: evaluate a tool call against a policy
  - policy_audit: retrieve audit trail for an agent
  - policy_reset: clear policy state for an agent or all

Inspired by omnigent-ai/omnigent policy model.

Usage:
    python -m lilith_orchestrator.policy_mcp
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .policy import PolicyConfig, PolicyEngine

logger = logging.getLogger("lilith.orchestrator.policy_mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


# Singleton engine; one per server lifetime
_engine: PolicyEngine | None = None


def _get_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine


def _reset_engine(config: PolicyConfig | None = None) -> PolicyEngine:
    """Replace the singleton engine. Returns the new engine."""
    global _engine
    _engine = PolicyEngine(config)
    return _engine


# ── Tool definitions ────────────────────────────────────────────────────────


def _check_tool() -> Tool:
    return Tool(
        name="policy_check",
        description="Evaluate whether an agent may invoke a tool under the current policy",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "tool_name": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["agent_name", "tool_name"],
        },
    )


def _audit_tool() -> Tool:
    return Tool(
        name="policy_audit",
        description="Retrieve the audit trail for an agent",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
            },
            "required": ["agent_name"],
        },
    )


def _reset_tool() -> Tool:
    return Tool(
        name="policy_reset",
        description="Clear policy state. Without agent_name, resets all agents.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
            },
        },
    )


def _configure_tool() -> Tool:
    return Tool(
        name="policy_configure",
        description="Replace the policy engine config (allowed/forbidden tools, paths, limits)",
        inputSchema={
            "type": "object",
            "properties": {
                "forbidden_tools": {"type": "array", "items": {"type": "string"}},
                "allowed_tools": {"type": "array", "items": {"type": "string"}},
                "forbidden_paths": {"type": "array", "items": {"type": "string"}},
                "allowed_paths": {"type": "array", "items": {"type": "string"}},
                "max_tool_calls": {"type": "integer"},
                "max_wall_time_seconds": {"type": "number"},
                "rate_limit_per_minute": {"type": "integer"},
                "audit_all": {"type": "boolean"},
            },
        },
    )


def create_server() -> Server:
    server = Server("lilith-policy")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [_check_tool(), _audit_tool(), _reset_tool(), _configure_tool()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "policy_check":
                engine = _get_engine()
                decision, violation, detail = engine.check_tool(
                    arguments.get("agent_name", ""),
                    arguments.get("tool_name", ""),
                    path=arguments.get("path"),
                )
                payload = {
                    "success": True,
                    "decision": decision.value,
                    "violation": violation.value if violation else None,
                    "detail": detail,
                }
            elif name == "policy_audit":
                engine = _get_engine()
                events = engine.audit(arguments.get("agent_name", ""))
                payload = {
                    "success": True,
                    "count": len(events),
                    "events": [
                        {
                            "timestamp": e.timestamp,
                            "agent": e.agent_name,
                            "decision": e.decision.value,
                            "violation": e.violation.value if e.violation else None,
                            "tool": e.tool_name,
                            "detail": e.detail,
                        }
                        for e in events
                    ],
                }
            elif name == "policy_reset":
                engine = _get_engine()
                agent = arguments.get("agent_name")
                engine.reset(agent)
                payload = {
                    "success": True,
                    "reset_scope": agent or "all",
                }
            elif name == "policy_configure":
                cfg = PolicyConfig(
                    allowed_tools=set(arguments.get("allowed_tools") or []),
                    forbidden_tools=set(arguments.get("forbidden_tools") or []),
                    allowed_paths=set(arguments.get("allowed_paths") or []),
                    forbidden_paths=set(arguments.get("forbidden_paths") or []),
                    max_tool_calls=int(arguments.get("max_tool_calls") or 1000),
                    max_wall_time_seconds=float(
                        arguments.get("max_wall_time_seconds") or 3600.0
                    ),
                    rate_limit_per_minute=int(
                        arguments.get("rate_limit_per_minute") or 120
                    ),
                    audit_all=bool(arguments.get("audit_all") or False),
                )
                _reset_engine(cfg)
                payload = {
                    "success": True,
                    "config": {
                        "allowed_tools": sorted(cfg.allowed_tools),
                        "forbidden_tools": sorted(cfg.forbidden_tools),
                        "max_tool_calls": cfg.max_tool_calls,
                        "audit_all": cfg.audit_all,
                    },
                }
            else:
                payload = {"success": False, "error": f"unknown tool: {name}"}
        except Exception as e:
            logger.exception(f"Tool {name} crashed")
            payload = {"success": False, "error": f"crash: {e!r}"}

        return [TextContent(type="text", text=json.dumps(payload, default=str))]

    return server


async def _run() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Lilith policy MCP server")
    parser.parse_args()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
