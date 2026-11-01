# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra
# Licensed under MIT License

"""Base tool classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSchema:
    """JSON Schema for tool parameters."""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    success: bool
    result: Any = None
    error: str | None = None


class Tool(ABC):
    """Base class for all tools."""

    def __init__(self, name: str, description: str, schema: ToolSchema) -> None:
        self.name = name
        self.description = description
        self.schema = schema

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """Execute the tool with given parameters."""
        pass

    def to_openai(self) -> dict[str, Any]:
        return self.schema.to_openai()