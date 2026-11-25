# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-sonnet-5-5
# Licensed under MIT License

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
        # Honest stub: real orchestration is not wired here, so no subagent is
        # created. Report that plainly instead of faking a spawn (no agent_id,
        # no "success"/"spawned"/"delegated" claim) so the model is not misled.
        # This tool is intentionally not registered in the default tool set
        # (see AgentLoop._init_default_tools) until real spawning exists.
        return {
            "status": "not_implemented",
            "implemented": False,
            "error": "task spawning is not implemented in this build; no subagent was created",
            "prompt": kwargs.get("prompt", ""),
        }


def create_taskspawn_tool() -> TaskSpawnTool:
    return TaskSpawnTool()