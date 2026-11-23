# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Streamed chunks with fields rex does not model must not crash the client."""

from __future__ import annotations

import json

from runtimelib.llm import messages
from runtimelib.llm.client import ChatCompletion, ChatCompletionChunk, ChatMessage, LiteLLMClient

# Real OpenAI stream shape: system_fingerprint on every chunk, service_tier,
# and a final usage-only chunk with empty choices (stream_options.include_usage).
_BASE = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-4o",
         "system_fingerprint": "fp_abc", "service_tier": "default", "obfuscation": "xx"}
_CHUNKS = [
    {**_BASE, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "o"},
                           "logprobs": None, "finish_reason": None}], "usage": None},
    {**_BASE, "choices": [{"index": 0, "delta": {"content": "k"}, "logprobs": None,
                           "finish_reason": "stop"}], "usage": None},
    {**_BASE, "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
]


class _Resp:
    def __init__(self, lines):
        self._lines = lines
        self.closed = False

    def iter_lines(self):
        yield from self._lines

    def close(self):
        self.closed = True


class _Http:
    def __init__(self, resp):
        self.resp = resp

    def request(self, *a, **kw):
        return self.resp


def test_stream_ignores_unknown_openai_fields():
    lines = []
    for c in _CHUNKS:
        lines += ["data: " + json.dumps(c), ""]
    lines.append("data: [DONE]")
    resp = _Resp(lines)
    client = LiteLLMClient("http://x", "k", "gpt-4o", http_backend=_Http(resp))
    chunks = list(client.chat_completion([ChatMessage(role="user", content="hi")], stream=True))
    assert len(chunks) == 3
    assert "".join(c.content or "" for c in chunks) == "ok"
    assert chunks[1].finish_reason == "stop"
    assert resp.closed


def test_from_dict_filters_unknown_fields_everywhere():
    full = {"id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "system_fingerprint": "fp", "service_tier": "default",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi", "refusal": None},
                         "finish_reason": "stop"}],
            "usage": {"total_tokens": 2}}
    for cls in (ChatCompletion, messages.ChatCompletion):
        assert cls.from_dict(full).message.content == "hi"
    for cls in (ChatCompletionChunk, messages.ChatCompletionChunk):
        assert cls.from_dict(_CHUNKS[0]).content == "o"
