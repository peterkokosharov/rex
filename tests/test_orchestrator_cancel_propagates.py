# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nemotron-super
# Licensed under MIT License

"""Tests that cancelling the caller of SubagentHandle.cancel() propagates."""

from __future__ import annotations

import asyncio

import pytest

from orchestrator import SubagentHandle, AgentContext


async def scenario():
    ctx = AgentContext(agent_id="x", role="CODER", worktree_path=".", session_id="s")
    handle = SubagentHandle(agent_id="x", context=ctx, _orchestrator=None)

    async def forever():
        await asyncio.Event().wait()        # never completes on its own

    handle._task = asyncio.create_task(forever())
    caller = asyncio.create_task(handle.cancel())
    await asyncio.sleep(0)                   # let cancel() reach `await self._task`
    caller.cancel()                          # cancel the caller of cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller


def test_cancelling_the_caller_of_cancel_propagates():
    asyncio.run(scenario())
