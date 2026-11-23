# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Agent core - ReAct loop with streaming and tool execution."""

from __future__ import annotations

from .loop import AgentLoop, AgentState, create_agent_loop
from .state import AgentState, CheckpointManager
from .callbacks import StreamingCallbacks, TokenCallback, ToolCallback, ThinkingCallback
from .context import (
    compact_messages,
    compact_threshold,
    context_tokens,
    ensure_tool_results,
    estimate_tokens,
    resolve_context_window,
)

__all__ = [
    "AgentLoop",
    "AgentState",
    "create_agent_loop",
    "compact_messages",
    "ensure_tool_results",
    "compact_threshold",
    "context_tokens",
    "estimate_tokens",
    "resolve_context_window",
    "CheckpointManager",
    "StreamingCallbacks",
    "TokenCallback",
    "ToolCallback",
    "ThinkingCallback",
]