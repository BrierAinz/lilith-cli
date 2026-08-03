"""MCP (Model Context Protocol) client for Lilith tools.

Implements an stdio-only MCP client that mounts every tool exposed by a
remote MCP server as a regular :class:`lilith_tools.base.BaseTool`
subclass registered in the global :class:`ToolRegistry`. The wire
protocol is JSON-RPC 2.0 over the subprocess' stdin/stdout; the
:class:`mcp.ClientSession` handles framing and lifecycle.

This module deliberately speaks the same language as the rest of
``lilith_tools``:

* each remote tool becomes a dynamic ``BaseTool`` subclass with a
  ``mcp_<server>_<tool>`` name so it can't collide with built-in tools
  or with the model's prefix-splitting repairs;
* ``execute()`` is synchronous (the agent loop runs it in
  ``asyncio.to_thread``) and delegates the actual RPC call back to the
  client's dedicated event loop via ``asyncio.run_coroutine_threadsafe``;
* server startup is fault-tolerant — a broken subprocess never crashes
  the host session, it just logs and the manager refuses to mount
  anything for that server.

Only the stdio transport is implemented in this tanda. HTTP/SSE can be
added later by swapping ``stdio_client`` for ``sse_client``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from typing import Any, Iterable

from .base import BaseTool, ToolResult
from .registry import ToolRegistry

logger = logging.getLogger(__name__)


# ── MCP SDK import (lazy + optional) ──────────────────────────────────
#
# The ``mcp`` package is declared by ``lilith-tools/pyproject.toml``
# (``mcp>=1.28.1``) and is therefore available wherever lilith-tools is
# installed. We still import lazily so that unit tests that don't need
# MCP can run without it.


_MCP_IMPORT_ERROR: Exception | None = None
try:  # pragma: no cover — exercised via integration tests
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except Exception as _exc:  # pragma: no cover — defensive
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    _MCP_IMPORT_ERROR = _exc


# ── Defaults ─────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS = 30.0
"""Per-call timeout for ``tools/call`` (also exposed as the synthetic
tool's ``timeout_seconds`` so the agent honours it as a floor)."""


# ── Schema helpers ───────────────────────────────────────────────────


def _normalize_parameters(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce an MCP ``inputSchema`` into the ``parameters`` dict shape
    that :class:`lilith_tools.base.BaseTool` expects.

    MCP schemas are JSON Schema objects. The base tool convention uses
    a flat ``{name: {type, description, required}}`` dict; the schema is
    flat enough that we can keep it almost verbatim and just decorate
    each property with ``required: True`` when it's listed in the top
    level ``required`` array.
    """
    if not schema or not isinstance(schema, dict):
        return {}
    properties = schema.get("properties") or {}
    required_list = schema.get("required") or []
    params: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            prop_schema = {"type": "string"}
        entry = {
            "type": prop_schema.get("type", "string"),
            "description": prop_schema.get("description", ""),
        }
        if "enum" in prop_schema:
            entry["enum"] = prop_schema["enum"]
        if "default" in prop_schema:
            entry["default"] = prop_schema["default"]
        if prop_name in required_list:
            entry["required"] = True
        params[prop_name] = entry
    return params


def _safe_remote_name(server_name: str, tool_name: str) -> str:
    """Build the synthetic tool name. Replaces anything that isn't a
    valid identifier character with ``_`` so the name parses cleanly
    downstream (and survives the prefix-splitter in
    ``agent._repair_tool_name``)."""
    pieces = [
        "".join(c if (c.isalnum() or c == "_") else "_" for c in part)
        for part in (server_name, tool_name)
    ]
    return f"mcp_{pieces[0]}_{pieces[1]}"


# ── Per-server client ────────────────────────────────────────────────


class MCPClient:
    """Lifecycle wrapper around a single MCP stdio server.

    Spawns the subprocess in a dedicated background thread running its
    own ``asyncio`` loop. The thread owns the ``ClientSession``;
    synchronous callers (e.g. tool ``execute``) cross the thread boundary
    via ``asyncio.run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        server_name: str,
        command: str,
        args: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.server_name = server_name
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.timeout = float(timeout)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any | None = None
        self._cm: Any = None  # ``stdio_client`` async context manager
        self._ready = threading.Event()
        self._ready_error: BaseException | None = None
        self._remote_tools: list[Any] = []
        self._mounted_names: list[str] = []
        self._closed = False

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> bool:
        """Spawn the subprocess and run ``initialize`` + ``tools/list``.

        Returns True if the server is healthy and at least one tool was
        advertised; False otherwise (the error is stored in
        ``self._ready_error`` for diagnostics).
        """
        if _MCP_IMPORT_ERROR is not None:
            self._ready_error = _MCP_IMPORT_ERROR
            logger.warning(
                "MCP server %s: SDK not importable (%s); skipping",
                self.server_name,
                _MCP_IMPORT_ERROR,
            )
            self._ready.set()
            return False

        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"mcp-{self.server_name}",
            daemon=True,
        )
        self._thread.start()
        # Wait for initialize/list_tools to settle so the caller knows
        # what to register. ``start`` is meant to be quick (subprocess
        # spawn + handshake); the per-call timeout is separate.
        self._ready.wait(timeout=15.0)
        if self._ready_error is not None:
            return False
        return bool(self._remote_tools)

    def _thread_main(self) -> None:
        """Run inside the dedicated thread: own loop + ClientSession."""
        self._loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._session_main())
        except BaseException as exc:  # noqa: BLE001 — top-level guard
            self._ready_error = exc
            logger.warning(
                "MCP server %s crashed: %s", self.server_name, exc
            )
        finally:
            self._ready.set()
            try:
                self._loop.close()
            except Exception:
                pass

    async def _session_main(self) -> None:
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                self._session = session
                await session.initialize()
                listed = await session.list_tools()
                self._remote_tools = list(getattr(listed, "tools", []) or [])
                # Mark ready BEFORE the context exits so callers don't
                # race with the shutdown of the streams.
                self._ready.set()
                # Park here until closed.
                await self._wait_closed_event()

    async def _wait_closed_event(self) -> None:
        """Idle coroutine that yields until ``close()`` flips a flag."""
        while not self._closed:
            await asyncio.sleep(0.05)

    def close(self) -> None:
        """Tear down the subprocess and the thread."""
        self._closed = True
        if self._loop is None or not self._loop.is_running():
            return
        try:
            # Submitting a no-op lets the idle loop tick once so the
            # context managers exit cleanly on the next ``await``.
            future = asyncio.run_coroutine_threadsafe(
                asyncio.sleep(0.01), self._loop
            )
            try:
                future.result(timeout=2.0)
            except (FutureTimeoutError, Exception):
                pass
        except Exception:
            pass

        if self._thread is not None:
            self._thread.join(timeout=3.0)

    # ── Discovery ────────────────────────────────────────────────

    @property
    def remote_tools(self) -> list[Any]:
        return list(self._remote_tools)

    @property
    def last_error(self) -> str:
        if self._ready_error is None:
            return ""
        return f"{type(self._ready_error).__name__}: {self._ready_error}"

    # ── Invocation ───────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Synchronous ``tools/call`` with a per-call timeout."""
        if self._loop is None or self._session is None:
            return ToolResult(
                success=False,
                data=None,
                error=f"MCP server '{self.server_name}' no está listo",
            )

        async def _do_call() -> Any:
            return await self._session.call_tool(name, arguments or {})

        try:
            future: Future = asyncio.run_coroutine_threadsafe(
                _do_call(), self._loop
            )
            result = future.result(timeout=self.timeout)
        except FutureTimeoutError:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"Timeout ({self.timeout:.0f}s) llamando a "
                    f"'{name}' en MCP server '{self.server_name}'"
                ),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                data=None,
                error=(
                    f"MCP '{self.server_name}/{name}' falló: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        return _call_result_to_tool_result(self.server_name, name, result)


def _call_result_to_tool_result(
    server_name: str, tool_name: str, result: Any
) -> ToolResult:
    """Translate an MCP ``CallToolResult`` into a :class:`ToolResult`.

    MCP returns ``content`` as a list of typed blocks
    (``TextContent``, ``ImageContent``, ``EmbeddedResource``). We
    concatenate the textual parts and preserve non-text blocks as a
    short description so the model still sees what came back.

    ``isError`` is honoured: when the server signals a protocol-level
    failure we return ``success=False`` so the agent loops the
    corrective tool_result pattern instead of treating the output as
    truth.
    """
    is_error = bool(getattr(result, "isError", False))
    content_blocks = getattr(result, "content", None) or []
    text_parts: list[str] = []
    other_parts: list[str] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text is not None:
            text_parts.append(str(text))
            continue
        # Best-effort description for non-text blocks.
        kind = type(block).__name__
        data = getattr(block, "data", None) or getattr(block, "blob", None)
        if data is not None:
            other_parts.append(f"[{kind}: <{len(str(data))} bytes>]")
        else:
            other_parts.append(f"[{kind}]")

    joined = "\n".join(text_parts).strip()
    if other_parts:
        joined = (joined + "\n" if joined else "") + "\n".join(other_parts)

    if is_error:
        return ToolResult(
            success=False,
            data=joined or None,
            error=(
                f"MCP '{server_name}/{tool_name}' reportó error"
            ),
        )
    return ToolResult(success=True, data=joined, error="")


# ── Synthetic tool class ─────────────────────────────────────────────


def _make_tool_class(client: MCPClient, remote: Any) -> type[BaseTool]:
    """Build a ``BaseTool`` subclass that proxies ``execute`` to the
    remote server.

    The class is created dynamically (``type(...)``) so each remote tool
    gets its own type — required because ``BaseTool.name``,
    ``description`` and ``parameters`` are class attributes read at
    introspection time by :class:`ToolRegistry`.
    """
    # NB: avoid local names that shadow class-body attributes — Python
    # evaluates the right-hand side of each ``name = value`` in an
    # empty class namespace first, so referring to enclosing-scope
    # variables that share a name with a class attribute raises
    # ``NameError``. The underscores below sidestep that.
    remote_name = getattr(remote, "name", "") or ""
    remote_desc = getattr(remote, "description", "") or ""
    remote_schema = getattr(remote, "inputSchema", None) or {}
    synthetic_name = _safe_remote_name(client.server_name, remote_name)
    params = _normalize_parameters(
        remote_schema if isinstance(remote_schema, dict) else None
    )
    timeout_floor = max(int(client.timeout), 1)
    # ``remote_name`` is referenced from ``execute`` below — bind it via
    # default-arg trick so the closure doesn't mutate later.
    _rn = remote_name
    _client = client

    class _MCPTool(BaseTool):
        name = synthetic_name
        description = remote_desc or f"MCP tool {remote_name}"
        parameters = params
        # Per-call timeout floor so the agent loop doesn't kill slow RPCs.
        timeout_seconds = timeout_floor

        def execute(self, **kwargs: Any) -> ToolResult:
            return _client.call_tool(_rn, kwargs)

    # Adding ``execute`` on the class body clears it from the ABC's
    # abstract set; explicit assignment after the fact does NOT. No-op
    # if it's already concrete.
    _MCPTool.__abstractmethods__ = frozenset()
    return _MCPTool


# ── Manager ──────────────────────────────────────────────────────────


class MCPClientManager:
    """Owns one :class:`MCPClient` per configured server.

    The manager is responsible for spawning every enabled server on
    startup, mounting its tools into the global :class:`ToolRegistry`,
    and exposing ``list_servers`` / ``reload`` for the ``/mcp`` REPL
    command. A broken server never aborts startup: the failure is
    captured and surfaced via :meth:`status`.
    """

    def __init__(self, servers: dict[str, dict[str, Any]] | None = None) -> None:
        self._configs: dict[str, dict[str, Any]] = dict(servers or {})
        self._clients: dict[str, MCPClient] = {}
        self._mounted: dict[str, list[str]] = {}

    # ── Configuration ────────────────────────────────────────────

    def update_servers(self, servers: dict[str, dict[str, Any]]) -> None:
        self._configs = dict(servers)

    @property
    def configured_names(self) -> list[str]:
        return sorted(self._configs)

    # ── Lifecycle ────────────────────────────────────────────────

    def start_all(self) -> dict[str, str]:
        """Spawn every configured server and mount its tools.

        Returns a mapping ``server_name -> status`` (``ok`` or a short
        error string) so the REPL can print a one-line summary.
        """
        statuses: dict[str, str] = {}
        for name, cfg in self._configs.items():
            if not cfg.get("enabled", True):
                statuses[name] = "disabled"
                continue
            statuses[name] = self._start_one(name, cfg)
        return statuses

    def _start_one(self, name: str, cfg: dict[str, Any]) -> str:
        # Tear down any prior instance for this name so ``reload`` is
        # idempotent.
        self._stop_one(name)

        command = str(cfg.get("command", "")).strip()
        if not command:
            return "error: 'command' no declarado"
        args = cfg.get("args") or []
        if not isinstance(args, list):
            return "error: 'args' debe ser lista"
        env_raw = cfg.get("env") or None
        if env_raw is not None and not isinstance(env_raw, dict):
            return "error: 'env' debe ser mapping"
        timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT_SECONDS))

        client = MCPClient(
            server_name=name,
            command=command,
            args=args,
            env=env_raw,
            timeout=timeout,
        )
        if not client.start():
            err = client.last_error or "no tools advertised"
            return f"error: {err}"

        mounted: list[str] = []
        for remote in client.remote_tools:
            if not getattr(remote, "name", ""):
                continue
            try:
                tool_cls = _make_tool_class(client, remote)
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "MCP server %s: no pude envolver tool %s (%s)",
                    name,
                    getattr(remote, "name", "?"),
                    exc,
                )
                continue
            ToolRegistry.register(tool_cls)
            mounted.append(tool_cls.name)

        self._clients[name] = client
        self._mounted[name] = mounted
        return "ok" if mounted else "error: 0 tools"

    def _stop_one(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        for tool_name in self._mounted.pop(name, []):
            try:
                ToolRegistry.unload(tool_name)
            except Exception:
                pass

    def shutdown(self) -> None:
        for name in list(self._clients):
            self._stop_one(name)

    # ── REPL-facing API ──────────────────────────────────────────

    def status(self) -> list[dict[str, Any]]:
        """Return one entry per configured server for ``/mcp list``."""
        rows: list[dict[str, Any]] = []
        for name in self.configured_names:
            cfg = self._configs.get(name) or {}
            if not cfg.get("enabled", True):
                rows.append(
                    {
                        "server": name,
                        "status": "disabled",
                        "tools": 0,
                        "error": "",
                    }
                )
                continue
            client = self._clients.get(name)
            mounted = self._mounted.get(name, [])
            if client is None:
                rows.append(
                    {
                        "server": name,
                        "status": "down",
                        "tools": 0,
                        "error": "",
                    }
                )
            else:
                rows.append(
                    {
                        "server": name,
                        "status": "ok",
                        "tools": len(mounted),
                        "error": client.last_error,
                    }
                )
        return rows

    def reload(self, name: str) -> str:
        if name not in self._configs:
            return f"Servidor MCP '{name}' no está configurado"
        return self._start_one(name, self._configs[name])

    # ── Test helpers ─────────────────────────────────────────────

    @property
    def mounted_tools(self) -> dict[str, list[str]]:
        return {name: list(tools) for name, tools in self._mounted.items()}


# ── Optional: context manager for tests / embedders ──────────────────


@contextmanager
def mcp_session(
    servers: dict[str, dict[str, Any]],
):
    """Context manager that starts every server and cleans up on exit.

    Primarily used by integration tests; the REPL uses the manager
    directly via :meth:`AgentSession` (see ``lilith_cli``).
    """
    manager = MCPClientManager(servers)
    manager.start_all()
    try:
        yield manager
    finally:
        manager.shutdown()


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MCPClient",
    "MCPClientManager",
    "mcp_session",
]