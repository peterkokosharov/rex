# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""JSON-lines stdio bridge for external UIs (the C++ Turbo Vision front-end).

Wire format, one JSON object per line:

    UI -> rex:  {"type":"chat","text":"..."}
                {"type":"reset"}
                {"type":"model","model":"..."}
                {"type":"command","name":"model","value":"..."}
                {"type":"stop"}
                {"type":"bye"}

    rex -> UI:  {"type":"ready","model":"..."}
                {"type":"token","text":"..."}
                {"type":"tool_start","name":"...","args":{...}}
                {"type":"tool_end","ok":true,"error":null}
                {"type":"final","text":"..."}
                {"type":"error","message":"..."}
                {"type":"reset_ok"}
                {"type":"model_ok","model":"..."}
                {"type":"bye"}

Everything is written to stdout with an explicit flush, because the caller
reads line by line from a pipe. Human-readable diagnostics stay on stderr.
One AgentLoop lives for the whole session, so conversation context survives
across ``chat`` messages and is cleared only by ``reset``.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from typing import Any, Callable, IO, Optional

from ..agent import create_agent_loop

Eof = object()


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

    # ---------- output ----------

    def emit(self, kind: str, **fields: Any) -> None:
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

    # ---------- setup ----------

    def _default_loop_factory(self, bridge: "JsonlBridge") -> Any:
        return create_agent_loop(
            self.config,
            on_token=self.on_token,
            on_tool_start=self.on_tool_start,
            on_tool_end=self.on_tool_end,
        )

    def _ensure_loop(self) -> Any:
        if self.loop is None:
            self.loop = self._loop_factory(self)
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
                # Stop must reach the running request immediately, not after
                # it finishes: the main thread is blocked inside loop.run().
                if item.get("type") == "stop":
                    loop = self.loop
                    if loop is not None and hasattr(loop, "interrupt"):
                        loop.interrupt()
                    else:
                        self._queue.put(item)
                    continue
                self._queue.put(item)
        except (OSError, ValueError) as exc:
            self._queue.put({"type": "_invalid", "message": str(exc)})
        finally:
            self._queue.put(Eof)

    # ---------- commands ----------

    def _handle_chat(self, item: dict) -> None:
        text = item.get("text") or item.get("prompt") or item.get("message") or ""
        if not isinstance(text, str) or not text:
            self.emit("error", message="empty chat text")
            return
        self.tokens = []
        try:
            response = self._ensure_loop().run(text)
        except Exception as exc:  # protocol must never die on one bad request
            self.emit("error", message=str(exc))
            return
        self.emit("final", text=response or "".join(self.tokens))

    def _handle_model(self, name: str) -> None:
        if not name:
            self.emit("error", message="empty model name")
            return
        self.config.set("liteLLM.model", name)
        loop = self._ensure_loop()
        if hasattr(loop, "llm") and hasattr(loop.llm, "default_model"):
            loop.llm.default_model = name
        self.emit("model_ok", model=name)

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
        self.emit("ready", model=self.config.get("liteLLM.model", ""))
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


def run_jsonl(config: Any, **kwargs: Any) -> int:
    """Convenience wrapper used by ``python3 -m runtime --jsonl``."""
    return JsonlBridge(config, **kwargs).run()
