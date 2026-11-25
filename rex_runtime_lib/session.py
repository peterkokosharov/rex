# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-4-8
# Licensed under MIT License
"""A thin in-process session over ``runtimelib.AgentLoop``, plus a one-call ACP launcher.

``AgentLoop`` keeps its conversation across ``run()`` calls, so one ``AgentLoop`` is one session. The only real
code here (beyond wiring) is turning the loop's push callbacks (``on_token`` / ``on_tool_start`` / ``on_tool_end``)
into a pull iterator for ``stream()``: the loop runs on a worker thread and feeds a queue the caller drains.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Optional

from runtimelib.agent import create_agent_loop
from runtimelib.config import Config, get_config

_DONE = object()  # sentinel: the worker thread finished


@dataclass
class AgentEvent:
    """One streamed event. ``kind`` is 'token' | 'tool_start' | 'tool_end' | 'final' | 'error'."""

    kind: str
    text: str = ""
    name: str = ""
    args: dict[str, Any] | None = None
    result: Any = None


def _build_config(config: Optional[Config], endpoint: Optional[dict], model: Optional[str],
                  overrides: Optional[dict]) -> Config:
    """Resolve a rex ``Config``. ``endpoint`` (base_url / api_key / model) injects an OpenAI-compatible backend
    for the default HTTP client; with an injected ``llm_client`` it is unused and may be omitted."""
    if config is not None:
        cfg = config
    elif endpoint is not None:
        data: dict[str, Any] = {"liteLLM": dict(endpoint)}
        if model and "model" not in data["liteLLM"]:
            data["liteLLM"]["model"] = model
        cfg = Config(data)
    else:
        cfg = get_config()
    for key, value in (overrides or {}).items():
        cfg.set(key, value)
    if model:
        cfg.set("liteLLM.model", model)
    return cfg


class Session:
    """One agent session. ``send`` blocks and returns the final text; ``stream`` yields events as they happen."""

    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        endpoint: Optional[dict[str, Any]] = None,
        llm_client: Any = None,
        model: Optional[str] = None,
        tools: Optional[list[str]] = None,
        role: str = "CODER",
        on_token: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_end: Optional[Callable[[Any], None]] = None,
        config_overrides: Optional[dict[str, Any]] = None,
        **loop_kwargs: Any,
    ) -> None:
        self._cfg = _build_config(config, endpoint, model, config_overrides)
        self._user_on_token = on_token
        self._user_on_tool_start = on_tool_start
        self._user_on_tool_end = on_tool_end
        self._sink: "queue.Queue[AgentEvent] | None" = None
        self.loop = create_agent_loop(
            self._cfg,
            llm_client=llm_client,
            tool_names=tools,
            role=role,
            on_token=self._on_token,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
            **loop_kwargs,
        )

    # -- callbacks: forward to the user's hooks and, while streaming, to the sink --
    def _on_token(self, text: str) -> None:
        if self._sink is not None:
            self._sink.put(AgentEvent("token", text=text))
        if self._user_on_token:
            self._user_on_token(text)

    def _on_tool_start(self, name: str, args: dict) -> None:
        if self._sink is not None:
            self._sink.put(AgentEvent("tool_start", name=name, args=args))
        if self._user_on_tool_start:
            self._user_on_tool_start(name, args)

    def _on_tool_end(self, result: Any) -> None:
        if self._sink is not None:
            self._sink.put(AgentEvent("tool_end", name=getattr(result, "tool_name", ""), result=result))
        if self._user_on_tool_end:
            self._user_on_tool_end(result)

    def send(self, message: str) -> str:
        """Send a user message and block until the agent finishes; returns the final assistant text."""
        return self.loop.run(message)

    def stream(self, message: str):
        """Send a user message and yield ``AgentEvent``s (tokens, tool start/end) as they happen, ending with a
        'final' event (or 'error'). The loop runs on a worker thread; iterate to completion to join it."""
        q: "queue.Queue[AgentEvent]" = queue.Queue()
        self._sink = q
        done: "queue.Queue[object]" = queue.Queue()

        def work() -> None:
            try:
                final = self.loop.run(message)
                q.put(AgentEvent("final", text=final or ""))
            except BaseException as exc:  # surface the failure as an event, never hang the caller
                q.put(AgentEvent("error", text=str(exc)))
            finally:
                done.put(_DONE)

        worker = threading.Thread(target=work, name="rex-session", daemon=True)
        worker.start()
        try:
            finished = False
            while not (finished and q.empty()):
                try:
                    yield q.get(timeout=0.05)
                except queue.Empty:
                    if not finished and not done.empty():
                        finished = True
        finally:
            self._sink = None
            worker.join()

    def interrupt(self) -> None:
        """Ask the running agent to stop at the next step (same as the CLI's Ctrl+C)."""
        self.loop.interrupt()

    def reset(self) -> None:
        """Forget the conversation so far; the next message starts a fresh context."""
        self.loop.reset_context()


def serve_acp(
    *,
    config: Optional[Config] = None,
    endpoint: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
    local_tools: bool = False,
    stream_in: Optional[IO[str]] = None,
    stream_out: Optional[IO[str]] = None,
    config_path: Optional[Path | str] = None,
    env_file: Optional[Path | str] = None,
    config_overrides: Optional[dict[str, Any]] = None,
) -> int:
    """Launch the existing rex ACP server (JSON-RPC over stdio) for IDE clients. Blocks until the client
    disconnects; returns its exit code. Pure wrapper over ``runtimelib.acp.run_acp``."""
    from runtimelib.acp import run_acp

    cfg = _build_config(config, endpoint, model, config_overrides)
    return run_acp(cfg, model=model, local_tools=local_tools, stream_in=stream_in, stream_out=stream_out,
                   config_path=config_path, env_file=env_file)
