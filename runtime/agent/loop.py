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
from ..tools import FileOpsTool, BashTool, TaskSpawnTool, WebSearchTool
from ..tools.base import Tool
from ..guard import get_guard_client


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None


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

        # Interrupt handling
        self._interrupt_event = threading.Event()
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _init_default_tools(self) -> None:
        """Initialize default tools."""
        self.tools["fileops"] = FileOpsTool(self.config)
        self.tools["bash"] = BashTool(self.config)
        self.tools["task"] = TaskSpawnTool()
        self.tools["websearch"] = WebSearchTool()

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
        print("\n[Прекъснато]", file=sys.stderr)

    def run(self, prompt: str) -> str:
        """Run the agent loop with a user prompt."""
        # Add user message
        self.state.messages.append(ChatMessage(role="user", content=prompt))

        while self.state.step < self.state.max_steps and not self.state.interrupted:
            self.state.step += 1

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
                return response.content

            # If no content and no tool calls, continue
            if not response.content and not response.tool_calls:
                self.state.messages.append(ChatMessage(role="assistant", content=""))
                continue

        if self.state.interrupted:
            return "[Изпълнението е прекъснато от потребителя]"

        if self.state.step >= self.state.max_steps:
            return f"[Достигнат е максимум от {self.state.max_steps} стъпки]"

        return ""

    def _get_llm_response(self) -> ChatMessage:
        """Get response from LLM with streaming."""
        # Prepare messages
        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(self.state.messages)

        # Stream response
        full_content = ""
        tool_calls: dict[int, ToolCall] = {}
        current_tool_call: Optional[ToolCall] = None

        stream = self.llm.chat_completion(
            messages=messages,
            tools=self.tool_schemas if self.tool_schemas else None,
            tool_choice="auto" if self.tool_schemas else None,
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
                # Tool calls complete, will be processed below
                pass

        # Build response message
        if tool_calls:
            # Sort by index to maintain order
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

                    # Notify tool start
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

            # Notify tool end
            self.on_tool_end(result)

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


def create_agent_loop(config: Optional[Config] = None, **kwargs) -> AgentLoop:
    """Factory function to create agent loop."""
    if config is None:
        from .config import get_config
        config = get_config()
    return AgentLoop(config, **kwargs)