# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License
"""Embedding API: ``from ai_runtime_rex.api import Agent``.

A thin facade over ``runtime.embed.Session`` for in-process use (Odoo, archeo, scripts). It never installs a
Ctrl+C handler, registers no atexit hook, writes nothing to stdout/stderr and keeps no history file::

    agent = Agent(model="sonnet", port=4000)
    print(agent.run("Summarise: ..."))
    for chunk in agent.stream("Explain it again"):
        ...

Tools: ``tools=None`` or ``[]`` means NO tools (plain chat); a list of names (``["fileops", "bash"]``, globs
allowed) enables only those; ``tools="all"`` enables the full coding tool set. The workspace is the current
directory. Permission questions are denied unless an ``asker`` is given.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Optional

from ai_runtime_rex.runtime.embed import AgentEvent, Session
from ai_runtime_rex.runtime.envconfig import env_config
from ai_runtime_rex.runtime.providers import make_llm

__all__ = ["Agent", "AgentEvent"]


class _NullSessionManager:
    """Keeps no history on disk (the default session manager appends to ~/.config/ai-rex)."""

    session_id = "embedded"
    checkpoint_manager = None

    def append(self, entry_type: str, data: dict[str, Any]) -> None:
        return None


class Agent:
    def __init__(
        self,
        model: Optional[str] = None,
        port: Optional[int] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        tools: "list[str] | str | None" = None,
        asker: Any = None,
        llm_client: Any = None,
        config_overrides: Optional[dict[str, Any]] = None,
    ) -> None:
        """Defaults: model ``sonnet``, gateway ``http://127.0.0.1:4000`` (see ``envconfig`` for the variables
        that apply when an argument is left out). ``llm_client`` replaces the HTTP client (tests, in-process kiko)."""
        cfg = env_config(model=model, port=port, base_url=base_url, api_key=api_key, provider=provider)
        if llm_client is None:
            llm_client = make_llm(cfg)  # None = OpenAI-compatible: the engine's own client
        tool_names = None if tools == "all" else list(tools or [])
        self._session = Session(
            config=cfg, llm_client=llm_client, tools=tool_names, asker=asker, config_overrides=config_overrides,
            session_manager=_NullSessionManager(), install_signal_handler=False, quiet=True,
        )

    @property
    def model(self) -> str:
        return str(self._session._cfg.get("liteLLM.model"))

    def run(self, text: str) -> str:
        """Send one user message, block, return the final answer text. Raises on LLM errors."""
        return self._session.send(text) or ""

    def stream(self, text: str, events: bool = False) -> Iterator[Any]:
        """Yield the answer text chunks as they arrive (``events=True``: ``AgentEvent`` objects incl. tool
        start/end and a final 'final'/'error' event). Without ``events`` an LLM error raises ``RuntimeError``."""
        for ev in self._session.stream(text):
            if events:
                yield ev
            elif ev.kind == "token":
                yield ev.text
            elif ev.kind == "error":
                raise RuntimeError(ev.text)

    def reset(self) -> None:
        """Forget the conversation so far."""
        self._session.reset()

    def interrupt(self) -> None:
        self._session.interrupt()

    def close(self) -> None:
        """Release MCP connections (only started with ``tools="all"``)."""
        self._session.loop.close_mcp()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
