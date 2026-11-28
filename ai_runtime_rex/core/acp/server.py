# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Agent Client Protocol (ACP) server backed by rex's ``--jsonl`` bridge.

An editor (JetBrains AI Assistant, an ACP client for VS Code, ...) speaks
JSON-RPC 2.0 over stdio to this module. Every ACP session owns one child
``python -m runtime --jsonl`` process, so conversation contexts stay separate
and ``session/cancel`` maps directly to the bridge's ``stop`` command.

Mapping:

    ACP                         rex JSONL
    --------------------------  ------------------------
    initialize                  (local handshake)
    session/new                 spawn child, wait for ``ready``
    session/prompt              chat
    session/update chunks       token / tool_start / tool_end
    session/cancel              stop
    session/close               bye + terminate

Known limitations, deliberately advertised as such: no history replay
(``loadSession`` is false), no image/audio prompts, no MCP passthrough, and
no permission questions yet. The child's permission gate (rules, hooks, mode
defaults) still applies; every ``permission`` question it asks is answered
``deny`` at once and logged on stderr, until questions are mapped to
``session/request_permission``. Calls that would need a question (network,
MCP, and in ``--local-tools`` mode every write or command without an allow
rule) are therefore refused. There is no cost reporting because rex reports
tokens, not prices.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, IO, Optional

from .. import __version__

PROTOCOL_VERSION = 1
AGENT_VERSION = __version__
READY_TIMEOUT = 30.0
CLOSE_TIMEOUT = 5.0

# Directory that contains the ``runtime`` and ``runtimelib`` packages. Needed
# when rex is launched straight from a source checkout: IntelliJ starts the
# bridge with cwd set to the user's project, not to the rex checkout.
REX_ROOT = Path(__file__).resolve().parents[3]

TOOL_KINDS = {
    "bash": "execute",
    "git": "execute",
    "websearch": "fetch",
    "task": "other",
}
EDIT_ACTIONS = {"write", "edit", "create", "delete", "remove", "move", "rename"}
DELETE_ACTIONS = {"delete", "remove"}


def _prompt_text(blocks: Any) -> str:
    """Flatten ACP content blocks into the plain text rex expects."""
    parts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif kind == "resource":
            resource = block.get("resource") or {}
            text = resource.get("text")
            if isinstance(text, str) and text:
                uri = str(resource.get("uri") or "context")
                parts.append(f"[context: {uri}]\n{text}")
    return "\n".join(parts).strip()


def _tool_kind(name: str, args: dict[str, Any]) -> str:
    if name in TOOL_KINDS:
        return TOOL_KINDS[name]
    if name == "fileops":
        action = str(args.get("action") or args.get("operation") or args.get("command") or "").lower()
        if action in DELETE_ACTIONS:
            return "delete"
        if action in EDIT_ACTIONS:
            return "edit"
        return "read"
    return "other"


def _tool_title(name: str, args: dict[str, Any]) -> str:
    action = args.get("action") or args.get("operation") or args.get("command") or args.get("path")
    if action:
        detail = str(action)
        if len(detail) > 80:
            detail = detail[:77] + "..."
        return f"{name}: {detail}"
    return name


class Session:
    """One ACP session and its rex JSONL child process."""

    def __init__(self, sid: str, cwd: str, proc: subprocess.Popen[str], server: "ACPServer") -> None:
        self.id = sid
        self.cwd = cwd
        self.proc = proc
        self.server = server
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.ready = threading.Event()
        self.stdin_lock = threading.Lock()
        self.busy = False
        self.cancel_requested = False
        threading.Thread(target=self._read_stdout, name=f"ai-rex-acp-out-{sid}", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"ai-rex-acp-err-{sid}", daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self.server.log(f"{self.id}: invalid JSON from rex (ignored)")
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "ready":
                    self.ready.set()
                self.events.put(event)
        except (OSError, ValueError) as exc:
            self.server.log(f"{self.id}: rex stdout closed: {exc}")
        finally:
            self.ready.set()
            self.events.put({"type": "_eof"})

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        try:
            for line in self.proc.stderr:
                self.server.log(f"{self.id}: {line.rstrip()}")
        except (OSError, ValueError):
            pass

    def send(self, item: dict[str, Any]) -> bool:
        try:
            with self.stdin_lock:
                assert self.proc.stdin is not None
                self.proc.stdin.write(json.dumps(item, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError) as exc:
            self.server.log(f"{self.id}: cannot write to rex: {exc}")
            return False

    def close(self) -> None:
        self.cancel_requested = True
        if self.proc.poll() is None:
            self.send({"type": "bye"})
            try:
                self.proc.wait(timeout=CLOSE_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()


class ACPServer:
    def __init__(
        self,
        config: Any,
        model: Optional[str] = None,
        local_tools: bool = False,
        stream_in: Optional[IO[str]] = None,
        stream_out: Optional[IO[str]] = None,
        config_path: Optional[Path | str] = None,
        env_file: Optional[Path | str] = None,
    ) -> None:
        self.config = config
        self.model = model
        self.local_tools = local_tools
        # Child sessions run with cwd set to the client's project, so the
        # parent's --config/--env-file are passed on as absolute paths.
        self.config_path = Path(config_path).resolve() if config_path else None
        self.env_file = Path(env_file).resolve() if env_file else None
        self.stdin = stream_in if stream_in is not None else sys.stdin
        self.stdout = stream_out if stream_out is not None else sys.stdout
        self.queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.sessions: dict[str, Session] = {}
        self.notices_enabled = False
        self._write_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._stopping = False

    # ---------- wire helpers ----------

    def write(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        with self._write_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def respond(self, request_id: Any, result: Any = None, error: Any = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result if result is not None else {}
        self.write(payload)

    def error(self, request_id: Any, code: int, message: str) -> None:
        self.respond(request_id, error={"code": code, "message": message})

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.write({"jsonrpc": "2.0", "method": method, "params": params})

    def update(self, sid: str, update: dict[str, Any]) -> None:
        self.notify("session/update", {"sessionId": sid, "update": update})

    def notice(self, sid: str, severity: str, title: str, description: str = "") -> None:
        if not self.notices_enabled:
            return
        payload: dict[str, Any] = {
            "sessionUpdate": "notice",
            "severity": severity,
            "title": title,
        }
        if description:
            payload["description"] = description
        self.update(sid, payload)

    def log(self, message: str) -> None:
        with self._log_lock:
            sys.stderr.write(f"ai-rex-acp: {message}\n")
            sys.stderr.flush()

    # ---------- process management ----------

    def child_command(self) -> list[str]:
        """Command line for one child rex session."""
        cmd = [sys.executable, "-P", "-m", "runtime", "--jsonl"]  # -P: never put the project cwd on sys.path
        if self.config_path:
            cmd.extend(["--config", str(self.config_path)])
        if self.env_file:
            cmd.extend(["--env-file", str(self.env_file)])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.local_tools:
            cmd.append("--local-tools")
        return cmd

    def _spawn(self, sid: str, cwd: str) -> Session:
        cmd = self.child_command()

        env = dict(os.environ)
        root = str(REX_ROOT)
        pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root + (os.pathsep + pythonpath if pythonpath else "")

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        session = Session(sid, cwd, proc, self)
        if not session.ready.wait(READY_TIMEOUT):
            session.close()
            raise TimeoutError(f"rex did not become ready in {READY_TIMEOUT:.0f}s")
        return session

    # ---------- ACP requests ----------

    def _initialize(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        requested = params.get("protocolVersion", PROTOCOL_VERSION)
        try:
            version = int(requested)
        except (TypeError, ValueError):
            version = PROTOCOL_VERSION
        chosen = version if version <= PROTOCOL_VERSION else PROTOCOL_VERSION
        client_caps = params.get("clientCapabilities") or {}
        notices = (((client_caps.get("session") or {}).get("notices")) if isinstance(client_caps, dict) else None)
        self.notices_enabled = isinstance(notices, dict)
        self.respond(msg.get("id"), {
            "protocolVersion": chosen,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
                "sessionCapabilities": {"close": {}},
            },
            "agentInfo": {"name": "ai-rex", "title": "ai-rex", "version": AGENT_VERSION},
            "authMethods": [],
        })

    def _session_new(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        cwd = params.get("cwd") or os.getcwd()
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
            self.error(msg.get("id"), -32602, "cwd must be an existing absolute directory")
            return
        sid = "sess_" + uuid.uuid4().hex[:12]
        try:
            session = self._spawn(sid, cwd)
        except Exception as exc:  # noqa: BLE001 - reported to the editor as an ACP error
            self.log(f"cannot start rex for {cwd}: {exc}")
            self.error(msg.get("id"), -32000, str(exc))
            return
        self.sessions[sid] = session
        if self.local_tools:
            self.notice(
                sid,
                "warning",
                "rex local tools",
                "Tools run on the host without Docker guard. Permission questions are not "
                "supported over ACP yet: calls without an allow rule are denied.",
            )
        self.respond(msg.get("id"), {"sessionId": sid})

    def _prompt(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        sid = params.get("sessionId")
        session = self.sessions.get(sid) if isinstance(sid, str) else None
        if session is None:
            self.error(msg.get("id"), -32002, "unknown session")
            return
        if session.busy:
            self.error(msg.get("id"), -32000, "a prompt is already running for this session")
            return
        text = _prompt_text(params.get("prompt"))
        if not text:
            self.error(msg.get("id"), -32602, "empty prompt")
            return
        session.busy = True
        session.cancel_requested = False
        token = uuid.uuid4().hex[:8]
        run = {
            "id": msg.get("id"),
            "sid": sid,
            "text": text,
            "message_id": "msg_" + token,
            "token": token,
            "tokens": "",
            "streamed": False,
            "tools": 0,
        }
        threading.Thread(
            target=self._prompt_worker,
            args=(session, run),
            name=f"ai-rex-acp-prompt-{token}",
            daemon=True,
        ).start()

    def _prompt_worker(self, session: Session, run: dict[str, Any]) -> None:
        sid = run["sid"]
        stop = "end_turn"
        try:
            if not session.send({"type": "chat", "text": run["text"]}):
                raise RuntimeError("rex process is not accepting commands")
            while True:
                event = session.events.get()
                kind = event.get("type")
                if kind == "_eof":
                    if not session.cancel_requested:
                        self._emit_message(sid, run, "[rex process exited]")
                    break
                if kind == "token":
                    text = event.get("text")
                    if isinstance(text, str) and text:
                        run["tokens"] += text
                        self._emit_message(sid, run, text)
                elif kind == "tool_start":
                    name = str(event.get("name") or "tool")
                    args = event.get("args") if isinstance(event.get("args"), dict) else {}
                    run["tools"] += 1
                    call_id = f"call_{run['token']}_{run['tools']}"
                    self.update(sid, {
                        "sessionUpdate": "tool_call",
                        "toolCallId": call_id,
                        "title": _tool_title(name, args),
                        "name": name,
                        "kind": _tool_kind(name, args),
                        "status": "pending",
                        "rawInput": args,
                    })
                    self.update(sid, {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": call_id,
                        "status": "in_progress",
                    })
                elif kind == "tool_end":
                    call_id = f"call_{run['token']}_{run['tools']}" if run["tools"] else None
                    if call_id:
                        ok = bool(event.get("ok"))
                        detail = str(event.get("error") or "") if not ok else ""
                        payload: dict[str, Any] = {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": call_id,
                            "status": "completed" if ok else "failed",
                        }
                        if detail:
                            payload["content"] = [{"type": "content", "content": {"type": "text", "text": detail}}]
                        self.update(sid, payload)
                elif kind == "final":
                    text = event.get("text")
                    if isinstance(text, str) and text and not run["streamed"]:
                        self._emit_message(sid, run, text)
                    break
                elif kind == "error":
                    message = str(event.get("message") or "rex error")
                    self._emit_message(sid, run, f"[rex error] {message}")
                    break
                elif kind == "permission":
                    # No ACP mapping yet: never leave the child waiting, never allow.
                    self.log(f"{sid}: permission denied (not supported over ACP yet): "
                             f"{event.get('tool')}: {event.get('summary')}")
                    session.send({"type": "permission_reply", "id": event.get("id"), "decision": "deny"})
                elif kind in ("ready", "reset_ok", "model_ok", "bye"):
                    continue
            if session.cancel_requested:
                stop = "cancelled"
        except Exception as exc:  # noqa: BLE001 - never leave a prompt unanswered
            self.log(f"prompt {run['token']} failed: {exc}")
            self._emit_message(sid, run, f"[rex error] {exc}")
        finally:
            session.busy = False
            self.queue.put(("done", run["id"], stop))

    def _emit_message(self, sid: str, run: dict[str, Any], text: str) -> None:
        if not text:
            return
        run["streamed"] = True
        self.update(sid, {
            "sessionUpdate": "agent_message_chunk",
            "messageId": run["message_id"],
            "content": {"type": "text", "text": text},
        })

    def _cancel(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        sid = params.get("sessionId")
        session = self.sessions.get(sid) if isinstance(sid, str) else None
        if session is None:
            return
        session.cancel_requested = True
        session.send({"type": "stop"})

    def _session_close(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        sid = params.get("sessionId")
        session = self.sessions.pop(sid, None) if isinstance(sid, str) else None
        if session is not None:
            session.close()
        self.respond(msg.get("id"), {})

    def _shutdown(self, msg: dict[str, Any]) -> None:
        self._stopping = True
        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()
        self.respond(msg.get("id"), {})

    # ---------- main loop ----------

    def _read_incoming(self) -> None:
        try:
            for line in self.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.log("invalid JSON from editor (ignored)")
                    continue
                if isinstance(msg, dict):
                    self.queue.put(("in", msg))
        except (OSError, ValueError):
            pass
        self.queue.put(("eof", None))

    def _handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        has_id = "id" in msg
        if method == "initialize" and has_id:
            self._initialize(msg)
        elif method == "ping" and has_id:
            self.respond(msg.get("id"), {})
        elif method == "session/new" and has_id:
            self._session_new(msg)
        elif method == "session/prompt" and has_id:
            self._prompt(msg)
        elif method == "session/close" and has_id:
            self._session_close(msg)
        elif method == "shutdown" and has_id:
            self._shutdown(msg)
        elif method == "session/cancel" and not has_id:
            self._cancel(msg)
        elif method in ("notifications/initialized", "initialized", "cancel"):
            return
        elif has_id:
            self.error(msg.get("id"), -32601, f"method not found: {method}")

    def run(self) -> int:
        threading.Thread(target=self._read_incoming, name="ai-rex-acp-in", daemon=True).start()
        while True:
            item = self.queue.get()
            if item[0] == "done":
                self.respond(item[1], {"stopReason": item[2]})
                continue
            if item[0] == "eof":
                break
            msg = item[1]
            if self._stopping and "id" in msg:
                self.error(msg.get("id"), -32000, "server is shutting down")
                continue
            self._handle(msg)
        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()
        return 0


def run_acp(
    config: Any,
    model: Optional[str] = None,
    local_tools: bool = False,
    stream_in: Optional[IO[str]] = None,
    stream_out: Optional[IO[str]] = None,
    config_path: Optional[Path | str] = None,
    env_file: Optional[Path | str] = None,
) -> int:
    """Run the ACP server with an already loaded rex config."""
    if stream_in is None and stream_out is None:
        from ..cli.jsonl_bridge import force_utf8_stdio
        force_utf8_stdio()
    return ACPServer(
        config,
        model=model,
        local_tools=local_tools,
        stream_in=stream_in,
        stream_out=stream_out,
        config_path=config_path,
        env_file=env_file,
    ).run()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-rex acp",
        description="Agent Client Protocol server for JetBrains AI Assistant and other ACP clients",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config file")
    parser.add_argument("--env-file", type=Path, default=None, help="Path to .env file")
    parser.add_argument("--model", default=None, help="Model alias to use for all sessions")
    parser.add_argument(
        "--local-tools",
        action="store_true",
        help="Run tools on the host instead of in the Docker guard (calls that need a permission question are denied)",
    )
    args = parser.parse_args(argv)

    from ..config import Config, get_config

    if args.config or args.env_file:
        config = Config.load(args.config, args.env_file)
    else:
        config = get_config()
    if args.model:
        config.set("liteLLM.model", args.model)
    config.set("cli.unsafe_local_tools", bool(args.local_tools))
    return run_acp(
        config,
        model=args.model,
        local_tools=args.local_tools,
        config_path=args.config,
        env_file=args.env_file,
    )
