# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License

"""Client-side tool relay through scripts/claude_cli_gateway.py, with a fake `claude` process.

The tools run in the client (rex). The gateway only relays: it shows the model the client's tool
definitions, returns the model's calls as OpenAI tool_calls, takes the tool results back in the next
request, and keeps the CLI's own built-in tools off. The replies below were recorded from the real
CLI 2.1.332 (haiku and sonnet) on 2026-11-28.

Regression (2026-11-28): with --json-schema the CLI forces its StructuredOutput tool; the model first
wrote a correct envelope as text, the CLI then injected "[structured-output-enforce]" and the model
answered through StructuredOutput with prose ("the file-reading tool isn't available"), so the call
was lost. The gateway no longer passes --json-schema unless asked to.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "claude_cli_gateway.py"
_spec = importlib.util.spec_from_file_location("claude_cli_gateway_relay", _PATH)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_claude.py"
# Flags as the real CLI 2.1.332 lists them in --help (the ones the gateway cares about).
REAL_FLAGS = {"--safe-mode", "--setting-sources", "--system-prompt", "--json-schema", "--tools",
              "--strict-mcp-config", "--permission-mode", "--disable-slash-commands", "--no-session-persistence"}
_REAL_HOME = {k: os.environ[k] for k in ("HOME", "USERPROFILE") if k in os.environ}

# Recorded from the real CLI (haiku, 2026-11-28), first turn of "read README.md ... project name".
REC_READ = '{"tool_calls":[{"name":"fileops","arguments":{"operation":"read","path":"README.md"}}]}'
# Recorded --json-schema failure: the StructuredOutput call carried prose instead of the call.
REC_SCHEMA_PROSE = ("I haven't received the contents of README.md yet, so I can't name the project. "
                    "The read request I sent hasn't returned a result.")

FILEOPS = {"type": "function", "function": {
    "name": "fileops", "description": "File operations: read, write, edit, list, glob, grep",
    "parameters": {"type": "object", "properties": {
        "operation": {"type": "string", "enum": ["read", "write", "edit", "list", "glob", "grep"]},
        "path": {"type": "string"}, "content": {"type": "string"}, "pattern": {"type": "string"}},
        "required": ["operation"]}}}
BASH = {"type": "function", "function": {
    "name": "bash", "description": "Execute shell commands",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"},
                                                     "timeout": {"type": "integer"}},
                   "required": ["command"]}}}
TOOLS = [FILEOPS, BASH]
USER = {"role": "user", "content": "Read README.md in this folder and tell me in one line what the project is called."}


class FakeCli:
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
def base():
    """A gateway on a free port running the fake CLI with the real CLI's flag set and default options."""
    servers = []

    def start(**kw):
        srv = gw.make_server(port=0, claude_bin=[sys.executable, str(FAKE)], flags=REAL_FLAGS, timeout=60, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def post(url, payload):
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        if payload.get("stream"):
            raw = r.read().decode()
            return [json.loads(x[6:]) for x in raw.split("\n\n") if x.startswith("data: ") and "[DONE]" not in x]
        return json.load(r)


def assert_builtins_off(argv):
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--disable-slash-commands" in argv and "--strict-mcp-config" in argv and "--safe-mode" in argv
    for bad in ("--mcp-config", "--allowedTools", "--allowed-tools", "--dangerously-skip-permissions",
                "--add-dir", "--agents", "--plugin-dir", "--json-schema"):
        assert bad not in argv


# ---------------------------------------------------------------- tool call returned

def test_tool_call_returned_as_openai_tool_calls(fake, base):
    fake.script(REC_READ)
    body = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS, "tool_choice": "auto"})
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    (call,) = choice["message"]["tool_calls"]
    assert call["type"] == "function" and call["id"].startswith("call_")
    assert call["function"]["name"] == "fileops"
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"operation": "read", "path": "README.md"}
    (cli,) = fake.calls()
    assert "name: fileops" in cli["stdin"] and "name: bash" in cli["stdin"]  # the client's tools, described


def test_schema_regression_call_is_not_replaced_by_structured_prose(fake, base):
    """The fake answers like the real CLI did under --json-schema: envelope text, prose structured output."""
    fake.script({"result": REC_READ, "structured_output": {"content": REC_SCHEMA_PROSE}})
    body = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert "--json-schema" not in fake.calls()[0]["argv"]


def test_tool_call_streamed_as_one_complete_chunk(fake, base):
    fake.script(REC_READ)
    chunks = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS, "stream": True})
    deltas = [c["choices"][0]["delta"] for c in chunks if c["choices"]]
    calls = [tc for d in deltas for tc in d.get("tool_calls") or []]
    assert len(calls) == 1 and calls[0]["index"] == 0 and calls[0]["id"].startswith("call_")
    assert json.loads(calls[0]["function"]["arguments"])["path"] == "README.md"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------- tool result continues the conversation

def test_tool_result_continues(fake, base):
    fake.script(REC_READ, '{"content":"The project is called py-vision."}')
    url = base()
    first = post(url, {"model": "sonnet", "messages": [USER], "tools": TOOLS})["choices"][0]["message"]
    call = first["tool_calls"][0]
    messages = [USER, first, {"role": "tool", "tool_call_id": call["id"],
                              "content": "# py-vision\nA Python port of Turbo Vision."}]
    second = post(url, {"model": "sonnet", "messages": messages, "tools": TOOLS})["choices"][0]
    assert second["finish_reason"] == "stop"
    assert second["message"] == {"role": "assistant", "content": "The project is called py-vision."}
    stdin = fake.calls()[1]["stdin"]
    assert f'<tool_result id="{call["id"]}" name="fileops" untrusted="true">' in stdin
    assert "A Python port of Turbo Vision." in stdin
    assert '"name": "fileops"' in stdin  # the earlier call is shown in the history as an envelope


# ---------------------------------------------------------------- parallel calls

def test_parallel_calls_get_distinct_ids_and_json_string_arguments(fake, base):
    fake.script('{"tool_calls":[{"name":"fileops","arguments":{"operation":"read","path":"README.md"}},'
                '{"name":"bash","arguments":{"command":"ls","timeout":5.0}}]}')
    msg = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})["choices"][0]["message"]
    a, b = msg["tool_calls"]
    assert a["id"] != b["id"]
    assert [a["function"]["name"], b["function"]["name"]] == ["fileops", "bash"]
    assert json.loads(b["function"]["arguments"]) == {"command": "ls", "timeout": 5}  # integer coerced


# ---------------------------------------------------------------- malformed output repair

def test_broken_json_is_repaired_by_one_retry(fake, base):
    fake.script('{"tool_calls":[{"name":"fileops","arguments":{"operation":"read",', REC_READ)
    body = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    calls = fake.calls()
    assert len(calls) == 2 and "Your previous reply was rejected" in calls[1]["stdin"]


def test_openai_shaped_call_is_normalised_without_retry(fake, base):
    fake.script('{"tool_calls":[{"id":"x1","type":"function","function":{"name":"fileops",'
                '"arguments":"{\\"operation\\":\\"read\\",\\"path\\":\\"README.md\\"}"}}]}')
    msg = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})["choices"][0]["message"]
    (call,) = msg["tool_calls"]
    assert call["function"]["name"] == "fileops" and call["id"] != "x1"
    assert json.loads(call["function"]["arguments"]) == {"operation": "read", "path": "README.md"}
    assert len(fake.calls()) == 1


def test_string_arguments_holding_an_object_are_accepted():
    s = gw.tool_spec({"tools": TOOLS})
    env = {"tool_calls": [{"name": "bash", "arguments": "{\"command\": \"ls\"}"}]}
    assert gw.validate_envelope(env, s) == (None, [("bash", {"command": "ls"})])
    with pytest.raises(gw.ProtocolError):
        gw.validate_envelope({"tool_calls": [{"name": "bash", "arguments": "ls"}]}, s)


def test_prose_then_envelope_on_its_own_line_is_salvaged(fake, base):
    fake.script("I'll read the file first.\n" + REC_READ)
    body = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["choices"][0]["message"]["content"] == "I'll read the file first."
    assert len(fake.calls()) == 1


def test_prose_wrapping_an_envelope_inline_is_nudged_once(fake, base):
    fake.script("I'll read the file first: " + REC_READ, REC_READ)
    body = post(base(), {"model": "haiku", "messages": [USER], "tools": TOOLS})
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    calls = fake.calls()
    assert len(calls) == 2 and calls[1]["stdin"].endswith(gw.NUDGE)


# ---------------------------------------------------------------- built-in tools disabled

def test_builtin_tools_disabled_in_tool_mode_and_plain_chat(fake, base):
    fake.script(REC_READ, "pong")
    url = base()
    post(url, {"model": "haiku", "messages": [USER], "tools": TOOLS})
    post(url, {"model": "haiku", "messages": [{"role": "user", "content": "ping"}]})
    for cli in fake.calls():
        assert_builtins_off(cli["argv"])


def test_strict_flags_only_when_the_cli_lists_them():
    cmd = gw.build_command("claude", "haiku", safe_mode=True)
    assert cmd[cmd.index("--tools") + 1] == "" and "--permission-mode" not in cmd
    cmd = gw.build_command("claude", "haiku", safe_mode=True, dont_ask=True, no_slash=True)
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk" and "--disable-slash-commands" in cmd


def test_json_schema_is_opt_in(monkeypatch):
    from unittest import mock
    srv = mock.Mock()
    srv.serve_forever.side_effect = KeyboardInterrupt
    with mock.patch.object(gw, "safe_which", return_value="claude"), \
            mock.patch.object(gw, "make_server", return_value=srv) as ms, \
            mock.patch.object(gw, "cli_flags", return_value=REAL_FLAGS):
        assert gw.main(["--port", "4000"]) == 0
        assert ms.call_args.kwargs["json_schema"] is False
        assert gw.main(["--port", "4000", "--no-json-schema"]) == 0
        assert ms.call_args.kwargs["json_schema"] is False
        assert gw.main(["--port", "4000", "--json-schema"]) == 0
        assert ms.call_args.kwargs["json_schema"] is True


# ---------------------------------------------------------------- no tools: plain chat unchanged

def test_no_tools_plain_chat_unchanged(fake, base):
    fake.script("Hello there.")
    body = post(base(), {"model": "haiku", "messages": [{"role": "user", "content": "say hello"}]})
    assert body["choices"][0] == {"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": "Hello there."}}
    (cli,) = fake.calls()
    assert cli["stdin"] == "say hello"
    assert "--system-prompt" not in cli["argv"] and "[Tools]" not in cli["stdin"]


def test_tool_choice_none_is_plain_chat(fake, base):
    fake.script("Hi.")
    post(base(), {"model": "haiku", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS,
                  "tool_choice": "none"})
    assert "[Tools]" not in fake.calls()[0]["stdin"]


# ---------------------------------------------------------------- live: the real CLI really has no tools

@pytest.mark.skipif(os.environ.get("REX_LIVE_CLAUDE") != "1",
                    reason="live test: set REX_LIVE_CLAUDE=1 (uses the logged-in claude CLI, model haiku)")
def test_live_cli_session_has_no_tools(monkeypatch):
    """Run the real CLI with the gateway's own argv (stream-json for the init event) and read its tool list."""
    for key, value in _REAL_HOME.items():
        monkeypatch.setenv(key, value)
    binary = gw.safe_which("claude")
    assert binary, "claude not on PATH"
    flags = gw.cli_flags(binary)
    cmd = gw.build_command(binary, "haiku", "--setting-sources" in flags, "--safe-mode" in flags,
                           gw.SYSTEM_PROMPT_LINE, dont_ask="--permission-mode" in flags,
                           no_slash="--disable-slash-commands" in flags)
    cmd[cmd.index("--output-format") + 1] = "stream-json"
    cmd.append("--verbose")
    wd = tempfile.mkdtemp(prefix="claude-gw-live-")
    try:
        out = subprocess.run(cmd, input=b"Say only: pong", capture_output=True, cwd=wd, timeout=180).stdout
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    events = [json.loads(ln) for ln in out.decode("utf-8", "replace").splitlines() if ln.startswith("{")]
    init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
    assert init["tools"] == [] and init["mcp_servers"] == []
    assert init["permissionMode"] == "dontAsk" and init.get("slash_commands") == []
