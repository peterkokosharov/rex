# SPDX-License-Identifier: MIT
"""REX Runtime API — Main entry point for orchestrators (RONI, Archeo)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .agent import AgentLoop, create_agent_loop
from .agent.state import CheckpointManager
from .config import Config, get_config
from .guard import SandboxManager, get_sandbox_manager, WorktreeHandle
from .hooks import HookEngine, HookEvent, HookContext, get_hook_engine
from .permissions import PermissionPolicy, DefaultPermissionPolicy, PermissionCheckingExecutor
from .session import SessionManager, create_session_manager
from .tools import ToolExecutor, ToolResult
from .tools.base import Tool


@dataclass
class AgentRole:
    """Agent role configuration."""
    name: str
    model_class: str = "SONNET"
    max_steps: int = 20
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class AgentContext:
    """Context for an agent execution."""
    agent_id: str
    role: AgentRole
    worktree: "WorktreeHandle"
    session_id: str
    parent_id: str | None = None
    depth: int = 0


@dataclass
class AgentResult:
    """Result of agent execution."""
    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    tokens_used: dict[str, int] = field(default_factory=dict)
    cost: float = 0.0
    checkpoints: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class SubagentHandle:
    """Handle to a spawned subagent for monitoring."""
    agent_id: str
    context: AgentContext
    _result_future: asyncio.Future = field(default_factory=asyncio.Future)

    async def wait(self) -> AgentResult:
        return await self._result_future

    def cancel(self) -> None:
        if not self._result_future.done():
            self._result_future.cancel()


class REX:
    """Main REX Runtime API — dual mode: Vibe Coding (human) + Tool Provider (RONI)."""

    def __init__(
        self,
        config: Optional[Config] = None,
        project_root: Optional[Path] = None,
        permission_policy: Optional["PermissionPolicy"] = None,
        session_manager: Optional["SessionManager"] = None,
        sandbox_manager: Optional["SandboxManager"] = None,
        hook_engine: Optional["HookEngine"] = None,
    ) -> None:
        self.config = config or get_config()
        self.project_root = project_root or Path.cwd()

        # Core components
        self.hook_engine = hook_engine or get_hook_engine()
        self.session_manager = session_manager or create_session_manager()
        self.sandbox_manager = sandbox_manager or get_sandbox_manager(self.config)
        self.permission_policy = permission_policy or DefaultPermissionPolicy()

        # Tool executor with permission checking
        from .tools import ToolExecutor
        base_executor = ToolExecutor()
        self.tool_executor = PermissionCheckingExecutor(self.permission_policy, base_executor)

        # Active agents tracking
        self._active_agents: dict[str, AgentLoop] = {}
        self._subagent_futures: dict[str, asyncio.Future] = {}

    # ==================== Vibe Coding Mode (CLI) ====================

    async def chat(self, prompt: str, role: str = "CODER") -> AgentResult:
        """Simple chat interface for vibe coding mode."""
        return await self.run_agent(
            task=prompt,
            role=AgentRole(name=role),
            tools=self._get_allowed_tools(role),
            permissions=self.permission_policy,
        )

    # ==================== Tool Provider Mode (RONI) ====================

    async def run_agent(
        self,
        task: str,
        role: AgentRole,
        tools: list[str],
        permissions: "PermissionPolicy",
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute single agent task. Blocks until completion."""

        agent_id = f"{role.name.lower()}-{uuid.uuid4().hex[:8]}"
        session_id = self.session_manager.session_id

        # Create worktree for isolation
        worktree = self.sandbox_manager.create_worktree(agent_id)

        # Build agent context
        agent_context = AgentContext(
            agent_id=agent_id,
            role=role,
            worktree=worktree,
            session_id=session_id,
            parent_id=context.get("parent_id") if context else None,
            depth=context.get("depth", 0) if context else 0,
        )

        # Create agent loop with permission-checking executor
        loop = create_agent_loop(
            self.config,
            tools=self._create_tools(tools, agent_context),
            on_token=lambda t: None,  # Callbacks via hooks
            on_tool_start=lambda n, a: self._emit_tool_start(agent_context, n, a),
            on_tool_end=lambda r: self._emit_tool_end(agent_context, r),
        )

        self._active_agents[agent_id] = loop

        # Emit AGENT_START
        self.hook_engine.trigger(HookEvent.AGENT_START,
            session_id=session_id, agent_id=agent_id, role=role.name, depth=agent_context.depth)

        try:
            # Run agent
            result_text = loop.run(task)

            # Emit AGENT_END
            self.hook_engine.trigger(HookEvent.AGENT_END,
                session_id=session_id, agent_id=agent_id, output=result_text)

            return AgentResult(success=True, output=result_text or "")

        except Exception as e:
            self.hook_engine.trigger(HookEvent.AGENT_ERROR,
                session_id=session_id, agent_id=agent_id, error=str(e))
            return AgentResult(success=False, output="", error=str(e))
        finally:
            self._active_agents.pop(agent_id, None)

    async def spawn_subagent(
        self,
        parent_context: AgentContext,
        task: str,
        role: AgentRole,
        tools: list[str],
        permissions: "PermissionPolicy",
    ) -> SubagentHandle:
        """Spawn isolated subagent for RONI delegation."""

        # Check recursion limits
        max_depth = self.config.get("task_spawn.max_depth", 3)
        if parent_context.depth >= max_depth:
            raise RuntimeError(f"Max spawn depth ({max_depth}) reached")

        # Create child context
        child_context = AgentContext(
            agent_id=f"{role.name.lower()}-{uuid.uuid4().hex[:8]}",
            role=role,
            worktree=self.sandbox_manager.create_worktree(f"{role.name.lower()}-{uuid.uuid4().hex[:8]}"),
            session_id=parent_context.session_id,
            parent_id=parent_context.agent_id,
            depth=parent_context.depth + 1,
        )

        handle = SubagentHandle(agent_id=child_context.agent_id, context=child_context)

        # Run in background
        async def _run():
            try:
                result = await self.run_agent(task, role, tools, permissions, {
                    "parent_id": parent_context.agent_id,
                    "depth": child_context.depth,
                })
                handle._result_future.set_result(result)
            except Exception as e:
                handle._result_future.set_exception(e)

        asyncio.create_task(_run())

        # Emit SUBAGENT_SPAWN
        self.hook_engine.trigger(HookEvent.SUBAGENT_SPAWN,
            session_id=parent_context.session_id,
            agent_id=parent_context.agent_id,
            child_id=child_context.agent_id,
            role=role.name)

        return handle

    def create_worktree(self, agent_id: str) -> WorktreeHandle:
        """Create git worktree for agent isolation."""
        return self.sandbox_manager.create_worktree(agent_id)

    def commit_and_merge(self, worktree: WorktreeHandle, message: str) -> dict:
        """Commit worktree changes and merge to main (orchestrator-only)."""
        return self.sandbox_manager.commit_and_merge(worktree, message)

    def subscribe_hooks(
        self,
        events: list["HookEvent"],
        callback: Callable[["HookContext"], None],
    ) -> "Subscription":
        """Subscribe to agent lifecycle events for observability."""
        return self.hook_engine.subscribe(events, callback)

    # ==================== Internal Helpers ====================

    def _get_allowed_tools(self, role: str) -> list[str]:
        role_config = self.config.get(f"roles.{role}", {})
        return role_config.get("allowed_tools", ["fileops", "bash", "git"])

    def _create_tools(self, tool_names: list[str], context: AgentContext) -> list:
        # Factory to create tool instances with context
        from .tools import get_registry
        registry = get_registry()
        tools = []
        for name in tool_names:
            tool = registry.get(name)
            if tool:
                tools.append(tool)
        return tools

    def _emit_tool_start(self, context: AgentContext, name: str, args: dict):
        self.hook_engine.trigger(HookEvent.TOOL_START,
            session_id=context.session_id, agent_id=context.agent_id,
            role=context.role.name, tool_name=name, tool_args=args)

    def _emit_tool_end(self, context: AgentContext, result: ToolResult):
        self.hook_engine.trigger(HookEvent.TOOL_END,
            session_id=context.session_id, agent_id=context.agent_id,
            role=context.role.name, tool_name=result.tool_name, tool_result=result)


class Subscription:
    """Hook subscription handle."""
    def __init__(self, hook_engine: "HookEngine", events: list["HookEvent"], callback: Callable):
        self.hook_engine = hook_engine
        self.events = events
        self.callback = callback

    def unsubscribe(self):
        for event in self.events:
            self.hook_engine.unsubscribe(event, self.callback)