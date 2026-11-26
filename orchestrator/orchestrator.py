# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, nemotron-super
# Licensed under MIT License

"""Orchestrator — multi-agent orchestration layer for ai-rex.

This is the main entry point for orchestrators. It manages:
- Subagent lifecycle (spawn, monitor, cancel, wait)
- Worktree creation and management
- Git operations (commit, merge, push via human confirmation)
- Review gates and quality checks
- Hook event subscription for observability
"""

from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, AsyncGenerator, TYPE_CHECKING
from pathlib import Path
import asyncio
import uuid
import subprocess
import tempfile
import os
import shutil
from datetime import datetime
from contextlib import asynccontextmanager

from .policy import RolePolicy, get_role_policy

if TYPE_CHECKING:
    from .taskspawn import TaskSpawnTool


@dataclass
class AgentContext:
    """Context for an agent (root or subagent)."""
    agent_id: str
    role: str
    worktree_path: str
    session_id: str
    parent_id: str | None = None
    depth: int = 0
    model: str = "gemini/gemini-flash"
    model_class: str = "SONNET"
    max_steps: int = 25
    allowed_tools: list[str] = field(default_factory=list)
    sandbox_profile: dict = field(default_factory=dict)
    permissions: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    task: str = ""


@dataclass
class SubagentHandle:
    """Handle for monitoring and controlling a subagent."""
    agent_id: str
    context: AgentContext
    _orchestrator: "Orchestrator"
    _task: asyncio.Task | None = None
    _result: Any = None
    _completed: asyncio.Event = field(default_factory=asyncio.Event)
    _cancelled: bool = False

    async def wait(self) -> "AgentResult":
        """Wait for subagent to complete and return result."""
        await self._completed.wait()
        return self._result

    async def cancel(self) -> None:
        """Cancel the subagent."""
        if not self._completed.is_set() and self._task:
            self._cancelled = True
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # The task we cancelled raising CancelledError is expected and
                # swallowed. But if *this* coroutine is itself being cancelled,
                # that cancellation must propagate to our caller.
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    raise

    async def stream_events(self) -> AsyncGenerator[dict, None]:
        """Stream lifecycle events (tool_start, tool_end, token, etc.)."""
        # In a real implementation, this would connect to the runtime's hook events
        # For now, yield completion event
        await self._completed.wait()
        yield {"type": "subagent_complete", "agent_id": self.agent_id, "result": self._result}


@dataclass
class AgentResult:
    """Result of an agent execution."""
    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    tokens_used: dict = field(default_factory=dict)
    cost: float = 0.0
    checkpoints: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class WorktreeHandle:
    """Handle for a git worktree."""
    agent_id: str
    path: str
    branch: str
    base_branch: str = "main"
    created_at: datetime = field(default_factory=datetime.now)

    def cleanup(self, keep_changes: bool = False) -> None:
        """Remove worktree (optionally keeping changes)."""
        pass  # Implemented in Orchestrator


@dataclass
class MergeResult:
    """Result of merging a worktree."""
    success: bool
    commit_hash: str | None = None
    merged_branch: str | None = None
    error: str | None = None


class Orchestrator:
    """Main orchestrator for multi-agent execution.

    Usage:
        orch = Orchestrator(config)
        await orch.initialize()

        # Run a root agent
        result = await orch.run_agent(
            task="Fix the login bug in auth.py",
            role="CODER",
        )

        # Or spawn subagents manually
        handle = await orch.spawn_subagent(
            task="Write tests for auth.py",
            role="TESTER",
        )
        result = await handle.wait()
    """

    def __init__(
        self,
        config: dict | None = None,
        project_root: str | Path = ".",
        session_id: str | None = None,
        policy_file: str | Path | None = None,
    ):
        self.config = config or {}
        self.project_root = Path(project_root).resolve()
        self.session_id = session_id or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.policy_file = policy_file

        # Load role policies
        if policy_file:
            from .policy import load_policy_file
            self.policies = load_policy_file(policy_file)
        else:
            from .policy import DEFAULT_POLICIES
            self.policies = DEFAULT_POLICIES

        # Runtime state
        self.session_id = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.worktrees: dict[str, str] = {}  # agent_id -> path
        self.subagents: dict[str, SubagentHandle] = {}
        self.hooks: list[Callable] = []
        self._subagent_counter = 0

        # Runtime components (injected or created lazily)
        self._runtime_loop = None
        self._sandbox_manager = None
        self._git_repo = None

    async def initialize(self) -> None:
        """Initialize the orchestrator (create session dir, verify git repo)."""
        # Create session directory
        self.session_dir = self.project_root / ".runtime" / "sessions" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Verify git repo
        self._git_repo = self.project_root
        try:
            await self._git("rev-parse", "--git-dir", cwd=self.project_root)
        except subprocess.CalledProcessError:
            raise RuntimeError(f"Not a git repository: {self.project_root}")

        # Create worktrees directory
        self.worktrees_dir = self.project_root / ".worktrees"
        self.worktrees_dir.mkdir(exist_ok=True)

        # Create session history file
        self.history_file = self.session_dir / "history.jsonl"
        self._write_history({"type": "session_start", "session_id": self.session_id, "project_root": str(self.project_root)})

    def _write_history(self, event: dict) -> None:
        """Append event to session history."""
        import json
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # --- Tool Spawning ---

    def create_taskspawn_tool(self) -> "TaskSpawnTool":
        """Create a TaskSpawnTool bound to this orchestrator."""
        from .taskspawn import TaskSpawnTool
        return TaskSpawnTool(orchestrator=self)

    # --- Subagent Management ---

    async def spawn_subagent(
        self,
        task: str,
        role: str = "CODER",
        tools: list[str] | None = None,
        model_class: str | None = None,
        max_steps: int | None = None,
        worktree: bool = True,
        parent_context: AgentContext | None = None,
    ) -> SubagentHandle:
        """Spawn a subagent for a delegated task.

        Args:
            task: Task description for the subagent
            role: Agent role (CODER, TESTER, REVIEWER, DEBUGGER, RESEARCHER, ORCHESTRATOR)
            tools: Override allowed tools for this subagent
            model_class: Override model class (OPUS, SONNET, HAIKU, FABLE, CODER, EYES, TUTOR, ROUTER)
            max_steps: Override max steps
            worktree: Create isolated git worktree
            parent_context: Parent agent context (for depth tracking)

        Returns:
            SubagentHandle for monitoring and waiting
        """
        role = role.upper()
        policy = self.get_policy(role)

        # Generate agent ID
        self._subagent_counter += 1
        agent_id = f"{role.lower()}-{self._subagent_counter:03d}"

        # Determine parent
        parent_id = parent_context.agent_id if parent_context else "root"
        depth = (parent_context.depth + 1) if parent_context else 1

        # Create worktree if requested
        worktree_path = str(self.project_root)
        if worktree:
            worktree_path = await self._create_worktree(agent_id)

        # Build context
        context = AgentContext(
            agent_id=agent_id,
            role=role,
            worktree_path=worktree_path,
            session_id=self.session_id,
            parent_id=parent_id,
            depth=depth,
            model=policy.model,
            model_class=model_class or policy.model_class,
            max_steps=max_steps or policy.max_steps,
            allowed_tools=tools or policy.allowed_tools,
            sandbox_profile=policy.sandbox.__dict__,
            permissions=policy.permissions,
            task=task,
        )

        # Create handle
        handle = SubagentHandle(
            agent_id=agent_id,
            context=context,
            _orchestrator=self,
        )

        # Record in history
        self._write_history({
            "type": "agent_spawn",
            "agent_id": agent_id,
            "role": role,
            "depth": depth,
            "task": task,
            "parent_id": parent_id,
        })

        # Start subagent execution
        handle._task = asyncio.create_task(self._run_subagent(handle))

        self.subagents[agent_id] = handle
        return handle

    async def _run_subagent(self, handle: SubagentHandle) -> None:
        """Run a subagent to completion using the AgentLoop."""
        try:
            # Build runtime config from context
            runtime_config = self._build_runtime_config(handle.context)

            # Build proper config dict for Config class
            config_dict = {
                "liteLLM": {
                    "base_url": runtime_config.get("liteLLM", {}).get("base_url", "http://127.0.0.1:4000"),
                    "api_key": runtime_config.get("liteLLM", {}).get("api_key", "dummy"),
                    "model": runtime_config.get("liteLLM", {}).get("model", "gemini-lite"),
                },
                "agent": {
                    "max_steps": handle.context.max_steps,
                    "allowed_tools": handle.context.allowed_tools,
                },
            }

            # Create AgentLoop
            from runtimelib.config import Config
            from runtimelib.agent import create_agent_loop
            loop = create_agent_loop(
                config=Config({
                    "liteLLM": {
                        "base_url": runtime_config.get("liteLLM", {}).get("base_url", "http://127.0.0.1:4000"),
                        "api_key": runtime_config.get("liteLLM", {}).get("api_key", "dummy"),
                        "model": runtime_config.get("liteLLM", {}).get("model", "gemini-lite"),
                    },
                    "agent": {
                        "max_steps": handle.context.max_steps,
                        "allowed_tools": handle.context.allowed_tools,
                    },
                }),
                on_token=lambda t: self._emit_hook({"type": "token", "agent_id": handle.agent_id, "token": t}),
                on_tool_start=lambda n, a: self._emit_hook({"type": "tool_start", "agent_id": handle.agent_id, "tool": n, "args": a}),
                on_tool_end=lambda r: self._emit_hook({"type": "tool_end", "agent_id": handle.agent_id, "tool_result": r}),
                on_thinking=lambda t: self._emit_hook({"type": "thinking", "agent_id": handle.agent_id, "thinking": t}),
                on_context=lambda c: self._emit_hook({"type": "context", "agent_id": handle.agent_id, **c}),
                session_manager=None,
                hook_engine=None,
                checkpoint_manager=None,
                gate=None,
                asker=None,
            )

            # Override the taskspawn tool with our orchestrator-bound one
            loop.tools["task"] = self.create_taskspawn_tool()
            loop.tool_schemas = [t.to_openai() for t in loop.tools.values()]

            # Add task to conversation
            from runtimelib.llm import ChatMessage
            loop.state.messages.append(ChatMessage(role="user", content=handle.context.task))

            # Run the agent loop
            result_text = loop.run(handle.context.task)

            # Build result
            result = AgentResult(
                success=True,
                output=result_text,
                tokens_used=loop.state.turn_tokens_in,  # Approximate
            )

            handle._result = result
            handle._completed.set()

            self._write_history({
                "type": "agent_complete",
                "agent_id": handle.agent_id,
                "success": True,
                "output": result.output,
            })

        except Exception as e:
            result = AgentResult(
                success=False,
                output="",
                error=str(e),
            )
            handle._result = result
            handle._completed.set()

            self._write_history({
                "type": "agent_error",
                "agent_id": handle.agent_id,
                "error": str(e),
            })

    def _build_runtime_config(self, context: AgentContext) -> dict:
        """Build runtime config dict from agent context."""
        return {
            "liteLLM": {
                "model": context.model,
                "base_url": self.config.get("liteLLM", {}).get("base_url", "http://127.0.0.1:4000"),
                "api_key": self.config.get("liteLLM", {}).get("api_key", "dummy"),
            },
            "agent": {
                "max_steps": context.max_steps,
                "allowed_tools": context.allowed_tools,
            },
            "sandbox": context.sandbox_profile,
            "permissions": context.permissions,
            "workspace": context.worktree_path,
        }

    async def run_agent(
        self,
        task: str,
        role: str = "CODER",
        tools: list[str] | None = None,
        model_class: str | None = None,
        max_steps: int | None = None,
    ) -> AgentResult:
        """Run a root agent task to completion."""
        handle = await self.spawn_subagent(
            task=task,
            role=role,
            tools=tools,
            model_class=model_class,
            max_steps=max_steps,
            worktree=True,
            parent_context=None,
        )
        return await handle.wait()

    # --- Worktree Management ---

    async def _create_worktree(self, agent_id: str) -> str:
        """Create a git worktree for an agent."""
        branch = f"{agent_id}-branch"
        worktree_path = self.worktrees_dir / agent_id

        try:
            # Create worktree
            await self._git("worktree", "add", "-b", branch, str(worktree_path), "main", cwd=self.project_root)
            self.worktrees[agent_id] = str(worktree_path)
            return str(worktree_path)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to create worktree: {e.stderr.decode()}")

    def get_worktree(self, agent_id: str) -> WorktreeHandle | None:
        """Get worktree handle for an agent."""
        path = self.worktrees.get(agent_id)
        if not path:
            return None
        return WorktreeHandle(
            agent_id=agent_id,
            path=path,
            branch=f"{agent_id}-branch",
        )

    async def remove_worktree(self, agent_id: str, keep_changes: bool = False) -> bool:
        """Remove a worktree."""
        path = self.worktrees.get(agent_id)
        if not path:
            return False

        try:
            await self._git("worktree", "remove", "--force", path, cwd=self.project_root)
            del self.worktrees[agent_id]
            return True
        except subprocess.CalledProcessError:
            return False

    async def commit_and_merge(
        self,
        agent_id: str,
        message: str,
        author: str | None = None,
    ) -> MergeResult:
        """Commit worktree changes and merge to main.

        This should only be called by the orchestrator after review.
        Human confirmation required for push (separate step).
        """
        worktree = self.get_worktree(agent_id)
        if not worktree:
            return MergeResult(success=False, error="Worktree not found")

        try:
            # Stage all changes
            await self._git("add", "-A", cwd=worktree.path)

            # Commit
            commit_args = ["commit", "-m", message]
            if author:
                commit_args.extend(["--author", author])
            await self._git(*commit_args, cwd=worktree.path)

            # Get commit hash
            result = await self._git("rev-parse", "HEAD", cwd=worktree.path, text=True)
            commit_hash = result.stdout.strip()

            # Merge to main
            await self._git("checkout", "main", cwd=self.project_root)
            await self._git(
                "merge",
                "--no-ff",
                f"{agent_id}-branch",
                "-m",
                f"Merge {agent_id}: {message}",
                cwd=self.project_root,
            )

            # Clean up worktree
            await self.remove_worktree(agent_id)

            return MergeResult(
                success=True,
                commit_hash=commit_hash,
                merged_branch=f"{agent_id}-branch",
            )

        except subprocess.CalledProcessError as e:
            return MergeResult(
                success=False,
                error=e.stderr.decode() if e.stderr else str(e),
            )

    # --- Git Operations ---

    def run_git(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        """Run git command in project root or worktree."""
        return subprocess.run(
            ["git"] + args,
            cwd=cwd or self.project_root,
            capture_output=True,
            text=True,
        )

    async def _git(
        self,
        *args: str,
        cwd: str | Path,
        text: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a git command off the event loop so parallel subagents are not
        blocked. Raises subprocess.CalledProcessError on a non-zero exit;
        callers that need the message catch it and read stderr."""
        return await asyncio.to_thread(
            subprocess.run,
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=text,
        )


    # --- Hook System ---

    def subscribe_hooks(self, callback: Callable[[dict], None]) -> None:
        """Subscribe to lifecycle events."""
        self.hooks.append(callback)

    def _emit_hook(self, event: dict) -> None:
        """Emit event to all subscribers."""
        for hook in self.hooks:
            try:
                hook(event)
            except Exception:
                pass  # Don't let hook errors break orchestration

    # --- Policy Access ---

    def get_policy(self, role: str) -> "RolePolicy":
        """Get role policy by name."""
        return self.policies.get(role.upper(), self.policies.get("CODER"))

    def get_all_policies(self) -> dict[str, "RolePolicy"]:
        return self.policies.copy()

    # --- Session Management ---

    def get_session_history(self) -> list[dict]:
        """Read full session history."""
        import json
        events = []
        if self.history_file.exists():
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        return events

    # --- Cleanup ---

    async def cleanup(self) -> None:
        """Clean up all worktrees and resources."""
        for agent_id in list(self.worktrees.keys()):
            await self.remove_worktree(agent_id)

    async def __aenter__(self) -> "Orchestrator":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.cleanup()