# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-opus-5-5
# Licensed under MIT License
"""MCP hardening: unambiguous server names, trust_readonly setting, HTTP redirects and size cap,
capped and cleaned descriptions / stderr, warning for an unset ${VAR}."""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from runtimelib import mcp as mcp_mod
from runtimelib.config import Config
from runtimelib.mcp import HttpTransport, MCPClientManager, MCPServerConfig, StdioTransport
from runtimelib.mcp.cliconfig import (McpConfigError, add_cli_flags, allow_rule, apply, parse_file,
                                      parse_inline, valid_server_name)
from runtimelib.tools.mcp_tool import MAX_DESCRIPTION, McpTool, load_mcp_tools


def cfg() -> Config:
    return Config({"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}})


# ---------------------------------------------------------------- 4: server names / allow rules

@pytest.mark.parametrize("name", ["x_", "_x", "a__b", "_", "x-_", "a.b"])
def test_bad_server_names(name):
    assert not valid_server_name(name)


@pytest.mark.parametrize("name", ["x", "my_server", "a-b", "gh2"])
def test_good_server_names(name):
    assert valid_server_name(name)


def test_allow_for_x_does_not_cover_server_x_underscore():
    rule = allow_rule("x")
    # server "x_" can no longer exist, so mcp__x___tool can only be server x's tool "_tool"
    with pytest.raises(McpConfigError):
        parse_inline("x_=prog")
    assert fnmatch.fnmatchcase("mcp__x__read", rule)


@pytest.mark.parametrize("pattern", ["x_", "_x", "mcp___x__t"])
def test_allow_rule_rejects_ambiguous_server_part(pattern):
    with pytest.raises(McpConfigError):
        allow_rule(pattern)


@pytest.mark.parametrize("pattern, rule", [
    ("demo", "mcp__demo__*"), ("demo__read_*", "mcp__demo__read_*"), ("mcp__demo__x", "mcp__demo__x"),
    ("demo__*", "mcp__demo__*"),
])
def test_allow_rule_good_forms(pattern, rule):
    assert allow_rule(pattern) == rule


@pytest.mark.parametrize("spec", ["x_=prog", "_x=prog", "a__b=prog"])
def test_inline_rejects_edge_underscores(spec):
    with pytest.raises(McpConfigError):
        parse_inline(spec)


def test_file_rejects_bad_server_name(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"mcpServers": {"x_": {"command": "prog"}}}), encoding="utf-8")
    with pytest.raises(McpConfigError, match="server name"):
        parse_file(p, {})


def test_loader_skips_bad_server_name(capsys):
    c = cfg()
    c.set("mcp.servers", {"x_": {"command": ["definitely-not-a-program-xyz"]}})
    tools, manager = load_mcp_tools(c, None)
    assert tools == []
    assert "server name" in capsys.readouterr().err


# ---------------------------------------------------------------- 5: trust_readonly setting

def test_trust_readonly_defaults_false():
    c = cfg()
    apply(c, environ={})
    assert c.get("mcp.trust_readonly") is False


def test_trust_readonly_flag_sets_it():
    c = cfg()
    apply(c, environ={}, trust_readonly=True)
    assert c.get("mcp.trust_readonly") is True


def test_trust_readonly_from_config_file_is_kept():
    c = cfg()
    c.set("mcp.trust_readonly", True)
    apply(c, environ={})
    assert c.get("mcp.trust_readonly") is True


def test_add_cli_flags():
    ap = argparse.ArgumentParser()
    add_cli_flags(ap)
    assert ap.parse_args([]).mcp_trust_readonly is False
    assert ap.parse_args(["--mcp-trust-readonly"]).mcp_trust_readonly is True


# ---------------------------------------------------------------- 7c: ${VAR} unset

def test_unset_var_without_default_warns_once(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"mcpServers": {"a": {"command": "prog", "args": ["${NOPE_X}", "${NOPE_X}"],
                                                  "env": {"K": "${NOPE_Y:-d}"}}}}), encoding="utf-8")
    seen = []
    c = cfg()
    apply(c, [str(p)], environ={}, warn=seen.append)
    assert c.get("mcp.servers")["a"]["command"] == ["prog", "", ""]
    assert len(seen) == 1 and "NOPE_X" in seen[0]


def test_unset_var_warns_on_stderr_without_callback(tmp_path, capsys):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"mcpServers": {"a": {"command": "${NOPE_Z}x"}}}), encoding="utf-8")
    parse_file(p, {})
    assert "NOPE_Z" in capsys.readouterr().err


# ---------------------------------------------------------------- 7a/7b: descriptions and stderr

class _FakeClient:
    def call_tool(self, name, args):
        return {"content": []}


def test_description_capped_and_cleaned():
    desc = "ok‮evil\x1b[31m" + "x" * 5000
    tool = McpTool("s", _FakeClient(), "t", desc, {})
    assert "‮" not in tool.description and "\x1b" not in tool.description
    assert len(tool.description) <= MAX_DESCRIPTION + 60
    assert tool.schema.description == tool.description


def test_stderr_tail_capped_and_cleaned(tmp_path):
    script = tmp_path / "noisy.py"
    script.write_text("import sys\nsys.stderr.write(('\\u202e\\x1b[2Jbad ' + 'y' * 300 + '\\n') * 40)\n"
                      "sys.stderr.flush()\nsys.exit(1)\n", encoding="utf-8")
    m = MCPClientManager()
    m.add_server(MCPServerConfig(name="noisy", command=[sys.executable, str(script)], init_timeout=10))
    with pytest.raises(RuntimeError) as exc:
        m.connect("noisy")
    text = str(exc.value)
    assert "‮" not in text and "\x1b" not in text
    assert len(text) < mcp_mod.MAX_STDERR_TAIL + 300


# ---------------------------------------------------------------- 6: HTTP redirects and size cap

class _Target(BaseHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        type(self).hits.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_http_redirect_is_refused_and_auth_never_leaves():
    _Target.hits = []
    target = _serve(_Target)
    location = f"http://127.0.0.1:{target.server_address[1]}/steal"

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)  # urllib follows 302 for POST (as GET)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    src = _serve(Redirect)
    try:
        t = HttpTransport(f"http://127.0.0.1:{src.server_address[1]}/mcp", {"Authorization": "Bearer secret"},
                          timeout=5)
        with pytest.raises(RuntimeError, match="redirect"):
            t.call("initialize", {}, timeout=5)
        assert _Target.hits == []
    finally:
        src.shutdown()
        target.shutdown()


def test_http_reply_size_is_capped(monkeypatch):
    monkeypatch.setattr(mcp_mod, "MAX_HTTP_REPLY", 1000)

    class Big(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b'{"jsonrpc":"2.0","id":1,"result":{"x":"' + b"a" * 5000 + b'"}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = _serve(Big)
    try:
        t = HttpTransport(f"http://127.0.0.1:{srv.server_address[1]}/mcp", timeout=5)
        with pytest.raises(RuntimeError, match="larger than"):
            t.call("initialize", {}, timeout=5)
    finally:
        srv.shutdown()
