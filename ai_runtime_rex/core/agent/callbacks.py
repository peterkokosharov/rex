# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Streaming callbacks for agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class StreamingCallbacks:
    """Container for all streaming callbacks."""
    on_token: Callable[[str], None] = lambda t: None
    on_tool_start: Callable[[str, dict], None] = lambda n, a: None
    on_tool_end: Callable[[ToolResult], None] = lambda r: None
    on_thinking: Callable[[str], None] = lambda t: None
    on_step_start: Callable[[int], None] = lambda s: None
    on_step_end: Callable[[int, str], None] = lambda s, r: None
    on_error: Callable[[Exception], None] = lambda e: None

    @classmethod
    def default(cls) -> "StreamingCallbacks":
        return cls()


@dataclass
class TokenCallback:
    """Token streaming callback."""
    callback: Callable[[str], None]
    buffer: str = ""

    def __call__(self, token: str) -> None:
        self.buffer += token
        self.callback(token)

    def flush(self) -> str:
        """Get and clear buffer."""
        result = self.buffer
        self.buffer = ""
        return result


@dataclass
class ToolCallback:
    """Tool execution callbacks."""
    on_start: Callable[[str, dict], None]
    on_end: Callable[[ToolResult], None]

    def __post_init__(self):
        if self.on_start is None:
            self.on_start = lambda n, a: None
        if self.on_end is None:
            self.on_end = lambda r: None


@dataclass
class ThinkingCallback:
    """Thinking/reasoning callback."""
    callback: Callable[[str], None]
    buffer: str = ""

    def __call__(self, thinking: str) -> None:
        self.buffer += thinking
        self.callback(thinking)

    def flush(self) -> str:
        result = self.buffer
        self.buffer = ""
        return result


from .loop import ToolResult