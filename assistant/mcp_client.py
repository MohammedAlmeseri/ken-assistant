"""Connects to the MCP servers and exposes their tools with a server prefix.

Tool names become '<server>__<tool>' (e.g. 'gmail__send_email') so two servers
can't collide and the channel is always known. Uses the MCP Python SDK 2.x `Client`.
"""

from __future__ import annotations

import json
import shutil
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Protocol

from .llm import ToolSpec


# ----------------------------------------------------------------------------
# Tool source interface
#   Anything that lists and calls tools: the real MCP manager or the mock.
# ----------------------------------------------------------------------------

class ToolSource(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


# ----------------------------------------------------------------------------
# MCP connections
#   Connects to every server in mcp_servers.json (HTTP or stdio). One failing server doesn't stop
#   the others. Tool names get a 'server__' prefix, e.g. whatsapp__send_message.
# ----------------------------------------------------------------------------

class MCPManager:
    def __init__(self, config_path: str | Path):
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Copy mcp_servers.example.json to mcp_servers.json.")
        self.servers: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
        for cfg in self.servers.values():  # "./x" in command or args means relative to the config file
            if str(cfg.get("command", "")).startswith("./"):
                cfg["command"] = str((path.parent / cfg["command"]).resolve())
            cfg["args"] = [str((path.parent / a).resolve()) if str(a).startswith("./") else a
                           for a in cfg.get("args", [])]
        self.clients: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> "MCPManager":
        for name, cfg in self.servers.items():
            for attempt in (1, 2):  # a cold start (first launch, busy disk) can time out once
                try:
                    self.clients[name] = await self._connect(cfg)
                    self.errors.pop(name, None)
                    break
                except Exception as exc:  # one server down shouldn't kill the app
                    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                        exc = exc.exceptions[0]  # show the real cause, not "unhandled errors in a TaskGroup"
                    self.errors[name] = f"{type(exc).__name__}: {exc}"
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def _connect(self, cfg: dict):
        from mcp import Client, StdioServerParameters

        if "url" in cfg:
            server: Any = cfg["url"]
        else:
            command = shutil.which(cfg["command"]) or cfg["command"]  # npx -> npx.cmd on Windows
            server = StdioServerParameters(command=command, args=cfg.get("args", []), env=cfg.get("env"))
        # A fresh stack per server, so a failed connect doesn't leave half-open state behind.
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(Client(server, read_timeout_seconds=60))
        except BaseException:
            await stack.aclose()
            raise
        self._stack.push_async_callback(stack.aclose)
        return client

    async def list_tools(self) -> list[ToolSpec]:
        specs = []
        for server, client in self.clients.items():
            cursor = None
            while True:
                result = await client.list_tools(cursor=cursor)
                for tool in result.tools:
                    specs.append(ToolSpec(f"{server}__{tool.name}", tool.description or "", tool.input_schema or {}))
                cursor = result.next_cursor
                if not cursor:
                    break
        return specs

    # ------------------------------------------------------------------------
    # Calling a tool
    #   Text parts are joined; failures come back as 'Tool error: …' text the agent can show.
    # ------------------------------------------------------------------------

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        server, _, tool = name.partition("__")
        client = self.clients.get(server)
        if client is None:
            return f"Tool error: the {server} server is not connected."
        result = await client.call_tool(tool, arguments)
        parts = [getattr(c, "text", "") for c in result.content if getattr(c, "type", "") == "text"]
        if not parts and result.structured_content is not None:
            parts = [json.dumps(result.structured_content, ensure_ascii=False)]
        text = "\n".join(p for p in parts if p)
        return f"Tool error: {text}" if result.is_error else text
