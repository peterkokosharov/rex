# SPDX-License-Identifier: MIT
"""Hooks engine for extensibility."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ..tools.base import ToolResult
from ..agent.loop import AgentState


class HookEvent(Enum):
    """Hook event types."""
    # Tool events
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    TOOL_ERROR = "tool_error"

    # Agent events
    AGENT_START = "agent_start"
    AGENT_STEP = "agent_step"
    AGENT_END = "agent_end"
    AGENT_ERROR = "agent_error"

    # Session events
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Subagent events
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_COMPLETE = "subagent_complete"

    # LLM events
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    LLM_STREAM = "llm_stream"


@dataclass
class HookContext:
    """Context passed to hooks."""
    event: HookEvent
    session_id: str
    agent_state: Optional[AgentState] = None
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: Optional[ToolResult] = None
    llm_messages: list[dict] = field(default_factory=list)
    llm_response: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Hook(ABC):
    """Base hook class."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def events(self) -> list[HookEvent]:
        pass

    @abstractmethod
    def run(self, context: HookContext) -> Any:
        """Execute hook. Return value may modify behavior."""
        pass


class HookRegistry:
    """Registry for hooks."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[Hook]] = {e: [] for e in HookEvent}

    def register(self, hook: Hook) -> None:
        """Register a hook."""
        for event in hook.events:
            self._hooks[event].append(hook)

    def unregister(self, hook: Hook) -> None:
        """Unregister a hook."""
        for event in hook.events:
            if hook in self._hooks[event]:
                self._hooks[event].remove(hook)

    def trigger(self, context: HookContext) -> list[Any]:
        """Trigger all hooks for an event."""
        results = []
        for hook in self._hooks.get(context.event, []):
            try:
                result = hook.run(context)
                results.append(result)
            except Exception as e:
                # Hook errors should not break the main flow
                print(f"[Hook Error] {hook.name}: {e}", file=__import__("sys").stderr)
        return results

    def get_hooks_for(self, event: HookEvent) -> list[Hook]:
        return self._hooks.get(event, [])


class HookEngine:
    """Main hooks engine."""

    def __init__(self, registry: Optional[HookRegistry] = None) -> None:
        self.registry = registry or HookRegistry()
        self._enabled = True

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def trigger(self, event: HookEvent, **kwargs: Any) -> list[Any]:
        """Trigger hooks for event."""
        if not self._enabled:
            return []
        context = HookContext(event=event, session_id=kwargs.get("session_id", ""), **kwargs)
        return self.registry.trigger(context)

    # Convenience methods
    def pre_tool(self, session_id: str, tool_name: str, tool_args: dict, agent_state: Optional[AgentState] = None) -> list[Any]:
        return self.trigger(HookEvent.PRE_TOOL, session_id=session_id, tool_name=tool_name, tool_args=tool_args, agent_state=agent_state)

    def post_tool(self, session_id: str, tool_name: str, tool_args: dict, tool_result: ToolResult, agent_state: Optional[AgentState] = None) -> list[Any]:
        return self.trigger(HookEvent.POST_TOOL, session_id=session_id, tool_name=tool_name, tool_args=tool_args, tool_result=tool_result, agent_state=agent_state)

    def tool_error(self, session_id: str, tool_name: str, tool_args: dict, error: Exception, agent_state: Optional[AgentState] = None) -> list[Any]:
        return self.trigger(HookEvent.TOOL_ERROR, session_id=session_id, tool_name=tool_name, tool_args=tool_args, metadata={"error": str(error)}, agent_state=agent_state)

    def agent_start(self, session_id: str, prompt: str, agent_state: AgentState) -> list[Any]:
        return self.trigger(HookEvent.AGENT_START, session_id=session_id, metadata={"prompt": prompt}, agent_state=agent_state)

    def agent_step(self, session_id: str, step: int, agent_state: AgentState) -> list[Any]:
        return self.trigger(HookEvent.AGENT_STEP, session_id=session_id, metadata={"step": step}, agent_state=agent_state)

    def agent_end(self, session_id: str, response: str, agent_state: AgentState) -> list[Any]:
        return self.trigger(HookEvent.AGENT_END, session_id=session_id, metadata={"response": response}, agent_state=agent_state)

    def session_start(self, session_id: str) -> list[Any]:
        return self.trigger(HookEvent.SESSION_START, session_id=session_id)

    def session_end(self, session_id: str) -> list[Any]:
        return self.trigger(HookEvent.SESSION_END, session_id=session_id)


# Global hook engine
_engine: HookEngine | None = None


def get_hook_engine() -> HookEngine:
    global _engine
    if _engine is None:
        _engine = HookEngine()
    return _engine