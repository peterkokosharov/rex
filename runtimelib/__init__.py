# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""runtimelib - Core library for Runtime AI agent platform."""

from __future__ import annotations

# Version
__version__ = "0.1.0"

# CLI
from .cli import CommandRegistry, CommandHandler, ReadlineInterface, HistoryManager, ArgumentParser

# Config
from .config import Config, ConfigSchema, validate_config, DEFAULT_CONFIG_PATH, DEFAULT_ENV_PATH, get_config
from .config.sources import FileSource, EnvSource, CLISource, EnvVarSource, merge_sources

# LLM
from .llm import LiteLLMClient, ChatMessage, ToolCall, ChatCompletionChunk, ChatCompletion
from .llm.models import ModelRegistry, ModelInfo, ModelClass, get_registry

# Guard
from .guard import GuardClient, get_guard_client

# REX
from .rex import REX, AgentRole, AgentContext, AgentResult, SubagentHandle, Subscription

# Tools
from .tools import Tool, ToolSchema, ToolResult, ToolRegistry, ToolExecutor
from .tools.fileops import FileOpsTool, create_fileops_tool
from .tools.bash import BashTool, create_bash_tool
from .tools.taskspawn import TaskSpawnTool, create_taskspawn_tool
from .tools.websearch import WebSearchTool, create_websearch_tool
from .tools.git import GitTool, create_git_tool

# Agent
from .agent import AgentLoop, AgentState, create_agent_loop
from .agent.state import CheckpointManager
from .agent.callbacks import StreamingCallbacks, TokenCallback, ToolCallback, ThinkingCallback

# Session
from .session import SessionManager, SessionEntry, create_session_manager

# Hooks
from .hooks import HookEngine, HookRegistry, Hook, HookEvent, HookContext, get_hook_engine

# Skills
from .skills import SkillRegistry, Skill, SkillSchema, SkillTrigger, SkillAutoMode, SkillEntrypoint, SkillHook

# MCP
from .mcp import MCPClient, MCPClientManager, MCPServerConfig, MCPTool, MCPResource, MCPPrompt, get_mcp_manager

# Logging
from .logging import setup_logging, get_logger, AuditLogger, set_trace_id, set_session_id, clear_context

__all__ = [
    # Version
    "__version__",

    # CLI
    "CommandRegistry",
    "CommandHandler",
    "ReadlineInterface",
    "HistoryManager",
    "ArgumentParser",

    # Config
    "Config",
    "ConfigSchema",
    "validate_config",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_PATH",
    "FileSource",
    "EnvSource",
    "CLISource",
    "EnvVarSource",
    "merge_sources",

    # LLM
    "LiteLLMClient",
    "ChatMessage",
    "ToolCall",
    "ChatCompletionChunk",
    "ChatCompletion",
    "ModelRegistry",
    "ModelInfo",
    "ModelClass",
    "get_registry",

    # Guard
    "GuardClient",
    "get_guard_client",

    # REX
    "REX",
    "AgentRole",
    "AgentContext",
    "AgentResult",
    "SubagentHandle",
    "Subscription",

    # Tools
    "Tool",
    "ToolSchema",
    "ToolResult",
    "ToolRegistry",
    "ToolExecutor",
    "FileOpsTool",
    "create_fileops_tool",
    "BashTool",
    "create_bash_tool",
    "TaskSpawnTool",
    "create_taskspawn_tool",
    "WebSearchTool",
    "create_websearch_tool",
    "GitTool",
    "create_git_tool",

    # Agent
    "AgentLoop",
    "AgentState",
    "create_agent_loop",
    "CheckpointManager",
    "StreamingCallbacks",
    "TokenCallback",
    "ToolCallback",
    "ThinkingCallback",

    # Session
    "SessionManager",
    "SessionEntry",
    "create_session_manager",

    # Hooks
    "HookEngine",
    "HookRegistry",
    "Hook",
    "HookEvent",
    "HookContext",
    "get_hook_engine",

    # Skills
    "SkillRegistry",
    "Skill",
    "SkillSchema",
    "SkillTrigger",
    "SkillAutoMode",
    "SkillEntrypoint",
    "SkillHook",

    # MCP
    "MCPClient",
    "MCPClientManager",
    "MCPServerConfig",
    "MCPTool",
    "MCPResource",
    "MCPPrompt",
    "get_mcp_manager",

    # Logging
    "setup_logging",
    "get_logger",
    "AuditLogger",
    "set_trace_id",
    "set_session_id",
    "clear_context",
]