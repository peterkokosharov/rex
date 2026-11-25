# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-4-8
# Licensed under MIT License
"""The composition seam: drive the rex agent off an in-process kiko gateway (or any OpenAI chat callable).

``AgentLoop`` talks to its model only through ``llm_client.chat_completion(...)``, yielding OpenAI-style
``ChatCompletionChunk`` objects when streaming. ``GatewayLLM`` is that interface implemented over a plain
``chat(body) -> dict | Iterable[dict]`` callable, so a ``kiko_runtime_lib.Gateway.chat`` plugs straight in with
no HTTP server. Light normalization only: assemble the OpenAI request body, hand kiko's chunk dicts to the
existing ``ChatCompletionChunk.from_dict`` / ``ChatCompletion.from_dict``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Callable, Optional

from runtimelib.llm.client import ChatCompletion, ChatCompletionChunk, ChatMessage

# chat(body) -> dict (non-stream) or an iterable of OpenAI chunk dicts (stream).
ChatCallable = Callable[[dict[str, Any]], "dict[str, Any] | Iterable[dict[str, Any]]"]


class GatewayLLM:
    """An ``llm_client`` for ``AgentLoop`` backed by any OpenAI chat-completions callable.

    ``chat`` is called with an OpenAI request body; with ``body["stream"]`` true it must return an iterable of
    ``chat.completion.chunk`` dicts, otherwise a single ``chat.completion`` dict. A
    ``kiko_runtime_lib.Gateway.chat`` already matches this, so ``GatewayLLM(gw.chat, model=...)`` composes the
    agent with an in-process kiko gateway.
    """

    def __init__(self, chat: ChatCallable, model: str, *, context_window: Optional[int] = None) -> None:
        self._chat = chat
        self.default_model = model
        self._context_window = context_window

    def chat_completion(
        self,
        messages: list[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> "ChatCompletion | Iterator[ChatCompletionChunk]":
        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if max_tokens:
            body["max_tokens"] = max_tokens
        out = self._chat(body)
        if stream:
            return (ChatCompletionChunk.from_dict(d) for d in out)  # type: ignore[union-attr]
        return ChatCompletion.from_dict(out)  # type: ignore[arg-type]

    def context_window(self, model: Optional[str] = None) -> Optional[int]:
        """The window the loop may probe; None lets the loop fall back to its own model table."""
        return self._context_window


def llm_from_gateway(gateway: Any, model: str, *, context_window: Optional[int] = None) -> GatewayLLM:
    """Wrap a ``kiko_runtime_lib.Gateway`` (anything with ``.chat(body)``) as a rex ``llm_client``."""
    return GatewayLLM(gateway.chat, model, context_window=context_window)
