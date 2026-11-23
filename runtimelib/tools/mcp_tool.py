# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Tools backed by MCP servers (stdio), named ``mcp__<server>__<tool>``.

Servers come from config ``mcp.servers.<name>: {command: [...], env: {...},
enabled: true, timeout: 60}``. A server that is disabled or fails to start is
skipped with one line on stderr; the others still load.

The gate classifies every ``mcp__*`` call as kind ``mcp`` (ask unless allowed).
"""

from __future__ import annotations

import re
import sys
from typing import Any

from .base import Tool, ToolSchema
from ..mcp import MCPClient, MCPClientManager, MCPServerConfig, tool_result_text

MAX_NAME = 64
INTERNAL_ARGS = ("_role", "_agent_id")


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", part)


def mcp_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` with ``[A-Za-z0-9_-]`` only, at most 64 characters."""
    prefix = f"mcp__{_safe(server)}__"
    return (prefix + _safe(tool))[:MAX_NAME]


class McpTool(Tool):
    """One tool of one MCP server."""

    def __init__(self, server: str, client: MCPClient, name: str, description: str,
                 input_schema: dict[str, Any]) -> None:
        self.server = server
        self.remote_name = name
        self._client = client
        params = input_schema if isinstance(input_schema, dict) and input_schema else {}
        if params.get("type") != "object":
            params = {"type": "object", "properties": params.get("properties", {})}
        local = mcp_tool_name(server, name)
        text = f"[MCP {server}] {description}".strip()
        super().__init__(local, text, ToolSchema(local, text, params))

    def execute(self, **kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if k not in INTERNAL_ARGS}
        text, is_error = tool_result_text(self._client.call_tool(self.remote_name, args))
        if is_error:
            raise RuntimeError(text or "MCP tool reported an error")
        return text


def _server_config(name: str, raw: Any) -> MCPServerConfig | None:
    if not isinstance(raw, dict) or raw.get("enabled", True) is False:
        return None
    command = raw.get("command")
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not command:
        raise ValueError("no command configured")
    env = raw.get("env") if isinstance(raw.get("env"), dict) else {}
    cfg = MCPServerConfig(name=name, command=[str(c) for c in command], env=env)
    if raw.get("timeout") is not None:
        cfg.timeout = float(raw["timeout"])
    return cfg


def load_mcp_tools(config: Any, workspace: str | None = None) -> tuple[list[Tool], MCPClientManager]:
    """Connect the configured servers (cwd = workspace) and wrap their tools.

    Returns ``(tools, manager)``; the caller disconnects the manager on exit.
    """
    manager = MCPClientManager()
    tools: list[Tool] = []
    servers = config.get("mcp.servers", {}) if config is not None else {}
    if not isinstance(servers, dict):
        return tools, manager
    for name, raw in servers.items():
        try:
            cfg = _server_config(str(name), raw)
            if cfg is None:
                continue
            manager.add_server(cfg)
            client = manager.connect(cfg.name, cwd=workspace)
            try:
                listed = client.list_tools()
            except Exception:
                manager.disconnect(cfg.name)
                raise
            for t in listed:
                tools.append(McpTool(cfg.name, client, t.name, t.description, t.input_schema))
        except Exception as exc:
            first = (str(exc).splitlines() or [type(exc).__name__])[0]
            print(f"rex: MCP server '{name}' skipped: {first}", file=sys.stderr)
    return tools, manager
