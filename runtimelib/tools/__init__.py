# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tools framework."""

from .base import Tool, ToolSchema, ToolResult
from .registry import ToolRegistry
from .executor import ToolExecutor
from .fileops import FileOpsTool, create_fileops_tool
from .bash import BashTool, create_bash_tool
from .taskspawn import TaskSpawnTool, create_taskspawn_tool
from .websearch import WebSearchTool, create_websearch_tool
from .git import GitTool, create_git_tool

__all__ = [
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
]