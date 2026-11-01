# SPDX-License-Identifier: MIT
"""MCP (Model Context Protocol) client integration."""

from __future__ import annotations

import json
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Optional


@dataclass
class MCPServerConfig:
    """MCP server configuration."""
    name: str
    command: list[str]
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio", "sse", "websocket"
    url: str = ""  # For SSE/WS transports


@dataclass
class MCPTool:
    """MCP tool definition."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPResource:
    """MCP resource definition."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class MCPPrompt:
    """MCP prompt template."""
    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)


class MCPTransport(ABC):
    """Abstract transport for MCP communication."""

    @abstractmethod
    def send(self, message: dict[str, Any]) -> None:
        pass

    @abstractmethod
    def receive(self) -> Optional[dict[str, Any]]:
        pass

    @abstractmethod
    def close(self) -> None:
        pass


class StdioTransport(MCPTransport):
    """Stdio transport for local MCP servers."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env or None,
        )
        self._lock = threading.Lock()
        self._response_thread: Optional[threading.Thread] = None
        self._responses: dict[int, threading.Event] = {}
        self._results: dict[int, Any] = {}
        self._request_id = 0
        self._start_reader()

    def _start_reader(self) -> None:
        def read_loop():
            if not self._proc.stdout:
                return
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "id" in resp and resp["id"] in self._responses:
                    req_id = resp["id"]
                    self._results[req_id] = resp
                    self._responses[req_id].set()

        self._response_thread = threading.Thread(target=read_loop, daemon=True)
        self._response_thread.start()

    def send(self, message: dict[str, Any]) -> None:
        with self._lock:
            if self._proc and self._proc.stdin:
                self._proc.stdin.write(json.dumps(message) + "\n")
                self._proc.stdin.flush()

    def receive(self) -> Optional[dict[str, Any]]:
        # For request-response pattern, use call() instead
        return None

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        """Send request and wait for response."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            event = threading.Event()
            self._responses[req_id] = event

        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        try:
            self.send(request)

            if not event.wait(timeout):
                raise TimeoutError(f"MCP request {method} timed out")

            result = self._results.pop(req_id, None)
            if not result:
                raise RuntimeError("No response received")

            if "error" in result:
                raise RuntimeError(f"MCP error: {result['error']['message']}")

            return result.get("result")

        finally:
            self._responses.pop(req_id, None)

    def close(self) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()


class MCPClient:
    """MCP client for connecting to MCP servers."""

    def __init__(self, transport: MCPTransport) -> None:
        self.transport = transport
        self._initialized = False
        self._tools: list[MCPTool] = []
        self._resources: list[MCPResource] = []
        self._prompts: list[MCPPrompt] = []

    def initialize(self) -> dict[str, Any]:
        """Initialize MCP connection."""
        result = self.transport.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "clientInfo": {
                "name": "runtime-ai",
                "version": "0.1.0",
            },
        })
        self._initialized = True
        return result

    def list_tools(self) -> list[MCPTool]:
        """List available tools from MCP server."""
        result = self.transport.call("tools/list", {})
        tools = []
        for t in result.get("tools", []):
            tools.append(MCPTool(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            ))
        self._tools = tools
        return tools

    def list_resources(self) -> list[MCPResource]:
        """List available resources from MCP server."""
        result = self.transport.call("resources/list", {})
        resources = []
        for r in result.get("resources", []):
            resources.append(MCPResource(
                uri=r["uri"],
                name=r["name"],
                description=r.get("description", ""),
                mime_type=r.get("mimeType", ""),
            ))
        self._resources = resources
        return resources

    def list_prompts(self) -> list[MCPPrompt]:
        """List available prompts from MCP server."""
        result = self.transport.call("prompts/list", {})
        prompts = []
        for p in result.get("prompts", []):
            prompts.append(MCPPrompt(
                name=p["name"],
                description=p.get("description", ""),
                arguments=p.get("arguments", []),
            ))
        self._prompts = prompts
        return prompts

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool."""
        return self.transport.call("tools/call", {
            "name": name,
            "arguments": arguments,
        })

    def read_resource(self, uri: str) -> Any:
        """Read an MCP resource."""
        return self.transport.call("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Get an MCP prompt."""
        return self.transport.call("prompts/get", {
            "name": name,
            "arguments": arguments or {},
        })

    def close(self) -> None:
        self.transport.close()


class MCPClientManager:
    """Manages multiple MCP server connections."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    def add_server(self, config: MCPServerConfig) -> None:
        """Add an MCP server configuration."""
        self._configs[config.name] = config

    def connect(self, name: str) -> MCPClient:
        """Connect to an MCP server."""
        if name in self._clients:
            return self._clients[name]

        config = self._configs.get(name)
        if not config:
            raise ValueError(f"MCP server not configured: {name}")

        if config.transport == "stdio":
            transport = StdioTransport(config.command + config.args, config.env)
        else:
            raise NotImplementedError(f"Transport {config.transport} not yet implemented")

        client = MCPClient(transport)
        client.initialize()
        self._clients[name] = client
        return client

    def get_client(self, name: str) -> MCPClient | None:
        return self._clients.get(name)

    def disconnect(self, name: str) -> None:
        """Disconnect from an MCP server."""
        client = self._clients.pop(name, None)
        if client:
            client.close()

    def disconnect_all(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def get_all_tools(self) -> dict[str, list[MCPTool]]:
        """Get tools from all connected servers."""
        return {name: client._tools for name, client in self._clients.items()}


# Global manager
_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager