# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5
# Licensed under MIT License

"""MCP (Model Context Protocol) client integration."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Optional

from .. import procutil

MAX_PAGES = 100  # safety bound for cursor paging


@dataclass
class MCPServerConfig:
    """MCP server configuration."""
    name: str
    command: list[str]
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # "stdio", "sse", "websocket"
    url: str = ""  # For SSE/WS transports
    timeout: float = 60.0  # per-request timeout in seconds
    headers: dict[str, str] = field(default_factory=dict)  # HTTP transport only
    init_timeout: float = 5.0  # initialize handshake timeout in seconds


@dataclass
class MCPTool:
    """MCP tool definition."""
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False  # annotations.readOnlyHint, as the server claims it


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
    """Stdio transport for local MCP servers (newline-delimited JSON-RPC 2.0).

    A reader thread routes responses to waiting callers by id, answers requests the server
    sends us (``ping`` -> ``{}``, anything else -> error -32601) and fails every pending call
    at once when the server's stdout closes. A second thread drains stderr into a ring buffer
    so a chatty server never blocks; the last lines are available via ``stderr_tail()``.
    """

    STDERR_LINES = 50

    def __init__(self, command: list[str], env: dict[str, str] | None = None,
                 cwd: str | None = None) -> None:
        if not command:
            raise ValueError("MCP stdio command is empty")
        command = list(command)
        if not os.path.dirname(command[0]) and not (os.altsep and os.altsep in command[0]):
            resolved = procutil.safe_which(command[0], exclude=(cwd,) if cwd else ())
            if resolved is None:
                raise FileNotFoundError(f"MCP server command not found on PATH: {command[0]}")
            command[0] = resolved
        full_env = dict(os.environ)
        if env:
            full_env.update({str(k): str(v) for k, v in env.items()})
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=cwd,
            **procutil.popen_group_kwargs(),
        )  # binary pipes: Windows text mode would write CRLF, we want exactly one LF
        self._lock = threading.Lock()  # guards writes and the pending table
        self._pending: dict[int, _Pending] = {}
        self._request_id = 0
        self._eof = False
        self._stderr: deque[str] = deque(maxlen=self.STDERR_LINES)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._reader.start()
        self._stderr_thread.start()

    # -- background threads -------------------------------------------------

    def _read_loop(self) -> None:
        try:
            for raw in self._proc.stdout:  # type: ignore[union-attr]
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict):
                    self._dispatch(msg)
        except (OSError, ValueError):
            pass
        finally:
            self._fail_all()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if method is not None:
            if "id" in msg:  # server request: must be answered or the server may hang
                if method == "ping":
                    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                else:
                    reply = {"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": f"Method not found: {method}"}}
                try:
                    self.send(reply)
                except Exception:
                    pass
            return  # notifications from the server are ignored
        rid = msg.get("id")
        if not isinstance(rid, (str, int)):
            return  # unhashable or odd id (list, dict): not ours, ignore
        with self._lock:
            pending = self._pending.get(rid)
        if pending is not None:
            pending.response = msg
            pending.event.set()

    def _fail_all(self) -> None:
        with self._lock:
            self._eof = True
            waiting = list(self._pending.values())
        for pending in waiting:
            pending.event.set()

    def _drain_stderr(self) -> None:
        try:
            for raw in self._proc.stderr:  # type: ignore[union-attr]
                self._stderr.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except (OSError, ValueError):
            pass

    # -- public API -----------------------------------------------------------

    def stderr_tail(self) -> str:
        """The last lines the server wrote to stderr (up to 50)."""
        return "\n".join(self._stderr)

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None and not self._eof

    def send(self, message: dict[str, Any]) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._lock:
            try:
                self._proc.stdin.write(data)  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"MCP server is gone: {exc}") from exc

    def receive(self) -> Optional[dict[str, Any]]:
        # Responses are routed by call(); there is no free-standing receive.
        return None

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification (no id, no response)."""
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        """Send a request and wait for its response."""
        pending = _Pending()
        with self._lock:
            if self._eof:
                raise RuntimeError(self._gone_message())
            self._request_id += 1
            req_id = self._request_id
            self._pending[req_id] = pending
        try:
            self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
            if not pending.event.wait(timeout):
                raise TimeoutError(f"MCP request {method} timed out after {timeout:g}s")
            if pending.response is None:  # woken by EOF
                raise RuntimeError(self._gone_message())
            if "error" in pending.response:
                err = pending.response["error"]
                message = err.get("message", err) if isinstance(err, dict) else err
                raise RuntimeError(f"MCP error: {message}")
            return pending.response.get("result")
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def _gone_message(self) -> str:
        tail = self.stderr_tail()
        msg = "MCP server exited"
        return f"{msg}; stderr: {tail}" if tail else msg

    def close(self, grace: float = 5.0) -> None:
        proc = self._proc
        try:
            if proc.stdin:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=grace)  # a well-behaved server exits when its stdin closes
        except subprocess.TimeoutExpired:
            procutil.kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._fail_all()


class HttpTransport(MCPTransport):
    """Streamable HTTP transport: every message is a POST; the reply is JSON or a short SSE stream.

    Blocking (urllib, stdlib), one request at a time. Keeps the ``Mcp-Session-Id`` the server hands out and
    ends the session with DELETE on close. Only http(s) URLs are accepted.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> None:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError(f"MCP http url must start with http:// or https://: {url}")
        self._url = url
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self._timeout = timeout
        self._session: str | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    alive = True

    def stderr_tail(self) -> str:
        return ""

    def _post(self, message: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **self._headers}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(self._url, data=json.dumps(message).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured URL
                self._session = resp.headers.get("Mcp-Session-Id") or self._session
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"MCP http error {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if "timed out" in str(exc):
                raise TimeoutError(f"MCP request {message.get('method')} timed out after {timeout:g}s") from exc
            raise RuntimeError(f"MCP server is gone: {exc}") from exc
        out: list[dict[str, Any]] = []
        if ctype == "text/event-stream":
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        out.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
        elif body.strip():
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return out
            out = parsed if isinstance(parsed, list) else [parsed]
        return [m for m in out if isinstance(m, dict)]

    def send(self, message: dict[str, Any]) -> None:
        self._post(message, self._timeout)

    def receive(self) -> Optional[dict[str, Any]]:
        return None

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        with self._lock:
            self._post(msg, self._timeout)

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        with self._lock:
            self._request_id += 1
            rid = self._request_id
            replies = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, timeout)
        for msg in replies:
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(f"MCP error: {err.get('message', err) if isinstance(err, dict) else err}")
                return msg.get("result")
        raise RuntimeError(f"MCP server sent no answer to {method}")

    def close(self) -> None:
        if not self._session:
            return
        import urllib.request
        req = urllib.request.Request(self._url, headers={**self._headers, "Mcp-Session-Id": self._session},
                                     method="DELETE")
        try:
            urllib.request.urlopen(req, timeout=3).close()  # noqa: S310
        except Exception:
            pass
        self._session = None


class _Pending:
    """One in-flight request."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: dict[str, Any] | None = None


class MCPClient:
    """MCP client for connecting to MCP servers."""

    def __init__(self, transport: MCPTransport, timeout: float = 60.0,
                 init_timeout: float = 5.0) -> None:
        self.transport = transport
        self.timeout = timeout
        self.init_timeout = init_timeout
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
        }, timeout=self.init_timeout)
        self.transport.notify("notifications/initialized")
        self._initialized = True
        return result

    def list_tools(self) -> list[MCPTool]:
        """List available tools from MCP server."""
        tools = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self.transport.call("tools/list", params, timeout=self.timeout) or {}
            for t in result.get("tools", []):
                tools.append(MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    read_only=bool((t.get("annotations") or {}).get("readOnlyHint")),
                ))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = tools
        return tools

    def list_resources(self) -> list[MCPResource]:
        """List available resources from MCP server."""
        result = self.transport.call("resources/list", {}, timeout=self.timeout)
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
        result = self.transport.call("prompts/list", {}, timeout=self.timeout)
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
        }, timeout=self.timeout)

    def read_resource(self, uri: str) -> Any:
        """Read an MCP resource."""
        return self.transport.call("resources/read", {"uri": uri}, timeout=self.timeout)

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Get an MCP prompt."""
        return self.transport.call("prompts/get", {
            "name": name,
            "arguments": arguments or {},
        }, timeout=self.timeout)

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

    def connect(self, name: str, cwd: str | None = None) -> MCPClient:
        """Connect to an MCP server; ``cwd`` (the workspace) is its working directory."""
        if name in self._clients:
            return self._clients[name]

        config = self._configs.get(name)
        if not config:
            raise ValueError(f"MCP server not configured: {name}")

        if config.transport == "http":
            transport: MCPTransport = HttpTransport(config.url, config.headers, timeout=config.init_timeout)
        elif config.transport == "stdio":
            transport = StdioTransport(config.command + config.args, config.env, cwd=cwd)
        else:
            raise NotImplementedError(f"Transport {config.transport} not supported (stdio and http only)")
        client = MCPClient(transport, timeout=config.timeout, init_timeout=config.init_timeout)
        try:
            client.initialize()
        except Exception as exc:
            tail = transport.stderr_tail() if hasattr(transport, "stderr_tail") else ""
            if isinstance(transport, StdioTransport):
                transport.close(grace=0.5)  # it never answered: do not wait long for a polite exit
            else:
                client.close()
            detail = "\n" + tail if tail and tail not in str(exc) else ""
            raise RuntimeError(f"MCP server '{name}' failed to start: {exc}{detail}") from exc
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
        for client in list(self._clients.values()):
            client.close()
        self._clients.clear()

    def get_all_tools(self) -> dict[str, list[MCPTool]]:
        """Get tools from all connected servers."""
        return {name: client._tools for name, client in self._clients.items()}


def tool_result_text(result: Any) -> tuple[str, bool]:
    """Flatten a ``tools/call`` result into ``(text, is_error)``.

    Text content blocks are joined with newlines; other block types are summarised as
    ``[type]``. ``isError`` maps to the second element.
    """
    if not isinstance(result, dict):
        return ("" if result is None else str(result)), False
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append("[" + str(block.get("type", "unknown")) + "]")
    return "\n".join(parts), bool(result.get("isError"))


# Global manager
_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    global _manager
    if _manager is None:
        _manager = MCPClientManager()
    return _manager