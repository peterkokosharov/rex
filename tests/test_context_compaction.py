#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tests for shared context across requests and model-aware auto-compaction."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.agent import (
    compact_messages,
    compact_threshold,
    context_tokens,
    create_agent_loop,
    estimate_tokens,
    resolve_context_window,
)
from runtimelib.config import Config
from runtimelib.llm import ChatMessage, ChatCompletionChunk, ToolCall
from runtimelib.session import SessionManager


class FakeLLM:
    """Streaming fake that records the messages it was given."""

    def __init__(self, model: str = "test-model") -> None:
        self.default_model = model
        self.seen: list[list[ChatMessage]] = []
        self.replies: list[str] = []

    def chat_completion(self, messages, **kwargs):
        self.seen.append(list(messages))
        reply = self.replies.pop(0) if self.replies else "ok"
        yield ChatCompletionChunk(
            id="1", model=self.default_model, created=0,
            choices=[{"delta": {"content": reply}, "finish_reason": "stop"}],
        )


def _config(model: str = "test-model", **extra) -> Config:
    data = {"liteLLM": {"base_url": "http://dummy:4000", "api_key": "dummy", "model": model}}
    data.update(extra)
    return Config(data)


def _loop(config: Config, llm: FakeLLM):
    session = MagicMock(spec=SessionManager)
    session.session_id = "test-session"
    return create_agent_loop(config, llm_client=llm, session_manager=session, tools=[])


def test_threshold_follows_model_window():
    assert resolve_context_window("claude-sonnet-4-0") == 200_000
    assert compact_threshold(resolve_context_window("claude-sonnet-4-0")) == 180_000
    assert resolve_context_window("gemini-1.5-flash") == 1_000_000
    assert compact_threshold(resolve_context_window("gemini-1.5-flash")) == 900_000


def test_model_name_hint_and_config_override():
    assert resolve_context_window("our-1m-model") == 1_000_000
    assert resolve_context_window("our-200k-model") == 200_000
    assert resolve_context_window("claude-sonnet-4-0", _config(agent={"context_window": 32000})) == 32000
    cfg = _config(agent={"context_windows": {"custom-ctx": 500000}})
    assert resolve_context_window("custom-ctx", cfg) == 500000
    assert resolve_context_window("unknown-model") == 200_000


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("abcde") == 2


def test_no_compaction_below_threshold():
    msgs = [ChatMessage(role="user", content="x" * 400)]  # 100 tokens
    out, changed = compact_messages(msgs, 180_000)
    assert changed is False
    assert out == msgs


def test_compaction_triggers_at_90_percent():
    # 950 estimated tokens against a 1000-token threshold (90% of ~1111 window).
    msgs = [
        ChatMessage(role="user", content="a" * 3800),
        ChatMessage(role="assistant", content="b" * 400),
        ChatMessage(role="user", content="latest question"),
    ]
    before = context_tokens(msgs)
    assert before > 1000
    out, changed = compact_messages(msgs, 1000)
    assert changed is True
    assert context_tokens(out) < 1000
    assert out[-1].content == "latest question"
    assert out[0].content.startswith("[Context compacted:")


def test_large_tool_output_is_trimmed_first():
    big = "t" * 12000
    msgs = [
        ChatMessage(role="user", content="old task"),
        ChatMessage(role="assistant", content="using a tool",
                    tool_calls=[ToolCall(id="1", function={"name": "bash", "arguments": "{}"})]),
        ChatMessage(role="tool", content=big, tool_call_id="1"),
        ChatMessage(role="assistant", content="done"),
        ChatMessage(role="user", content="new task"),
    ]
    out, changed = compact_messages(msgs, 600)
    assert changed is True
    assert any("[tool output compacted]" in (m.content or "") for m in out)
    assert out[-1].content == "new task"


def test_context_and_step_survive_two_requests():
    llm = FakeLLM("claude-sonnet-4-0")
    llm.replies = ["first answer", "second answer"]
    loop = _loop(_config("claude-sonnet-4-0"), llm)

    assert loop.run("first question") == "first answer"
    loop.state.step = 19  # simulate an almost exhausted per-request budget
    assert loop.run("second question") == "second answer"

    roles = [(m.role, m.content) for m in loop.state.messages]
    assert roles == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]
    assert loop.state.step == 1  # reset, not 20
    assert loop.state.turn == 2
    # The second request actually saw the first exchange (index 0 is the system prompt).
    assert [m.content for m in llm.seen[1][1:]] == [
        "first question", "first answer", "second question",
    ]


def test_stale_interrupt_does_not_block_next_request():
    llm = FakeLLM()
    llm.replies = ["one", "two"]
    loop = _loop(_config(), llm)
    loop.run("one")
    loop.state.interrupted = True
    loop._interrupt_event.set()

    assert loop.run("two") == "two"
    assert loop.state.interrupted is False
    assert not loop._interrupt_event.is_set()


def test_auto_compact_during_request():
    llm = FakeLLM("claude-sonnet-4-0")
    loop = _loop(_config("claude-sonnet-4-0", agent={"context_window": 2000}), llm)
    assert loop.compact_threshold() == 1800
    loop.state.messages = [
        ChatMessage(role="user", content="old " * 4000),
        ChatMessage(role="assistant", content="old reply " * 500),
        ChatMessage(role="user", content="new question"),
    ]

    loop.run("new question")

    assert loop.context_usage()["tokens"] < 1800
    assert any("[Context compacted:" in (m.content or "") for m in loop.state.messages)
    assert any(m.content == "new question" for m in loop.state.messages)
