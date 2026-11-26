# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5, claude-sonnet-5-5
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
from typing import Any, Callable, Optional, Sequence

from ..llm.messages import ChatMessage

DEFAULT_CONTEXT_WINDOW = 32_000  # conservative: compact early rather than overflow
COMPACT_RATIO = 0.9
MIN_COMPACT_RATIO = 0.5
MAX_COMPACT_RATIO = 0.95
DEFAULT_WARN_PERCENT = 80
COMPACT_TARGET_RATIO = 0.75  # trim below the trigger so we do not compact every step
CHARS_PER_TOKEN = 4
TOOL_TRIM_CHARS = 1500
KEEP_RECENT = 6
COMPACT_NOTE = "[Context compacted: {n} earlier messages were omitted to fit the context window.]"
CANCELLED_RESULT = '{"success": false, "result": null, "error": "cancelled: the tool call was not run"}'
COMPACTED_ARGS = '{"_compacted": true}'

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


def clamp_ratio(value: Any) -> float:
    """Auto-compaction ratio, clamped to 0.5-0.95; missing or invalid -> 0.9.

    Auto-compaction cannot be switched off: 0, negative or junk values fall
    back to the default instead of disabling it.
    """
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return COMPACT_RATIO
    if ratio != ratio or ratio <= 0:
        return COMPACT_RATIO
    return min(MAX_COMPACT_RATIO, max(MIN_COMPACT_RATIO, ratio))


def overhead_tokens(system_prompt: str | None, tool_schemas: Optional[Sequence[Any]] = None) -> int:
    """Estimated tokens sent on every request besides the messages."""
    total = estimate_tokens(system_prompt)
    if tool_schemas:
        try:
            total += estimate_tokens(json.dumps(list(tool_schemas), ensure_ascii=False))
        except (TypeError, ValueError):
            total += 64 * len(tool_schemas)
    return total


def resolve_context_window_info(
    model: str = "",
    config: Optional[Any] = None,
    registry: Optional[Any] = None,
    endpoint: Optional[Callable[[str], Optional[int]]] = None,
) -> tuple[int, str]:
    """Context window and where it came from for ``model``.

    Source is one of ``config``, ``endpoint``, ``table``, ``name``, ``default``.
    Order: ``models.<name>.context_window`` (then the old ``agent.context_window``
    and ``agent.context_windows`` overrides), the endpoint probe, the built-in
    model table, a size hint in the model name (``1m``, ``200k``), then 32k.
    """
    if config is not None:
        models = config.get("models")
        if isinstance(models, dict) and model:
            for key in (model, str(model).lower()):
                entry = models.get(key)
                if isinstance(entry, dict) and _positive_int(entry.get("context_window")):
                    return _positive_int(entry["context_window"]), "config"
        explicit = _positive_int(config.get("agent.context_window"))
        if explicit:
            return explicit, "config"
        windows = config.get("agent.context_windows") or {}
        if isinstance(windows, dict):
            for key in (model, str(model).lower()):
                if key in windows and _positive_int(windows[key]):
                    return _positive_int(windows[key]), "config"

    if endpoint is not None and model:
        try:
            probed = _positive_int(endpoint(model))
        except Exception:  # a failing probe must never break the loop
            probed = 0
        if probed:
            return probed, "endpoint"

    if registry is None:
        from ..llm.models import get_registry

        registry = get_registry()

    info = registry.get(model) if model else None
    if info is not None and info.max_input_tokens:
        return int(info.max_input_tokens), "table"

    if model:
        for value, unit in _SIZE_RE.findall(str(model).lower()):
            size = int(float(value) * _UNIT[unit.lower()])
            if size >= 8_000:
                return size, "name"

    return DEFAULT_CONTEXT_WINDOW, "default"


def resolve_context_window(
    model: str = "",
    config: Optional[Any] = None,
    registry: Optional[Any] = None,
    endpoint: Optional[Callable[[str], Optional[int]]] = None,
) -> int:
    """Context window in tokens for ``model`` (see ``resolve_context_window_info``)."""
    return resolve_context_window_info(model, config, registry, endpoint)[0]


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def compact_threshold(window: int, ratio: float = COMPACT_RATIO) -> int:
    """Token count at which auto-compaction triggers."""
    return max(1, int(window * clamp_ratio(ratio)))


def _copy(message: ChatMessage, content: Optional[str] = None) -> ChatMessage:
    return replace(message, content=content) if content is not None else replace(message)


def ensure_tool_results(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """Return messages where every tool call has exactly one tool result.

    OpenAI-compatible APIs reject a history where an assistant ``tool_calls``
    entry has no matching ``tool`` message, or a ``tool`` message has no call.
    Missing results (after a stop, an interrupt or a crash) get a "cancelled"
    result inserted right after the call group; orphan results are dropped.
    """
    out: list[ChatMessage] = []
    i = 0
    items = list(messages)
    while i < len(items):
        m = items[i]
        if m.role == "tool":
            i += 1  # orphan: its assistant call is not directly before it
            continue
        out.append(m)
        i += 1
        if m.role != "assistant" or not m.tool_calls:
            continue
        wanted = [tc.id if hasattr(tc, "id") else tc.get("id") for tc in m.tool_calls]
        seen: set[str] = set()
        while i < len(items) and items[i].role == "tool":
            r = items[i]
            if r.tool_call_id in wanted and r.tool_call_id not in seen:
                out.append(r)
                seen.add(r.tool_call_id)
            i += 1
        for tc_id in wanted:
            if tc_id not in seen:
                out.append(ChatMessage(role="tool", content=CANCELLED_RESULT, tool_call_id=tc_id))
    return out


def _group_start(messages: Sequence[ChatMessage], index: int) -> int:
    """Move ``index`` forward so it does not start inside a tool-result group."""
    while index < len(messages) and messages[index].role == "tool":
        index += 1
    if index >= len(messages):
        # Only tool results are left: step back to the assistant that owns them.
        index = len(messages) - 1
        while index > 0 and messages[index].role == "tool":
            index -= 1
    return index


def _shrink(message: ChatMessage) -> Optional[ChatMessage]:
    """Smaller copy of ``message``, or None when it cannot shrink further."""
    if message.content and len(message.content) > 100:
        kept = max(100, len(message.content) // 2)
        return _copy(message, message.content[:kept] + "\n...[message truncated]...")
    if message.tool_calls and any(
        tc.function.get("arguments") != COMPACTED_ARGS for tc in message.tool_calls
    ):
        # Keep the calls (their ids pair with tool results), shrink the arguments.
        calls = [
            replace(tc, function={**tc.function, "arguments": COMPACTED_ARGS})
            for tc in message.tool_calls
        ]
        return replace(message, tool_calls=calls)
    return None


def compact_messages(
    messages: Sequence[ChatMessage],
    threshold: int,
    target: Optional[int] = None,
    keep_recent: int = KEEP_RECENT,
    overhead: int = 0,
) -> tuple[list[ChatMessage], bool]:
    """Return compacted messages and whether anything changed.

    Old tool output is shortened first, then whole old turns are dropped at
    user-message boundaries (so assistant/tool pairs stay intact), and finally
    an oversized single message is truncated. The most recent ``keep_recent``
    messages are never dropped by the boundary pass. The latest user message
    is never dropped nor truncated: it is what the model must answer.
    """
    original = list(messages)
    # The system prompt and tool schemas travel with every request, so the
    # messages have only what is left of the threshold.
    threshold = max(1, threshold - max(0, overhead))
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
        return ensure_tool_results(out), True

    # 2) Drop old turns at user boundaries; never drop the latest user turn.
    last_user = max((i for i, m in enumerate(out) if m.role == "user"), default=None)
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
    # Never split an assistant tool call from its results.
    keep_from = _group_start(out, keep_from)
    if last_user is not None and keep_from > last_user:
        keep_from = last_user  # a user message is never inside a tool group

    dropped = keep_from
    if dropped > 0:
        out = [ChatMessage(role="user", content=COMPACT_NOTE.format(n=dropped))] + out[dropped:]
        if last_user is not None:
            last_user = last_user - dropped + 1
    if context_tokens(out) <= target:
        return ensure_tool_results(out), True

    # 3) A single very large message still fits nowhere: truncate the biggest
    #    ones until we are under target. Bounded to avoid a pathological loop.
    #    The latest user message is left whole.
    for _ in range(64):
        if context_tokens(out) <= target:
            break
        candidates = sorted(
            (i for i in range(len(out)) if i != last_user),
            key=lambda i: message_tokens(out[i]),
            reverse=True,
        )
        for idx in candidates:
            shrunk = _shrink(out[idx])
            if shrunk is not None:
                out[idx] = shrunk
                break
        else:
            break

    return ensure_tool_results(out), True
