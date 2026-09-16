"""Bridges Model Context Protocol (MCP) servers into this project's tool
registry, so any MCP server the user lists in config/mcp_servers.json —
search, filesystem access, GitHub, whatever — becomes callable through the
exact same tool-calling loop already built for native @tool functions
(agent_manager.py needs zero changes).

MCP's Python client is async; the rest of this codebase is sync. Bridged
by running one persistent asyncio event loop in a background thread and
calling into it with run_coroutine_threadsafe — spinning up a fresh event
loop (or a fresh server subprocess) per tool call would be far slower and
would drop each server's session state between calls.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .registry import _REGISTRY

CONFIG_PATH = Path(__file__).parent.parent / "config" / "mcp_servers.json"


def _allowed_types(prop_schema: dict) -> set[str]:
    if "type" in prop_schema:
        return {prop_schema["type"]}
    return {branch.get("type") for branch in prop_schema.get("anyOf", []) if "type" in branch}


def _coerce_args(schema: dict, args: dict) -> dict:
    """Small models (1.5B-class especially) reliably get array-typed
    parameters wrong in function calls — passing "a+b" or "a, b" as a
    plain string instead of ["a", "b"]. Rather than trust every tool call
    to be well-typed, fix the common case: a string where the schema
    wants an array gets split on the first delimiter that yields >1 token.
    """
    properties = schema.get("properties", {})
    fixed = dict(args)
    for key, value in args.items():
        prop = properties.get(key)
        if not prop or not isinstance(value, str):
            continue
        if "array" not in _allowed_types(prop):
            continue
        for delimiter in (",", "+", " "):
            parts = [p.strip() for p in value.split(delimiter) if p.strip()]
            if len(parts) > 1:
                fixed[key] = parts
                break
    return fixed


def _resolve_command(command: str) -> str:
    # A venv's own Scripts/bin dir isn't guaranteed to be on PATH unless the
    # venv was activated in the parent shell — check there first so a bare
    # "mcp-server-fetch" in the config works either way.
    venv_bin = Path(sys.executable).parent
    for candidate in (venv_bin / command, venv_bin / f"{command}.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which(command) or command


class MCPBridge:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._sessions: dict[str, ClientSession] = {}
        self._stack = AsyncExitStack()
        # AsyncExitStack's __aenter__ must run on the loop that will later
        # tear it down; do it once up front rather than per-server.
        self._run(self._stack.__aenter__())

    def _run(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def load_and_register(self) -> dict[str, list[str] | str]:
        """Connects every server in config/mcp_servers.json. Returns
        {server_name: [tool names]} on success, or {server_name: "error..."}
        for a server that failed to start — one bad server must not block
        the others or crash the app.
        """
        results: dict[str, list[str] | str] = {}
        if not CONFIG_PATH.exists():
            return results

        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for name, spec in config.get("mcpServers", {}).items():
            try:
                tools = self._run(self._connect(name, spec), timeout=30)
                results[name] = tools
            except Exception as e:
                results[name] = f"failed to start: {e}"
        return results

    async def _connect(self, name: str, spec: dict) -> list[str]:
        params = StdioServerParameters(
            command=_resolve_command(spec["command"]),
            args=spec.get("args", []),
            env=spec.get("env"),
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[name] = session

        listing = await session.list_tools()
        for tool in listing.tools:
            self._register_tool(name, tool)
        return [t.name for t in listing.tools]

    def _register_tool(self, server_name: str, tool):
        full_name = f"mcp_{server_name}_{tool.name}"
        schema = tool.input_schema or {}

        def call(**kwargs):
            fixed = _coerce_args(schema, kwargs)
            return self._run(self._call_tool(server_name, tool.name, fixed))

        _REGISTRY[full_name] = {
            "fn": call,
            "schema": {
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": f"[MCP:{server_name}] {tool.description or tool.name}",
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            },
        }

    async def _call_tool(self, server_name: str, tool_name: str, args: dict) -> str:
        session = self._sessions[server_name]
        result = await session.call_tool(tool_name, args)
        parts = [block.text for block in result.content if hasattr(block, "text")]
        return "\n".join(parts) if parts else str(result)


_bridge: MCPBridge | None = None


def init_mcp_tools() -> dict[str, list[str] | str]:
    """Call once at startup (main.py / api/server.py already do). Safe to
    call more than once — a no-op after the first successful call.
    """
    global _bridge
    if _bridge is not None:
        return {}
    _bridge = MCPBridge()
    return _bridge.load_and_register()
