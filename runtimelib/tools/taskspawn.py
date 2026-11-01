# SPDX-License-Identifier: MIT
"""Task spawn tool for subagent creation."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolSchema


class TaskSpawnTool(Tool):
    """Tool to spawn subagents for delegated tasks."""

    def __init__(self) -> None:
        schema = ToolSchema(
            name="task",
            description="Spawn a subagent to handle a delegated task",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Task description for the subagent",
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Allowed tools for subagent",
                    },
                    "model_class": {
                        "type": "string",
                        "description": "Model class to use (OPUS, SONNET, HAIKU, etc.)",
                        "default": "SONNET",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "Maximum steps for subagent",
                        "default": 20,
                    },
                },
                "required": ["prompt"],
            },
        )
        super().__init__("task", "Spawn subagent for delegated task", schema)

    def execute(self, **kwargs: Any) -> Any:
        # This will be implemented by the agent loop
        # For now, return a placeholder
        return {
            "status": "delegated",
            "prompt": kwargs["prompt"],
            "tools": kwargs.get("tools", []),
            "model_class": kwargs.get("model_class", "SONNET"),
            "note": "Subagent execution handled by orchestrator",
        }


def create_taskspawn_tool() -> TaskSpawnTool:
    return TaskSpawnTool()