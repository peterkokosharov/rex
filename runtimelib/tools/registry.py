# SPDX-License-Identifier: MIT
"""Tool registry for discovery and management."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .base import Tool, ToolSchema


class ToolRegistry:
    """Registry for tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._factories: dict[str, Callable[[], Tool]] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def register_factory(self, name: str, factory: Callable[[], Tool]) -> None:
        """Register a tool factory for lazy loading."""
        self._factories[name] = factory

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name (loads from factory if needed)."""
        if name in self._tools:
            return self._tools[name]
        if name in self._factories:
            tool = self._factories[name]()
            self._tools[name] = tool
            return tool
        return None

    def list_tools(self) -> list[str]:
        """List all available tool names."""
        return sorted(set(self._tools.keys()) | set(self._factories.keys()))

    def get_schemas(self) -> list[ToolSchema]:
        """Get OpenAI schemas for all registered tools."""
        return [tool.schema for tool in self._tools.values()]

    def get_openai_schemas(self) -> list[dict[str, Any]]:
        """Get OpenAI function calling schemas for all tools."""
        return [tool.to_openai() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self._tools or name in self._factories

    def remove(self, name: str) -> bool:
        """Remove a tool."""
        removed = False
        if name in self._tools:
            del self._tools[name]
            removed = True
        if name in self._factories:
            del self._factories[name]
            removed = True
        return removed


# Global registry
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry