# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Tools backed by MCP servers (stdio), named ``mcp__<server>__<tool>``.

Servers come from config ``mcp.servers.<name>: {command: [...], env: {...},
enabled: true, timeout: 60, startup_timeout: 5}`` (stdio) or ``{url: "https://...", headers: {...}}`` (streamable
HTTP); the ``ai-rex`` flags ``--mcp-config``, ``--mcp`` and ``--no-mcp`` fill this section.
A server that is disabled or fails to start is
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
MAX_OUTPUT = 100_000  # characters of one MCP result handed to the model
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", part)


def mcp_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` with ``[A-Za-z0-9_-]`` only, at most 64 characters."""
    prefix = f"mcp__{_safe(server)}__"
    return (prefix + _safe(tool))[:MAX_NAME]


class McpTool(Tool):
    """One tool of one MCP server."""

    def __init__(self, server: str, client: MCPClient, name: str, description: str,
                 input_schema: dict[str, Any], read_only: bool = False) -> None:
        self.server = server
        self.read_only = read_only  # the server's own readOnlyHint claim; the loop turns it into an allow rule
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
        # The output comes from a program we did not write: no control or bidi characters, bounded size.
        text = _CONTROL.sub("", text)
        if len(text) > MAX_OUTPUT:
            text = text[:MAX_OUTPUT] + f"\n... (truncated, {len(text) - MAX_OUTPUT} more characters)"
        if is_error:
            raise RuntimeError(text or "MCP tool reported an error")
        return text


def _server_config(name: str, raw: Any) -> MCPServerConfig | None:
    if not isinstance(raw, dict) or raw.get("enabled", True) is False:
        return None
    url = raw.get("url")
    kind = str(raw.get("type") or raw.get("transport") or "").lower()
    if url or kind in ("http", "streamable-http", "streamable_http"):
        if not isinstance(url, str) or not url:
            raise ValueError("no url configured")
        headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else {}
        cfg = MCPServerConfig(name=name, command=[], transport="http", url=url,
                              headers={str(k): str(v) for k, v in headers.items()})
    else:
        command = raw.get("command")
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command:
            raise ValueError("no command configured")
        env = raw.get("env") if isinstance(raw.get("env"), dict) else {}
        cfg = MCPServerConfig(name=name, command=[str(c) for c in command], env=env)
    if raw.get("timeout") is not None:
        cfg.timeout = float(raw["timeout"])
    if raw.get("startup_timeout") is not None:
        cfg.init_timeout = float(raw["startup_timeout"])
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
    seen: set[str] = set()
    for name, raw in servers.items():
        try:
            if "__" in _safe(str(name)):
                # mcp__<server>__<tool> must split unambiguously (rules, hooks, UIs).
                raise ValueError("server names must not contain '__'")
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
                tool = McpTool(cfg.name, client, t.name, t.description, t.input_schema,
                               bool(getattr(t, "read_only", False)))
                if tool.name in seen:
                    # Sanitising and the 64-char cut can map two tools to one name;
                    # a silent overwrite would let rules approve the wrong tool.
                    print(f"ai-rex: MCP tool '{t.name}' of server '{name}' skipped: "
                          f"duplicate name {tool.name}", file=sys.stderr)
                    continue
                seen.add(tool.name)
                tools.append(tool)
        except Exception as exc:
            first = (str(exc).splitlines() or [type(exc).__name__])[0]
            print(f"ai-rex: MCP server '{name}' skipped: {first}", file=sys.stderr)
    return tools, manager
