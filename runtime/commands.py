# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Runtime-specific commands using runtimelib."""

from __future__ import annotations

from typing import Any, Callable

import runtimelib
from runtimelib import Config, CommandRegistry
from runtimelib.tools.base import ToolResult


CommandHandler = Callable[[dict[str, Any]], int]


def register_runtime_commands(registry: CommandRegistry, config: Config) -> None:
    """Register runtime-specific commands."""

    @registry.register("guard", "Manage the Docker guard")
    def cmd_guard(args: dict[str, Any]) -> int:
        action = args.get("_0", "status")
        if action == "start":
            from runtimelib.guard import get_guard_client
            client = get_guard_client(config)
            client.start()
            print("Guard container started")
        elif action == "stop":
            from runtimelib.guard import get_guard_client
            client = get_guard_client(config)
            client.stop()
            print("Guard container stopped")
        elif action == "status":
            print("Guard status: not implemented")
        else:
            print(f"Unknown action: {action}. Use: start, stop, status")
        return 0

    @registry.register("agent", "Manage the agent")
    def cmd_agent(args: dict[str, Any]) -> int:
        action = args.get("_0", "info")
        if action == "info":
            from runtimelib.agent import create_agent_loop
            loop = create_agent_loop(config)
            print(f"Max steps: {loop.state.max_steps}")
            print(f"Tools: {', '.join(loop.tools.keys())}")
            print(f"Model: {loop.llm.default_model}")
        elif action == "reset":
            print("The agent will restart on the next command")
        else:
            print(f"Unknown action: {action}")
        return 0

    @registry.register("tool", "Run a tool directly")
    def cmd_tool(args: dict[str, Any]) -> int:
        tool_name = args.get("_0", "")
        if not tool_name:
            print("Usage: /tool <name> [args...]")
            return 1

        from runtimelib.tools import get_registry
        tool_registry = get_registry()

        # Register default tools if not already
        if not tool_registry.list_tools():
            from runtimelib.tools.fileops import create_fileops_tool
            from runtimelib.tools.bash import create_bash_tool
            from runtimelib.tools.taskspawn import create_taskspawn_tool
            from runtimelib.tools.websearch import create_websearch_tool
            from runtimelib.tools.git import create_git_tool

            tool_registry.register(create_fileops_tool(config))
            tool_registry.register(create_bash_tool(config))
            tool_registry.register(create_taskspawn_tool())
            tool_registry.register(create_websearch_tool())
            tool_registry.register(create_git_tool(config))

        tool = tool_registry.get(tool_name)
        if not tool:
            print(f"Tool not found: {tool_name}")
            print(f"Available: {', '.join(tool_registry.list_tools())}")
            return 1

        # Parse remaining args as tool arguments
        tool_args = {}
        for key, value in args.items():
            if not key.startswith("_"):
                tool_args[key] = value

        try:
            result = tool.execute(**tool_args)
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"Error: {e}")
        return 0

    @registry.register("history", "Conversation history")
    def cmd_history(args: dict[str, Any]) -> int:
        action = args.get("_0", "show")
        if action == "show":
            limit = args.get("limit", 20)
            from runtimelib.session import create_session_manager
            session = create_session_manager()
            history = session.get_history(limit=limit)
            for entry in history:
                print(f"[{entry.timestamp}] {entry.type}: {entry.data}")
        elif action == "clear":
            from runtimelib.session import create_session_manager
            session = create_session_manager()
            session.new_session()
            print("History cleared")
        elif action == "export":
            path = args.get("_1", "session_export.json")
            from runtimelib.session import create_session_manager
            session = create_session_manager()
            session.export_json(Path(path))
            print(f"Exported to {path}")
        return 0


from pathlib import Path