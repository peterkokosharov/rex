#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5, claude-opus-5-5
# Licensed under MIT License

"""Context estimate (prompt + tool schemas), 80% warning, mandatory compaction, window chain."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.agent import (
    compact_messages,
    context_tokens,
    create_agent_loop,
    resolve_context_window,
)
from runtimelib.agent.context import clamp_ratio, overhead_tokens, resolve_context_window_info
from runtimelib.cli.jsonl_bridge import JsonlBridge
from runtimelib.config import Config
from runtimelib.llm import ChatCompletionChunk, ChatMessage
from runtimelib.llm.client import LiteLLMClient
from runtimelib.session import SessionManager


class FakeLLM:
    def __init__(self, model="test-model", window=None):
        self.default_model = model
        self.window = window
        self.seen = []

    def context_window(self, model=None):
        return self.window

    def chat_completion(self, messages, **kwargs):
        self.seen.append(list(messages))
        yield ChatCompletionChunk(
            id="1", model=self.default_model, created=0,
            choices=[{"delta": {"content": "ok"}, "finish_reason": "stop"}],
        )


def _config(model="test-model", **extra):
    data = {"liteLLM": {"base_url": "http://dummy:4000", "api_key": "dummy", "model": model}}
    data.update(extra)
    return Config(data)


def _loop(config, llm):
    session = MagicMock(spec=SessionManager)
    session.session_id = "s"
    return create_agent_loop(config, llm_client=llm, session_manager=session, tools=[])


def _fill(loop, tokens):
    loop.state.messages = [ChatMessage(role="user", content="x" * (tokens * 4))]


def test_overhead_counts_prompt_and_schemas():
    base = overhead_tokens("a" * 400)
    assert base == 100
    assert overhead_tokens("a" * 400, [{"name": "t" * 400}]) > base + 100
    loop = _loop(_config(agent={"context_window": 100_000}), FakeLLM())
    assert loop.overhead_tokens() > 0
    assert loop.report_context()["used"] == loop.overhead_tokens()


def test_compact_messages_subtracts_overhead():
    msgs = [ChatMessage(role="user", content="old " * 300), ChatMessage(role="assistant", content="r " * 300),
            ChatMessage(role="user", content="new")]
    n = context_tokens(msgs)
    assert compact_messages(msgs, n + 10)[1] is False
    assert compact_messages(msgs, n + 10, overhead=50)[1] is True


def test_warning_once_then_rearmed_after_compaction():
    seen = []
    loop = _loop(_config(agent={"context_window": 10_000}), FakeLLM())
    loop.on_context = seen.append
    base = loop.overhead_tokens()
    _fill(loop, 8_100 - base)
    first = loop.report_context()
    assert first["warning"] is True and first["percent"] == 81
    _fill(loop, 8_500 - base)
    assert "warning" not in loop.report_context()
    # a forced compaction re-arms the warning
    _fill(loop, 9_500 - base)
    loop._compact_messages()
    assert loop._ctx_warned is False
    _fill(loop, 8_200 - base)
    assert loop.report_context()["warning"] is True
    assert len(seen) == 3


def test_warn_percent_from_config():
    loop = _loop(_config(agent={"context_window": 10_000}, context={"warn_percent": 50}), FakeLLM())
    _fill(loop, 5_200 - loop.overhead_tokens())
    assert loop.report_context().get("warning") is True


def test_run_reports_context_after_turn():
    seen = []
    loop = _loop(_config(agent={"context_window": 50_000}), FakeLLM())
    loop.on_context = seen.append
    loop.run("hello")
    assert len(seen) == 1 and seen[0]["limit"] == 50_000 and seen[0]["source"] == "config"


def test_model_switch_clears_warning_without_compaction():
    cfg = _config("small", models={"small": {"context_window": 220_000}, "big": {"context_window": 1_000_000}})
    llm = FakeLLM("small")
    loop = _loop(cfg, llm)
    _fill(loop, 200_000 - loop.overhead_tokens())
    assert loop.report_context()["warning"] is True
    before = list(loop.state.messages)
    llm.default_model = "big"
    status = loop.report_context()
    assert status["percent"] == 20 and "warning" not in status and status["model"] == "big"
    assert loop._ctx_warned is False
    assert loop.state.messages == before  # nothing compacted at switch time
    # smaller model again at >= 90%: compaction happens on the next request, not at switch
    llm.default_model = "small"
    loop.state.messages = [ChatMessage(role="user", content="q" * 400_000),
                           ChatMessage(role="assistant", content="a" * 400_000),
                           ChatMessage(role="user", content="last")]
    n = len(loop.state.messages)
    assert loop.report_context()["percent"] >= 90
    assert len(loop.state.messages) == n
    loop.run("again")
    assert any("[Context compacted:" in (m.content or "") for m in loop.state.messages)


def test_window_priority_chain():
    probe = lambda m: 123_000
    cfg = _config(models={"claude-sonnet-4-0": {"context_window": 50_000}})
    assert resolve_context_window_info("claude-sonnet-4-0", cfg, endpoint=probe) == (50_000, "config")
    assert resolve_context_window_info("claude-sonnet-4-0", _config(), endpoint=probe) == (123_000, "endpoint")
    assert resolve_context_window_info("claude-sonnet-4-0", _config()) == (200_000, "table")
    assert resolve_context_window_info("our-1m-model", _config()) == (1_000_000, "name")
    assert resolve_context_window_info("mystery", _config()) == (32_000, "default")
    assert resolve_context_window("mystery", _config(), endpoint=lambda m: None) == 32_000
    # old keys still override
    assert resolve_context_window_info("mystery", _config(agent={"context_window": 9000})) == (9000, "config")

    def boom(m):
        raise RuntimeError("x")

    # a failing probe falls through
    assert resolve_context_window_info("mystery", _config(), endpoint=boom)[1] == "default"


def test_dotted_model_name_in_config():
    cfg = _config(models={"gpt-4.1": {"context_window": 77_000}})
    assert resolve_context_window_info("gpt-4.1", cfg) == (77_000, "config")


def test_compact_ratio_clamped_and_never_off():
    assert clamp_ratio(0) == 0.9 and clamp_ratio(-1) == 0.9 and clamp_ratio(None) == 0.9
    assert clamp_ratio("junk") == 0.9
    assert clamp_ratio(0.2) == 0.5 and clamp_ratio(2) == 0.95 and clamp_ratio(0.8) == 0.8
    loop = _loop(_config(agent={"context_window": 10_000}, cli={"compact_ratio": 0}), FakeLLM())
    assert loop.compact_threshold() == 9_000
    loop = _loop(_config(agent={"context_window": 10_000}, cli={"compact_ratio": 5}), FakeLLM())
    assert loop.compact_threshold() == 9_500


class _Http:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, headers=None, body=None, timeout=0, stream=False):
        self.calls.append((url, timeout))
        path = "/" + url.split("/", 3)[3]
        resp = MagicMock()
        if path in self.routes:
            resp.status = 200
            resp.json.return_value = self.routes[path]
        else:
            resp.status = 404
        return resp


def test_endpoint_probe_litellm_and_v1_models_with_cache():
    info = {"data": [{"model_name": "m", "model_info": {"max_input_tokens": 64_000}}]}
    http = _Http({"/model/info": info})
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=http)
    assert client.context_window("m") == 64_000
    assert client.context_window("m") == 64_000
    assert len(http.calls) == 1 and http.calls[0][1] == 3.0
    http = _Http({"/v1/models": {"data": [{"id": "m", "context_length": 131_072}]}})
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=http)
    assert client.context_window() == 131_072
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=_Http({}))
    assert client.context_window() is None


def test_endpoint_probe_ignores_small_litellm_max_tokens():
    small = {"data": [{"model_name": "m", "model_info": {"max_tokens": 8192}}]}
    v1 = {"data": [{"id": "m", "context_window": 200_000}]}
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=_Http({"/model/info": small, "/v1/models": v1}))
    assert client.context_window() == 200_000  # output limit skipped, /v1/models used
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=_Http({"/model/info": small}))
    assert client.context_window() is None
    big = {"data": [{"model_name": "m", "model_info": {"max_tokens": 128_000}}]}
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=_Http({"/model/info": big}))
    assert client.context_window() == 128_000
    both = {"data": [{"model_name": "m", "model_info": {"max_input_tokens": 100_000, "max_tokens": 4096}}]}
    client = LiteLLMClient("http://h:4000", "k", "m", http_backend=_Http({"/model/info": both}))
    assert client.context_window() == 100_000


def test_loop_uses_endpoint_window():
    loop = _loop(_config("mystery"), FakeLLM("mystery", window=500_000))
    assert loop.context_window_info() == (500_000, "endpoint")


class _CtxLoop:
    def __init__(self, bridge):
        self.llm = MagicMock()
        self.on_context = bridge.on_context
        self.limits = {"a": 220_000, "b": 1_000_000}
        self.used = 200_000

    def interrupt(self):
        pass

    def reset_context(self):
        pass

    def report_context(self):
        model = self.llm.default_model
        limit = self.limits[model]
        status = {"used": self.used, "limit": limit, "percent": self.used * 100 // limit,
                  "model": model, "source": "config"}
        self.on_context(status)
        return status

    def run(self, prompt):
        self.report_context()
        return "answer"


def test_bridge_emits_context_after_final_and_after_model_ok():
    cfg = Config({"liteLLM": {"model": "a"}})
    out = io.StringIO()
    stream = io.StringIO(
        '{"type":"chat","text":"hi"}\n{"type":"model","model":"b"}\n{"type":"bye"}\n')
    bridge = JsonlBridge(cfg, loop_factory=lambda b: _CtxLoop(b), stream=stream, out=out)
    bridge._ensure_loop().llm.default_model = "a"
    bridge.run()
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    kinds = [e["type"] for e in events]
    assert kinds == ["ready", "final", "context", "model_ok", "context", "bye"]
    assert events[2] == {"type": "context", "used": 200_000, "limit": 220_000, "percent": 90,
                         "model": "a", "source": "config"}
    assert events[4]["limit"] == 1_000_000 and events[4]["percent"] == 20 and events[4]["model"] == "b"
