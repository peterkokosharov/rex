# SPDX-License-Identifier: MIT
"""Agent loop - ReAct pattern with streaming and tool execution."""

from __future__ import annotations

import json
import signal
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import Config
from ..llm import LiteLLMClient, ChatMessage, ChatCompletionChunk, ToolCall
from ..tools import FileOpsTool, BashTool, TaskSpawnTool, WebSearchTool, GitTool
from ..tools.base import Tool, ToolResult
from ..guard import get_guard_client
from ..session import SessionManager, create_session_manager
from ..hooks import HookEngine, HookEvent, get_hook_engine
from ..agent.state import CheckpointManager


@dataclass
class AgentState:
    """Current state of the agent."""
    messages: list[ChatMessage] = field(default_factory=list)
    step: int = 0
    max_steps: int = 20
    current_tool_calls: list[ToolCall] = field(default_factory=list)
    interrupted: bool = False
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

        # System prompt
        self.system_prompt = self._build_system_prompt()

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

    def _build_system_prompt(self) -> str:
        """Build system prompt with tool descriptions."""
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
- Maximum steps: {self.state.max_steps}

Current workspace: {Path.cwd()}
"""

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        """Handle Ctrl+C interrupt."""
        self._interrupt_event.set()
        self.state.interrupted = True
        self.hook_engine.trigger(HookEvent.AGENT_ERROR,
            session_id=self.session_manager.session_id, agent_id=self.agent_id,
            role=self.role, error="Interrupted by user")
        print("\n[Прекъснато]", file=sys.stderr)

    def run(self, prompt: str) -> str:
        """Run the agent loop with a user prompt."""
        # Add user message
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

            # If no content and no tool calls, continue
            if not response.content and not response.tool_calls:
                self.state.messages.append(ChatMessage(role="assistant", content=""))
                continue

        if self.state.interrupted:
            return "[Изпълнението е прекъснато от потребителя]"

        if self.state.step >= self.state.max_steps:
            self.hook_engine.trigger(HookEvent.AGENT_END,
                session_id=self.session_manager.session_id, agent_id=self.agent_id,
                role=self.role, metadata={"output": f"[Достигнат е максимум от {self.state.max_steps} стъпки]"})
            return f"[Достигнат е максимум от {self.state.max_steps} стъпки]"

        return ""

    def _get_llm_response(self) -> ChatMessage:
        """Get response from LLM with streaming."""
        # Prepare messages
        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(self.state.messages)

        # Tool choice logic:
        # - First turn: no tools (tool_choice="none") unless user explicitly asks for action
        # - Subsequent turns: tools enabled
        # - Detect explicit action requests in user message
        is_first_turn = self.state.step == 1
        user_msg = self.state.messages[-1].content if self.state.messages else ""

        # Check if user explicitly asks for tool use
        action_keywords = ["run", "execute", "create", "write", "edit", "delete", "search", "find", "list", "git", "commit", "push", "pull", "test", "build", "install", "fix", "debug"]
        explicit_action = any(kw in user_msg.lower() for kw in action_keywords)

        # Enable tools if: not first turn, OR user explicitly asks for action
        enable_tools = not is_first_turn or explicit_action

        # Stream response
        full_content = ""
        tool_calls: dict[int, ToolCall] = {}

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
                    index = tc_delta.index if hasattr(tc_delta, 'index') else 0

                    if index not in tool_calls:
                        tool_calls[index] = ToolCall(
                            id=tc_delta.id,
                            type=tc_delta.type,
                            function={"name": "", "arguments": ""}
                        )

                    tc = tool_calls[index]
                    if tc_delta.function:
                        if tc_delta.function.get("name"):
                            tc.function["name"] = tc_delta.function["name"]
                        if tc_delta.function.get("arguments"):
                            tc.function["arguments"] += tc_delta.function["arguments"]

            # Handle finish reason
            if chunk.finish_reason == "tool_calls":
                pass

        # Build response message
        if tool_calls:
            sorted_calls = [tool_calls[i] for i in sorted(tool_calls.keys())]
            msg = ChatMessage(role="assistant", content=full_content, tool_calls=sorted_calls)
        else:
            msg = ChatMessage(role="assistant", content=full_content)

        self.state.messages.append(msg)
        return msg

    def _execute_tool_calls(self, tool_calls: list[ToolCall]) -> None:
        """Execute tool calls and add results to conversation."""
        for tc in tool_calls:
            if self._interrupt_event.is_set():
                self.state.interrupted = True
                break

            tool_name = tc.function.get("name", "")
            tool = self.tools.get(tool_name)

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
                    args = json.loads(args_str) if args_str else {}

                    # Add role and agent context for permission checking
                    args["_role"] = self.role
                    args["_agent_id"] = self.agent_id

                    # Emit tool start hook
                    self.hook_engine.trigger(HookEvent.TOOL_START,
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
            self.hook_engine.trigger(HookEvent.TOOL_END,
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

            # Emit tool end callback
            self.on_tool_end(result)

    def _get_agent_state(self) -> "AgentState":
        """Get current agent state for checkpointing."""
        return self.state

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        """Handle Ctrl+C interrupt."""
        self._interrupt_event.set()
        self.state.interrupted = True
        self.hook_engine.trigger(HookEvent.AGENT_ERROR,
            session_id=self.session_manager.session_id, agent_id=self.agent_id,
            role=self.role, error="Interrupted by user")
        print("\n[Прекъснато]", file=sys.stderr)


def create_agent_loop(config: Optional[Config] = None, **kwargs) -> AgentLoop:
    """Factory function to create agent loop."""
    if config is None:
        from ..config import get_config
        config = get_config()
    return AgentLoop(config, **kwargs)