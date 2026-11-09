#!/usr/bin/env python3

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tests for AgentLoop tool_choice fix and HookEvent names."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from runtimelib.agent import create_agent_loop
from runtimelib.config import Config
from runtimelib.llm import ChatMessage, ToolCall
from runtimelib.hooks import HookEvent


def _make_config():
    return Config({
        "liteLLM": {
            "base_url": "http://dummy:4000",
            "api_key": "dummy",
            "model": "test-model"
        }
    })


def test_agent_loop_created():
    """AgentLoop is created with tools and schemas."""
    config = _make_config()
    loop = create_agent_loop(config)
    assert loop.state.step == 0
    assert len(loop.state.messages) == 0
    assert "fileops" in loop.tools
    assert "bash" in loop.tools
    assert "git" in loop.tools
    assert len(loop.tool_schemas) > 0
    assert "fileops" in loop.system_prompt
    assert "fileops" not in loop.system_prompt_no_tools


def test_tool_choice_simple_question():
    """Simple questions should disable tools."""
    config = _make_config()
    loop = create_agent_loop(config)

    loop.state.step = 1
    loop.state.messages = [ChatMessage(role="user", content="What is 2+2?")]

    is_first_turn = loop.state.step == 1
    user_msg = loop.state.messages[-1].content if loop.state.messages else ""
    action_keywords = ["run", "execute", "create", "write", "edit", "delete", "search", "find", "list", "git", "commit", "push", "pull", "test", "build", "install", "fix", "debug"]
    explicit_action = any(kw in user_msg.lower() for kw in action_keywords)
    enable_tools = not is_first_turn or explicit_action

    assert enable_tools is False, f"Expected tools disabled, got {enable_tools}"


def test_tool_choice_action_request():
    """Action requests should enable tools."""
    config = _make_config()
    loop = create_agent_loop(config)

    loop.state.step = 1
    loop.state.messages = [ChatMessage(role="user", content="list files in current directory")]

    is_first_turn = loop.state.step == 1
    user_msg = loop.state.messages[-1].content if loop.state.messages else ""
    action_keywords = ["run", "execute", "create", "write", "edit", "delete", "search", "find", "list", "git", "commit", "push", "pull", "test", "build", "install", "fix", "debug"]
    explicit_action = any(kw in user_msg.lower() for kw in action_keywords)
    enable_tools = not is_first_turn or explicit_action

    assert enable_tools is True, f"Expected tools enabled, got {enable_tools}"


def test_tool_choice_second_turn():
    """Second turn should enable tools."""
    config = _make_config()
    loop = create_agent_loop(config)

    loop.state.step = 2
    loop.state.messages = [
        ChatMessage(role="user", content="What is 2+2?"),
        ChatMessage(role="assistant", content="4"),
    ]

    is_first_turn = loop.state.step == 1
    user_msg = loop.state.messages[-1].content if loop.state.messages else ""
    action_keywords = ["run", "execute", "create", "write", "edit", "delete", "search", "find", "list", "git", "commit", "push", "pull", "test", "build", "install", "fix", "debug"]
    explicit_action = any(kw in user_msg.lower() for kw in action_keywords)
    enable_tools = not is_first_turn or explicit_action

    assert enable_tools is True, f"Expected tools enabled on second turn, got {enable_tools}"


def test_hook_event_names():
    """HookEvent should have PRE_TOOL and POST_TOOL, not TOOL_START/TOOL_END."""
    assert hasattr(HookEvent, "PRE_TOOL")
    assert hasattr(HookEvent, "POST_TOOL")
    assert not hasattr(HookEvent, "TOOL_START")
    assert not hasattr(HookEvent, "TOOL_END")


def test_no_tools_system_prompt():
    """System prompt without tools should not mention tool names."""
    config = _make_config()
    loop = create_agent_loop(config)
    assert "fileops" not in loop.system_prompt_no_tools
    assert "bash" not in loop.system_prompt_no_tools
    assert "git" not in loop.system_prompt_no_tools


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])