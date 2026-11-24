# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""base_url /v1 normalisation, HTTP errors and empty answers are loud."""

from __future__ import annotations

import pytest

from runtimelib.agent import create_agent_loop
from runtimelib.config import Config
from runtimelib.llm.client import ChatCompletionChunk, ChatMessage, LiteLLMClient


class _Resp:
    def __init__(self, status, body=b""):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def iter_lines(self):
        return iter(())

    def close(self):
        pass


class _Http:
    def __init__(self, resp):
        self.resp = resp
        self.urls = []

    def request(self, method, url, **kw):
        self.urls.append(url)
        return self.resp


@pytest.mark.parametrize("base", ["http://h:4000", "http://h:4000/", "http://h:4000/v1", "http://h:4000/v1/"])
def test_base_url_never_doubles_v1(base):
    http = _Http(_Resp(200))
    c = LiteLLMClient(base, "k", "m", http_backend=http)
    list(c.chat_completion([ChatMessage(role="user", content="x")], stream=True))
    assert http.urls == ["http://h:4000/v1/chat/completions"]


def test_stream_404_raises_clear_error():
    c = LiteLLMClient("http://h", "k", "m", http_backend=_Http(_Resp(404, b'{"detail":"Not Found"}')))
    with pytest.raises(RuntimeError, match=r"HTTP 404.*Not Found"):
        list(c.chat_completion([ChatMessage(role="user", content="x")], stream=True))


def test_nonstream_500_raises():
    c = LiteLLMClient("http://h", "k", "m", http_backend=_Http(_Resp(400, b"bad")))
    c._request_with_retry = lambda *a, **k: _Resp(400, b"bad")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        c.chat_completion([ChatMessage(role="user", content="x")], stream=False)


def test_empty_model_answer_is_an_error():
    loop = create_agent_loop(Config({"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}}))

    class Llm:
        default_model = "m"

        def chat_completion(self, **kw):
            return iter([ChatCompletionChunk(choices=[{"delta": {}, "finish_reason": "stop"}])])

    loop.llm = Llm()
    with pytest.raises(RuntimeError, match="empty response"):
        loop.run("hi")
