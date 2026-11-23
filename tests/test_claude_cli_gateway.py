# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Tests for scripts/claude_cli_gateway.py (claude subprocess mocked)."""

import importlib.util
import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "claude_cli_gateway.py"
_spec = importlib.util.spec_from_file_location("claude_cli_gateway", _PATH)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)


def _proc(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout.encode(), stderr=b"")


@pytest.fixture
def server():
    calls = []

    def runner(prompt, model, claude_bin, timeout):
        calls.append({"prompt": prompt, "model": model})
        if "FAIL" in prompt:
            raise gw.GatewayError(502, "claude CLI error (exit 1): boom", "cli_error")
        return {"text": "ok", "usage": {"input_tokens": 3, "output_tokens": 1}}

    srv = gw.make_server(port=0, claude_bin="claude", runner=runner)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.calls = calls
    yield srv
    srv.shutdown()
    srv.server_close()


def _url(srv, path):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def _post(srv, payload):
    req = urllib.request.Request(
        _url(srv, "/v1/chat/completions"), data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"}, method="POST")
    return urllib.request.urlopen(req, timeout=10)


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"
    assert gw.HOST == "127.0.0.1"


def test_models_lists_aliases(server):
    with urllib.request.urlopen(_url(server, "/v1/models"), timeout=10) as r:
        ids = [m["id"] for m in json.load(r)["data"]]
    assert {"haiku", "sonnet", "opus"} <= set(ids)


def test_chat_completion_non_stream(server):
    with _post(server, {"model": "sonnet", "messages": [{"role": "user", "content": "say ok"}],
                        "tools": [{"type": "function", "function": {"name": "x"}}]}) as r:
        body = json.load(r)
    assert body["object"] == "chat.completion"
    msg = body["choices"][0]["message"]
    assert msg == {"role": "assistant", "content": "ok"}
    assert "tool_calls" not in msg
    assert body["usage"]["total_tokens"] == 4
    assert server.calls[-1] == {"prompt": "say ok", "model": "sonnet"}


def test_default_model_used(server):
    with _post(server, {"messages": [{"role": "user", "content": "hi"}]}) as r:
        assert json.load(r)["model"] == "haiku"


def test_stream_sse_matches_rex_client(server):
    from runtimelib.llm.client import ChatCompletionChunk, LiteLLMClient

    with _post(server, {"model": "haiku", "stream": True,
                        "messages": [{"role": "user", "content": "say ok"}]}) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        lines = [ln.decode().rstrip("\n") for ln in r.read().splitlines()]
    data_lines = [ln for ln in lines if ln.startswith("data: ")]
    assert data_lines[-1] == "data: [DONE]"
    parser = LiteLLMClient("http://x", "dummy", "haiku")
    chunks = []
    for ln in data_lines:
        parsed = parser._parse_sse_line(ln)
        if parsed.get("done"):
            break
        chunks.append(ChatCompletionChunk(**parsed))
    assert "".join(c.content or "" for c in chunks) == "ok"
    assert chunks[-1].finish_reason == "stop"
    assert all(c.tool_calls is None for c in chunks)


def test_error_path_returns_json(server):
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(server, {"model": "haiku", "messages": [{"role": "user", "content": "FAIL"}]})
    assert ei.value.code == 502
    assert json.load(ei.value)["error"]["type"] == "cli_error"


def test_bad_model_and_bad_json(server):
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(server, {"model": "gpt-4o", "messages": [{"role": "user", "content": "x"}]})
    assert ei.value.code == 400
    req = urllib.request.Request(_url(server, "/v1/chat/completions"), data=b"{nope", method="POST")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 400
    assert "error" in json.load(ei.value)


def test_flatten_messages():
    prompt = gw.flatten_messages([
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": [{"type": "text", "text": "read a.txt"}]},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "1", "function": {"name": "fileops", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "hello"},
        {"role": "user", "content": "what did it say?"},
    ])
    assert prompt.startswith("[System instructions]\nBe brief.")
    assert "User: read a.txt" in prompt
    assert "[called tool fileops" in prompt
    assert "Tool result: hello" in prompt
    assert prompt.index("Tool result") < prompt.index("what did it say?")


def test_valid_model():
    assert gw.valid_model("haiku") and gw.valid_model("claude-sonnet-4-6")
    assert not gw.valid_model("gpt-4o") and not gw.valid_model("claude-x; rm")


def test_run_claude_command_and_parse():
    out = json.dumps({"type": "result", "is_error": False, "result": "ok",
                      "usage": {"input_tokens": 5, "output_tokens": 2}})
    with mock.patch.object(gw.subprocess, "run", return_value=_proc(out)) as run:
        res = gw.run_claude("say ok", "haiku", "claude", 30)
    assert res["text"] == "ok"
    cmd = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "say ok" not in cmd
    assert kwargs["input"] == b"say ok"
    assert kwargs["timeout"] == 30
    assert "shell" not in kwargs


@pytest.mark.parametrize("effect,status", [
    (subprocess.TimeoutExpired(cmd="claude", timeout=1), 504),
    (FileNotFoundError(), 502),
    (_proc("not json", 1), 502),
    (_proc(json.dumps({"is_error": True, "result": "bad"}), 1), 502),
])
def test_run_claude_errors(effect, status):
    kw = {"side_effect": effect} if isinstance(effect, BaseException) else {"return_value": effect}
    with mock.patch.object(gw.subprocess, "run", **kw):
        with pytest.raises(gw.GatewayError) as ei:
            gw.run_claude("x", "haiku", "claude", 1)
    assert ei.value.status == status


def test_rex_client_end_to_end(server):
    """rex's own LiteLLMClient (real kikolib HTTP backend) against the gateway."""
    from runtimelib.llm.client import ChatMessage, LiteLLMClient

    client = LiteLLMClient(_url(server, ""), "dummy", "haiku", timeout=10)
    assert "haiku" in [m["id"] for m in client.list_models()]
    resp = client.chat_completion([ChatMessage(role="user", content="say ok")])
    assert resp.status == 200 and resp.json()["choices"][0]["message"]["content"] == "ok"
    chunks = list(client.chat_completion([ChatMessage(role="user", content="say ok")], stream=True))
    assert "".join(c.content or "" for c in chunks) == "ok"
