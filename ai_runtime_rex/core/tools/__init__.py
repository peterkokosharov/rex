# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Tools framework.

Note: TaskSpawnTool has moved to orchestrator package (orchestrator.taskspawn).
The runtime keeps a thin wrapper for backward compatibility.
"""

from .base import Tool, ToolSchema, ToolResult
from .registry import ToolRegistry
from .executor import ToolExecutor
from .fileops import FileOpsTool, create_fileops_tool
from .bash import BashTool, create_bash_tool
from .websearch import WebSearchTool, create_websearch_tool
from .git import GitTool, create_git_tool
from .diagram import DiagramTool, create_diagram_tool
from .skills import LoadSkillTool, ReadSkillFileTool, create_skill_tools

from .taskspawn import TaskSpawnTool, create_taskspawn_tool

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
    "DiagramTool",
    "create_diagram_tool",
    "LoadSkillTool",
    "ReadSkillFileTool",
    "create_skill_tools",
]