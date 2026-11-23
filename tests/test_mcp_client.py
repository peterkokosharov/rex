# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""MCP client tests against a tiny fake stdio server (tests/fixtures/mcp_echo_server.py)."""

import json
import sys
import time
from pathlib import Path

import pytest

from runtimelib.mcp import (
    MCPClient,
    MCPClientManager,
    MCPServerConfig,
    StdioTransport,
    tool_result_text,
)

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")


@pytest.fixture
def client():
    c = MCPClient(StdioTransport([sys.executable, FIXTURE]), timeout=10)
    c.initialize()
    yield c
    c.close()


def call(c, _tool, **args):
    return tool_result_text(c.call_tool(_tool, args))


def test_handshake_order_and_paging(client):
    names = [t.name for t in client.list_tools()]
    assert names == ["echo", "fail", "slow", "die", "spam", "log", "env", "pingstate"]
    seen, _ = call(client, "log")
    assert seen.split(",")[:3] == ["initialize", "notifications/initialized", "tools/list"]
    # second page was requested with the cursor
    assert seen.split(",").count("tools/list") == 2
    assert client._tools[0].input_schema["type"] == "object"


def test_echo_and_is_error(client):
    assert call(client, "echo", text="hi") == ("hi", False)
    assert call(client, "fail") == ("boom", True)


def test_server_ping_is_answered(client):
    deadline = time.time() + 5
    state = {}
    while time.time() < deadline and not state:
        state = json.loads(call(client, "pingstate")[0])
        time.sleep(0.05)
    assert state["result"] == {}


def test_unknown_method_error_raises(client):
    with pytest.raises(RuntimeError, match="no such method"):
        client.transport.call("nope/nope", {}, timeout=5)


def test_stderr_spam_does_not_block(client):
    assert call(client, "spam") == ("spammed", False)
    deadline = time.time() + 5
    while time.time() < deadline and "spam line 1999" not in client.transport.stderr_tail():
        time.sleep(0.05)
    tail = client.transport.stderr_tail().splitlines()
    assert len(tail) == 50
    assert "spam line 1999" in tail[-1]
    assert call(client, "echo", text="still alive") == ("still alive", False)


def test_env_merged_over_environ(monkeypatch):
    monkeypatch.setenv("MCP_TEST_BASE", "base")
    t = StdioTransport([sys.executable, FIXTURE], env={"MCP_TEST_EXTRA": "extra"})
    c = MCPClient(t, timeout=10)
    c.initialize()
    try:
        assert call(c, "env", name="MCP_TEST_EXTRA")[0] == "extra"
        assert call(c, "env", name="MCP_TEST_BASE")[0] == "base"
        assert call(c, "env", name="PATH")[0] != "<unset>"
    finally:
        c.close()


def test_timeout_then_still_usable():
    c = MCPClient(StdioTransport([sys.executable, FIXTURE]), timeout=0.5)
    c.initialize()
    try:
        started = time.time()
        with pytest.raises(TimeoutError):
            c.call_tool("slow", {"seconds": 3})
        assert time.time() - started < 2.5
    finally:
        c.close()


def test_process_death_fails_fast(client):
    started = time.time()
    with pytest.raises(RuntimeError, match="exited"):
        client.call_tool("die", {})
    assert time.time() - started < 5  # far below the 10 s request timeout
    with pytest.raises(RuntimeError):
        client.call_tool("echo", {"text": "x"})


def test_command_not_found():
    with pytest.raises(FileNotFoundError):
        StdioTransport(["definitely-not-a-command-xyz"])


def test_manager_connect_and_tools():
    mgr = MCPClientManager()
    mgr.add_server(MCPServerConfig(name="fx", command=[sys.executable], args=[FIXTURE]))
    c = mgr.connect("fx")
    try:
        assert mgr.connect("fx") is c
        c.list_tools()
        assert len(mgr.get_all_tools()["fx"]) == 8
    finally:
        mgr.disconnect_all()
    assert mgr.get_client("fx") is None


def test_manager_failed_start_reports_stderr(monkeypatch):
    monkeypatch.setenv("MCP_FIXTURE_FAIL_INIT", "1")
    mgr = MCPClientManager()
    mgr.add_server(MCPServerConfig(name="bad", command=[sys.executable, FIXTURE], init_timeout=5))
    with pytest.raises(RuntimeError, match="failed to start"):
        mgr.connect("bad")
    assert mgr.get_client("bad") is None


def test_manager_rejects_non_stdio():
    mgr = MCPClientManager()
    mgr.add_server(MCPServerConfig(name="s", command=[], transport="sse", url="http://x"))
    with pytest.raises(NotImplementedError):
        mgr.connect("s")


def test_tool_result_text_shapes():
    assert tool_result_text({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == ("a\nb", False)
    assert tool_result_text({"content": [{"type": "image"}], "isError": True}) == ("[image]", True)
    assert tool_result_text(None) == ("", False)
