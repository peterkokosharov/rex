# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-sonnet-5-5
# Licensed under MIT License

"""Default tool wiring: edit, webfetch, MCP tools; no fake websearch."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from runtimelib.agent.gate import PermissionRequest
from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.llm import ToolCall
from runtimelib.permissions.rules import classify
from runtimelib.tools.edit import READ_TRACKER
from runtimelib.tools.mcp_tool import mcp_tool_name

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_echo_server.py")
BASE = {"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}}


class _Session:
    session_id = "s"

    def append(self, *a, **k):
        pass


class Asker:
    def __init__(self, answer="allow_once"):
        self.answer = answer
        self.requests: list[PermissionRequest] = []

    def ask(self, request):
        self.requests.append(request)
        return self.answer


def make_loop(extra=None, asker=None):
    data = {**BASE, **(extra or {})}
    err = io.StringIO()
    with redirect_stderr(err):
        loop = AgentLoop(Config(data), llm_client=object(), asker=asker, session_manager=_Session())
    return loop, err.getvalue()


def call(loop, name, args):
    tc = ToolCall(id="c1", type="function", function={"name": name, "arguments": json.dumps(args)})
    loop._execute_tool_calls([tc])
    return json.loads(loop.state.messages[-1].content)


class DefaultTools(unittest.TestCase):
    def test_edit_and_webfetch_registered_websearch_gone(self):
        loop, _ = make_loop()
        try:
            names = {s["function"]["name"] for s in loop.tool_schemas}
            self.assertIn("edit", names)
            self.assertIn("webfetch", names)
            self.assertNotIn("websearch", names)
            self.assertIs(loop.tools["edit"]._tracker, READ_TRACKER)
            self.assertIs(loop.tools["fileops"]._tracker, READ_TRACKER)
            self.assertIn("Read a file before editing it", loop.system_prompt)
        finally:
            loop.close_mcp()

    def test_webfetch_allow_private_from_config_not_in_schema(self):
        loop, _ = make_loop()
        self.assertFalse(loop.tools["webfetch"].allow_private)
        params = loop.tools["webfetch"].schema.parameters["properties"]
        self.assertNotIn("allow_private", params)
        loop2, _ = make_loop({"webfetch": {"allow_private": True}})
        self.assertTrue(loop2.tools["webfetch"].allow_private)

    def test_webfetch_and_mcp_kinds(self):
        self.assertEqual(classify("webfetch", {"url": "http://a"}), "network")
        self.assertEqual(classify("mcp__fx__echo", {}), "mcp")

    def test_webfetch_asks_in_gate(self):
        asker = Asker("deny")
        loop, _ = make_loop(asker=asker)
        out = call(loop, "webfetch", {"url": "http://example.invalid/"})
        self.assertFalse(out["success"])
        self.assertEqual(asker.requests[0].kind, "network")


class McpWiring(unittest.TestCase):
    def cfg(self, **server):
        spec = {"command": [sys.executable, FIXTURE]}
        spec.update(server)
        return {"mcp": {"servers": {"fx": spec}}}

    def test_names_sanitised(self):
        self.assertEqual(mcp_tool_name("my server", "a.b"), "mcp__my_server__a_b")
        self.assertEqual(len(mcp_tool_name("s", "x" * 100)), 64)

    def test_tools_in_schema_and_callable_through_gate(self):
        asker = Asker("allow_once")
        loop, err = make_loop(self.cfg(), asker=asker)
        try:
            self.assertEqual(err, "")
            names = {s["function"]["name"] for s in loop.tool_schemas}
            self.assertTrue({"mcp__fx__echo", "mcp__fx__fail", "mcp__fx__env"} <= names)
            out = call(loop, "mcp__fx__echo", {"text": "hi"})
            self.assertTrue(out["success"], out)
            self.assertEqual(out["result"], "hi")
            self.assertEqual(asker.requests[0].kind, "mcp")
            bad = call(loop, "mcp__fx__fail", {})
            self.assertFalse(bad["success"])
            self.assertIn("boom", bad["error"])
        finally:
            loop.close_mcp()

    def test_denied_mcp_call_never_reaches_server(self):
        loop, _ = make_loop(self.cfg())  # no asker: ask becomes deny
        try:
            out = call(loop, "mcp__fx__echo", {"text": "hi"})
            self.assertFalse(out["success"])
            self.assertIn("denied", out["error"].lower())
        finally:
            loop.close_mcp()

    def test_server_env_passed(self):
        loop, _ = make_loop(self.cfg(env={"X_FIXTURE": "1"}), asker=Asker())
        try:
            out = call(loop, "mcp__fx__env", {"name": "X_FIXTURE"})
            self.assertEqual(out["result"], "1")
        finally:
            loop.close_mcp()

    def test_disabled_and_failing_servers_skipped(self):
        data = {"mcp": {"servers": {
            "off": {"command": [sys.executable, FIXTURE], "enabled": False},
            "bad": {"command": [sys.executable, FIXTURE], "env": {"MCP_FIXTURE_FAIL_INIT": "1"}},
            "nocmd": {},
        }}}
        loop, err = make_loop(data)
        try:
            self.assertFalse([n for n in loop.tools if n.startswith("mcp__")])
            lines = [ln for ln in err.splitlines() if "MCP server" in ln]
            self.assertEqual(len(lines), 2, err)  # bad + nocmd; disabled is silent
            self.assertTrue(any("'bad'" in ln for ln in lines))
        finally:
            loop.close_mcp()

    def test_close_disconnects(self):
        loop, _ = make_loop(self.cfg())
        client = loop._mcp_manager.get_client("fx")
        self.assertIsNotNone(client)
        loop.close_mcp()
        self.assertFalse(client.transport.alive)
        loop.close_mcp()  # idempotent


if __name__ == "__main__":
    unittest.main()
