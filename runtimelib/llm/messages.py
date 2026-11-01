# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""LLM message types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str | None = None, tool_calls: list[ToolCall] | None = None) -> "ChatMessage":
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)

    @classmethod
    def tool(cls, tool_call_id: str, content: str) -> "ChatMessage":
        return cls(role="tool", content=content, tool_call_id=tool_call_id)


@dataclass
class ToolCall:
    id: str
    type: str = "function"
    function: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "function": self.function}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(
            id=data["id"],
            type=data.get("type", "function"),
            function=data.get("function", {}),
        )


@dataclass
class ChatCompletionChunk:
    id: str
    model: str
    choices: list[dict[str, Any]]
    created: int
    object: str = "chat.completion.chunk"

    @property
    def delta(self) -> dict[str, Any]:
        return self.choices[0].get("delta", {}) if self.choices else {}

    @property
    def content(self) -> str | None:
        return self.delta.get("content")

    @property
    def tool_calls(self) -> list[ToolCall] | None:
        tcs = self.delta.get("tool_calls")
        if tcs:
            return [ToolCall.from_dict(tc) for tc in tcs]
        return None

    @property
    def finish_reason(self) -> str | None:
        return self.choices[0].get("finish_reason") if self.choices else None


@dataclass
class ChatCompletion:
    id: str
    model: str
    choices: list[dict[str, Any]]
    created: int
    object: str = "chat.completion"
    usage: dict[str, int] | None = None

    @property
    def message(self) -> ChatMessage:
        choice = self.choices[0] if self.choices else {}
        msg_data = choice.get("message", {})
        tool_calls = None
        if msg_data.get("tool_calls"):
            tool_calls = [ToolCall.from_dict(tc) for tc in msg_data["tool_calls"]]
        return ChatMessage(
            role=msg_data.get("role", "assistant"),
            content=msg_data.get("content"),
            tool_calls=tool_calls,
        )