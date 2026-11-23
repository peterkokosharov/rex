# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: claude-opus-5-5
# Licensed under MIT License

"""MCP server processes must not leak: /reset, the REX facade and close_mcp."""

from __future__ import annotations

import asyncio
import atexit
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from runtimelib.agent.loop import AgentLoop
from runtimelib.config import Config
from runtimelib.tools.base import Tool, ToolSchema

CFG = {"liteLLM": {"base_url": "http://x", "api_key": "k", "model": "m"}}


class FakeTool(Tool):
    def __init__(self) -> None:
        super().__init__("bash", "fake", ToolSchema("bash", "fake", {"type": "object", "properties": {}}))

    def execute(self, **kwargs):
        return {"ok": True}


class FakeManager:
    def __init__(self) -> None:
        self.disconnects = 0

    def disconnect_all(self) -> None:
        self.disconnects += 1


class _Session:
    session_id = "s1"

    def append(self, *a, **k):
        pass


def make_loop() -> AgentLoop:
    return AgentLoop(Config(dict(CFG)), llm_client=object(), tools=[FakeTool()], session_manager=_Session())


def test_close_mcp_unregisters_exit_hook_and_is_idempotent():
    loop = make_loop()
    manager = FakeManager()
    loop._mcp_manager = manager
    atexit.register(loop.close_mcp)
    with mock.patch("runtimelib.agent.loop.atexit.unregister", wraps=atexit.unregister) as unreg:
        loop.close_mcp()
        loop.close_mcp()
    assert manager.disconnects == 1
    unreg.assert_called_once_with(loop.close_mcp)


def test_repl_reset_keeps_loop_and_mcp():
    import runtime.__main__ as main
    from runtimelib.cli import CommandRegistry

    loop = make_loop()
    manager = FakeManager()
    loop._mcp_manager = manager
    registry = CommandRegistry()
    main.register_core_commands(registry, loop.config)
    old = main._REPL_LOOP
    main._REPL_LOOP = loop
    try:
        loop.state.messages.append(SimpleNamespace(role="user", content="x"))
        registry.execute("reset", {})
        assert main._REPL_LOOP is loop
        assert loop.state.messages == []
        assert loop._mcp_manager is manager and manager.disconnects == 0
    finally:
        main._REPL_LOOP = old
        loop._mcp_manager = None


def test_rex_facade_closes_loop_mcp():
    from runtimelib import rex as rexmod
    from runtimelib.permissions.policy import DefaultPermissionPolicy

    closed = []

    class FakeLoop:
        def run(self, task):
            return "done"

        def close_mcp(self):
            closed.append(True)

    class FakeSandbox:
        def create_worktree(self, agent_id):
            return SimpleNamespace(path=Path("."), branch="b", agent_id=agent_id)

    hooks = SimpleNamespace(trigger=lambda *a, **k: None)
    r = rexmod.REX(Config(dict(CFG)), session_manager=_Session(), sandbox_manager=FakeSandbox(),
                   hook_engine=hooks)
    # _create_tools imports a registry helper that does not exist (separate bug); stub it.
    with mock.patch.object(rexmod, "create_agent_loop", return_value=FakeLoop()), \
            mock.patch.object(r, "_create_tools", return_value=[]):
        result = asyncio.run(r.run_agent("t", rexmod.AgentRole(name="CODER"), [], DefaultPermissionPolicy()))
    assert result.success
    assert closed == [True]
