# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# Licensed under MIT License
"""``--mcp-config`` / ``--mcp`` / ``--no-mcp`` / ``--mcp-allow``: parsing, gate, cleanup, timeout, /mcp, end to end."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.mcp import HttpTransport, MCPClient
from runtimelib.mcp.cliconfig import McpConfigError, allow_rule, apply, parse_file, parse_inline
from runtimelib.tools.mcp_tool import load_mcp_tools

ROOT = Path(__file__).resolve().parents[1]
TINY = str(ROOT / "tests" / "fixtures" / "mcp_tiny_server.py")
BASE = {"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}}


def cfg(**extra) -> Config:
    data = json.loads(json.dumps(BASE))
    data.update(extra)
    return Config(data)


def write_json(path: Path, obj) -> str:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def tiny_file(tmp_path: Path, name="tiny", **extra) -> str:
    entry = {"command": sys.executable, "args": ["-u", TINY], **extra}
    return write_json(tmp_path / f"{name}.json", {"mcpServers": {name: entry}})


class _Session:
    session_id = "s1"

    def append(self, *a, **k):
        pass


def make_loop(config: Config) -> AgentLoop:
    return AgentLoop(config, llm_client=object(), tool_names=["mcp__*"], session_manager=_Session())


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ------------------------------------------------------------------ parsing

def test_parse_file_claude_code_shape(tmp_path, monkeypatch):
    path = write_json(tmp_path / "m.json", {"mcpServers": {
        "a": {"command": "prog", "args": ["x", "${HOME_X}"], "env": {"K": "v"}},
        "b": {"type": "stdio", "command": "q"},
        "c": {"type": "http", "url": "https://h/mcp", "headers": {"Authorization": "Bearer ${TOK:-none}"}},
        "d": {"url": "http://h/mcp"},
        "e": {"command": "z", "disabled": True},
    }})
    got = parse_file(path, {"HOME_X": "/h"})
    assert got["a"] == {"command": ["prog", "x", "/h"], "env": {"K": "v"}}
    assert got["b"] == {"command": ["q"]}
    assert got["c"] == {"url": "https://h/mcp", "type": "http", "headers": {"Authorization": "Bearer none"}}
    assert got["d"]["type"] == "http"
    assert got["e"]["enabled"] is False


@pytest.mark.parametrize("content,needle", [
    ("{not json", "not valid JSON"),
    ('{"x": 1}', "mcpServers"),
    ('{"mcpServers": {"a": {"type": "sse", "url": "http://h"}}}', "not supported"),
    ('{"mcpServers": {"a": {}}}', "no command or url"),
    ('{"mcpServers": {"a": {"command": "p", "args": "x"}}}', "args must be a list"),
])
def test_parse_file_errors(tmp_path, content, needle):
    p = tmp_path / "bad.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(McpConfigError, match=needle):
        parse_file(p, {})


def test_parse_file_missing(tmp_path):
    with pytest.raises(McpConfigError, match="cannot read"):
        parse_file(tmp_path / "nope.json", {})


def test_parse_inline_quotes_no_shell():
    name, raw = parse_inline('demo=python "my server.py" --flag "a b"')
    assert name == "demo"
    assert raw == {"command": ["python", "my server.py", "--flag", "a b"]}
    assert parse_inline("web=https://h/mcp") == ("web", {"type": "http", "url": "https://h/mcp"})
    # shell metacharacters stay plain words
    assert parse_inline("x=prog ; rm -rf /")[1]["command"][:2] == ["prog", ";"]


@pytest.mark.parametrize("spec", ["nocommand", "=cmd", "n=", "bad__name=cmd", "bad name=cmd", 'n=cmd "unclosed'])
def test_parse_inline_errors(spec):
    with pytest.raises(McpConfigError):
        parse_inline(spec)


def test_allow_rule_forms():
    assert allow_rule("demo") == "mcp__demo__*"
    assert allow_rule("demo__greet") == "mcp__demo__greet"
    assert allow_rule("demo__read_*") == "mcp__demo__read_*"
    assert allow_rule("mcp__demo__greet") == "mcp__demo__greet"


# --------------------------------------------------------------- precedence

def test_precedence_env_then_file_then_inline_and_no_mcp(tmp_path):
    env_file = write_json(tmp_path / "e.json", {"mcpServers": {"s": {"command": "env"}, "only_env": {"command": "e2"}}})
    flag_file = write_json(tmp_path / "f.json", {"mcpServers": {"s": {"command": "file"}, "only_file": {"command": "f"}}})
    environ = {"AI_REX_MCP_CONFIG": os.pathsep.join([env_file, str(tmp_path / "missing.json")])}
    warns: list[str] = []

    c = cfg(mcp={"servers": {"from_config": {"command": ["c"]}}})
    names = apply(c, [flag_file], ["s=inline arg"], environ=environ, warn=warns.append)
    servers = c.get("mcp.servers")
    assert servers["s"]["command"] == ["inline", "arg"]  # inline beats file beats env
    assert set(names) == {"s", "only_env", "only_file", "from_config"}
    assert len(warns) == 1 and "missing.json" in warns[0]  # a bad env path warns only

    c2 = cfg()
    apply(c2, [flag_file], environ=environ, warn=warns.append)
    assert c2.get("mcp.servers")["s"]["command"] == ["file"]

    c3 = cfg(mcp={"servers": {"from_config": {"command": ["c"]}}})
    assert apply(c3, [flag_file], ["x=y"], no_mcp=True, allow=["a"], environ=environ) == []
    assert c3.get("mcp.servers") == {}


def test_bad_flag_file_raises_but_env_value_only_warns(tmp_path):
    with pytest.raises(McpConfigError):
        apply(cfg(), [str(tmp_path / "none.json")], environ={})


def test_startup_timeout_env_and_allow_rules(tmp_path):
    c = cfg(permissions={"allow": ["fileops(read:*)"]})
    apply(c, [], ["a=prog"], allow=["a__greet"], environ={"AI_REX_MCP_STARTUP_TIMEOUT": "12"})
    assert c.get("mcp.servers")["a"]["startup_timeout"] == 12.0
    assert c.get("permissions.allow") == ["fileops(read:*)", "mcp__a__greet"]


# ---------------------------------------------------- real server: gate etc.

def test_tools_gate_readonly_allowed_others_ask_and_noninteractive_denies(tmp_path):
    c = cfg()
    apply(c, [tiny_file(tmp_path)], environ={})
    loop = make_loop(c)
    try:
        assert {"mcp__tiny__greet", "mcp__tiny__save", "mcp__tiny__dirty"} <= set(loop.tools)
        assert "bash" not in loop.tools
        loop.gate.rules._persist = False
        assert loop.gate.check("mcp__tiny__greet", {"name": "x"}).allowed  # server says readOnlyHint
        denied = loop.gate.check("mcp__tiny__save", {"note": "n"})  # no asker -> ask is denied
        assert not denied.allowed
        out = loop.tools["mcp__tiny__greet"].execute(name="Ann")
        assert out == "hello Ann"
        assert loop.tools["mcp__tiny__dirty"].execute() == "abc"  # control and bidi characters removed
    finally:
        loop.close_mcp()


def test_mcp_allow_flag_lets_a_write_tool_run(tmp_path):
    c = cfg()
    apply(c, [tiny_file(tmp_path)], allow=["tiny__save"], environ={})
    loop = make_loop(c)
    try:
        assert loop.gate.check("mcp__tiny__save", {"note": "n"}).allowed
    finally:
        loop.close_mcp()


def test_deny_rule_beats_readonly_hint(tmp_path):
    c = cfg(permissions={"deny": ["mcp__tiny__greet"]})
    apply(c, [tiny_file(tmp_path)], environ={})
    loop = make_loop(c)
    try:
        assert not loop.gate.check("mcp__tiny__greet", {}).allowed
    finally:
        loop.close_mcp()


def test_child_process_ends_with_close_mcp(tmp_path):
    pidfile = tmp_path / "pid"
    c = cfg()
    apply(c, [tiny_file(tmp_path, env={"MCP_TINY_PIDFILE": str(pidfile)})], environ={})
    loop = make_loop(c)
    pid = int(pidfile.read_text())
    assert alive(pid)
    loop.close_mcp()
    deadline = time.time() + 5
    while alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not alive(pid)


def test_slow_server_times_out_with_one_line_and_session_survives(tmp_path, capsys):
    c = cfg()
    path = write_json(tmp_path / "m.json", {"mcpServers": {
        "slow": {"command": sys.executable, "args": ["-u", TINY], "env": {"MCP_TINY_INIT_DELAY": "5"},
                 "startupTimeout": 0.5},
        "tiny": {"command": sys.executable, "args": ["-u", TINY]},
    }})
    apply(c, [path], environ={})
    started = time.time()
    loop = make_loop(c)
    try:
        assert time.time() - started < 4
        err = capsys.readouterr().err.strip().splitlines()
        assert len(err) == 1 and err[0].startswith("ai-rex: MCP server 'slow' skipped:") and "timed out" in err[0]
        assert "mcp__tiny__greet" in loop.tools and not any(t.startswith("mcp__slow") for t in loop.tools)
    finally:
        loop.close_mcp()


def test_missing_command_does_not_kill_session(tmp_path, capsys):
    c = cfg()
    apply(c, [], ["ghost=no-such-program-xyz", f"tiny={sys.executable} -u {TINY}"], environ={})
    loop = make_loop(c)
    try:
        err = capsys.readouterr().err
        assert "MCP server 'ghost' skipped" in err and "not found" in err
        assert "mcp__tiny__greet" in loop.tools
    finally:
        loop.close_mcp()


def test_no_mcp_starts_nothing(tmp_path):
    pidfile = tmp_path / "pid"
    c = cfg()
    apply(c, [tiny_file(tmp_path, env={"MCP_TINY_PIDFILE": str(pidfile)})], no_mcp=True, environ={})
    loop = make_loop(c)
    assert not any(t.startswith("mcp__") for t in loop.tools)
    assert not pidfile.exists()


def test_mcp_status_lines(tmp_path):
    import runtime.__main__ as main
    c = cfg()
    assert "No MCP servers" in main.mcp_status_lines(c, None)[0]
    apply(c, [tiny_file(tmp_path)], environ={})
    assert "start with the first message" in main.mcp_status_lines(c, None)[0]
    loop = make_loop(c)
    try:
        text = "\n".join(main.mcp_status_lines(c, loop))
        assert "tiny: connected, 3 tool(s)" in text and "mcp__tiny__greet [read-only]" in text
    finally:
        loop.close_mcp()


# ------------------------------------------------------------- HTTP transport

class _McpHttp(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append((msg.get("method"), self.headers.get("Authorization"), self.headers.get("Mcp-Session-Id")))
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "h", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "ping", "description": "p", "inputSchema": {"type": "object"},
                                 "annotations": {"readOnlyHint": True}}]}
        else:
            result = {"content": [{"type": "text", "text": "pong"}]}
        payload = {"jsonrpc": "2.0", "id": mid, "result": result}
        if method == "tools/call":  # answer this one as an SSE stream
            body = ("event: message\ndata: " + json.dumps(payload) + "\n\n").encode()
            ctype = "text/event-stream"
        else:
            body, ctype = json.dumps(payload).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", "sess-1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        type(self).seen.append(("DELETE", None, self.headers.get("Mcp-Session-Id")))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_http_server_via_config():
    _McpHttp.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHttp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
        c = cfg()
        apply(c, [], [f"web={url}"], environ={})
        c.get("mcp.servers")["web"]["headers"] = {"Authorization": "Bearer t"}
        tools, manager = load_mcp_tools(c, None)
        try:
            assert [t.name for t in tools] == ["mcp__web__ping"] and tools[0].read_only
            assert tools[0].execute() == "pong"  # SSE reply
        finally:
            manager.disconnect_all()
        assert ("DELETE", None, "sess-1") in _McpHttp.seen
        assert ("initialize", "Bearer t", None) in _McpHttp.seen
        assert ("tools/list", "Bearer t", "sess-1") in _McpHttp.seen
    finally:
        server.shutdown()


def test_http_transport_rejects_non_http_url():
    with pytest.raises(ValueError):
        HttpTransport("file:///etc/passwd")
    assert MCPClient  # imported for the type only


def test_http_server_down_is_one_line(capsys):
    c = cfg()
    apply(c, [], ["web=http://127.0.0.1:9/mcp"], environ={})
    tools, manager = load_mcp_tools(c, None)
    assert tools == []
    assert capsys.readouterr().err.startswith("ai-rex: MCP server 'web' skipped:")


# ----------------------------------------------- end to end: CLI + stub model

class _Model(BaseHTTPRequestHandler):
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        tools = [t["function"]["name"] for t in body.get("tools") or []]
        last = body["messages"][-1]
        if not tools:
            msg = {"role": "assistant", "content": "plain answer"}
            finish = "stop"
        elif last["role"] == "tool":
            msg = {"role": "assistant", "content": "The tool said: " + str(last["content"])}
            finish = "stop"
        else:
            tool = next((n for n in tools if n.endswith("__" + os.environ.get("STUB_TOOL", "greet"))), None)
            assert tool, tools
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": tool, "arguments": json.dumps({"name": "Ann", "note": "n"})}}]}
            finish = "tool_calls"
        if body.get("stream"):
            delta = dict(msg)
            if "tool_calls" in delta:
                delta["tool_calls"] = [dict(tc, index=0) for tc in delta["tool_calls"]]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta}]})}\n\n".encode())
            end = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
            self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
        else:
            data = json.dumps({"choices": [{"message": msg, "finish_reason": finish}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


@pytest.fixture
def model():
    _Model.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], _Model.bodies
    server.shutdown()


def cli(args, port, cwd, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AI_REX_", "RUNTIME_", "LITELLM_", "STUB_"))}
    env.update(PYTHONPATH=str(ROOT), AI_REX_PORT=str(port), **(extra_env or {}))
    return subprocess.run([sys.executable, "-P", "-m", "runtime", *args], input="", text=True,
                          capture_output=True, env=env, cwd=cwd, timeout=60)


def test_cli_oneshot_no_tools_still_uses_mcp_config(model, tmp_path):
    port, bodies = model
    r = cli(["-i", "greet Ann", "--no-tools", "--non-interactive", "--mcp-config", tiny_file(tmp_path)], port, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("The tool said:") and "hello Ann" in r.stdout
    assert [t["function"]["name"] for t in bodies[0]["tools"]] == ["mcp__tiny__greet", "mcp__tiny__save",
                                                                   "mcp__tiny__dirty"]


def test_cli_non_interactive_denies_ask_tool_and_allow_flag_permits(model, tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_TOOL", "save")  # read by the stub model in this process
    port, bodies = model
    out = tmp_path / "notes.txt"
    envs = {"MCP_TINY_OUT": str(out)}
    args = ["-i", "save it", "--no-tools", "--non-interactive", "--mcp", f"tiny={sys.executable} -u {TINY}"]
    r = cli(args, port, tmp_path, envs)
    assert r.returncode == 3, (r.returncode, r.stderr)  # denied
    assert not out.exists()
    r = cli(args + ["--mcp-allow", "tiny__save"], port, tmp_path, envs)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == "n\n"


def test_cli_no_mcp_wins_over_env_and_flags(model, tmp_path):
    port, bodies = model
    r = cli(["-i", "hi", "--no-tools", "--no-mcp", "--mcp-config", tiny_file(tmp_path)], port, tmp_path,
            {"AI_REX_MCP_CONFIG": tiny_file(tmp_path, "other")})
    assert r.returncode == 0, r.stderr
    assert "tools" not in bodies[0]


def test_cli_env_config_and_bad_flag(model, tmp_path):
    port, bodies = model
    r = cli(["-i", "greet", "--no-tools", "--non-interactive"], port, tmp_path,
            {"AI_REX_MCP_CONFIG": tiny_file(tmp_path)})
    assert r.returncode == 0 and "hello Ann" in r.stdout, r.stderr
    r = cli(["-i", "x", "--mcp-config", str(tmp_path / "nope.json")], port, tmp_path)
    assert r.returncode == 2 and r.stderr.startswith("ai-rex: cannot read MCP config")
    r = cli(["-i", "x", "--mcp", "broken"], port, tmp_path)
    assert r.returncode == 2 and "NAME=COMMAND" in r.stderr


def test_cli_no_orphan_after_oneshot(model, tmp_path):
    port, _ = model
    pidfile = tmp_path / "pid"
    r = cli(["-i", "greet", "--no-tools", "--non-interactive", "--mcp-config",
             tiny_file(tmp_path, env={"MCP_TINY_PIDFILE": str(pidfile)})], port, tmp_path)
    assert r.returncode == 0, r.stderr
    pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not alive(pid)
