# SPDX-License-Identifier: MIT

# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: nvidia nemotron ultra, claude-opus-5-5
# Licensed under MIT License

"""Base tool classes."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


def _clip(text: Any, limit: int = 200) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


def default_describe(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Default (summary, detail) of a call for permission prompts.

    bash shows the command, fileops ``op path`` with the text to write or the
    replacement, other tools their arguments as JSON. Internal ``_`` keys are left out.
    """
    args = {k: v for k, v in args.items() if not str(k).startswith("_")}
    if name == "bash":
        command = str(args.get("command", ""))
        summary = "bash: " + _clip(command.splitlines()[0] if command else "", 120)
        detail = command
        cwd = args.get("cwd")
        if cwd not in (None, "", "."):
            summary += f" (cwd: {_clip(cwd, 60)})"
            detail += f"\n\ncwd: {cwd}"
        env = args.get("env")
        if env:
            items = list(env.items()) if isinstance(env, dict) else [("?", env)]
            summary += " (env: " + _clip(", ".join(str(k) for k, _ in items), 60) + ")"
            detail += "\n\nenv:" + "".join(f"\n  {k}={v}" for k, v in items)
        return summary, detail
    if name == "fileops":
        op = str(args.get("operation") or args.get("op") or "read")
        path = str(args.get("path", ""))
        detail = ""
        if "content" in args:
            detail = str(args["content"])
        elif "old_str" in args or "new_str" in args:
            detail = "--- old\n" + str(args.get("old_str", "")) + "\n+++ new\n" + str(args.get("new_str", ""))
        return f"fileops {op} {path}".strip(), detail
    try:
        detail = json.dumps(args, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        detail = str(args)
    return f"{name}: " + _clip(json.dumps(args, ensure_ascii=False, default=str), 120), detail


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

    def describe(self, args: dict[str, Any]) -> tuple[str, str]:
        """(summary, detail) shown when the call needs permission. Override for a better preview."""
        return default_describe(self.name, args)

    def to_openai(self) -> dict[str, Any]:
        return self.schema.to_openai()