# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""Gateway tool mode end to end: a fake `claude` binary (real subprocess) and rex's AgentLoop."""

import importlib.util
import json
import os
import sys
import time
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.llm.client import LiteLLMClient
from runtimelib.tools.base import Tool, ToolSchema

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "claude_cli_gateway.py"
_spec = importlib.util.spec_from_file_location("claude_cli_gateway_e2e", _PATH)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_claude.py"
READ_CALL = '{"tool_calls":[{"name":"fileops","arguments":{"operation":"read","path":"a.txt"}}]}'
FINAL = '{"content":"The file says hello."}'
ALL_FLAGS = {"--safe-mode", "--system-prompt", "--json-schema", "--setting-sources"}
# Read at import, before tests/conftest.py points HOME/USERPROFILE at an empty folder for each test.
# The live test needs the real ones back: without them the claude CLI has no login.
_REAL_HOME = {k: os.environ[k] for k in ("HOME", "USERPROFILE") if k in os.environ}


class FakeCli:
    """Scripts the fake binary through environment variables and reads back what it saw."""

    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.log = self.dir / "log.jsonl"
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(self.dir / "script.json"))
        monkeypatch.setenv("FAKE_CLAUDE_COUNTER", str(self.dir / "count"))
        monkeypatch.setenv("FAKE_CLAUDE_LOG", str(self.log))

    def script(self, *replies):
        (self.dir / "script.json").write_text(json.dumps(list(replies)), encoding="utf-8")

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    return FakeCli(tmp_path, monkeypatch)


@pytest.fixture
def gateway():
    servers = []

    def start(flags=ALL_FLAGS, timeout=60.0):
        srv = gw.make_server(port=0, claude_bin=[sys.executable, str(FAKE)], flags=flags, timeout=timeout)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


class FakeFileops(Tool):
    def __init__(self):
        params = {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["read"]}, "path": {"type": "string"}},
            "required": ["operation", "path"]}
        super().__init__("fileops", "Read a file.", ToolSchema("fileops", "Read a file.", params))
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append({k: v for k, v in kwargs.items() if not k.startswith("_")})
        return {"content": "hello from a.txt"}


class AllowGate:
    """Allows every call; the real permission rules are tested elsewhere."""

    def check(self, name, args, tool=None):
        return SimpleNamespace(allowed=True, reason="", source="test")

    def post(self, *args, **kwargs):
        return None


class _Session:
    session_id = "s1"

    def append(self, *a, **k):
        pass


TOOLS = [{"type": "function", "function": {"name": "echo", "description": "Echo.",
                                           "parameters": {"type": "object",
                                                          "properties": {"text": {"type": "string"}}}}}]


def _post(base, payload):
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return urllib.request.urlopen(req, timeout=60)


# ---------------------------------------------------------------- argv safety

@pytest.mark.parametrize("flags", [ALL_FLAGS, {"--safe-mode"}])
def test_tool_mode_argv_stays_toolless(fake, gateway, flags):
    fake.script('{"tool_calls":[{"name":"echo","arguments":{"text":"hi"}}]}', "plain")
    _srv, base = gateway(flags)
    with _post(base, {"messages": [{"role": "user", "content": "echo hi"}], "tools": TOOLS}) as r:
        assert json.load(r)["choices"][0]["finish_reason"] == "tool_calls"
    with _post(base, {"messages": [{"role": "user", "content": "hi"}]}) as r:
        assert json.load(r)["choices"][0]["message"]["content"] == "plain"
    tool_call, chat_call = fake.calls()
    for argv in (tool_call["argv"], chat_call["argv"]):
        assert argv[argv.index("--tools") + 1] == ""
        assert "--strict-mcp-config" in argv and "--safe-mode" in argv
        assert "--mcp-config" not in argv and "--setting-sources" not in argv
        assert not any(a.startswith("--allowed") or a.startswith("--permission") for a in argv)
    assert "--json-schema" not in tool_call["argv"]  # opt-in only since 2026-11-28
    assert ("--system-prompt" in tool_call["argv"]) == ("--system-prompt" in flags)
    assert "--json-schema" not in chat_call["argv"] and "--system-prompt" not in chat_call["argv"]
    if "--system-prompt" in flags:
        sp = tool_call["argv"][tool_call["argv"].index("--system-prompt") + 1]
        assert sp == gw.SYSTEM_PROMPT_LINE
    assert "[Tools]" in tool_call["stdin"] and "echo hi" not in " ".join(tool_call["argv"])


# ---------------------------------------------------------------- scenarios through the real subprocess path

def test_bad_then_good_through_fake(fake, gateway):
    fake.script("{oops", '{"tool_calls":[{"name":"echo","arguments":{"text":"hi"}}]}')
    _srv, base = gateway({"--safe-mode"})
    with _post(base, {"messages": [{"role": "user", "content": "x"}], "tools": TOOLS}) as r:
        body = json.load(r)
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "echo"
    assert body["usage"]["prompt_tokens"] == 14
    assert "Your previous reply was rejected" in fake.calls()[1]["stdin"]


def test_timeout_during_retry_kills_and_504(fake, gateway, monkeypatch):
    monkeypatch.setattr(gw, "MIN_RETRY_SECONDS", 0.5)
    fake.script("{oops", {"result": FINAL, "sleep": 30})
    _srv, base = gateway({"--safe-mode"}, timeout=3.0)
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, {"messages": [{"role": "user", "content": "x"}], "tools": TOOLS})
    assert ei.value.code == 504
    assert json.loads(ei.value.read())["error"]["type"] == "timeout"


def _wait_log(capsys, needle):
    err = ""
    for _ in range(100):  # the log line is written right after the response
        err += capsys.readouterr().err
        if needle in err:
            break
        time.sleep(0.02)
    return err


def test_cli_not_logged_in_says_why(fake, gateway, capsys):
    """Regression: the live test lost the CLI login (empty HOME) and only showed a bare 502."""
    fake.script({"result": "Not logged in · Please run /login", "is_error": True, "exit": 1})
    _srv, base = gateway()
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, {"messages": [{"role": "user", "content": "x"}], "tools": TOOLS})
    assert ei.value.code == 502
    err = json.loads(ei.value.read())["error"]
    assert err["type"] == "cli_error"
    assert err["message"] == "claude CLI error (exit 1): Not logged in · Please run /login"
    log = _wait_log(capsys, "status=502")
    assert "status=502 tools=1 calls=0 retry=0 error=cli_error exit=1 reason=Not logged in" in log
    assert len(fake.calls()) == 1  # CLI errors are not retried


def test_cli_without_json_reports_stderr(fake, gateway, capsys):
    fake.script({"raw": "", "stderr": "\nerror: unknown option '--frobnicate'\nmore\n", "exit": 2})
    _srv, base = gateway()
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, {"messages": [{"role": "user", "content": "x"}]})
    msg = json.loads(ei.value.read())["error"]["message"]
    assert msg == "claude CLI failed (exit 2, no JSON result): error: unknown option '--frobnicate'"
    assert "error=cli_error exit=2 stderr=error: unknown option '--frobnicate'" in _wait_log(capsys, "status=502")


def test_protocol_failure_logs_reason(fake, gateway, capsys):
    fake.script("{oops")
    _srv, base = gateway({"--safe-mode"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post(base, {"messages": [{"role": "user", "content": "x"}], "tools": TOOLS})
    assert ei.value.code == 502
    log = _wait_log(capsys, "status=502")
    assert "retry=1 error=tool_protocol_error reason=the reply is not valid JSON" in log


# ---------------------------------------------------------------- AgentLoop round trip

@pytest.mark.parametrize("flags", [ALL_FLAGS, {"--safe-mode"}], ids=["json-schema", "plain-parser"])
def test_agent_loop_round_trip(fake, gateway, flags):
    fake.script(READ_CALL, FINAL)
    _srv, base = gateway(flags)
    tool = FakeFileops()
    client = LiteLLMClient(base, "dummy", "haiku", timeout=60)
    loop = AgentLoop(Config({"liteLLM": {"base_url": base, "api_key": "dummy", "model": "haiku"}}),
                     llm_client=client, tools=[tool], gate=AllowGate(), session_manager=_Session())

    answer = loop.run("What does a.txt say?")

    assert answer == "The file says hello."
    assert tool.calls == [{"operation": "read", "path": "a.txt"}]
    first, second = fake.calls()
    assert "What does a.txt say?" in first["stdin"] and "<tool_result id=" not in first["stdin"]
    stdin = second["stdin"]
    call_id = next(m.tool_calls[0].id for m in loop.state.messages if m.role == "assistant" and m.tool_calls)
    assert call_id.startswith("call_")
    assert f'<tool_result id="{call_id}" name="fileops" untrusted="true">' in stdin
    assert "hello from a.txt" in stdin
    assert '"name": "fileops"' in stdin and stdin.count("name: fileops") == 1


# ---------------------------------------------------------------- optional live test (real CLI, costs a little)

@pytest.mark.skipif(os.environ.get("REX_LIVE_CLAUDE") != "1",
                    reason="live test: set REX_LIVE_CLAUDE=1 (uses the logged-in claude CLI, model haiku)")
@pytest.mark.parametrize("use_schema", [False], ids=["plain-parser"])  # --json-schema: opt-in, known to lose calls
def test_live_echo_round_trip(use_schema, monkeypatch):
    for key, value in _REAL_HOME.items():  # undo the conftest home isolation: the CLI needs its login
        monkeypatch.setenv(key, value)
    binary = gw.safe_which("claude")
    assert binary, "claude not on PATH"
    flags = gw.cli_flags(binary)
    assert flags and "--safe-mode" in flags
    srv = gw.make_server(port=0, claude_bin=binary, flags=flags, json_schema=use_schema, timeout=180)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    tools = [{"type": "function", "function": {
        "name": "echo", "description": "Echo the given text back.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                       "additionalProperties": False}}}]
    messages = [{"role": "user", "content": "call echo with text=hi, then say done"}]
    try:
        with _post(base, {"model": "haiku", "messages": messages, "tools": tools}) as r:
            first = json.load(r)["choices"][0]
        assert first["finish_reason"] == "tool_calls"
        (call,) = first["message"]["tool_calls"]
        assert call["function"]["name"] == "echo"
        assert json.loads(call["function"]["arguments"]) == {"text": "hi"}
        messages += [first["message"],
                     {"role": "tool", "tool_call_id": call["id"],
                      "content": json.dumps({"success": True, "result": "hi", "error": None})}]
        with _post(base, {"model": "haiku", "messages": messages, "tools": tools}) as r:
            second = json.load(r)["choices"][0]
        assert second["finish_reason"] == "stop"
        assert "done" in (second["message"]["content"] or "").lower()
    finally:
        srv.shutdown()
        srv.server_close()
