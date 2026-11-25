# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra
# Licensed under MIT License

"""WebSearchTool — multi-engine web search tool for ai-rex."""

from __future__ import annotations

from typing import Any
import asyncio

from .base import Tool, ToolSchema, ToolResult


class WebSearchTool:
    """Web search tool using multiple engines with fallback."""

    name = "websearch"
    description = "Search the web using multiple engines (Brave, Bing, DuckDuckGo)"

    def __init__(self) -> None:
        self._schema = {
            "name": "websearch",
            "description": "Search the web using multiple engines (Brave, Bing, DuckDuckGo)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 10},
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["brave", "bing", "duckduckgo"],
                    },
                },
                "required": ["query"],
            },
        }
        self._engines = {}

    @property
    def schema(self) -> dict:
        return self._schema

    def __init__(self) -> None:
        self._engines = {}

    def _get_engine(self, name: str):
        """Get or create engine instance."""
        if name not in self._engines:
            if name == "brave":
                from .engines.brave import BraveEngine
                self._engines[name] = BraveEngine()
            elif name == "bing":
                from .engines.bing import BingEngine
                self._engines[name] = BingEngine()
            elif name == "duckduckgo":
                from .engines.duckduckgo import DuckDuckGoEngine
                self._engines[name] = DuckDuckGoEngine()
        return self._engines.get(name)

    @property
    def schema(self) -> dict:
        return {
            "name": "websearch",
            "description": "Search the web using multiple engines (Brave, Bing, DuckDuckGo)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 10},
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["brave", "bing", "duckduckgo"],
                    },
                },
                "required": ["query"],
            },
        }

    async def _execute_async(self, **kwargs) -> dict:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        engines = kwargs.get("engines", ["brave", "bing", "duckduckgo"])

        if not query or not query.strip():
            return {"success": False, "error": "Empty query", "results": []}

        # Get engine instances
        engine_instances = []
        for engine_name in kwargs.get("engines", ["brave", "bing", "duckduckgo"]):
            if engine := self._get_engine(engine_name):
                pass  # Engine is stored in self._engines

        # Get the actual engine instances
        engine_instances = [self._engines[name] for name in ["brave", "bing", "duckduckgo"] if name in self._engines]

        if not any(self._engines.values()):
            return {"success": False, "error": "No valid engines", "results": []}

        # Run searches concurrently
        async def search_engine(engine):
            try:
                return engine.name, await engine.search(query, max_results * 2)
            except Exception:
                return engine.name, []

        tasks = [search_engine(engine) for engine in self._engines.values()]
        engine_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        engine_results_dict = {}
        for result in engine_results:
            if isinstance(result, tuple):
                engine_name, results = result
                if isinstance(results, list):
                    engine_results_dict[engine_name] = results

        # Merge results
        from .engines.merger import merge_results
        merged = merge_results(engine_results_dict, max_results=max_results)

        return {
            "success": True,
            "results": merged,
            "count": len(merged),
        }

    async def _execute_async(self, **kwargs) -> dict:
        query = kwargs.get("query", "")
        max_results = kwargs.get("max_results", 10)
        engines = kwargs.get("engines", ["brave", "bing", "duckduckgo"])

        if not query or not query.strip():
            return {"success": False, "error": "Empty query", "results": []}

        # Get engine instances
        engine_instances = []
        for engine_name in kwargs.get("engines", ["brave", "bing", "duckduckgo"]):
            if engine := self._get_engine(engine_name):
                pass  # Engine is stored in self._engines

        # Get the actual engine instances
        engine_instances = [self._engines[name] for name in ["brave", "bing", "duckduckgo"] if name in self._engines]

        if not any(self._engines.values()):
            return {"success": False, "error": "No valid engines", "results": []}

        # Run searches concurrently
        async def search_engine(engine):
            try:
                return engine.name, await engine.search(query, max_results * 2)
            except Exception:
                return engine.name, []

        tasks = [search_engine(engine) for engine in self._engines.values()]
        engine_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        engine_results_dict = {}
        for result in engine_results:
            if isinstance(result, tuple):
                engine_name, results = result
                if isinstance(results, list):
                    engine_results_dict[engine_name] = results

        # Merge results
        from .engines.merger import merge_results
        merged = merge_results(engine_results_dict, max_results=max_results)

        return {
            "success": True,
            "results": merged,
            "count": len(merged),
        }

    def execute(self, **kwargs) -> dict:
        """Synchronous wrapper for execute."""
        return asyncio.run(self._execute_async(**kwargs))

    def _get_engine(self, name: str):
        """Get or create engine instance."""
        if name not in self._engines:
            if name == "brave":
                from .engines.brave import BraveEngine
                self._engines[name] = BraveEngine()
            elif name == "bing":
                from .engines.bing import BingEngine
                self._engines[name] = BingEngine()
            elif name == "duckduckgo":
                from .engines.duckduckgo import DuckDuckGoEngine
                self._engines[name] = DuckDuckGoEngine()
        return self._engines.get(name)

    @property
    def schema(self) -> dict:
        return {
            "name": "websearch",
            "description": "Search the web using multiple engines (Brave, Bing, DuckDuckGo)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 10},
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["brave", "bing", "duckduckgo"],
                    },
                },
                "required": ["query"],
            },
        }

    def execute(self, **kwargs) -> dict:
        """Synchronous wrapper for execute."""
        import asyncio
        return asyncio.run(self._execute_async(**kwargs))

    def to_openai(self) -> dict:
        return self.schema


# For backward compatibility with runtimelib.tools.__init__
def create_websearch_tool() -> "WebSearchTool":
    return WebSearchTool()