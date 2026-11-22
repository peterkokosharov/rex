# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Context-window accounting and automatic compaction for the agent loop.

The trigger is a fraction of the *current model's* context window (90% by
default): 200k -> 180k tokens, 1M -> 900k tokens. Token counts are estimates
(four characters per token) because the runtime is stdlib-only and cannot run
a model-specific tokenizer. Sizes are deliberately conservative.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any, Optional, Sequence

from ..llm.messages import ChatMessage

DEFAULT_CONTEXT_WINDOW = 200_000
COMPACT_RATIO = 0.9
COMPACT_TARGET_RATIO = 0.75  # trim below the trigger so we do not compact every step
CHARS_PER_TOKEN = 4
TOOL_TRIM_CHARS = 1500
KEEP_RECENT = 6
COMPACT_NOTE = "[Context compacted: {n} earlier messages were omitted to fit the context window.]"

_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*([km])(?![\w])", re.IGNORECASE)
_UNIT = {"k": 1_000, "m": 1_000_000}


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens from text without a tokenizer (~4 chars/token)."""
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def message_tokens(message: ChatMessage) -> int:
    """Estimated tokens for one message, including tool-call arguments."""
    total = estimate_tokens(message.content)
    if message.tool_calls:
        try:
            calls = [tc.to_dict() if hasattr(tc, "to_dict") else tc for tc in message.tool_calls]
            total += estimate_tokens(json.dumps(calls, ensure_ascii=False))
        except (TypeError, ValueError):
            total += 64
    return total


def context_tokens(messages: Sequence[ChatMessage]) -> int:
    """Estimated tokens in the shared conversation context."""
    return sum(message_tokens(m) for m in messages)


def resolve_context_window(
    model: str = "",
    config: Optional[Any] = None,
    registry: Optional[Any] = None,
) -> int:
    """Context window in tokens for ``model``.

    Priority: explicit config override, per-model config map, model registry,
    a size hint in the model name (``1m``, ``200k``), then 200k.
    """
    if config is not None:
        explicit = config.get("agent.context_window")
        if explicit:
            return int(explicit)
        windows = config.get("agent.context_windows") or {}
        if isinstance(windows, dict):
            for key in (model, str(model).lower()):
                if key in windows and windows[key]:
                    return int(windows[key])

    if registry is None:
        from ..llm.models import get_registry

        registry = get_registry()

    info = registry.get(model) if model else None
    if info is not None and info.max_input_tokens:
        return int(info.max_input_tokens)

    if model:
        for value, unit in _SIZE_RE.findall(str(model).lower()):
            size = int(float(value) * _UNIT[unit.lower()])
            if size >= 8_000:
                return size

    return DEFAULT_CONTEXT_WINDOW


def compact_threshold(window: int, ratio: float = COMPACT_RATIO) -> int:
    """Token count at which auto-compaction triggers."""
    return max(1, int(window * ratio))


def _copy(message: ChatMessage, content: Optional[str] = None) -> ChatMessage:
    return replace(message, content=content) if content is not None else replace(message)


def compact_messages(
    messages: Sequence[ChatMessage],
    threshold: int,
    target: Optional[int] = None,
    keep_recent: int = KEEP_RECENT,
) -> tuple[list[ChatMessage], bool]:
    """Return compacted messages and whether anything changed.

    Old tool output is shortened first, then whole old turns are dropped at
    user-message boundaries (so assistant/tool pairs stay intact), and finally
    an oversized single message is truncated. The most recent ``keep_recent``
    messages are never dropped by the boundary pass.
    """
    original = list(messages)
    if context_tokens(original) <= threshold:
        return original, False

    target = target if target is not None else max(1, int(threshold * COMPACT_TARGET_RATIO))
    out = [_copy(m) for m in original]
    recent = min(keep_recent, max(1, len(out) // 2))

    # 1) Shorten large tool outputs, oldest first, keeping recent turns intact.
    for i, m in enumerate(out[:-recent]):
        if m.role == "tool" and m.content and len(m.content) > TOOL_TRIM_CHARS:
            out[i] = _copy(m, m.content[:TOOL_TRIM_CHARS] + "\n...[tool output compacted]...")
    if context_tokens(out) <= target:
        return out, True

    # 2) Drop old turns at user boundaries; never drop the latest user turn.
    keep_from = 0
    for i, m in enumerate(out[:-1]):
        if m.role != "user":
            continue
        keep_from = i
        note_size = estimate_tokens(COMPACT_NOTE.format(n=i))
        if context_tokens(out[i:]) + note_size <= target:
            break
    if keep_from <= 0:
        keep_from = min(len(out) - 1, max(1, recent))

    dropped = keep_from
    out = [ChatMessage(role="user", content=COMPACT_NOTE.format(n=dropped))] + out[dropped:]
    if context_tokens(out) <= target:
        return out, True

    # 3) A single very large message still fits nowhere: truncate the biggest
    #    ones until we are under target. Bounded to avoid a pathological loop.
    for _ in range(32):
        if context_tokens(out) <= target:
            break
        idx = max(range(len(out)), key=lambda i: message_tokens(out[i]))
        message = out[idx]
        if message.content and len(message.content) > 100:
            kept = max(100, len(message.content) // 2)
            out[idx] = _copy(message, message.content[:kept] + "\n...[message truncated]...")
            continue
        if message.tool_calls:
            out[idx] = ChatMessage(
                role=message.role,
                content="[tool call compacted]",
                tool_call_id=message.tool_call_id,
            )
            continue
        break

    return out, True
