# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nemotron-super
# Licensed under MIT License
"""REX.spawn_subagent keeps a reference to the spawned asyncio.Task."""
import asyncio

from unittest.mock import Mock, AsyncMock

from ai_runtime_rex.core.rex import (
    AgentContext,
    AgentRole,
    AgentResult,
    REX,
    SubagentHandle,
)


def test_spawn_subagent_keeps_a_task_reference():
    rex = REX(sandbox_manager=Mock(), hook_engine=Mock())
    rex.run_agent = AsyncMock(return_value=AgentResult(success=True, output="ok"))

    parent_context = AgentContext(
        agent_id="root",
        role=AgentRole(name="CODER"),
        worktree=Mock(),
        session_id="s1",
        depth=0,
    )

    async def main():
        handle = await rex.spawn_subagent(
            parent_context,
            "do a thing",
            AgentRole(name="CODER"),
            tools=[],
            permissions=rex.permission_policy,
        )
        assert isinstance(handle, SubagentHandle)
        assert handle._task is not None
        assert isinstance(handle._task, asyncio.Task)

        result = await handle.wait()
        assert result.success is True

    asyncio.run(main())
