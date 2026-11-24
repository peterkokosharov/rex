# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Token usage: stream request option, chunk field, loop totals, bridge event."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from runtimelib.agent import create_agent_loop
from runtimelib.cli import JsonlBridge
from runtimelib.config import Config
from runtimelib.llm.client import ChatCompletionChunk, LiteLLMClient, ChatMessage


def _cfg():
    return Config({"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}})


class _Llm:
    default_model = "m"

    def __init__(self, with_usage):
        self.with_usage = with_usage

    def chat_completion(self, **kw):
        chunks = [ChatCompletionChunk(choices=[{"delta": {"content": "hello world"}, "finish_reason": "stop"}])]
        if self.with_usage:
            chunks.append(ChatCompletionChunk(choices=[], usage={"prompt_tokens": 10, "completion_tokens": 4}))
        return iter(chunks)


def _loop(with_usage):
    loop = create_agent_loop(_cfg())
    loop.llm = _Llm(with_usage)
    return loop


def test_stream_request_asks_for_usage_and_chunk_keeps_it():
    sent = {}

    class Http:
        def request(self, method, url, headers=None, body=None, **kw):
            sent["body"] = json.loads(body)
            return SimpleNamespace(iter_lines=lambda: iter(()), close=lambda: None)

    c = LiteLLMClient("http://x", "k", "m", http_backend=Http())
    list(c.chat_completion([ChatMessage(role="user", content="hi")], stream=True))
    assert sent["body"]["stream_options"] == {"include_usage": True}
    chunk = ChatCompletionChunk.from_dict({"choices": [], "usage": {"prompt_tokens": 3}})
    assert chunk.usage == {"prompt_tokens": 3}


def test_loop_sums_turn_and_session():
    loop = _loop(True)
    loop.run("one")
    loop.run("two")
    s = loop.state
    assert (s.turn_tokens_in, s.turn_tokens_out) == (10, 4)
    assert (s.total_tokens_in, s.total_tokens_out) == (20, 8)
    assert not s.turn_estimated
    loop.reset_context()
    assert loop.state.total_tokens_in == 0


def test_loop_estimates_when_provider_sends_nothing():
    loop = _loop(False)
    loop.run("one")
    assert loop.state.turn_estimated
    assert loop.state.turn_tokens_in > 0 and loop.state.turn_tokens_out > 0


def _bridge_loop(bridge, loop):
    loop.on_token = bridge.on_token
    return loop


def test_bridge_emits_usage_after_final():
    out = io.StringIO()
    b = JsonlBridge(_cfg(), loop_factory=lambda br: _bridge_loop(br, _loop(True)),
                    stream=io.StringIO('{"type":"chat","text":"hi"}\n{"type":"bye"}\n'), out=out)
    b.run()
    events = [json.loads(x) for x in out.getvalue().splitlines()]
    kinds = [e["type"] for e in events]
    assert kinds.index("usage") == kinds.index("final") + 1
    u = events[kinds.index("usage")]
    assert u["turn"] == {"input_tokens": 10, "output_tokens": 4}
    assert u["session"] == {"input_tokens": 10, "output_tokens": 4}
    assert u["estimated"] is False
