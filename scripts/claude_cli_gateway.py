#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Tiny OpenAI-compatible gateway in front of the local Claude Code CLI.

Lets rex (or any OpenAI-compatible client) chat with a Claude model through the
locally logged-in `claude` CLI, without an API key. Chat only: Claude's own
tools are disabled, request `tools` are ignored, `tool_calls` are never returned.

Usage:
    python scripts/claude_cli_gateway.py [--port 4000] [--default-model haiku] [--token-file F]
                                         [--max-concurrent 4] [--allow-unsafe]

The token comes from --token-file F, else --token T (visible in the process
list, so a warning is printed), else the REX_GATEWAY_TOKEN environment variable.

Binds to 127.0.0.1 only. Requests whose Host header is not 127.0.0.1 or
localhost are refused (DNS rebinding), POST bodies must be application/json,
and with a token every request needs "Authorization: Bearer <token>".
Each CLI call runs in its own private temporary directory, removed afterwards,
with --safe-mode (no user CLAUDE.md/memory/settings; login kept) when the CLI has it.
`claude --help` is probed once at startup; if that fails the gateway refuses to start unless
--allow-unsafe is given. At most --max-concurrent claude processes run at once (default 4);
a timed-out call kills the whole process tree.
Logs method, path, model, duration and status only.
"""

from __future__ import annotations

import argparse
import errno
import hmac
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _load_procutil() -> Any:
    """Load runtimelib/procutil.py by path (stdlib only; avoids importing the whole runtime package)."""
    path = Path(__file__).resolve().parent.parent / "runtimelib" / "procutil.py"
    spec = importlib.util.spec_from_file_location("_gateway_procutil", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_procutil = _load_procutil()
kill_tree = _procutil.kill_tree
safe_which = _procutil.safe_which
popen_group_kwargs = _procutil.popen_group_kwargs

HOST = "127.0.0.1"
DEFAULT_PORT = 4000
TOKEN_ENV = "REX_GATEWAY_TOKEN"
DEFAULT_MODEL = "haiku"
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_CONCURRENT = 4
ALIASES = ("haiku", "sonnet", "opus")
MAX_BODY = 32 * 1024 * 1024
_ADDR_IN_USE = (errno.EADDRINUSE,)
_ADDR_DENIED = (errno.EACCES,)


class GatewayError(Exception):
    """Error that maps to an OpenAI-style error response."""

    def __init__(self, status: int, message: str, err_type: str = "gateway_error") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.err_type = err_type


def valid_model(name: str) -> bool:
    """Accept CLI aliases and full ids like claude-sonnet-4-6."""
    if name in ALIASES:
        return True
    return name.startswith("claude-") and all(c.isalnum() or c in "-._[]" for c in name)


def _content_text(content: Any) -> str:
    """Return plain text from an OpenAI message content (string or parts list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return str(content)


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Flatten chat messages into one prompt for `claude -p`."""
    system_parts: list[str] = []
    turns: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        text = _content_text(msg.get("content"))
        if role in ("system", "developer"):
            if text:
                system_parts.append(text)
            continue
        if role == "assistant":
            calls = msg.get("tool_calls") or []
            for call in calls:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                text += f"\n[called tool {fn.get('name', '?')} with {fn.get('arguments', '')}]"
            label = "Assistant"
        elif role == "tool":
            label = "Tool result"
        else:
            label = "User"
        if text.strip():
            turns.append(f"{label}: {text.strip()}")

    out: list[str] = []
    if system_parts:
        out.append("[System instructions]\n" + "\n\n".join(system_parts))
    if len(turns) == 1 and turns[0].startswith("User: "):
        out.append(turns[0][len("User: "):])
    elif turns:
        out.append("[Conversation so far]\n" + "\n\n".join(turns)
                   + "\n\n[Reply as the Assistant to the last User message. Plain text only; no tools are available.]")
    return "\n\n".join(out)


def build_command(claude_bin: str, model: str, setting_sources: bool = False,
                  safe_mode: bool = False) -> list[str]:
    """Argument list for one non-interactive, tool-less Claude call.

    ``safe_mode`` (`--safe-mode`) disables all customizations, including the
    user's global CLAUDE.md, memory, hooks and settings, while keeping the
    login. Without it, ``setting_sources`` loads only the user's own settings,
    never project or local ones (the user's CLAUDE.md still loads then).
    """
    cmd = [
        claude_bin, "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    if safe_mode:
        cmd.append("--safe-mode")
    elif setting_sources:
        cmd += ["--setting-sources", "user"]
    return cmd


_FLAG_RE = re.compile(rb"--[a-z][a-z0-9-]*")


def cli_flags(claude_bin: str) -> set[str] | None:
    """Option names listed by `claude --help`, or None when the probe fails (retried once).

    Called once at startup; requests never probe again.
    """
    for _ in range(2):
        try:
            proc = subprocess.run([claude_bin, "--help"], capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = proc.stdout or b""
        if isinstance(out, str):
            out = out.encode("utf-8", errors="replace")
        return {m.decode("ascii") for m in _FLAG_RE.findall(out)}
    return None


def run_claude(prompt: str, model: str, claude_bin: str, timeout: float,
               flags: Any = frozenset()) -> dict[str, Any]:
    """Run the CLI with the prompt on stdin; return {'text', 'usage'}.

    ``flags`` is the startup probe result (see cli_flags); nothing is probed here.
    The CLI runs in a fresh private directory (mode 0700 on POSIX) that is
    removed after the call, so it never sees another request's files. On a
    timeout the whole process tree is killed.
    """
    flags = flags or frozenset()
    cmd = build_command(claude_bin, model, "--setting-sources" in flags, "--safe-mode" in flags)
    workdir = tempfile.mkdtemp(prefix="claude-gw-")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            **popen_group_kwargs(),
        )
        try:
            out, _err = proc.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
            raise GatewayError(504, f"claude CLI timed out after {timeout:.0f}s", "timeout")
    except OSError as exc:
        raise GatewayError(502, f"cannot start claude CLI: {exc.__class__.__name__}", "cli_unavailable")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    stdout = out.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(stdout) if stdout else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise GatewayError(502, f"claude CLI failed (exit {proc.returncode}, no JSON result)", "cli_error")
    if data.get("is_error") or proc.returncode != 0:
        detail = str(data.get("result") or data.get("subtype") or "unknown error")[:300]
        raise GatewayError(502, f"claude CLI error (exit {proc.returncode}): {detail}", "cli_error")
    usage = data.get("usage") or {}
    return {"text": str(data.get("result") or ""), "usage": usage}


def _openai_usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt = int(usage.get("input_tokens", 0) or 0) + int(usage.get("cache_read_input_tokens", 0) or 0) \
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    completion = int(usage.get("output_tokens", 0) or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def completion_body(text: str, model: str, usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": _openai_usage(usage),
    }


def sse_events(text: str, model: str) -> list[bytes]:
    """SSE chunks rex can parse: role+content chunk, stop chunk, [DONE].

    rex ignores unknown chunk fields; these chunks carry only
    id/object/created/model/choices.
    """
    cid = "chatcmpl-" + uuid.uuid4().hex
    created = int(time.time())

    def chunk(delta: dict[str, Any], finish: str | None) -> bytes:
        body = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return b"data: " + json.dumps(body).encode("utf-8") + b"\n\n"

    return [
        chunk({"role": "assistant", "content": text}, None),
        chunk({}, "stop"),
        b"data: [DONE]\n\n",
    ]


def error_body(message: str, err_type: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type, "code": err_type}}


_LOCAL_HOSTS = ("127.0.0.1", "localhost")


def host_allowed(host: str | None, port: int) -> bool:
    """Host header must name this loopback server (DNS-rebinding defence)."""
    if not host:
        return False
    host = host.strip().lower()
    allowed = set(_LOCAL_HOSTS) | {f"{h}:{port}" for h in _LOCAL_HOSTS}
    return host in allowed


def token_ok(header: str | None, token: str | None) -> bool:
    """No token configured: always ok. Otherwise a constant-time Bearer check."""
    if not token:
        return True
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer "):].encode("utf-8"), token.encode("utf-8"))


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "claude-cli-gateway/1"
    # Set on the server instance: default_model, claude_bin, timeout, runner.

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # default access log is replaced by _log (no bodies, no headers)

    def _log(self, model: str, started: float, status: int) -> None:
        ms = (time.monotonic() - started) * 1000
        sys.stderr.write(f"{self.command} {self.path} model={model or '-'} {ms:.0f}ms status={status}\n")

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _check_request(self, started: float) -> bool:
        """Host and token checks for every request; sends the error itself."""
        if not host_allowed(self.headers.get("Host"), self.server.server_address[1]):
            self._send_json(403, error_body("forbidden host", "forbidden"))
            self._log("", started, 403)
            return False
        if not token_ok(self.headers.get("Authorization"), self.server.token):
            self._send_json(401, error_body("missing or wrong bearer token", "unauthorized"))
            self._log("", started, 401)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        started = time.monotonic()
        if not self._check_request(started):
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/v1/models", "/models"):
            now = int(time.time())
            models = list(ALIASES)
            if self.server.default_model not in models:
                models.append(self.server.default_model)
            body = {"object": "list", "data": [
                {"id": m, "object": "model", "created": now, "owned_by": "claude-cli"} for m in models]}
            self._send_json(200, body)
            self._log("", started, 200)
        elif path in ("", "/v1"):
            self._send_json(200, {
                "name": "claude-cli-gateway",
                "endpoints": ["/v1/models", "/v1/chat/completions", "/health"],
                "default_model": self.server.default_model,
            })
            self._log("", started, 200)
        elif path == "/health":
            self._send_json(200, {"status": "ok"})
            self._log("", started, 200)
        else:
            self._send_json(404, error_body("not found", "not_found"))
            self._log("", started, 404)

    def do_POST(self) -> None:  # noqa: N802
        started = time.monotonic()
        if not self._check_request(started):
            return
        path = self.path.split("?", 1)[0].rstrip("/")
        model = ""
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, error_body("not found", "not_found"))
            self._log(model, started, 404)
            return
        try:
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                raise GatewayError(415, "Content-Type must be application/json", "invalid_request_error")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise GatewayError(400, "invalid Content-Length", "invalid_request_error")
            if length <= 0 or length > MAX_BODY:
                raise GatewayError(400, "missing or too large request body", "invalid_request_error")
            try:
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise GatewayError(400, "request body is not valid JSON", "invalid_request_error")
            if not isinstance(req, dict) or not isinstance(req.get("messages"), list):
                raise GatewayError(400, "'messages' must be a list", "invalid_request_error")
            model = str(req.get("model") or self.server.default_model)
            if not valid_model(model):
                raise GatewayError(400, f"unknown model '{model}' (use haiku/sonnet/opus or a claude-* id)",
                                   "invalid_request_error")
            prompt = flatten_messages(req["messages"])
            if not prompt.strip():
                raise GatewayError(400, "no text in messages", "invalid_request_error")
            slots = self.server.slots
            if not slots.acquire(timeout=self.server.timeout):
                raise GatewayError(503, "too many concurrent requests; try again later", "overloaded")
            try:
                result = self.server.runner(prompt, model, self.server.claude_bin, self.server.timeout)
            finally:
                slots.release()
        except GatewayError as exc:
            self._send_json(exc.status, error_body(exc.message, exc.err_type))
            self._log(model, started, exc.status)
            return
        except Exception as exc:  # never leave the client without an answer
            try:
                self._send_json(500, error_body(f"internal error: {exc.__class__.__name__}", "internal_error"))
            except OSError:
                pass
            self._log(model, started, 500)
            return

        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in sse_events(result["text"], model):
                    self.wfile.write(event)
                self.wfile.flush()
            except OSError:
                pass  # client went away
            self.close_connection = True
        else:
            self._send_json(200, completion_body(result["text"], model, result["usage"]))
        self._log(model, started, 200)


class GatewayServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that refuses a second bind on the same port.

    On Windows SO_REUSEADDR lets two processes bind the same address, so it is
    turned off there and SO_EXCLUSIVEADDRUSE is used instead. POSIX keeps the
    default SO_REUSEADDR (it only allows rebinding a port in TIME_WAIT).
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def make_server(port: int = DEFAULT_PORT, default_model: str = DEFAULT_MODEL,
                claude_bin: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                runner: Any = None, token: str | None = None, flags: Any = None,
                max_concurrent: int = DEFAULT_MAX_CONCURRENT) -> ThreadingHTTPServer:
    """Create the server bound to 127.0.0.1 (never another interface)."""
    server = GatewayServer((HOST, port), GatewayHandler)
    server.daemon_threads = True
    server.default_model = default_model
    server.claude_bin = claude_bin or safe_which("claude") or "claude"
    server.timeout = timeout
    server.cli_flags = frozenset(flags or ())  # probed once at startup, never again
    server.slots = threading.BoundedSemaphore(max(1, int(max_concurrent)))
    server.runner = runner or (lambda prompt, model, binary, tmo: run_claude(
        prompt, model, binary, tmo, server.cli_flags))
    server.token = token or None
    return server


def resolve_token(token: str | None, token_file: str | None, environ: Any = None) -> str | None:
    """Pick the bearer token: --token-file, else --token (warns), else $REX_GATEWAY_TOKEN.

    Raises ValueError for an unreadable or empty token file, or both flags given.
    """
    if token and token_file:
        raise ValueError("use either --token or --token-file, not both")
    if token_file:
        try:
            with open(token_file, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            raise ValueError(f"cannot read --token-file: {exc.strerror or exc}") from None
        if not value:
            raise ValueError("--token-file is empty")
        return value
    if token:
        print(f"warning: --token is visible in the process list; prefer --token-file or {TOKEN_ENV}",
              file=sys.stderr)
        return token
    env = os.environ if environ is None else environ
    return env.get(TOKEN_ENV) or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAI-compatible gateway to the local Claude Code CLI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--default-model", default=DEFAULT_MODEL,
                        help="haiku, sonnet, opus or a full id like claude-sonnet-4-6")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per CLI call")
    parser.add_argument("--token", default=None,
                        help="if set, every request needs 'Authorization: Bearer <token>' "
                             f"(visible in the process list; prefer --token-file or ${TOKEN_ENV})")
    parser.add_argument("--token-file", default=None,
                        help="read the bearer token from this file (surrounding whitespace stripped)")
    parser.add_argument("--max-concurrent", type=int, default=DEFAULT_MAX_CONCURRENT,
                        help="at most this many claude processes at once (default %(default)s)")
    parser.add_argument("--allow-unsafe", action="store_true",
                        help="start even when 'claude --help' cannot be probed (privacy flags unknown)")
    args = parser.parse_args(argv)
    if args.max_concurrent < 1:
        parser.error("--max-concurrent must be at least 1")
    try:
        token = resolve_token(args.token, args.token_file)
    except ValueError as exc:
        parser.error(str(exc))
    if not valid_model(args.default_model):
        parser.error(f"invalid --default-model '{args.default_model}'")
    claude_bin = safe_which("claude")
    if not claude_bin:
        print("claude CLI not found on PATH (the current directory is never searched)", file=sys.stderr)
        return 2
    flags = cli_flags(claude_bin)
    if flags is None and not args.allow_unsafe:
        print("error: could not run 'claude --help', so the privacy flags are unknown and your user "
              "CLAUDE.md/settings might be sent with every request; fix the CLI or pass --allow-unsafe",
              file=sys.stderr)
        return 2
    try:
        server = make_server(args.port, args.default_model, claude_bin, args.timeout, token=token,
                             flags=flags, max_concurrent=args.max_concurrent)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if exc.errno in _ADDR_IN_USE or winerror == 10048:
            print(f"error: port {args.port} already in use (is another gateway running?)", file=sys.stderr)
        elif exc.errno in _ADDR_DENIED or winerror == 10013:
            print(f"error: port {args.port} not allowed (in use exclusively, reserved, or privileged)",
                  file=sys.stderr)
        else:
            print(f"error: cannot listen on {HOST}:{args.port}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if flags is None:
        print("note: could not run 'claude --help'; safe mode unknown - user CLAUDE.md may be sent",
              file=sys.stderr)
    elif "--safe-mode" in flags:
        pass
    elif "--setting-sources" in flags:
        print("note: this claude CLI has no --safe-mode; your user CLAUDE.md/settings load into every request",
              file=sys.stderr)
    else:
        print("note: this claude CLI has no --safe-mode or --setting-sources; "
              "project and user settings may load", file=sys.stderr)
    if not token:
        print("note: no token set; any local process can use this gateway (and your Claude login). "
              f"Use --token-file or ${TOKEN_ENV}.", file=sys.stderr)
    print(f"claude-cli-gateway on http://{HOST}:{args.port}/v1 (default model {args.default_model})",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
