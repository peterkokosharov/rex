# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""JSON-lines stdio bridge for external UIs (the C++ Turbo Vision front-end).

Wire format, one JSON object per line:

    UI -> rex:  {"type":"chat","text":"..."}
                {"type":"reset"}
                {"type":"model","model":"..."}
                {"type":"command","name":"model","value":"..."}
                {"type":"permission_reply","id":"perm-1","decision":"allow_once|allow_always|deny"}
                {"type":"stop"}
                {"type":"bye"}

    rex -> UI:  {"type":"permission","id":"perm-1","tool":"...","kind":"...","summary":"...","detail":"...","args":{...},"rule":null,"reason":""}
                {"type":"ready","model":"...","instructions":[paths]}  (instructions only when AGENTS.md files were loaded)
                {"type":"token","text":"..."}
                {"type":"tool_start","name":"...","args":{...}}
                {"type":"tool_end","ok":true,"error":null}
                {"type":"final","text":"..."}
                {"type":"error","message":"..."}
                {"type":"reset_ok"}
                {"type":"model_ok","model":"..."}
                {"type":"context","used":N,"limit":N,"percent":N,"model":"...","source":"config|endpoint|table|name|default"[,"warning":true]}
                {"type":"bye"}

``context`` follows every ``final`` and every ``model_ok`` (recomputed against
the new model's window); ``warning`` appears only once per arm, when the
estimate first reaches ``context.warn_percent`` (default 80).

Everything is written to stdout with an explicit flush, because the caller
reads line by line from a pipe. Human-readable diagnostics stay on stderr.
One AgentLoop lives for the whole session, so conversation context survives
across ``chat`` messages and is cleared only by ``reset``.

Permissions: when the gate must ask, :class:`JsonlAsker` emits ``permission``
and blocks the loop until the UI answers with ``permission_reply`` (same
``id``). The reply is read by the reader thread, so it works while ``chat`` is
running. Anything but ``allow_once`` / ``allow_always`` is a deny. ``stop``,
``bye``, a closed stdin, or no answer within ``permissions.ask_timeout``
seconds (default 300) deny every open question; ``stop`` (and ``bye`` during a
request) also interrupts the loop. A question asked after the loop was
interrupted is denied at once, without a ``permission`` event.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from typing import Any, Callable, IO, Optional

from ..agent import create_agent_loop
from ..agent.gate import ALLOW_ALWAYS, ALLOW_ONCE, DENY, PermissionRequest

Eof = object()
DEFAULT_ASK_TIMEOUT = 300.0


class JsonlAsker:
    """Gate asker that asks the front-end over the JSONL bridge."""

    def __init__(self, bridge: "JsonlBridge", timeout: float = DEFAULT_ASK_TIMEOUT) -> None:
        self.bridge = bridge
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[str, list[Any]] = {}  # id -> [event, decision]
        self._closed = False

    def _interrupted(self) -> bool:
        check = getattr(self.bridge.loop, "interrupt_requested", None)
        try:
            return bool(check()) if callable(check) else False
        except Exception:
            return False

    def ask(self, request: PermissionRequest) -> str:
        slot: list[Any] = [threading.Event(), DENY]
        with self._lock:
            if self._closed:
                return DENY
            self._pending[request.id] = slot
        try:
            # Checked after the slot is registered: a stop that lands before this
            # check is seen here, one that lands after it denies the slot.
            if self._interrupted():
                return DENY
            self.bridge.emit(
                "permission",
                id=request.id,
                tool=request.tool,
                kind=request.kind,
                summary=request.summary,
                detail=request.detail,
                args=request.args,
                rule=request.rule,
                reason=request.reason,
            )
            if not slot[0].wait(self.timeout):
                return DENY
            return slot[1]
        finally:
            with self._lock:
                self._pending.pop(request.id, None)

    def reply(self, request_id: Any, decision: Any) -> bool:
        """Deliver an answer; returns False for an unknown or already answered id."""
        with self._lock:
            slot = self._pending.get(request_id) if isinstance(request_id, str) else None
        if slot is None or slot[0].is_set():
            return False
        slot[1] = decision if decision in (ALLOW_ONCE, ALLOW_ALWAYS) else DENY
        slot[0].set()
        return True

    def deny_all(self, closed: bool = False) -> None:
        """Deny every open question (stop, bye, closed stdin).

        ``closed`` marks stdin as gone: questions asked later are denied at once.
        """
        with self._lock:
            if closed:
                self._closed = True
            slots = list(self._pending.values())
        for slot in slots:
            if not slot[0].is_set():
                slot[1] = DENY
                slot[0].set()


class JsonlBridge:
    """Persistent agent driven by JSON lines on stdin/stdout."""

    def __init__(
        self,
        config: Any,
        loop_factory: Optional[Callable[["JsonlBridge"], Any]] = None,
        stream: Optional[IO[str]] = None,
        out: Optional[IO[str]] = None,
    ) -> None:
        self.config = config
        self.stream = stream if stream is not None else sys.stdin
        self.out = out if out is not None else sys.stdout
        self._loop_factory = loop_factory or self._default_loop_factory
        self._queue: queue.Queue[Any] = queue.Queue()
        self._emit_lock = threading.Lock()
        self.loop: Any = None
        self.tokens: list[str] = []
        self._pending_context: Optional[dict] = None
        self._busy = False  # a chat request is running
        try:
            timeout = float(config.get("permissions.ask_timeout", DEFAULT_ASK_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_ASK_TIMEOUT
        self.asker = JsonlAsker(self, timeout if timeout > 0 else DEFAULT_ASK_TIMEOUT)

    # ---------- output ----------

    def emit(self, kind: str, /, **fields: Any) -> None:
        payload = {"type": kind}
        payload.update(fields)
        line = json.dumps(payload, ensure_ascii=False)
        with self._emit_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def on_token(self, token: str) -> None:
        if not token:
            return
        self.tokens.append(token)
        self.emit("token", text=token)

    def on_tool_start(self, name: str, args: dict) -> None:
        self.emit(
            "tool_start",
            name=name,
            args={k: v for k, v in (args or {}).items() if not k.startswith("_")},
        )

    def on_tool_end(self, result: Any) -> None:
        self.emit(
            "tool_end",
            ok=bool(getattr(result, "success", False)),
            error=getattr(result, "error", None),
        )

    def on_context(self, status: dict) -> None:
        """Keep the newest status; it is sent after ``final`` / ``model_ok``."""
        self._pending_context = dict(status)

    def _flush_context(self) -> None:
        status, self._pending_context = self._pending_context, None
        if status:
            self.emit("context", **status)

    # ---------- setup ----------

    def _default_loop_factory(self, bridge: "JsonlBridge") -> Any:
        return create_agent_loop(
            self.config,
            on_token=self.on_token,
            on_tool_start=self.on_tool_start,
            on_tool_end=self.on_tool_end,
            on_context=self.on_context,
        )

    def _ensure_loop(self) -> Any:
        if self.loop is None:
            self.loop = self._loop_factory(self)
            if hasattr(self.loop, "on_context"):
                self.loop.on_context = self.on_context
            gate = getattr(self.loop, "gate", None)
            if gate is not None and hasattr(gate, "set_asker"):
                gate.set_asker(self.asker)
        return self.loop

    # ---------- input ----------

    def _reader(self) -> None:
        try:
            for line in self.stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._queue.put({"type": "_invalid", "message": str(exc)})
                    continue
                if not isinstance(item, dict):
                    self._queue.put({"type": "_invalid", "message": "expected a JSON object"})
                    continue
                # A reply must reach the blocked asker, so it never goes
                # through the queue (the main thread is inside loop.run()).
                if item.get("type") == "permission_reply":
                    if not self.asker.reply(item.get("id"), item.get("decision")):
                        sys.stderr.write(f"rex: unknown permission id: {item.get('id')!r}\n")
                    continue
                if item.get("type") == "bye":
                    # Interrupt first, then deny: a question asked in between
                    # sees the interrupt (see JsonlAsker.ask).
                    loop = self.loop
                    if self._busy and loop is not None and hasattr(loop, "interrupt"):
                        loop.interrupt()
                    self.asker.deny_all(closed=True)
                # Stop must reach the running request immediately, not after
                # it finishes: the main thread is blocked inside loop.run().
                if item.get("type") == "stop":
                    loop = self.loop
                    if loop is not None and hasattr(loop, "interrupt"):
                        loop.interrupt()
                        self.asker.deny_all()
                    else:
                        self.asker.deny_all()
                        self._queue.put(item)
                    continue
                self._queue.put(item)
        except (OSError, ValueError) as exc:
            self._queue.put({"type": "_invalid", "message": str(exc)})
        finally:
            self.asker.deny_all(closed=True)
            self._queue.put(Eof)

    # ---------- commands ----------

    def _handle_chat(self, item: dict) -> None:
        text = item.get("text") or item.get("prompt") or item.get("message") or ""
        if not isinstance(text, str) or not text:
            self.emit("error", message="empty chat text")
            return
        self.tokens = []
        self._pending_context = None
        loop = self._ensure_loop()
        self._busy = True
        try:
            response = loop.run(text)
        except Exception as exc:  # protocol must never die on one bad request
            self._pending_context = None
            self.emit("error", message=str(exc))
            return
        finally:
            self._busy = False
        self.emit("final", text=response or "".join(self.tokens))
        self._flush_context()

    def _handle_model(self, name: str) -> None:
        if not name:
            self.emit("error", message="empty model name")
            return
        self.config.set("liteLLM.model", name)
        loop = self._ensure_loop()
        if hasattr(loop, "llm") and hasattr(loop.llm, "default_model"):
            loop.llm.default_model = name
        self.emit("model_ok", model=name)
        # The new model has its own window: recompute the status right away.
        self._pending_context = None
        report = getattr(loop, "report_context", None)
        if callable(report):
            try:
                status = report()
                if self._pending_context is None and isinstance(status, dict):
                    self._pending_context = status
            except Exception as exc:
                self.emit("error", message=f"context estimate failed: {exc}")
        self._flush_context()

    def _dispatch(self, item: dict) -> bool:
        """Handle one command. Return False when the bridge should stop."""
        kind = item.get("type")
        if kind == "chat":
            self._handle_chat(item)
        elif kind == "reset":
            self._ensure_loop().reset_context()
            self.tokens = []
            self.emit("reset_ok")
        elif kind == "model":
            self._handle_model(str(item.get("model") or ""))
        elif kind == "command":
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if name == "model":
                self._handle_model(value)
            elif name == "reset":
                self._ensure_loop().reset_context()
                self.tokens = []
                self.emit("reset_ok")
            else:
                self.emit("error", message=f"unknown command: {name}")
        elif kind == "stop":
            loop = self._ensure_loop()
            if hasattr(loop, "interrupt"):
                loop.interrupt()
        elif kind == "_invalid":
            self.emit("error", message=str(item.get("message") or "invalid input"))
        elif kind == "bye":
            self.emit("bye")
            return False
        else:
            self.emit("error", message=f"unknown type: {kind}")
        return True

    def run(self) -> int:
        self._ensure_loop()
        extra = {}
        paths = list(getattr(self.loop, "instruction_paths", None) or [])
        if paths:
            extra["instructions"] = paths
        self.emit("ready", model=self.config.get("liteLLM.model", ""), **extra)
        reader = threading.Thread(target=self._reader, daemon=True)
        reader.start()

        while True:
            item = self._queue.get()
            if item is Eof:
                self.emit("bye")
                break
            if not self._dispatch(item):
                break
        return 0


def force_utf8_stdio() -> None:
    """Make stdin/stdout/stderr UTF-8 regardless of the OS code page.

    On Windows a piped stdio defaults to the ANSI code page (e.g. cp1251), which
    corrupts the JSON seen by front-ends. Streams without ``reconfigure``
    (replaced by tests or wrappers) are left alone.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def run_jsonl(config: Any, **kwargs: Any) -> int:
    """Convenience wrapper used by ``python3 -m runtime --jsonl``."""
    if "stream" not in kwargs and "out" not in kwargs:
        force_utf8_stdio()
    return JsonlBridge(config, **kwargs).run()
