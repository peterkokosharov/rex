# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# LLM: Nemotron 3 Ultra, claude-sonnet-5-5
# Licensed under MIT License

"""WebSearchTool - multi-engine web search tool for ai-rex.

A proper :class:`Tool` (same shape as fileops/edit): one ``ToolSchema`` and a
synchronous ``execute`` returning a plain dict that the executor wraps in a
``ToolResult``. The engine set and the User-Agent are configurable; the default
UA is honest (it does not impersonate a browser). Each engine is optional and
isolated, so one failing engine does not kill the search, and an engine error or
block is reported distinctly from a genuine "no results".
"""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

from .base import Tool, ToolSchema
from .engines import DEFAULT_ENGINES, ENGINE_WEIGHTS
from .engines.base import DEFAULT_USER_AGENT
from .engines.merger import merge_results

# Known engine name -> lazy constructor. Adding an engine is a one-line change.
_ENGINE_FACTORIES = {
    "brave": lambda ua: _import_brave()(ua),
    "bing": lambda ua: _import_bing()(ua),
    "duckduckgo": lambda ua: _import_ddg()(ua),
}


def _import_brave():
    from .engines.brave import BraveEngine
    return BraveEngine


def _import_bing():
    from .engines.bing import BingEngine
    return BingEngine


def _import_ddg():
    from .engines.duckduckgo import DuckDuckGoEngine
    return DuckDuckGoEngine


def _valid_result_url(url: str) -> bool:
    """True only for http/https URLs (drops javascript:, file:, data:, ...)."""
    if not url:
        return False
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https")


class WebSearchTool(Tool):
    """Web search across several engines, with deduplication and ranking."""

    def __init__(self, config: Any | None = None) -> None:
        self._config = config
        self._default_engines = self._config_engines(config)
        self._user_agent = self._config_user_agent(config)
        self._engines: dict[str, Any] = {}

        schema = ToolSchema(
            name="websearch",
            description="Search the web across multiple engines (Brave, Bing, DuckDuckGo), "
                        "deduplicate and rank the results.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of merged results to return",
                        "default": 10,
                    },
                    "engines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Engines to query; a subset of brave, bing, duckduckgo",
                        "default": list(self._default_engines),
                    },
                },
                "required": ["query"],
            },
        )
        super().__init__("websearch", schema.description, schema)

    # -- configuration ------------------------------------------------------
    @staticmethod
    def _config_engines(config: Any | None) -> list[str]:
        value = None
        if config is not None:
            try:
                value = config.get("websearch.engines", None)
            except Exception:
                value = None
        if isinstance(value, (list, tuple)) and value:
            return [str(e) for e in value]
        return list(DEFAULT_ENGINES)

    @staticmethod
    def _config_user_agent(config: Any | None) -> str:
        value = None
        if config is not None:
            try:
                value = config.get("websearch.user_agent", None)
            except Exception:
                value = None
        return str(value) if value else DEFAULT_USER_AGENT

    # -- engines ------------------------------------------------------------
    def _get_engine(self, name: str) -> Any | None:
        """Return (and cache) an engine instance, or None for an unknown name."""
        if name in self._engines:
            return self._engines[name]
        factory = _ENGINE_FACTORIES.get(name)
        if factory is None:
            return None
        engine = factory(self._user_agent)
        self._engines[name] = engine
        return engine

    # -- execution ----------------------------------------------------------
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Synchronous entry point used by the agent loop (wraps the async core)."""
        return asyncio.run(self._execute_async(**kwargs))

    async def _execute_async(self, **kwargs: Any) -> dict[str, Any]:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return {"success": False, "error": "Empty query", "query": "",
                    "results": [], "count": 0, "engines_used": [], "errors": {}}

        try:
            max_results = int(kwargs.get("max_results") or 10)
        except (TypeError, ValueError):
            max_results = 10
        max_results = max(1, max_results)

        requested = kwargs.get("engines") or self._default_engines
        engine_names = [str(e) for e in requested if str(e)]
        if not engine_names:
            return {"success": False, "error": "No engines configured", "query": query,
                    "results": [], "count": 0, "engines_used": [], "errors": {}}

        errors: dict[str, str] = {}
        engine_results: dict[str, list[dict[str, Any]]] = {}

        async def run_one(name: str) -> list[dict[str, Any]]:
            engine = self._get_engine(name)
            if engine is None:
                raise ValueError(f"unknown engine: {name}")
            # Over-fetch so that dedup still leaves enough after merging.
            results = await engine.search(query, max_results * 2)
            # Drop any result whose URL is not http/https.
            return [r for r in results if _valid_result_url(r.get("url", ""))]

        gathered = await asyncio.gather(
            *(run_one(name) for name in engine_names), return_exceptions=True
        )
        for name, outcome in zip(engine_names, gathered):
            if isinstance(outcome, BaseException):
                errors[name] = f"{type(outcome).__name__}: {outcome}"
            else:
                engine_results[name] = outcome

        merged = merge_results(engine_results, max_results=max_results,
                               engine_weights=dict(ENGINE_WEIGHTS))

        # Success when at least one engine answered without error. An empty
        # `errors` with count 0 therefore means "no results", not "blocked".
        success = bool(engine_results)
        out: dict[str, Any] = {
            "success": success,
            "query": query,
            "results": merged,
            "count": len(merged),
            "engines_used": list(engine_results.keys()),
            "errors": errors,
        }
        if not success:
            out["error"] = "all engines failed"
        return out


def create_websearch_tool(config: Any | None = None) -> WebSearchTool:
    return WebSearchTool(config)
