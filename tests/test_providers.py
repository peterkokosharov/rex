# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License
"""Contract tests for the Anthropic and Gemini adapters: recorded-shape SSE fixtures, no real network."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ai_runtime_rex.api import Agent
from ai_runtime_rex.runtime import providers as pv

ANTHROPIC_TEXT = (
    'event: message_start\ndata: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":11}}}\n\n'
    'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n'
    'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
ANTHROPIC_TOOL = (
    'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":'
    '{"type":"tool_use","id":"toolu_1","name":"fileops","input":{}}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":'
    '{"type":"input_json_delta","partial_json":"{\\"path\\":"}}\n\n'
    'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":'
    '{"type":"input_json_delta","partial_json":"\\"a.txt\\"}"}}\n\n'
    'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":9}}\n\n'
)
GEMINI_TEXT = (
    'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hel"}]}}]}\n\n'
    'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"lo"}]},"finishReason":"STOP"}],'
    '"usageMetadata":{"promptTokenCount":7,"candidatesTokenCount":2}}\n\n'
)
GEMINI_TOOL = (
    'data: {"candidates":[{"content":{"role":"model","parts":[{"functionCall":'
    '{"name":"fileops","args":{"path":"a.txt"}}}]},"finishReason":"STOP"}]}\n\n'
)

BODY = {
    "model": "m", "stream": True, "temperature": 0.2, "max_tokens": 50,
    "messages": [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "read a.txt"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fileops", "arguments": '{"path": "a.txt"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file text"},
        {"role": "user", "content": "thanks"},
    ],
    "tools": [{"type": "function", "function": {"name": "fileops", "description": "files", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False}}}],
}


def _events(text):
    out, ev, data = [], "", []
    for line in text.splitlines():
        if not line:
            if data:
                out.append((ev, "\n".join(data)))
            ev, data = "", []
        elif line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    return out


def test_anthropic_request_mapping():
    r = pv.anthropic_request(BODY)
    assert r["system"] == "be brief" and r["max_tokens"] == 50 and r["stream"] is True
    assert r["messages"][1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "fileops",
                                              "input": {"path": "a.txt"}}
    assert r["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "file text"}]}
    assert r["messages"][3] == {"role": "user", "content": "thanks"}
    assert r["tools"][0]["input_schema"]["properties"]["path"] == {"type": "string"}


def test_anthropic_text_and_tool_events():
    agg = pv.aggregate(pv.anthropic_events(iter(_events(ANTHROPIC_TEXT))))
    assert agg["choices"][0]["message"]["content"] == "Hello"
    assert agg["usage"] == {"prompt_tokens": 11, "completion_tokens": 5}
    tool = pv.aggregate(pv.anthropic_events(iter(_events(ANTHROPIC_TOOL))))
    assert tool["choices"][0]["finish_reason"] == "tool_calls"
    call = tool["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "toolu_1" and json.loads(call["function"]["arguments"]) == {"path": "a.txt"}


def test_gemini_request_mapping():
    r = pv.gemini_request(BODY)
    assert r["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert r["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 50}
    assert r["contents"][1] == {"role": "model", "parts": [
        {"functionCall": {"name": "fileops", "args": {"path": "a.txt"}}}]}
    assert r["contents"][2]["parts"][0]["functionResponse"]["name"] == "fileops"
    assert "additionalProperties" not in r["tools"][0]["functionDeclarations"][0]["parameters"]


def test_gemini_text_and_tool_events():
    agg = pv.aggregate(pv.gemini_events(iter(_events(GEMINI_TEXT))))
    assert agg["choices"][0]["message"]["content"] == "Hello"
    assert agg["usage"] == {"prompt_tokens": 7, "completion_tokens": 2}
    tool = pv.aggregate(pv.gemini_events(iter(_events(GEMINI_TOOL))))
    assert tool["choices"][0]["finish_reason"] == "tool_calls"
    assert json.loads(tool["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}


@pytest.fixture
def server():
    seen = []

    class H(BaseHTTPRequestHandler):
        reply = ""

        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(H.reply.encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield H, srv.server_address[1], seen
    srv.shutdown()


def test_agent_anthropic_end_to_end(server, monkeypatch):
    H, port, seen = server
    H.reply = ANTHROPIC_TEXT
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    out = Agent(provider="anthropic", base_url=f"http://127.0.0.1:{port}", model="claude-x", tools=[]).run("hi")
    assert out == "Hello"
    assert seen[0]["path"] == "/v1/messages"
    assert seen[0]["headers"]["x-api-key"] == "sk-secret"
    assert seen[0]["headers"]["anthropic-version"] == pv.ANTHROPIC_VERSION
    assert "tools" not in seen[0]["body"]


def test_agent_gemini_end_to_end(server, monkeypatch):
    H, port, seen = server
    H.reply = GEMINI_TEXT
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret")
    out = Agent(provider="gemini", base_url=f"http://127.0.0.1:{port}", model="gem-x", tools=[]).run("hi")
    assert out == "Hello"
    assert seen[0]["path"] == "/v1beta/models/gem-x:streamGenerateContent?alt=sse"
    assert seen[0]["headers"]["x-goog-api-key"] == "g-secret"


def test_native_provider_needs_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AI_REX_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Agent(provider="anthropic")


def test_auto_detects_native_urls():
    from ai_runtime_rex.runtime.envconfig import detect_provider
    assert detect_provider("auto", "https://api.anthropic.com") == "anthropic"
    assert detect_provider(None, "https://generativelanguage.googleapis.com") == "gemini"
    assert detect_provider("auto", "http://127.0.0.1:4000") == "openai"
    assert detect_provider("openai", "https://api.anthropic.com") == "openai"
