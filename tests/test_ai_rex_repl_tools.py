# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""Env-only ``ai-rex`` (plain REPL and one-shot) must give the model the coding tools, and they must run.

Owner's live test 2026-11-28: the plain REPL without Docker answered "fileops/bash are not available" because
every tool call went to the absent Docker guard. Without Docker the env-only modes now use local tools behind
the permission gate (reads allowed, writes/commands ask; denied when nobody can answer).
"""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import runtime.__main__ as cli
from ai_runtime_rex.runtime.envconfig import env_config
from runtime.oneshot import Options, run_oneshot


class _ToolStub(BaseHTTPRequestHandler):
    """First answer: a fileops read of a.txt (when tools are offered); then the text of the tool result."""

    bodies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        results = [m for m in body["messages"] if m.get("role") == "tool"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if body.get("tools") and not results:
            call = {"index": 0, "id": "c1", "type": "function",
                    "function": {"name": "fileops", "arguments": json.dumps({"operation": "read", "path": "a.txt"})}}
            chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        else:
            text = "RESULT:" + (str(results[-1]["content"]) if results else "none")
            chunks = [{"choices": [{"index": 0, "delta": {"content": text}}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def stub(tmp_path, monkeypatch):
    _ToolStub.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ToolStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("SECRET-CONTENT-42", encoding="utf-8")
    for k in list(__import__("os").environ):
        if k.startswith(("AI_REX_", "RUNTIME_", "LITELLM_")):
            monkeypatch.delenv(k)
    yield server.server_address[1], _ToolStub.bodies
    server.shutdown()


def _tool_names(body):
    return {t["function"]["name"] for t in body.get("tools") or []}


def test_tool_mode_without_docker_is_local_behind_the_gate():
    cfg = env_config(port=1)
    note = cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=False)
    assert cfg.get("cli.unsafe_local_tools") is True and note and "Docker" in note
    from runtimelib.agent.gate import resolve_mode
    assert resolve_mode(cfg) == "local"  # local mode: writes / commands ask


def test_tool_mode_keeps_guard_with_docker_or_explicit_choice(monkeypatch):
    cfg = env_config(port=1)
    assert cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=True) is None
    assert not cfg.get("cli.unsafe_local_tools")
    monkeypatch.setenv("AI_REX_CLI__UNSAFE_LOCAL_TOOLS", "false")  # explicit opt-out wins over the fallback
    cfg = env_config(port=1)
    assert cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=False) is None
    assert cfg.get("cli.unsafe_local_tools") is False
    cfg = env_config(port=1)  # file-config / bridge modes are untouched
    cfg.set("cli.unsafe_local_tools", None)
    assert cli.apply_tool_mode(cfg, flag=False, env_only=False, docker=False) is None


def test_plain_repl_chat_sends_coding_tools_and_runs_them(stub, monkeypatch):
    port, bodies = stub
    cfg = env_config(port=port)
    cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=False)
    registry = cli.CommandRegistry()
    cli.register_core_commands(registry, cfg)
    monkeypatch.setattr(cli, "_REPL_LOOP", None)
    monkeypatch.setattr(cli, "_ASKER", None)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert registry.execute("chat", {"prompt": "read a.txt"}) == 0
    names = _tool_names(bodies[0])
    assert {"fileops", "bash", "edit"} <= names, names
    assert "SECRET-CONTENT-42" in json.dumps(bodies[-1]["messages"])  # the read really ran
    assert "RESULT:" in out.getvalue()


def test_oneshot_tools_on_by_default_and_off_with_no_tools(stub):
    port, bodies = stub
    cfg = env_config(port=port)
    cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=False)
    out, err = io.StringIO(), io.StringIO()
    rc = run_oneshot(cfg, Options(text="read a.txt"), stdin=io.StringIO(""), stdout=out, stderr=err)
    assert rc == 0, err.getvalue()
    assert {"fileops", "bash"} <= _tool_names(bodies[0])
    assert "SECRET-CONTENT-42" in out.getvalue()
    bodies.clear()
    rc = run_oneshot(cfg, Options(text="hi", no_tools=True), stdin=io.StringIO(""), stdout=io.StringIO(),
                     stderr=io.StringIO())
    assert rc == 0 and "tools" not in bodies[0]


def test_repl_uses_the_native_provider_adapter(tmp_path, monkeypatch):
    """The REPL must build the anthropic/gemini adapter like one-shot does (it used the OpenAI client)."""
    from ai_runtime_rex.runtime.embed import GatewayLLM

    calls = []

    def fake_chat(body):
        calls.append(body)
        chunks = iter([{"choices": [{"index": 0, "delta": {"content": "NATIVE"}, "finish_reason": "stop"}]}])
        return chunks if body.get("stream") else {"choices": [{"message": {"role": "assistant", "content": "NATIVE"},
                                                               "finish_reason": "stop"}]}

    monkeypatch.setattr("ai_runtime_rex.runtime.providers.make_llm", lambda config: GatewayLLM(fake_chat, "m"))
    monkeypatch.chdir(tmp_path)
    cfg = env_config(provider="anthropic", api_key="k")
    registry = cli.CommandRegistry()
    cli.register_core_commands(registry, cfg)
    monkeypatch.setattr(cli, "_REPL_LOOP", None)
    monkeypatch.setattr("sys.stdout", io.StringIO())
    assert registry.execute("chat", {"prompt": "hi"}) == 0
    assert calls, "the REPL did not use the provider adapter"


def test_env_section_override_reaches_the_engine(monkeypatch):
    """Documented: AI_REX_AGENT__MAX_STEPS=30 sets agent.max_steps (the section name was left upper-case)."""
    monkeypatch.setenv("AI_REX_AGENT__MAX_STEPS", "30")
    assert env_config(port=1).get("agent.max_steps") == 30


class _WriteStub(_ToolStub):
    """Asks for a fileops write (needs permission in local mode), then answers."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        done = any(m.get("role") == "tool" for m in body["messages"])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if not done:
            args = {"operation": "write", "path": "w.txt", "content": "pwned"}
            call = {"index": 0, "id": "c1", "type": "function",
                    "function": {"name": "fileops", "arguments": json.dumps(args)}}
            chunks = [{"choices": [{"index": 0, "delta": {"tool_calls": [call]}}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}]
        else:
            chunks = [{"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}]
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def test_non_interactive_denies_writes_exit_3(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WriteStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.chdir(tmp_path)
    try:
        cfg = env_config(port=server.server_address[1])
        cli.apply_tool_mode(cfg, flag=False, env_only=True, docker=False)
        out = io.StringIO()
        rc = run_oneshot(cfg, Options(text="write", as_json=True, asker=None), stdin=io.StringIO(""), stdout=out,
                         stderr=io.StringIO())
    finally:
        server.shutdown()
    data = json.loads(out.getvalue())
    assert rc == 3 and data["denied"] and not (tmp_path / "w.txt").exists()


def test_bad_input_file_encoding_is_usage_error(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"caf\xe9 \xff")
    err = io.StringIO()
    rc = run_oneshot(env_config(port=1), Options(input_file=str(bad)), stdin=io.StringIO(""), stdout=io.StringIO(),
                     stderr=err)
    assert rc == 2 and "UTF-8" in err.getvalue() and "Traceback" not in err.getvalue()


def test_failed_run_leaves_the_output_file_alone(tmp_path):
    out = tmp_path / "o.txt"
    out.write_text("KEEP", encoding="utf-8")
    rc = run_oneshot(env_config(port=1), Options(text="x", no_tools=True, output=str(out)),
                     stdin=io.StringIO(""), stdout=io.StringIO(), stderr=io.StringIO())
    assert rc == 1 and out.read_text(encoding="utf-8") == "KEEP"
    missing = tmp_path / "never.txt"
    run_oneshot(env_config(port=1), Options(text="x", no_tools=True, output=str(missing)),
                stdin=io.StringIO(""), stdout=io.StringIO(), stderr=io.StringIO())
    assert not missing.exists()


def test_permission_prompt_goes_to_stderr_not_stdout(monkeypatch):
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    asker = cli.TerminalAsker(out=err)

    class Req:
        tool, summary, detail, reason = "bash", "echo hi", "", ""

    assert asker.ask(Req()) == "allow_once"
    assert out.getvalue() == "" and "Allow?" in err.getvalue()
