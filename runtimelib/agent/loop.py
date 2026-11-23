# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Agent loop - ReAct pattern with streaming and tool execution."""

from __future__ import annotations

import json
import signal
import sys
import threading
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..config import Config
from ..llm import LiteLLMClient, ChatMessage, ChatCompletionChunk, ToolCall
from ..llm.models import get_registry
from ..tools import FileOpsTool, BashTool, TaskSpawnTool, WebSearchTool, GitTool, DiagramTool
from ..tools.skills import LoadSkillTool, ReadSkillFileTool
from ..skills import discover_skills, skill_index
from ..tools.base import Tool, ToolResult
from ..guard import get_guard_client
from ..session import SessionManager, create_session_manager
from ..hooks import HookEngine, HookEvent, get_hook_engine
from ..agent.state import CheckpointManager
from .context import (
    COMPACT_RATIO,
    compact_messages,
    CANCELLED_RESULT,
    compact_threshold,
    context_tokens,
    ensure_tool_results,
    resolve_context_window,
)


def _parse_tool_arguments(text: str) -> dict[str, Any]:
    """Parse tool arguments; tolerate trailing text emitted by some models."""
    if not text or not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value, _ = decoder.raw_decode(text.lstrip())
    return value if isinstance(value, dict) else {}


@dataclass
class AgentState:
    """Current state of the agent.

    ``messages`` is the shared conversation context and survives every call to
    ``AgentLoop.run()``. ``step`` and ``interrupted`` are per-request and are
    reset at the start of each run; ``turn`` counts requests in this context.
    """
    messages: list[ChatMessage] = field(default_factory=list)
    step: int = 0
    max_steps: int = 20
    current_tool_calls: list[ToolCall] = field(default_factory=list)
    interrupted: bool = False
    turn: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0


class AgentLoop:
    """Main ReAct agent loop."""

    def __init__(
        self,
        config: Config,
        llm_client: Optional[LiteLLMClient] = None,
        tools: Optional[list[Tool]] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_end: Optional[Callable[[ToolResult], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        session_manager: Optional[SessionManager] = None,
        hook_engine: Optional[HookEngine] = None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        agent_id: str = "default",
        role: str = "CODER",
        depth: int = 0,
    ) -> None:
        self.config = config
        self.llm = llm_client or LiteLLMClient.from_config(config)
        self.state = AgentState()
        # LLM: nvidia nemotron ultra - respect configured step budget; the
        # hard-coded 20 was too low for repository-wide explanations.
        self.state.max_steps = int(config.get("agent.max_steps", 60))
        self.on_token = on_token or (lambda t: None)
        self.on_tool_start = on_tool_start or (lambda n, a: None)
        self.on_tool_end = on_tool_end or (lambda r: None)
        self.on_thinking = on_thinking or (lambda t: None)

        # Initialize tools
        self.tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.tools[tool.name] = tool
        else:
            self._init_default_tools()

        # Tool schemas for LLM
        self.tool_schemas = [t.to_openai() for t in self.tools.values()]

        # System prompts: with and without tools
        self.system_prompt = self._build_system_prompt(include_tools=True)
        self.system_prompt_no_tools = self._build_system_prompt(include_tools=False)

        # Session, hooks, checkpoints
        self.session_manager = session_manager or create_session_manager()
        self.hook_engine = hook_engine or get_hook_engine()
        self.checkpoint_manager = checkpoint_manager
        self.agent_id = agent_id
        self.role = role
        self.depth = depth

        # Interrupt handling
        self._interrupt_event = threading.Event()
        signal.signal(signal.SIGINT, self._handle_interrupt)

        # Emit session start
        self.hook_engine.trigger(HookEvent.SESSION_START, session_id=self.session_manager.session_id)
        self.hook_engine.trigger(HookEvent.AGENT_START,
            session_id=self.session_manager.session_id, agent_id=agent_id, role=role, depth=depth)

    def _init_default_tools(self) -> None:
        """Initialize default tools."""
        self.tools["fileops"] = FileOpsTool(self.config)
        self.tools["bash"] = BashTool(self.config)
        self.tools["task"] = TaskSpawnTool()
        self.tools["websearch"] = WebSearchTool()
        self.tools["git"] = GitTool(self.config)
        self.tools["render_diagram"] = DiagramTool(self.config)
        skills = discover_skills(Path.cwd(), self.config)
        if skills:
            self.tools["load_skill"] = LoadSkillTool(self.config, skills)
            self.tools["read_skill_file"] = ReadSkillFileTool(self.config, skills)

    def _build_system_prompt(self, include_tools: bool = True) -> str:
        """Build system prompt, optionally with tool descriptions."""
        if include_tools:
            tool_descs = []
            for tool in self.tools.values():
                schema = tool.schema
                params = schema.parameters.get("properties", {})
                param_descs = []
                for name, info in params.items():
                    desc = info.get("description", "")
                    required = name in schema.parameters.get("required", [])
                    param_descs.append(f"  {name}{' (required)' if required else ''}: {desc}")
                tool_descs.append(f"{tool.name}: {tool.description}\n" + "\n".join(param_descs))

            skills_block = ""
            if "load_skill" in self.tools:
                index = skill_index(list(self.tools["load_skill"]._skills))
                skills_block = (
                    "\nSkills available (name: description). When a task matches a skill's description, "
                    "call load_skill(name) first and follow its instructions; read_skill_file(name, relpath) "
                    "reads extra files listed there:\n" + index + "\n"
                )

            return f"""You are Runtime, a coding agent that helps users with software development tasks.

You have access to the following tools:
{chr(10).join(tool_descs)}

Guidelines:
- Use tools to accomplish tasks. Think before acting.
- For file operations, prefer reading before writing.
- For bash commands, explain what you're doing.
- Be concise in your responses.
- If you need to use a tool, call it. Don't describe what you would do.
- You can chain multiple tool calls in one response.
- When the user asks for a diagram (flowchart, sequence, class, state, ER, gantt, UML-like), call render_diagram, then answer with a compact text sketch of the same diagram (Unicode box-drawing characters, at most ~60 columns, inside a ``` block; sketch only the main path if it is too big) followed by the Markdown image line the tool returned. Do not paste Mermaid source or ASCII-art diagrams instead of calling the tool.
- Maximum steps: {self.state.max_steps}
{skills_block}
Workspace: the current directory. Use RELATIVE paths such as "src/Main.java" or "README.md". Never pass absolute paths like "/home/user/project/..." - the tool sandbox rejects them.
"""
        else:
            return f"""You are Runtime, a helpful assistant. Answer questions concisely and accurately.

Workspace: the current directory. Use RELATIVE paths such as "src/Main.java" or "README.md". Never pass absolute paths like "/home/user/project/..." - the tool sandbox rejects them.
"""

    def interrupt(self) -> None:
        """Request interruption of the current request (safe from another thread)."""
        if self._interrupt_event.is_set():
            return
        self._interrupt_event.set()
        self.state.interrupted = True
        self.hook_engine.trigger(HookEvent.AGENT_ERROR,
            session_id=self.session_manager.session_id, agent_id=self.agent_id,
            role=self.role, metadata={"error": "Interrupted by user"})
        print("\n[Прекъснато]", file=sys.stderr)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        """Handle Ctrl+C interrupt."""
        self.interrupt()

    def reset_context(self) -> None:
        """Start a fresh conversation without rebuilding the loop or its signal handler."""
        self.state.messages = []
        self.state.step = 0
        self.state.current_tool_calls = []
        self.state.interrupted = False
        self.state.turn = 0
        self._interrupt_event.clear()

    def run(self, prompt: str) -> str:
        """Run one request, reusing the conversation context from previous ones.

        The step budget and the interrupt flag belong to a single request. They
        are reset here so a second prompt cannot inherit an exhausted step
        counter or a stale Ctrl+C from the first one.
        """
        self.state.step = 0
        self.state.current_tool_calls = []
        self.state.interrupted = False
        self._interrupt_event.clear()
        self.state.turn += 1
        self.state.messages = ensure_tool_results(self.state.messages)

        # Add user message to the shared context
        self.state.messages.append(ChatMessage(role="user", content=prompt))

        # Record user message in session
        self.session_manager.append("user_message", {"prompt": prompt, "agent_id": self.agent_id})

        while self.state.step < self.state.max_steps and not self.state.interrupted:
            self.state.step += 1

            # Emit step start
            self.hook_engine.trigger(HookEvent.AGENT_STEP,
                session_id=self.session_manager.session_id, agent_id=self.agent_id,
                role=self.role, metadata={"step": self.state.step})

            # Checkpoint if needed
            if self.checkpoint_manager and self.checkpoint_manager.should_checkpoint(self.state.step):
                self.checkpoint_manager.save_checkpoint(self._get_agent_state())

            # Get LLM response
            response = self._get_llm_response()

            if self.state.interrupted:
                break

            # Check for tool calls
            if response.tool_calls:
                self._execute_tool_calls(response.tool_calls)
                continue

            # Regular text response
            if response.content:
                # Record assistant response
                self.session_manager.append("assistant_message", {"content": response.content, "agent_id": self.agent_id})
                self.hook_engine.trigger(HookEvent.AGENT_END,
                    session_id=self.session_manager.session_id, agent_id=self.agent_id,
                    role=self.role, metadata={"output": response.content})
                return response.content

            # If no content and no tool calls, return empty response instead of looping
            if not response.content and not response.tool_calls:
                self.state.messages.append(ChatMessage(role="assistant", content=""))
                return ""

        # Whatever stopped the loop, no tool call may stay without a result.
        self.state.messages = ensure_tool_results(self.state.messages)

        if self.state.interrupted:
            text = "[Изпълнението е прекъснато от потребителя]"
            self.state.messages.append(ChatMessage(role="assistant", content=text))
            return text

        if self.state.step >= self.state.max_steps:
            text = f"[Достигнат е максимум от {self.state.max_steps} стъпки]"
            self.state.messages.append(ChatMessage(role="assistant", content=text))
            self.hook_engine.trigger(HookEvent.AGENT_END,
                session_id=self.session_manager.session_id, agent_id=self.agent_id,
                role=self.role, metadata={"output": text})
            return text

        self.state.messages.append(ChatMessage(role="assistant", content=""))
        return ""

    def context_window(self) -> int:
        """Context window in tokens for the currently selected model."""
        return resolve_context_window(
            getattr(self.llm, "default_model", "") or self.config.get("liteLLM.model", ""),
            self.config,
            get_registry(),
        )

    def compact_threshold(self) -> int:
        """Token count that triggers auto-compaction for the current model."""
        ratio = float(self.config.get("cli.compact_ratio", COMPACT_RATIO))
        return compact_threshold(self.context_window(), ratio)

    def context_usage(self) -> dict[str, int]:
        """Token accounting for /context and tests."""
        return {
            "tokens": context_tokens(self.state.messages),
            "window": self.context_window(),
            "threshold": self.compact_threshold(),
            "messages": len(self.state.messages),
            "turn": self.state.turn,
        }

    def _compact_messages(self) -> None:
        """Auto-compact the shared context at 90% of the model's window."""
        threshold = self.compact_threshold()
        compacted, changed = compact_messages(self.state.messages, threshold)
        if changed:
            self.state.messages = compacted
            print(
                f"[Context compacted: {context_tokens(compacted)} tokens, threshold {threshold}]",
                file=sys.stderr,
            )

    def _get_llm_response(self) -> ChatMessage:
        """Get response from LLM with streaming."""
        # LLM: nvidia nemotron ultra - always expose tools, like Claude Code.
        # The old keyword heuristic left tools off for plain questions, and the
        # model then emitted fake textual "tool calls" instead of real ones.
        enable_tools = True

        # Auto-compact at 90% of the current model's context window.
        self._compact_messages()
        self.state.messages = ensure_tool_results(self.state.messages)

        # Use appropriate system prompt based on whether tools are enabled
        system_prompt = self.system_prompt if enable_tools else self.system_prompt_no_tools
        messages = [ChatMessage(role="system", content=system_prompt)]
        messages.extend(self.state.messages)

        # Stream response
        full_content = ""
        tool_calls: dict[int, ToolCall] = {}
        last_index: int | None = None

        stream = self.llm.chat_completion(
            messages=messages,
            tools=self.tool_schemas if (self.tool_schemas and enable_tools) else None,
            tool_choice="auto" if (self.tool_schemas and enable_tools) else "none",
            stream=True,
        )

        for chunk in stream:
            if self._interrupt_event.is_set():
                self.state.interrupted = True
                break

            # Handle content delta
            if chunk.content:
                full_content += chunk.content
                self.on_token(chunk.content)

            # Handle tool call deltas
            if chunk.tool_calls:
                for tc_delta in chunk.tool_calls:
                    last_index = accumulate_tool_call(tool_calls, tc_delta, last_index)

            # Handle finish reason
            if chunk.finish_reason == "tool_calls":
                pass

        # Build response message
        if tool_calls and not self.state.interrupted:
            sorted_calls = [tool_calls[i] for i in sorted(tool_calls.keys())]
            for tc in sorted_calls:
                if not tc.id:  # provider sent no id at all; results still need one
                    tc.id = f"call_{uuid.uuid4().hex[:24]}"
            msg = ChatMessage(role="assistant", content=full_content, tool_calls=sorted_calls)
        else:
            msg = ChatMessage(role="assistant", content=full_content)

        self.state.messages.append(msg)
        return msg

    def _execute_tool_calls(self, tool_calls: list[ToolCall]) -> None:
        """Execute tool calls and add results to conversation.

        Every call gets a tool message, also when the user interrupts: the
        remaining calls are answered with a "cancelled" result so the history
        stays valid for the next request.
        """
        for pos, tc in enumerate(tool_calls):
            if self._interrupt_event.is_set():
                self.state.interrupted = True
                for rest in tool_calls[pos:]:
                    self.state.messages.append(ChatMessage(
                        role="tool", content=CANCELLED_RESULT, tool_call_id=rest.id,
                    ))
                break

            tool_name = tc.function.get("name", "")
            tool = self.tools.get(tool_name)
            args: dict[str, Any] = {}

            if not tool:
                result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=f"Unknown tool: {tool_name}"
                )
            else:
                try:
                    # Parse arguments
                    args_str = tc.function.get("arguments", "{}")
                    args = _parse_tool_arguments(args_str)

                    # Add role and agent context for permission checking
                    args["_role"] = self.role
                    args["_agent_id"] = self.agent_id

                    # Emit tool start hook
                    self.hook_engine.trigger(HookEvent.PRE_TOOL,
                        session_id=self.session_manager.session_id, agent_id=self.agent_id,
                        role=self.role, tool_name=tool_name, tool_args=args)

                    # Notify tool start callback
                    self.on_tool_start(tool_name, args)

                    # Execute tool
                    result_data = tool.execute(**args)

                    result = ToolResult(
                        tool_name=tool_name,
                        success=True,
                        result=result_data,
                    )
                except json.JSONDecodeError as e:
                    result = ToolResult(
                        tool_name=tool_name,
                        success=False,
                        error=f"Invalid tool arguments: {e}"
                    )
                except Exception as e:
                    result = ToolResult(
                        tool_name=tool_name,
                        success=False,
                        error=f"Tool execution failed: {e}"
                    )

            # Emit tool end hook
            self.hook_engine.trigger(HookEvent.POST_TOOL,
                session_id=self.session_manager.session_id, agent_id=self.agent_id,
                role=self.role, tool_name=tool_name, tool_result=result)

            # Notify tool end callback
            self.on_tool_end(result)

            # Record tool call in session
            self.session_manager.append("tool_call", {
                "tool": tool_name,
                "args": args,
                "success": result.success,
                "result": result.result if result.success else None,
                "error": result.error,
                "agent_id": self.agent_id
            })

            # Add tool result to messages
            result_content = json.dumps(
                {"success": result.success, "result": result.result, "error": result.error},
                ensure_ascii=False
            )
            self.state.messages.append(ChatMessage(
                role="tool",
                content=result_content,
                tool_call_id=tc.id
            ))

    def _get_agent_state(self) -> "AgentState":
        """Get current agent state for checkpointing."""
        return self.state


def create_agent_loop(config: Optional[Config] = None, **kwargs) -> AgentLoop:
    """Factory function to create agent loop."""
    if config is None:
        from ..config import get_config
        config = get_config()
    return AgentLoop(config, **kwargs)


def accumulate_tool_call(calls: dict[int, ToolCall], delta: ToolCall, last_index: int | None) -> int:
    """Merge one streamed tool-call delta into ``calls`` (keyed by index).

    OpenAI-style streams send ``index`` on every delta but ``id``, ``type``
    and ``name`` only on the first one; arguments arrive in pieces, and the
    pieces of parallel calls may interleave. A delta without an index (some
    gateways) continues the last call, or starts a new one when it brings a
    new id. Returns the index the delta was merged into.
    """
    index = delta.index
    if index is None:
        if last_index is None or (delta.id and calls[last_index].id and delta.id != calls[last_index].id):
            index = max(calls, default=-1) + 1
        else:
            index = last_index
    tc = calls.get(index)
    if tc is None:
        tc = calls[index] = ToolCall(id="", type="function", function={"name": "", "arguments": ""})
    if delta.id and not tc.id:
        tc.id = delta.id
    if delta.type:
        tc.type = delta.type
    fn = delta.function or {}
    if fn.get("name"):
        tc.function["name"] = fn["name"]
    args = fn.get("arguments")
    if args:
        tc.function["arguments"] += args if isinstance(args, str) else json.dumps(args)
    return index

