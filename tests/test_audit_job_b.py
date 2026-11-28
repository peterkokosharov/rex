# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Audit fixes (job B): provider selection, output limit, redirects, Gemini schemas, leak rule, MCP read-only
trust, skip folders, REPL commands, stream interrupt, tool result headers, endpoint precedence."""

from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runtimelib.config import Config
from runtimelib.llm.client import ChatCompletionChunk
from runtimelib.tools.base import Tool, ToolResult, ToolSchema
from ai_runtime_rex.core.agent import loop as loop_mod
from ai_runtime_rex.core.agent.loop import NUDGE_LEAKED, leaked_tool_call
from ai_runtime_rex.core.tools import fileops
from ai_runtime_rex.core.tools.fileops import FileOpsTool
from ai_runtime_rex.runtime import providers as pv
from ai_runtime_rex.runtime.envconfig import resolve_endpoint

BASE = {"liteLLM": {"base_url": "http://d:4000", "api_key": "k", "model": "m"}}
LOCAL = Config({**BASE, "cli": {"unsafe_local_tools": True}})


def _text(t, finish="stop"):
    return ChatCompletionChunk(choices=[{"delta": {"content": t}, "finish_reason": finish}])


def _raw_call(cid, name, raw_args, finish="tool_calls"):
    return ChatCompletionChunk(choices=[{"delta": {"tool_calls": [{"index": 0, "id": cid, "type": "function",
                                                                   "function": {"name": name, "arguments": raw_args}}]},
                                         "finish_reason": finish}])


class Script:
    default_model = "m"

    def __init__(self, replies):
        self.replies = list(replies)

    def chat_completion(self, **kw):
        return iter(self.replies.pop(0))


def _loop(replies, tokens=None, config=LOCAL):
    from runtimelib.agent import create_agent_loop
    loop = create_agent_loop(config, on_token=(tokens.append if tokens is not None else None))
    loop.llm = Script(replies)
    return loop


# --- 1: make_llm through Config.load ---------------------------------------------------------------------

def _load(tmp_path, lite):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"liteLLM": lite}), encoding="utf-8")
    return Config.load(p)


def test_make_llm_config_file_without_endpoint_uses_defaults_or_clear_error(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "AI_REX_API_KEY", "LITELLM_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        pv.make_llm(_load(tmp_path, {"provider": "anthropic", "model": "x"}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    llm = pv.make_llm(_load(tmp_path, {"provider": "anthropic"}))
    assert llm.default_model == "claude-sonnet-5-5"


def test_make_llm_normalises_case_and_rejects_unknown(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(pv, "anthropic_chat", lambda *a, **k: seen.setdefault("maker", "anthropic"))
    monkeypatch.setattr(pv, "gemini_chat", lambda *a, **k: seen.setdefault("maker", "gemini"))
    pv.make_llm(_load(tmp_path, {"provider": "Anthropic", "base_url": "http://x", "api_key": "k", "model": "m"}))
    assert seen["maker"] == "anthropic"
    with pytest.raises(ValueError, match="unknown provider"):
        pv.make_llm(_load(tmp_path, {"provider": "antropic", "base_url": "http://x", "api_key": "k", "model": "m"}))
    assert pv.make_llm(_load(tmp_path, {"provider": "OpenAI", "base_url": "http://x"})) is None


# --- 2: output limit --------------------------------------------------------------------------------------

def test_anthropic_default_max_tokens_is_model_appropriate():
    body = {"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]}
    assert pv.anthropic_request(body)["max_tokens"] == 32000
    assert pv.anthropic_request({**body, "model": "claude-opus-5-5"})["max_tokens"] == 32000
    assert pv.anthropic_request({**body, "max_tokens": 100})["max_tokens"] == 100
    assert pv.anthropic_request(body, default_max_tokens=5000)["max_tokens"] == 5000


def test_max_tokens_setting_and_env_reach_the_adapter(tmp_path, monkeypatch):
    got = {}

    def fake(base, key, timeout=600.0, default_max_tokens=None):
        got["max"] = default_max_tokens
        return lambda body: None
    monkeypatch.setattr(pv, "anthropic_chat", fake)
    lite = {"provider": "anthropic", "base_url": "http://x", "api_key": "k", "model": "m"}
    monkeypatch.setenv("AI_REX_MAX_TOKENS", "12345")
    pv.make_llm(_load(tmp_path, lite))
    assert got["max"] == 12345
    pv.make_llm(_load(tmp_path, {**lite, "max_tokens": 777}))
    assert got["max"] == 777


def test_truncated_tool_call_is_reported_and_retried_once(capsys):
    cut = [_raw_call("c1", "fileops", '{"operation": "write", "path": "a.txt", "content": "abc', finish="length")]
    loop = _loop([cut, [_text("done")]])
    assert loop.run("write a big file") == "done"
    tools = [m.content for m in loop.state.messages if m.role == "tool"]
    assert not any("Invalid tool arguments" in (t or "") for t in tools)
    users = [m.content for m in loop.state.messages if m.role == "user"]
    assert any("cut off" in u and "in parts" in u for u in users)
    assert "truncated" in capsys.readouterr().err
    for m in loop.state.messages:  # history stays valid: no call without a result
        assert not (m.role == "assistant" and m.tool_calls)


def test_second_truncation_falls_back_to_the_normal_error():
    cut = [_raw_call("c1", "fileops", '{"operation": "write", "content": "abc', finish="length")]
    cut2 = [_raw_call("c2", "fileops", '{"operation": "write", "content": "abc', finish="length")]
    loop = _loop([cut, cut2, [_text("gave up")]])
    assert loop.run("go") == "gave up"
    assert any("Invalid tool arguments" in (m.content or "") for m in loop.state.messages if m.role == "tool")


def test_truncated_text_answer_is_continued_and_joined():
    loop = _loop([[_text("first half ", finish="length")], [_text("second half")]])
    assert loop.run("long answer") == "first half second half"


# --- 3: redirects and response size ------------------------------------------------------------------------

@pytest.fixture()
def redirect_pair():
    seen = []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", f"http://localhost:{target.server_address[1]}/v1/messages")
            self.end_headers()

    src = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for s in (target, src):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield src.server_address[1], seen
    for s in (target, src):
        s.shutdown()


def test_redirect_is_refused_and_key_never_leaves(redirect_pair):
    port, seen = redirect_pair
    chat = pv.anthropic_chat(f"http://127.0.0.1:{port}", "sk-secret", timeout=5)
    with pytest.raises(pv.ProviderError, match="redirect"):
        chat({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert seen == []


def test_response_size_is_capped(monkeypatch):
    monkeypatch.setattr(pv, "MAX_RESPONSE_BYTES", 100)
    lines = [b"data: " + b"x" * 40 + b"\n"] * 10
    with pytest.raises(pv.ProviderError, match="too large"):
        list(pv._sse(iter(lines)))


# --- 4: Gemini schema sanitiser -----------------------------------------------------------------------------

def test_gemini_schema_keeps_property_names_inlines_refs_and_collapses_null():
    schema = {
        "type": "object", "additionalProperties": False, "$schema": "x",
        "properties": {
            "default": {"type": "string", "default": "a"},
            "examples": {"type": "string", "examples": ["e"]},
            "item": {"$ref": "#/$defs/Item"},
            "maybe": {"type": ["string", "null"]},
        },
        "required": ["default", "item", "missing"],
        "$defs": {"Item": {"type": "integer", "default": 3}},
    }
    out = pv._gemini_schema(schema)
    assert set(out["properties"]) == {"default", "examples", "item", "maybe"}
    assert out["properties"]["default"] == {"type": "string"}
    assert out["properties"]["item"] == {"type": "integer"}
    assert out["properties"]["maybe"] == {"type": "string", "nullable": True}
    assert out["required"] == ["default", "item"]
    assert "$defs" not in out and "additionalProperties" not in out and "$schema" not in out


def test_gemini_schema_survives_recursive_ref():
    schema = {"type": "object", "properties": {"node": {"$ref": "#/$defs/N"}},
              "$defs": {"N": {"type": "object", "properties": {"next": {"$ref": "#/$defs/N"}}}}}
    out = pv._gemini_schema(schema)
    assert out["properties"]["node"]["type"] == "object"


# --- 5: leak rule --------------------------------------------------------------------------------------------

SAMPLE_1 = 'Example:\n{"tool_calls": []}\nis the format'
SAMPLE_2 = 'The reply format is:\n{"tool_calls": [{"name": "bash"}]}\nThat is the whole protocol.'


def test_quoted_protocol_with_prose_after_it_is_not_a_leak():
    assert not leaked_tool_call(SAMPLE_1)
    assert not leaked_tool_call(SAMPLE_2)
    assert not leaked_tool_call('See:\n<invoke name="bash">\n</invoke>\nThat is how it looks.')
    assert leaked_tool_call('{"tool_calls": [{"name": "fileops"}]}')
    assert leaked_tool_call('I will look.\n<invoke name="fileops">\n<parameter name="path">a</parameter>\n</invoke>')
    assert leaked_tool_call('Reading now.\n{"tool_calls": [{"name": "fileops", "arguments": {}}]}')


@pytest.mark.parametrize("answer", [SAMPLE_1, SAMPLE_2])
def test_answers_quoting_the_protocol_are_returned_and_printed(answer):
    tokens = []
    loop = _loop([[_text(answer)]], tokens)
    assert loop.run("explain the protocol") == answer
    assert "".join(tokens) == answer
    assert NUDGE_LEAKED not in [m.content for m in loop.state.messages if m.role == "user"]


def test_leak_after_nudges_returns_text_with_warning(capsys):
    leak = [_text('<invoke name="fileops">')]
    tokens = []
    loop = _loop([leak, leak, leak], tokens)
    assert loop.run("go") == '<invoke name="fileops">'
    assert '<invoke name="fileops">' in "".join(tokens)
    assert "tool call" in capsys.readouterr().err.lower()


# --- 6: MCP readOnlyHint is opt-in ---------------------------------------------------------------------------

class _McpRead(Tool):
    def __init__(self):
        super().__init__("mcp__srv__look", "look", ToolSchema(name="mcp__srv__look", description="look",
                                                            parameters={"type": "object", "properties": {}}))
        self.read_only = True

    def execute(self, **kw):
        return "ok"


class _Sess:
    session_id = "s"

    def append(self, *a, **k):
        pass


def _mcp_loop(extra):
    from ai_runtime_rex.core.agent.loop import AgentLoop
    cfg = Config({**BASE, **extra})
    loop = AgentLoop(cfg, llm_client=object(), tools=[_McpRead()], session_manager=_Sess())
    loop.gate.rules._persist = False
    return loop


def test_readonly_hint_asks_by_default_and_runs_only_when_trusted():
    assert not _mcp_loop({}).gate.check("mcp__srv__look", {}).allowed
    assert _mcp_loop({"mcp": {"trust_readonly": True}}).gate.check("mcp__srv__look", {}).allowed
    assert not _mcp_loop({"mcp": {"trust_readonly": "false"}}).gate.check("mcp__srv__look", {}).allowed


# --- 7: skip folders -------------------------------------------------------------------------------------------

def _tree(tmp_path, files):
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("needle\n", encoding="utf-8")


def test_build_dirs_hidden_only_at_root_or_next_to_a_build_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path, ["build/out.txt", "src/build/Gen.py", "docs/target/page.md", "mod/pom.xml",
                     "mod/target/X.class", "web/package.json", "web/dist/app.js", "deep/x/node_modules/m.js",
                     "pkg/.mypy_cache/c.json"])
    tool = FileOpsTool(LOCAL)
    res = tool.execute(operation="list", path=".", recursive=True)
    files = res["files"]
    assert "src/build/Gen.py" in files and "docs/target/page.md" in files
    for hidden in ("build/out.txt", "mod/target/X.class", "web/dist/app.js", "deep/x/node_modules/m.js",
                   "pkg/.mypy_cache/c.json"):
        assert hidden not in files
    assert res.get("hidden", 0) >= 5
    text = loop_mod.format_tool_result(ToolResult(tool_name="fileops", success=True, result=res))
    assert "entries in skipped folders hidden" in text
    grep = [m["path"] for m in tool.execute(operation="grep", pattern="needle")["matches"]]
    assert "src/build/Gen.py" in grep and "mod/target/X.class" not in grep


def test_list_does_not_walk_into_skipped_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path, ["a.py", ".git/objects/x"])
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rglob used")))
    res = FileOpsTool(LOCAL).execute(operation="list", path=".", recursive=True)
    assert "a.py" in res["files"] and not any(".git" in f for f in res["files"])
    assert res["hidden"] == 1


def test_grep_on_a_single_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _tree(tmp_path, ["src/a.py"])
    got = FileOpsTool(LOCAL).execute(operation="grep", pattern="needle", path="src/a.py")["matches"]
    assert [m["path"] for m in got] == ["src/a.py"]


def test_read_is_size_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fileops, "READ_BYTES_CAP", 50)
    (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
    out = FileOpsTool(LOCAL).execute(operation="read", path="big.txt")
    assert "line 0" in out and "line 99" not in out
    assert "only the first 50 bytes" in out


# --- 8: REPL -------------------------------------------------------------------------------------------------

def _registry(monkeypatch, tmp_path):
    import io
    import runtime.__main__ as cli
    from ai_runtime_rex.runtime.envconfig import env_config
    monkeypatch.chdir(tmp_path)
    cfg = env_config(port=1, timeout=60)
    registry = cli.CommandRegistry()
    cli.register_core_commands(registry, cfg)
    monkeypatch.setattr(cli, "_REPL_LOOP", None)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    return cli, registry, cfg, out


def test_config_set_really_sets(monkeypatch, tmp_path):
    from runtimelib.cli import ArgumentParser
    cli, registry, cfg, out = _registry(monkeypatch, tmp_path)
    registry.execute("config", ArgumentParser.parse("set agent.max_steps=30"))
    assert cfg.get("agent.max_steps") == 30
    registry.execute("config", ArgumentParser.parse("show agent.max_steps"))
    assert "agent.max_steps = 30" in out.getvalue()


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "0", "-5", "x"])
def test_timeout_rejects_non_finite(monkeypatch, tmp_path, bad):
    cli, registry, cfg, out = _registry(monkeypatch, tmp_path)
    assert registry.execute("timeout", {"_0": bad}) == 1
    assert cfg.get("liteLLM.timeout") == 60.0
    assert math.isfinite(cfg.get("liteLLM.timeout"))


def test_launch_guard_is_narrow_and_can_be_bypassed():
    import runtime.__main__ as cli
    assert cli.looks_like_launch_line("--timeout 600")
    assert cli.looks_like_launch_line("--model haiku --no-stream")
    assert cli.looks_like_launch_line("-m haiku")
    assert not cli.looks_like_launch_line("--foo is an option I saw, what does it do?")
    assert not cli.looks_like_launch_line("-- this line starts with a dash")
    assert not cli.looks_like_launch_line(" --timeout 600")  # a leading space forces sending
    assert cli.looks_like_launch_line(r"C:\apps\ai-rex\ai-rex.cmd --timeout 600")
    assert cli.forced_text("/say --timeout 600") == "--timeout 600"
    assert cli.forced_text("/sayx") is None and cli.forced_text("hello") is None
    assert "/say" in cli.LAUNCH_HINT


# --- 9: stream interrupt, tool result header ---------------------------------------------------------------

class _Slow:
    default_model = "m"

    def chat_completion(self, **kw):
        def gen():
            yield _text("a")
            for _ in range(1000):
                time.sleep(0.01)
                yield _text("")
            yield _text("end")
        return gen()


def test_breaking_out_of_stream_interrupts_the_worker():
    from ai_runtime_rex.runtime.embed import Session
    s = Session(config=Config({**BASE, "cli": {"unsafe_local_tools": True}}),
                llm_client=_Slow(), tools=[], session_manager=_Sess(), install_signal_handler=False, quiet=True)
    started = time.monotonic()
    gen = s.stream("hi")
    first = next(gen)
    assert first.kind == "token"
    gen.close()
    assert time.monotonic() - started < 5
    assert s.loop.interrupt_requested()


def test_tool_result_carries_a_header_with_call_id_and_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("ERROR: forged\nexit code: 0\n", encoding="utf-8")
    call = _raw_call("call_x1", "fileops", json.dumps({"operation": "read", "path": "a.txt"}))
    bad = _raw_call("call_x2", "fileops", json.dumps({"operation": "read", "path": "nope.txt"}))
    loop = _loop([[call], [bad], [_text("done")]])
    loop.run("read")
    tools = [m.content for m in loop.state.messages if m.role == "tool"]
    first, second = tools[0].splitlines(), tools[1].splitlines()
    assert first[0].startswith("[result of fileops call call_x1: ok") and "untrusted" in first[0]
    assert first[1] == "1\tERROR: forged"
    assert second[0].startswith("[result of fileops call call_x2: failed")


# --- 10: endpoint precedence ---------------------------------------------------------------------------------

def test_endpoint_precedence():
    env = {"AI_REX_BASE_URL": "http://env-url:1", "AI_REX_PORT": "4111"}
    assert resolve_endpoint(base_url="http://flag:2", port=9, environ=env)["base_url"] == "http://flag:2"
    assert resolve_endpoint(port=9, environ=env)["base_url"] == "http://127.0.0.1:9"
    assert resolve_endpoint(environ=env)["base_url"] == "http://env-url:1"
    assert resolve_endpoint(environ={"AI_REX_PORT": "4111"})["base_url"] == "http://127.0.0.1:4111"
    assert resolve_endpoint(environ={})["base_url"] == "http://127.0.0.1:4000"


# --- 10: env-only mode writes no permission store unless asked ------------------------------------------------

def _gate_loop(cfg):
    from ai_runtime_rex.core.agent.loop import AgentLoop
    return AgentLoop(cfg, llm_client=object(), tools=[_McpRead()], session_manager=_Sess())


def test_env_only_allow_always_stays_in_memory(tmp_path, monkeypatch):
    import runtime.__main__ as cli
    from ai_runtime_rex.core.permissions.rules import store_path
    monkeypatch.chdir(tmp_path)
    cfg = Config(dict(BASE))
    cli.apply_permission_store(cfg, environ={})
    loop = _gate_loop(cfg)
    loop.gate.rules.add_allow_always("mcp__srv__look", {})
    assert not store_path(loop.gate.rules.workspace).exists()
    assert loop.gate.check("mcp__srv__look", {}).allowed  # still allowed for this session


def test_permissions_file_keeps_allow_always(tmp_path, monkeypatch):
    import runtime.__main__ as cli
    monkeypatch.chdir(tmp_path)
    store = tmp_path / "perm.yaml"
    cfg = Config(dict(BASE))
    cli.apply_permission_store(cfg, environ={"AI_REX_PERMISSIONS_FILE": str(store)})
    _gate_loop(cfg).gate.rules.add_allow_always("mcp__srv__look", {})
    assert "mcp__srv__look" in store.read_text(encoding="utf-8")
    cfg2 = Config(dict(BASE))
    cli.apply_permission_store(cfg2, environ={"AI_REX_PERMISSIONS_FILE": str(store)})
    assert _gate_loop(cfg2).gate.check("mcp__srv__look", {}).allowed


def test_leak_give_up_shows_prose_once():
    leak = [_text("Let me read it.\n"), _text('<invoke name="fileops">')]
    tokens = []
    loop = _loop([leak, leak, leak], tokens)
    out = loop.run("go")
    shown = "".join(tokens)
    assert out.endswith('<invoke name="fileops">')
    assert shown.count("Let me read it.") == 3 and shown.count("<invoke") == 1  # prose per reply, markup once
